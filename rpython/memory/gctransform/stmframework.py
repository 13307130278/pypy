from rpython.annotator import model as annmodel
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.memory.gctransform.framework import ( TYPE_ID,
     BaseFrameworkGCTransformer, BaseRootWalker, sizeofaddr)
from rpython.memory.gctypelayout import WEAKREF, WEAKREFPTR
from rpython.rtyper import rmodel, llannotation
from rpython.translator.backendopt.support import var_needsgc


class StmFrameworkGCTransformer(BaseFrameworkGCTransformer):

    def _declare_functions(self, GCClass, getfn, s_gc, s_typeid16):
        BaseFrameworkGCTransformer._declare_functions(self, GCClass, getfn,
                                                      s_gc, s_typeid16)
        gc = self.gcdata.gc
        #
        def pypy_stmcb_size_rounded_up(obj):
            return gc.get_size(obj)
        pypy_stmcb_size_rounded_up.c_name = "pypy_stmcb_size_rounded_up"
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_size_rounded_up, [llannotation.SomeAddress()],
                  annmodel.SomeInteger()))
        #
        def invokecallback(root, visit_fn):
            visit_fn(root)
        def pypy_stmcb_trace(obj, visit_fn):
            gc.trace(obj, invokecallback, visit_fn)
        pypy_stmcb_trace.c_name = "pypy_stmcb_trace"
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_trace, [llannotation.SomeAddress(),
                                     llannotation.SomePtr(GCClass.VISIT_FPTR)],
                  annmodel.s_None))

    def build_root_walker(self):
        return StmRootWalker(self)

    def push_roots(self, hop, keep_current_args=False):
        livevars = self.get_livevars_for_roots(hop, keep_current_args)
        self.num_pushs += len(livevars)
        for var in livevars:
            hop.genop("stm_push_root", [var])
        return livevars

    def pop_roots(self, hop, livevars):
        for var in reversed(livevars):
            hop.genop("stm_pop_root_into", [var])

    def transform_block(self, *args, **kwds):
        self.in_stm_ignored = False
        BaseFrameworkGCTransformer.transform_block(self, *args, **kwds)
        assert not self.in_stm_ignored, (
            "unbalanced stm_ignore_start/stm_ignore_stop in block")

    def gct_stm_ignored_start(self, hop):
        assert not self.in_stm_ignored
        self.in_stm_ignored = True
        self.default(hop)

    def gct_stm_ignored_stop(self, hop):
        assert self.in_stm_ignored
        self.in_stm_ignored = False
        self.default(hop)

    def var_needs_set_transform(self, var):
        return True

    def transform_generic_set(self, hop):
        assert self.write_barrier_ptr == "stm"
        opname = hop.spaceop.opname
        v_struct = hop.spaceop.args[0]
        assert opname in ('setfield', 'setarrayitem', 'setinteriorfield',
                          'raw_store')
        if (v_struct.concretetype.TO._gckind == "gc"
                and hop.spaceop not in self.clean_sets):
            if self.in_stm_ignored:
                # detect if we're inside a 'stm_ignored' block and in
                # that case don't call stm_write().  This only works for
                # writing non-GC pointers.
                if var_needsgc(hop.spaceop.args[-1]):
                    raise Exception("in stm_ignored block: write of a gc "
                                    "pointer")
            else:
                self.write_barrier_calls += 1
                hop.genop("stm_write", [v_struct])
        hop.rename('bare_' + opname)

    def gc_header_for(self, obj, needs_hash=False):
        return self.gcdata.gc.gcheaderbuilder.header_of_object(obj)

    def gct_gc_adr_of_root_stack_top(self, hop):
        hop.genop("stm_get_root_stack_top", [], resultvar=hop.spaceop.result)

##    def _gct_with_roots_pushed(self, hop):
##        livevars = self.push_roots(hop)
##        self.default(hop)
##        self.pop_roots(hop, livevars)

##    # sync with lloperation.py
##    gct_stm_become_inevitable                       = _gct_with_roots_pushed
##    gct_stm_partial_commit_and_resume_other_threads = _gct_with_roots_pushed
##    gct_stm_perform_transaction                     = _gct_with_roots_pushed
##    gct_stm_inspect_abort_info                      = _gct_with_roots_pushed
##    gct_stm_threadlocalref_set                      = _gct_with_roots_pushed


class StmRootWalker(BaseRootWalker):

    def need_thread_support(self, gctransformer, getfn):
        # gc_thread_start() and gc_thread_die() don't need to become
        # anything.  When a new thread start, there is anyway first
        # the "after/before" callbacks from rffi, which contain calls
        # to "stm_enter_callback_call/stm_leave_callback_call".
        pass

    def walk_stack_roots(self, collect_stack_root):
        raise NotImplementedError
