from rpython.annotator import model as annmodel
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi, llgroup
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.memory.gctransform.support import get_rtti
from rpython.memory.gctransform.framework import (TYPE_ID,
     BaseFrameworkGCTransformer, BaseRootWalker, sizeofaddr,
     propagate_no_write_barrier_needed)
from rpython.memory.gctypelayout import WEAKREF, WEAKREFPTR
from rpython.memory.gc.stmgc import StmGC
from rpython.rlib.debug import ll_assert
from rpython.rtyper import rmodel, llannotation
from rpython.rtyper.annlowlevel import llhelper
from rpython.translator.backendopt.support import var_needsgc
from rpython.rlib.objectmodel import specialize
from rpython.rlib import rstm


VISIT_FPTR = StmGC.VISIT_FPTR

def invokecallback(root, visit_fn):
    """Used as a callback for gc.trace()."""
    visit_fn(root)

@specialize.arg(0)
def get_visit_function(callback, arg):
    """Hack: take a 'callback, arg' pair received from RPython code
    calling gc.trace(), and return a raw function pointer suitable for
    calling the C code.  We hide the 'arg' in some global if needed."""
    if callback is invokecallback:
        return arg      # the arg is directly the 'visit_fn' in this case
    raw_visit_glob = _get_raw_visit_glob(callback)
    raw_visit_glob.arg = arg
    return llhelper(VISIT_FPTR, raw_visit_glob.visit)

@specialize.memo()
def _get_raw_visit_glob(callback):
    class RawVisitGlob:
        _alloc_flavor_ = "raw"
    raw_visit_glob = RawVisitGlob()
    raw_visit_glob.visit = lambda obj: callback(obj, raw_visit_glob.arg)
    return _raw_visit_globs.setdefault(callback, raw_visit_glob)
_raw_visit_globs = {}


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
        def pypy_stmcb_trace(obj, visit_fn):
            gc.tracei(obj, invokecallback, visit_fn)
        pypy_stmcb_trace.c_name = "pypy_stmcb_trace"
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_trace, [llannotation.SomeAddress(),
                                     llannotation.SomePtr(VISIT_FPTR)],
                  annmodel.s_None))
        #
        def pypy_stmcb_obj_supports_cards(obj):
            typeid = gc.get_type_id(obj)
            return gc.is_varsize(typeid)
        pypy_stmcb_obj_supports_cards.c_name = "pypy_stmcb_obj_supports_cards"
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_obj_supports_cards, [llannotation.SomeAddress()],
                  annmodel.SomeInteger()))
        #
        def pypy_stmcb_trace_cards(obj, visit_fn, start, stop):
            typeid = gc.get_type_id(obj)
            if not gc.has_gcptr_in_varsize(typeid):
                return    # there are cards, but they don't need tracing
            length = (obj + gc.varsize_offset_to_length(typeid)).signed[0]
            ll_assert(stop <= length, "trace_cards: stop > length")
            gc.trace_partial(obj, start, stop, invokecallback, visit_fn)
        pypy_stmcb_trace_cards.c_name = "pypy_stmcb_trace_cards"
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_trace_cards,
                  [llannotation.SomeAddress(),
                   llannotation.SomePtr(VISIT_FPTR),
                   annmodel.s_Int,
                   annmodel.s_Int],
                  annmodel.s_None))
        #
        def pypy_stmcb_get_card_base_itemsize(obj, offset_itemsize):
            gc.get_card_base_itemsize(obj, offset_itemsize)
        pypy_stmcb_get_card_base_itemsize.c_name = (
            "pypy_stmcb_get_card_base_itemsize")
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_get_card_base_itemsize,
                  [llannotation.SomeAddress(),
                   llannotation.SomePtr(rffi.CArrayPtr(lltype.Unsigned))],
                  annmodel.s_None))
        #
        def pypy_stmcb_fetch_finalizer(typeid):
            typeid = lltype.cast_primitive(llgroup.HALFWORD, typeid)
            return llmemory.cast_ptr_to_adr(gc.getfinalizer(typeid))
        pypy_stmcb_fetch_finalizer.c_name = (
            "pypy_stmcb_fetch_finalizer")
        self.autoregister_ptrs.append(
            getfn(pypy_stmcb_fetch_finalizer,
                  [annmodel.s_Int], llannotation.SomeAddress()))

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
        if hop.spaceop.args[-1].concretetype == lltype.Void:
            pass   # ignore setfields of a Void type
        elif not var_needsgc(v_struct):
            if (var_needsgc(hop.spaceop.args[-1]) and
                'is_excdata' not in hop.spaceop.args[0].concretetype.TO._hints):
                raise Exception("%s: GC pointer written into a non-GC location"
                                % (hop.spaceop,))
        elif hop.spaceop not in self.clean_sets:
            if self.in_stm_ignored:
                # detect if we're inside a 'stm_ignored' block and in
                # that case don't call stm_write().  This only works for
                # writing non-GC pointers.
                if var_needsgc(hop.spaceop.args[-1]):
                    raise Exception("in stm_ignored block: write of a gc "
                                    "pointer")
            elif self._set_into_gc_array_part(hop.spaceop) is not None:
                self.write_barrier_from_array_calls += 1
                v_index = self._set_into_gc_array_part(hop.spaceop)
                assert v_index.concretetype == lltype.Signed
                hop.genop("stm_write", [v_struct, v_index])
            else:
                self.write_barrier_calls += 1
                hop.genop("stm_write", [v_struct])
                # we just did a full write barrier here, so we can use
                # this helper to propagate this knowledge forward and
                # avoid to repeat the write barrier.
                if self.curr_block is not None:   # for tests
                    propagate_no_write_barrier_needed(self.clean_sets,
                                                      self.curr_block,
                                                      {v_struct: True},
                                                      self.collect_analyzer,
                                                      self._entrymap)
        hop.rename('bare_' + opname)

    def gct_gc_writebarrier(self, hop):
        v_struct = hop.spaceop.args[0]
        assert var_needsgc(v_struct), ("gc_writebarrier: the argument is %r"
                                       % v_struct.concretetype)
        hop.genop("stm_write", [v_struct])

    def gc_header_for(self, obj, needs_hash=False):
        return self.gcdata.gc.gcheaderbuilder.header_of_object(obj)

    def gct_gc_adr_of_root_stack_top(self, hop):
        hop.genop("stm_get_root_stack_top", [], resultvar=hop.spaceop.result)

    def gct_get_write_barrier_failing_case(self, hop):
        op = hop.spaceop
        c_write_slowpath = rmodel.inputconst(
            lltype.Signed, rstm.adr_write_slowpath)
        hop.genop("cast_int_to_ptr", [c_write_slowpath], resultvar=op.result)

    def gct_get_write_barrier_from_array_failing_case(self, hop):
        op = hop.spaceop
        c_write_slowpath = rmodel.inputconst(
            lltype.Signed, rstm.adr_write_slowpath_card)
        hop.genop("cast_int_to_ptr", [c_write_slowpath], resultvar=op.result)

    def gct_gc_can_move(self, hop):
        hop.rename('stm_can_move')

    def _gct_with_roots_pushed(self, hop):
        livevars = self.push_roots(hop)
        self.default(hop)
        self.pop_roots(hop, livevars)

    def gct_stm_malloc_nonmovable(self, hop):
        op = hop.spaceop
        PTRTYPE = op.result.concretetype
        TYPE = PTRTYPE.TO
        type_id = self.get_type_id(TYPE)

        c_type_id = rmodel.inputconst(TYPE_ID, type_id)
        info = self.layoutbuilder.get_info(type_id)
        c_size = rmodel.inputconst(lltype.Signed, info.fixedsize)

        livevars = self.push_roots(hop)
        v_result = hop.genop("stm_allocate_nonmovable",
                             [c_size, c_type_id],
                             resulttype=llmemory.GCREF)
        self.pop_roots(hop, livevars)
        hop.genop("cast_opaque_ptr", [v_result], resultvar=op.result)

    # sync with lloperation.py
    gct_stm_become_inevitable                       = _gct_with_roots_pushed
    gct_stm_stop_all_other_threads                  = _gct_with_roots_pushed
    gct_stm_transaction_break                       = _gct_with_roots_pushed
    gct_stm_collect                                 = _gct_with_roots_pushed
    gct_stm_queue_get                               = _gct_with_roots_pushed
    gct_stm_queue_join                              = _gct_with_roots_pushed


class StmRootWalker(BaseRootWalker):

    def need_thread_support(self, gctransformer, getfn):
        # gc_thread_start() and gc_thread_die() don't need to become
        # anything.  When a new thread start, there is anyway first
        # the "after/before" callbacks from rffi, which contain calls
        # to "stm_enter_callback_call/stm_leave_callback_call".
        pass

    def walk_stack_roots(self, collect_stack_root, is_minor=False):
        raise NotImplementedError
