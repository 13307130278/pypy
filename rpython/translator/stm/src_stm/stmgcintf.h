#ifndef _RPY_STMGCINTF_H
#define _RPY_STMGCINTF_H


/* meant to be #included after src_stm/stmgc.h */

#include <errno.h>
#include "stmgc.h"
#include "stm/atomic.h"    /* for spin_loop() and write_fence() */

extern __thread struct stm_thread_local_s stm_thread_local;
extern __thread long pypy_stm_ready_atomic;
extern __thread uintptr_t pypy_stm_nursery_low_fill_mark;

void pypy_stm_setup(void);
void pypy_stm_setup_prebuilt(void);   /* generated into stm_prebuilt.c */

static inline void pypy_stm_commit_if_not_atomic(void) {
    int e = errno;
    if (pypy_stm_ready_atomic == 1) {
        stm_commit_transaction();
    }
    else {
        stm_become_inevitable("commit_if_not_atomic in atomic");
    }
    errno = e;
}
static inline void pypy_stm_start_inevitable_if_not_atomic(void) {
    if (pypy_stm_ready_atomic == 1) {
        int e = errno;
        stm_start_inevitable_transaction(&stm_thread_local);
        pypy_stm_nursery_low_fill_mark = 0;
        errno = e;
    }
}
static inline void pypy_stm_increment_atomic(void) {
    pypy_stm_ready_atomic++;
}
static inline void pypy_stm_decrement_atomic(void) {
    if (--pypy_stm_ready_atomic == 0)
        pypy_stm_ready_atomic = 1;
}
static inline long pypy_stm_get_atomic(void) {
    return pypy_stm_ready_atomic - 1;
}
long pypy_stm_enter_callback_call(void);
void pypy_stm_leave_callback_call(long);
void pypy_stm_set_transaction_length(long);
void pypy_stm_perform_transaction(object_t *, int(object_t *, int));

static inline int pypy_stm_should_break_transaction(void)
{
    /* we should break the current transaction if we have used more than
       some initial portion of the nursery, or if we are running inevitable
       (in which case pypy_stm_nursery_low_fill_mark is set to 0)
    */
    uintptr_t current = (uintptr_t)STM_SEGMENT->nursery_current;
    return current > pypy_stm_nursery_low_fill_mark;
}


#if 0    /* fprinting versions */
# define spinlock_acquire(lock, targetvalue)                            \
    do { if (__sync_bool_compare_and_swap(&(lock), 0, (targetvalue))) { \
             dprintf(("<<< locked %d\n", (int)targetvalue));            \
             break;                                                     \
         }                                                              \
         do { spin_loop(); } while (lock);                              \
    } while (1)
# define spinlock_release(lock)                                         \
    do { dprintf(("unlocked >>>\n")); write_fence();                    \
         assert((lock) != 0); (lock) = 0; } while (0)
#else
# define spinlock_acquire(lock, targetvalue)                                 \
    do { if (__sync_bool_compare_and_swap(&(lock), 0, (targetvalue))) break; \
         do { spin_loop(); } while (lock);                                   \
    } while (1)
# define spinlock_release(lock)                                 \
    do { write_fence(); assert((lock) != 0); (lock) = 0; } while (0)
#endif


#endif  /* _RPY_STMGCINTF_H */
