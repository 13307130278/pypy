from rpython.jit.backend.llsupport.rewrite import GcRewriterAssembler
from rpython.jit.backend.llsupport.descr import (
    CallDescr, FieldDescr, InteriorFieldDescr, ArrayDescr)
from rpython.jit.metainterp.resoperation import ResOperation, rop
from rpython.jit.metainterp.history import BoxPtr, ConstPtr, ConstInt
from rpython.rlib.objectmodel import specialize
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.debug import (have_debug_prints, debug_start, debug_stop,
                                debug_print)
from rpython.jit.codewriter.effectinfo import EffectInfo


class GcStmRewriterAssembler(GcRewriterAssembler):
    # This class performs the same rewrites as its base class,
    # plus the rewrites described above.

    def __init__(self, *args):
        GcRewriterAssembler.__init__(self, *args)
        self.always_inevitable = False
        self.read_barrier_applied = {}

    def other_operation(self, op):
        opnum = op.getopnum()
        if opnum == rop.INCREMENT_DEBUG_COUNTER:
            self.newops.append(op)
            return
        # ----------  transaction breaks  ----------
        if opnum == rop.STM_TRANSACTION_BREAK:
            self.emitting_an_operation_that_can_collect()
            self.next_op_may_be_in_new_transaction()
            self.newops.append(op)
            return
        # ----------  pure operations, guards  ----------
        if op.is_always_pure() or op.is_guard() or op.is_ovf():
            self.newops.append(op)
            return
        # ----------  non-pure getfields  ----------
        if opnum in (rop.GETFIELD_GC, rop.GETARRAYITEM_GC,
                     rop.GETINTERIORFIELD_GC):
            self.handle_getfields(op)
            return
        # ----------  calls  ----------
        if op.is_call():
            if opnum == rop.CALL and op.getdescr():
                d = op.getdescr()
                assert isinstance(d, CallDescr)
                ei = d.get_extra_info()
                if ei and (ei.oopspecindex ==
                           EffectInfo.OS_JIT_STM_SHOULD_BREAK_TRANSACTION):
                    self.newops.append(op)
                    return
            #
            self.next_op_may_be_in_new_transaction()
            #
            if opnum == rop.CALL_RELEASE_GIL:
                # self.fallback_inevitable(op)
                # is done by assembler._release_gil_shadowstack()
                self.newops.append(op)
            elif opnum == rop.CALL_ASSEMBLER:
                assert 0   # case handled by the parent class
            else:
                # only insert become_inevitable if calling a
                # non-transactionsafe and non-releasegil function
                descr = op.getdescr()
                assert not descr or isinstance(descr, CallDescr)

                if not descr or not descr.get_extra_info() \
                      or descr.get_extra_info().call_needs_inevitable():
                    self.fallback_inevitable(op)
                else:
                    self.newops.append(op)
            return
        # ----------  setters for pure fields  ----------
        if opnum in (rop.STRSETITEM, rop.UNICODESETITEM):
            self.handle_setters_for_pure_fields(op)
            return
        # ----------  copystrcontent  ----------
        if opnum in (rop.COPYSTRCONTENT, rop.COPYUNICODECONTENT):
            self.handle_copystrcontent(op)
            return
        # ----------  raw getfields and setfields  ----------
        if opnum in (rop.GETFIELD_RAW, rop.SETFIELD_RAW):
            if self.maybe_handle_raw_accesses(op):
                return
        # ----------  labels  ----------
        if opnum == rop.LABEL:
            # note that the parent class also clears some things on a LABEL
            self.next_op_may_be_in_new_transaction()
            self.newops.append(op)
            return
        # ----------  jumps, finish, other ignored ops  ----------
        if opnum in (rop.JUMP, rop.FINISH, rop.FORCE_TOKEN,
                     rop.READ_TIMESTAMP, rop.MARK_OPAQUE_PTR,
                     rop.JIT_DEBUG, rop.KEEPALIVE,
                     rop.QUASIIMMUT_FIELD, rop.RECORD_KNOWN_CLASS,
                     ):
            self.newops.append(op)
            return
        # ----------  fall-back  ----------
        # Check that none of the ops handled here can collect.
        # This is not done by the fallback here
        assert not op.is_call() and not op.is_malloc()
        self.fallback_inevitable(op)

    def next_op_may_be_in_new_transaction(self):
        self.always_inevitable = False
        self.read_barrier_applied.clear()

    def handle_getfields(self, op):
        # XXX missing optimitations: the placement of stm_read should
        # ideally be delayed for a bit longer after the getfields; if we
        # group together several stm_reads then we can save one
        # instruction; if delayed over a cond_call_gc_wb then we can
        # omit the stm_read completely; ...
        self.newops.append(op)
        v_ptr = op.getarg(0)
        if (v_ptr not in self.read_barrier_applied and
            v_ptr not in self.write_barrier_applied):
            op1 = ResOperation(rop.STM_READ, [v_ptr], None)
            self.newops.append(op1)
            self.read_barrier_applied[v_ptr] = None


    def must_apply_write_barrier(self, val, v):
        return val not in self.write_barrier_applied


    def handle_copystrcontent(self, op):
        xxxxxxxx
        # first, a write barrier on the target string
        lst = op.getarglist()
        lst[1] = self.gen_barrier(lst[1], 'W')
        op = op.copy_and_change(op.getopnum(), args=lst)
        # then an immutable read barrier the source string
        # XXX: 'I' enough?
        self.handle_category_operations(op, 'R')

    @specialize.arg(1)
    def _do_stm_call(self, funcname, args, result):
        addr = self.gc_ll_descr.get_malloc_fn_addr(funcname)
        descr = getattr(self.gc_ll_descr, funcname + '_descr')
        op1 = ResOperation(rop.CALL, [ConstInt(addr)] + args,
                           result, descr=descr)
        self.newops.append(op1)

    def fallback_inevitable(self, op):
        if not self.always_inevitable:
            self.emitting_an_operation_that_can_collect()
            self._do_stm_call('stm_try_inevitable', [], None)
            self.always_inevitable = True
        self.newops.append(op)
        debug_print("fallback for", op.repr())

    def maybe_handle_raw_accesses(self, op):
        xxxxx
        from rpython.jit.backend.llsupport.descr import FieldDescr
        descr = op.getdescr()
        assert isinstance(descr, FieldDescr)
        if descr.stm_dont_track_raw_accesses:
            self.newops.append(op)
            return True
        return False
