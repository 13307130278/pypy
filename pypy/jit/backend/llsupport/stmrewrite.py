from pypy.jit.backend.llsupport.rewrite import GcRewriterAssembler
from pypy.jit.metainterp.resoperation import ResOperation, rop
from pypy.jit.metainterp.history import BoxPtr, ConstPtr, ConstInt

#
# STM Support
# -----------    
#
# Any SETFIELD_GC, SETARRAYITEM_GC, SETINTERIORFIELD_GC must be done on a
# W object.  The operation that forces an object p1 to be W is
# COND_CALL_GC_WB(p1, 0, descr=x2Wdescr), for x in 'PGORL'.  This
# COND_CALL_GC_WB is a bit special because if p1 is not W, it *replaces*
# its value with the W copy (by changing the register's value and
# patching the stack location if any).  It's still conceptually the same
# object, but the pointer is different.
#
# The case of GETFIELD_GC & friends is similar, excepted that it goes to
# a R or L object (at first, always a R object).
#
# The name "x2y" of write barriers is called the *category* or "cat".
#


class GcStmRewriterAssembler(GcRewriterAssembler):
    # This class performs the same rewrites as its base class,
    # plus the rewrites described in stm.txt.

    def __init__(self, *args):
        GcRewriterAssembler.__init__(self, *args)
        self.known_category = {}    # variable: letter (R, W, ...)
        self.always_inevitable = False
        self.more_precise_categories = {
            'P': {'R': self.gc_ll_descr.P2Rdescr,
                  'W': self.gc_ll_descr.P2Wdescr,
                 },
            'R': {'W': self.gc_ll_descr.R2Wdescr,
                 },
            'W': {},
           }

    def rewrite(self, operations):
        # overridden method from parent class
        #
        for op in operations:
            if op.getopnum() == rop.DEBUG_MERGE_POINT:
                continue
            # ----------  pure operations, guards  ----------
            if op.is_always_pure() or op.is_guard() or op.is_ovf():
                self.newops.append(op)
                continue
            # ----------  getfields  ----------
            if op.getopnum() in (rop.GETFIELD_GC,
                                 rop.GETARRAYITEM_GC,
                                 rop.GETINTERIORFIELD_GC):
                self.handle_category_operations(op, 'R')
                continue
            # ----------  setfields  ----------
            if op.getopnum() in (rop.SETFIELD_GC,
                                 rop.SETARRAYITEM_GC,
                                 rop.SETINTERIORFIELD_GC,
                                 rop.STRSETITEM,
                                 rop.UNICODESETITEM):
                self.handle_category_operations(op, 'W')
                continue
            # ----------  mallocs  ----------
            if op.is_malloc():
                self.handle_malloc_operation(op)
                continue
            # ----------  calls  ----------
            if op.is_call():
                self.known_category.clear()
                if op.getopnum() == rop.CALL_RELEASE_GIL:
                    self.fallback_inevitable(op)
                else:
                    self.newops.append(op)
                continue
            # ----------  copystrcontent  ----------
            if op.getopnum() in (rop.COPYSTRCONTENT,
                                 rop.COPYUNICODECONTENT):
                self.handle_copystrcontent(op)
                continue
            # ----------  labels  ----------
            if op.getopnum() == rop.LABEL:
                self.known_category.clear()
                self.always_inevitable = False
                self.newops.append(op)
                continue
            # ----------  jump, finish, other ignored ops  ----------
            if op.getopnum() in (rop.JUMP,
                                 rop.FINISH,
                                 rop.FORCE_TOKEN,
                                 rop.READ_TIMESTAMP,
                                 rop.MARK_OPAQUE_PTR,
                                 rop.JIT_DEBUG,
                                 rop.KEEPALIVE,
                                 ):
                self.newops.append(op)
                continue
            # ----------  fall-back  ----------
            self.fallback_inevitable(op)
            #
        return self.newops


    def gen_write_barrier(self, v):
        raise NotImplementedError

    def gen_barrier(self, v_base, target_category):
        v_base = self.unconstifyptr(v_base)
        assert isinstance(v_base, BoxPtr)
        source_category = self.known_category.get(v_base, 'P')
        mpcat = self.more_precise_categories[source_category]
        try:
            write_barrier_descr = mpcat[target_category]
        except KeyError:
            return v_base    # no barrier needed
        args = [v_base, self.c_zero]
        self.newops.append(ResOperation(rop.COND_CALL_GC_WB, args, None,
                                        descr=write_barrier_descr))
        self.known_category[v_base] = target_category
        return v_base

    def unconstifyptr(self, v):
        if isinstance(v, ConstPtr):
            v_in = v
            v_out = BoxPtr()
            self.newops.append(ResOperation(rop.SAME_AS, [v_in], v_out))
            v = v_out
        assert isinstance(v, BoxPtr)
        return v

    def handle_category_operations(self, op, target_category):
        lst = op.getarglist()
        lst[0] = self.gen_barrier(lst[0], target_category)
        self.newops.append(op.copy_and_change(op.getopnum(), args=lst))

    def handle_malloc_operation(self, op):
        GcRewriterAssembler.handle_malloc_operation(self, op)
        self.known_category[op.result] = 'W'

    def handle_copystrcontent(self, op):
        # first, a write barrier on the target string
        lst = op.getarglist()
        lst[1] = self.gen_barrier(lst[1], 'W')
        op = op.copy_and_change(op.getopnum(), args=lst)
        # then a read barrier the source string
        self.handle_category_operations(op, 'R')

    def fallback_inevitable(self, op):
        self.known_category.clear()
        if not self.always_inevitable:
            addr = self.gc_ll_descr.get_malloc_fn_addr('stm_try_inevitable')
            descr = self.gc_ll_descr.stm_try_inevitable_descr
            op1 = ResOperation(rop.CALL, [ConstInt(addr)], None, descr=descr)
            self.newops.append(op1)
            self.always_inevitable = True
        self.newops.append(op)
