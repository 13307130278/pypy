/* Imported by rpython/translator/stm/import_stmgc.py */
#ifndef _STM_CORE_H_
# error "must be compiled via stmgc.c"
#endif


enum contention_kind_e {

    /* A write-write contention occurs when we running our transaction
       and detect that we are about to write to an object that another
       thread is also writing to.  This kind of contention must be
       resolved before continuing.  This *must* abort one of the two
       threads: the caller's thread is not at a safe-point, so cannot
       wait! */
    WRITE_WRITE_CONTENTION,

    /* A write-read contention occurs when we are trying to commit: it
       means that an object we wrote to was also read by another
       transaction.  Even though it would seem obvious that we should
       just abort the other thread and proceed in our commit, a more
       subtle answer would be in some cases to wait for the other thread
       to commit first.  It would commit having read the old value, and
       then we can commit our change to it. */
    WRITE_READ_CONTENTION,

    /* An inevitable contention occurs when we're trying to become
       inevitable but another thread already is.  We can never abort the
       other thread in this case, but we still have the choice to abort
       ourselves or pause until the other thread commits. */
    INEVITABLE_CONTENTION,
};

struct contmgr_s {
    enum contention_kind_e kind;
    struct stm_priv_segment_info_s *other_pseg;
    bool abort_other;
    bool try_sleep;  // XXX add a way to timeout, but should handle repeated
                     // calls to contention_management() to avoid re-sleeping
                     // for the whole duration
};


/************************************************************/


__attribute__((unused))
static void cm_always_abort_myself(struct contmgr_s *cm)
{
    cm->abort_other = false;
}

__attribute__((unused))
static void cm_always_abort_other(struct contmgr_s *cm)
{
    cm->abort_other = true;
}

__attribute__((unused))
static void cm_abort_the_younger(struct contmgr_s *cm)
{
    if (STM_PSEGMENT->start_time >= cm->other_pseg->start_time) {
        /* We started after the other thread.  Abort */
        cm->abort_other = false;
    }
    else {
        cm->abort_other = true;
    }
}

__attribute__((unused))
static void cm_always_wait_for_other_thread(struct contmgr_s *cm)
{
    cm_abort_the_younger(cm);
    cm->try_sleep = true;
}

__attribute__((unused))
static void cm_pause_if_younger(struct contmgr_s *cm)
{
    if (STM_PSEGMENT->start_time >= cm->other_pseg->start_time) {
        /* We started after the other thread.  Pause */
        cm->try_sleep = true;
        cm->abort_other = false;
    }
    else {
        cm->abort_other = true;
    }
}


/************************************************************/


static void contention_management(uint8_t other_segment_num,
                                  enum contention_kind_e kind)
{
    assert(_has_mutex());
    assert(other_segment_num != STM_SEGMENT->segment_num);

    if (must_abort())
        abort_with_mutex();

    /* Who should abort here: this thread, or the other thread? */
    struct contmgr_s contmgr;
    contmgr.kind = kind;
    contmgr.other_pseg = get_priv_segment(other_segment_num);
    contmgr.abort_other = false;
    contmgr.try_sleep = false;

    /* Pick one contention management... could be made dynamically choosable */
#ifdef STM_TESTS
    cm_abort_the_younger(&contmgr);
#else
    cm_always_wait_for_other_thread(&contmgr);
#endif

    /* Fix the choices that are found incorrect due to TS_INEVITABLE
       or NSE_SIGABORT */
    if (contmgr.other_pseg->pub.nursery_end == NSE_SIGABORT) {
        contmgr.abort_other = true;
        contmgr.try_sleep = false;
    }
    else if (STM_PSEGMENT->transaction_state == TS_INEVITABLE) {
        assert(contmgr.other_pseg->transaction_state != TS_INEVITABLE);
        contmgr.abort_other = true;
    }
    else if (contmgr.other_pseg->transaction_state == TS_INEVITABLE) {
        contmgr.abort_other = false;
    }

    if (contmgr.try_sleep && kind != WRITE_WRITE_CONTENTION &&
        contmgr.other_pseg->safe_point != SP_WAIT_FOR_C_TRANSACTION_DONE) {
        /* Sleep.

           - Not for write-write contentions, because we're not at a
             safe-point.

           - To prevent loops of threads waiting for each others, use
             a crude heuristic of never pausing for a thread that is
             itself already paused here.
        */
        contmgr.other_pseg->signal_when_done = true;

        dprintf(("pausing...\n"));
        cond_signal(C_AT_SAFE_POINT);
        STM_PSEGMENT->safe_point = SP_WAIT_FOR_C_TRANSACTION_DONE;
        cond_wait(C_TRANSACTION_DONE);
        STM_PSEGMENT->safe_point = SP_RUNNING;
        dprintf(("pausing done\n"));

        if (must_abort())
            abort_with_mutex();
    }
    else if (!contmgr.abort_other) {
        dprintf(("abort in contention\n"));
        abort_with_mutex();
    }
    else {
        /* We have to signal the other thread to abort, and wait until
           it does. */
        contmgr.other_pseg->pub.nursery_end = NSE_SIGABORT;

        int sp = contmgr.other_pseg->safe_point;
        switch (sp) {

        case SP_RUNNING:
            /* The other thread is running now, so as NSE_SIGABORT was
               set in its 'nursery_end', it will soon enter a
               mutex_lock() and thus abort.

               In this case, we will wait until it broadcasts "I'm done
               aborting".  Important: this is not a safe point of any
               kind!  The shadowstack may not be correct here.  It
               should not end in a deadlock, because the target thread
               is, in principle, guaranteed to call abort_with_mutex()
               very soon.
            */
            dprintf(("contention: wait C_ABORTED...\n"));
            cond_wait(C_ABORTED);
            dprintf(("contention: done\n"));

            if (must_abort())
                abort_with_mutex();
            break;

        /* The other cases are where the other thread is at a
           safe-point.  We wake it up by sending the correct signal.
           We don't have to wait here: the other thread will not do
           anything more than abort when it really wakes up later.
        */
        case SP_WAIT_FOR_C_REQUEST_REMOVED:
            cond_broadcast(C_REQUEST_REMOVED);
            break;

        case SP_WAIT_FOR_C_AT_SAFE_POINT:
            cond_broadcast(C_AT_SAFE_POINT);
            break;

        case SP_WAIT_FOR_C_TRANSACTION_DONE:
            cond_broadcast(C_TRANSACTION_DONE);
            break;

#ifdef STM_TESTS
        case SP_WAIT_FOR_OTHER_THREAD:
            /* for tests: the other thread will abort as soon as
               stm_stop_safe_point() is called */
            break;
#endif

        default:
            stm_fatalerror("unexpected other_pseg->safe_point: %d", sp);
        }

        if (is_aborting_now(other_segment_num)) {
            /* The other thread is blocked in a safe-point with NSE_SIGABORT.
               We don't have to wake it up right now, but we know it will
               abort as soon as it wakes up.  We can safely force it to
               reset its state now. */
            dprintf(("killing data structures\n"));
            abort_data_structures_from_segment_num(other_segment_num);
        }
        dprintf(("killed other thread\n"));
    }
}

static void write_write_contention_management(uintptr_t lock_idx)
{
    s_mutex_lock();

    uint8_t prev_owner = ((volatile uint8_t *)write_locks)[lock_idx];
    if (prev_owner != 0 && prev_owner != STM_PSEGMENT->write_lock_num) {

        uint8_t other_segment_num = prev_owner - 1;
        assert(get_priv_segment(other_segment_num)->write_lock_num ==
               prev_owner);

        contention_management(other_segment_num, WRITE_WRITE_CONTENTION);

        /* now we return into _stm_write_slowpath() and will try again
           to acquire the write lock on our object. */
    }

    s_mutex_unlock();
}

static void write_read_contention_management(uint8_t other_segment_num)
{
    contention_management(other_segment_num, WRITE_READ_CONTENTION);
}

static void inevitable_contention_management(uint8_t other_segment_num)
{
    contention_management(other_segment_num, INEVITABLE_CONTENTION);
}