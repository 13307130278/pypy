from pypy.rpython.lltypesystem import lltype, opimpl
from pypy.rpython.llinterp import LLFrame
from pypy.rpython.test.test_llinterp import get_interpreter, clear_tcache
from pypy.translator.stm.transform2 import STMTransformer
from pypy.translator.stm.writebarrier import MORE_PRECISE_CATEGORIES
from pypy.conftest import option


class _stmptr(lltype._ptr):
    """Like lltype._ptr, but also keeps a current category level"""

    __slots__ = ['_category', '_original_ptr']

    def __init__(self, ptr, category):
        lltype._ptr.__init__(self, ptr._TYPE, ptr._obj0, ptr._solid)
        _stmptr._category.__set__(self, category)
        _stmptr._original_ptr.__set__(self, ptr)

    def __eq__(self, other):
        return self._original_ptr == other


class BaseTestTransform(object):
    do_write_barrier = False
    do_turn_inevitable = False
    do_jit_driver = False

    def build_state(self):
        self.writemode = set()
        self.barriers = []

    def get_category(self, p):
        if isinstance(p, _stmptr):
            return p._category
        if not p:
            return 'N'
        if p._solid:
            return 'G'     # allocated with immortal=True
        raise AssertionError("unknown category on %r" % (p,))

    def interpret(self, fn, args):
        self.build_state()
        clear_tcache()
        interp, self.graph = get_interpreter(fn, args, view=False)
        interp.tester = self
        interp.frame_class = LLSTMFrame
        #
        self.translator = interp.typer.annotator.translator
        self.stmtransformer = STMTransformer(self.translator)
        if self.do_jit_driver:
            self.stmtransformer.transform_jit_driver()
        if self.do_write_barrier:
            self.stmtransformer.transform_write_barrier()
        if self.do_turn_inevitable:
            self.stmtransformer.transform_turn_inevitable()
        if option.view:
            self.translator.view()
        #
        result = interp.eval_graph(self.graph, args)
        return result


class LLSTMFrame(LLFrame):

    def all_stm_ptrs(self):
        for frame in self.llinterpreter.frame_stack:
            for value in frame.bindings.values():
                if isinstance(value, _stmptr):
                    yield value

    def get_category(self, p):
        return self.llinterpreter.tester.get_category(p)

    def check_category(self, p, expected):
        cat = self.get_category(p)
        assert cat in MORE_PRECISE_CATEGORIES[expected]
        return cat

    def op_stm_barrier(self, kind, obj):
        frm, middledigit, to = kind
        assert middledigit == '2'
        cat = self.check_category(obj, frm)
        if cat in MORE_PRECISE_CATEGORIES[to]:
            # a barrier, but with no effect
            self.llinterpreter.tester.barriers.append(kind.lower())
            return obj
        else:
            # a barrier, calling a helper
            ptr2 = _stmptr(obj, to)
            if to == 'W':
                self.llinterpreter.tester.writemode.add(ptr2._obj)
            self.llinterpreter.tester.barriers.append(kind)
            return ptr2

    def op_stm_ptr_eq(self, obj1, obj2):
        self.check_category(obj1, 'P')
        self.check_category(obj2, 'P')
        self.llinterpreter.tester.barriers.append('=')
        return obj1 == obj2

    def op_getfield(self, obj, field):
        if not obj._TYPE.TO._immutable_field(field):
            self.check_category(obj, 'R')
        return LLFrame.op_getfield(self, obj, field)

    def op_setfield(self, obj, fieldname, fieldvalue):
        if not obj._TYPE.TO._immutable_field(fieldname):
            self.check_category(obj, 'W')
            # convert R -> O all other pointers to the same object we can find
            for p in self.all_stm_ptrs():
                if p._category == 'R' and p._T == obj._T and p == obj:
                    _stmptr._category.__set__(p, 'O')
        return LLFrame.op_setfield(self, obj, fieldname, fieldvalue)

    def op_cast_pointer(self, RESTYPE, obj):
        cat = self.check_category(obj, 'P')
        p = opimpl.op_cast_pointer(RESTYPE, obj)
        return _stmptr(p, cat)
    op_cast_pointer.need_result_type = True

    def op_malloc(self, obj, flags):
        p = LLFrame.op_malloc(self, obj, flags)
        ptr2 = _stmptr(p, 'W')
        self.llinterpreter.tester.writemode.add(ptr2._obj)
        return ptr2
