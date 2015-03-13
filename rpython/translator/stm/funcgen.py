from rpython.flowspace.model import Constant
from rpython.translator.c.support import c_string_constant, cdecl
from rpython.translator.c.node import Node, ContainerNode
from rpython.translator.c.primitive import name_small_integer
from rpython.rtyper.lltypesystem import lltype, llmemory


class StmHeaderOpaqueDefNode(Node):
    typetag = ''
    dependencies = ()

    def __init__(self, db, T):
        Node.__init__(self, db)
        self.T = T
        self.name = 'object_t'

    def setup(self):
        pass

    def definition(self):
        return []

    def c_struct_field_name(self, _):
        return 'tid'


class StmHeader_OpaqueNode(ContainerNode):
    nodekind = 'stmhdr'
    globalcontainer = True
    typename = 'object_t @'
    implementationtypename = typename
    _funccodegen_owner = None

    def __init__(self, db, T, obj):
        assert isinstance(obj._name, int)
        self.db = db
        self.T = T
        self.obj = obj

    def initializationexpr(self, decoration=''):
        yield '{ { }, %s }' % (
            name_small_integer(self.obj.typeid16, self.db))
        #    self.obj.prebuilt_hash


def stm_hint_commit_soon(funcgen, op):
    return 'stmcb_commit_soon();'

def stm_register_thread_local(funcgen, op):
    return 'pypy_stm_register_thread_local();'

def stm_unregister_thread_local(funcgen, op):
    return 'pypy_stm_unregister_thread_local();'

def stm_read(funcgen, op):
    assert isinstance(op.args[0].concretetype, lltype.Ptr)
    assert op.args[0].concretetype.TO._gckind == 'gc'
    arg0 = funcgen.expr(op.args[0])
    return 'stm_read((object_t *)%s);' % (arg0,)

def stm_write(funcgen, op):
    assert isinstance(op.args[0].concretetype, lltype.Ptr)
    assert op.args[0].concretetype.TO._gckind == 'gc'
    arg0 = funcgen.expr(op.args[0])
    if len(op.args) == 1:
        return 'stm_write((object_t *)%s);' % (arg0,)
    else:
        arg1 = funcgen.expr(op.args[1])
        return 'stm_write_card((object_t *)%s, %s);' % (arg0, arg1)

def stm_can_move(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = stm_can_move((object_t *)%s);' % (result, arg0)

def stm_allocate_tid(funcgen, op):
    arg_size    = funcgen.expr(op.args[0])
    arg_type_id = funcgen.expr(op.args[1])
    result      = funcgen.expr(op.result)
    # XXX NULL returns?
    return ('%s = (rpygcchar_t *)stm_allocate(%s); ' % (result, arg_size) +
            '((rpyobj_t *)%s)->tid = %s;' % (result, arg_type_id))

def stm_allocate_weakref(funcgen, op):
    arg_size    = funcgen.expr(op.args[0])
    arg_type_id = funcgen.expr(op.args[1])
    result      = funcgen.expr(op.result)
    # XXX NULL returns?
    return ('%s = (rpygcchar_t *)stm_allocate_weakref(%s); ' % (result, arg_size) +
            '((rpyobj_t *)%s)->tid = %s;' % (result, arg_type_id))

def stm_allocate_finalizer(funcgen, op):
    arg_size    = funcgen.expr(op.args[0])
    arg_type_id = funcgen.expr(op.args[1])
    result      = funcgen.expr(op.result)
    # XXX NULL returns?
    return ('%s = (rpygcchar_t *)stm_allocate_with_finalizer(%s); ' % (
        result, arg_size) +
            '((rpyobj_t *)%s)->tid = %s;' % (result, arg_type_id))

def stm_allocate_f_light(funcgen, op):
    arg_size    = funcgen.expr(op.args[0])
    arg_type_id = funcgen.expr(op.args[1])
    result      = funcgen.expr(op.result)
    # XXX NULL returns?
    return ('%s = (rpygcchar_t *)stm_allocate(%s); ' % (
        result, arg_size) +
            '((rpyobj_t *)%s)->tid = %s;\n' % (result, arg_type_id) +
            'stm_enable_light_finalizer((object_t *)%s);' % (result,))

def stm_allocate_preexisting(funcgen, op):
    arg_size   = funcgen.expr(op.args[0])
    arg_idata  = funcgen.expr(op.args[1])
    result     = funcgen.expr(op.result)
    resulttype = cdecl(funcgen.lltypename(op.result), '')
    return ('%s = (%s)stm_allocate_preexisting(%s,'
            ' _stm_real_address((object_t *)%s));' % (
        result, resulttype, arg_size, arg_idata))

def stm_allocate_nonmovable(funcgen, op):
    arg_size    = funcgen.expr(op.args[0])  # <- could be smaller than 16 here
    arg_type_id = funcgen.expr(op.args[1])
    result      = funcgen.expr(op.result)
    # XXX NULL returns?
    return ('%s = (rpygcchar_t *)_stm_allocate_external(%s >= 16 ? %s : 16); ' %
            (result, arg_size, arg_size) +
            'pypy_stm_memclearinit((object_t*)%s, 0, %s >= 16 ? %s : 16);' %
            (result, arg_size, arg_size) +
            '((rpyobj_t *)%s)->tid = %s;' % (result, arg_type_id))

def stm_set_into_obj(funcgen, op):
    assert op.args[0].concretetype == llmemory.GCREF
    arg_obj = funcgen.expr(op.args[0])
    arg_ofs = funcgen.expr(op.args[1])
    arg_val = funcgen.expr(op.args[2])
    valtype = cdecl(funcgen.lltypename(op.args[2]), '')
    return '*(TLPREFIX %s *)(%s + %s) = %s;' % (
        valtype, arg_obj, arg_ofs, arg_val)

def stm_collect(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    return 'stm_collect(%s);' % (arg0,)

def stm_id(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = stm_id((object_t *)%s);' % (result, arg0)

def stm_identityhash(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = stm_identityhash((object_t *)%s);' % (result, arg0)

def stm_addr_get_tid(funcgen, op):
    arg0   = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = ((struct rpyobj_s *)%s)->tid;' % (result, arg0)

def stm_become_inevitable(funcgen, op):
    try:
        info = op.args[0].value
    except IndexError:
        info = "?"    # cannot insert it in 'llop'
    try:
        info = '%s:%s' % (funcgen.graph.name, info)
    except AttributeError:
        pass
    string_literal = c_string_constant(info)
    return 'pypy_stm_become_inevitable(%s);' % (string_literal,)

def stm_stop_all_other_threads(funcgen, op):
    return 'stm_stop_all_other_threads();'

def stm_resume_all_other_threads(funcgen, op):
    return 'stm_resume_all_other_threads();'

def stm_push_root(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    return 'STM_PUSH_ROOT(stm_thread_local, %s);' % (arg0,)

def stm_pop_root_into(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    if isinstance(op.args[0], Constant):
        return '/* %s = */ STM_POP_ROOT_RET(stm_thread_local);' % (arg0,)
    return 'STM_POP_ROOT(stm_thread_local, %s);' % (arg0,)

def stm_commit_if_not_atomic(funcgen, op):
   return 'pypy_stm_commit_if_not_atomic();'

def stm_start_if_not_atomic(funcgen, op):
    return 'pypy_stm_start_if_not_atomic();'

def stm_enter_callback_call(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = pypy_stm_enter_callback_call(%s);' % (result, arg0)

def stm_leave_callback_call(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    return 'pypy_stm_leave_callback_call(%s, %s);' % (arg0, arg1)

def stm_should_break_transaction(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = pypy_stm_should_break_transaction();' % (result,)

def stm_set_transaction_length(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    return 'pypy_stm_set_transaction_length(%s);' % (arg0,)

def stm_transaction_break(funcgen, op):
    return 'pypy_stm_transaction_break();'

def stm_increment_atomic(funcgen, op):
    return 'pypy_stm_increment_atomic();'

def stm_decrement_atomic(funcgen, op):
    return 'pypy_stm_decrement_atomic();'

def stm_get_atomic(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = pypy_stm_get_atomic();' % (result,)

def stm_is_inevitable(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = stm_is_inevitable();' % (result,)

def stm_abort_and_retry(funcgen, op):
    return 'stm_abort_transaction();'

def stm_ignored_start(funcgen, op):
    return '/* stm_ignored_start */'

def stm_ignored_stop(funcgen, op):
    return '/* stm_ignored_stop */'

def stm_get_root_stack_top(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = (%s)&stm_thread_local.shadowstack;' % (
        result, cdecl(funcgen.lltypename(op.result), ''))

def stm_push_marker(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    return 'STM_PUSH_MARKER(stm_thread_local, %s, %s);' % (arg0, arg1)

def stm_update_marker_num(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    return 'STM_UPDATE_MARKER_NUM(stm_thread_local, %s);' % (arg0,)

def stm_pop_marker(funcgen, op):
    return 'STM_POP_MARKER(stm_thread_local);'

def stm_expand_marker(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = _pypy_stm_test_expand_marker();' % (result,)

def stm_setup_expand_marker_for_pypy(funcgen, op):
    # hack hack hack
    offsets = []
    for arg in op.args[1:]:
        name = 'inst_' + ''.join(arg.value.chars)
        S = op.args[0].concretetype.TO
        while True:
            node = funcgen.db.gettypedefnode(S)
            if name in node.fieldnames:
                break
            S = S.super
        name = node.c_struct_field_name(name)
        offsets.append('offsetof(struct %s, %s)' % (node.name, name))
    assert len(offsets) == 4
    return 'pypy_stm_setup_expand_marker(%s, %s, %s, %s);' % (
        offsets[0], offsets[1], offsets[2], offsets[3])

def stm_rewind_jmp_frame(funcgen, op):
    if len(op.args) == 0:
        assert op.result.concretetype is lltype.Void
        return '/* automatic stm_rewind_jmp_frame */'
    elif op.args[0].value == 1:
        assert op.result.concretetype is llmemory.Address
        return '%s = &rjbuf1;' % (funcgen.expr(op.result),)
    else:
        assert False, op.args[0].value

def stm_count(funcgen, op):
    result = funcgen.expr(op.result)
    return '%s = _pypy_stm_count();' % (result,)

def stm_really_force_cast_ptr(funcgen, op):
    # pffff, try very very hard to cast a pointer in one address space
    # to consider it as a pointer in another address space, without
    # changing it in any way.  It works if we cast via an integer
    # (but not directly).
    result = funcgen.expr(op.result)
    arg = funcgen.expr(op.args[0])
    typename = cdecl(funcgen.lltypename(op.result), '')
    return '%s = (%s)(uintptr_t)%s;' % (result, typename, arg)

def stm_memclearinit(funcgen, op):
    gcref = funcgen.expr(op.args[0])
    offset = funcgen.expr(op.args[1])
    size = funcgen.expr(op.args[2])
    return 'pypy_stm_memclearinit((object_t*)%s, (size_t)%s, (size_t)%s);' % (
        gcref, offset, size)

def stm_hashtable_create(funcgen, op):
    _STM_HASHTABLE_ENTRY = op.args[0].concretetype.TO
    type_id = funcgen.db.gctransformer.get_type_id(_STM_HASHTABLE_ENTRY)
    expr_type_id = funcgen.expr(Constant(type_id, lltype.typeOf(type_id)))
    result = funcgen.expr(op.result)
    return ('stm_hashtable_entry_userdata = %s; '
            '%s = stm_hashtable_create();' % (expr_type_id, result,))

def stm_hashtable_free(funcgen, op):
    arg = funcgen.expr(op.args[0])
    return 'stm_hashtable_free(%s);' % (arg,)

def stm_hashtable_read(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    arg2 = funcgen.expr(op.args[2])
    result = funcgen.expr(op.result)
    return '%s = (rpygcchar_t *)stm_hashtable_read((object_t *)%s, %s, %s);' % (
        result, arg0, arg1, arg2)

def stm_hashtable_write(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    arg2 = funcgen.expr(op.args[2])
    arg3 = funcgen.expr(op.args[3])
    return ('stm_hashtable_write((object_t *)%s, %s, %s, (object_t *)%s, '
            '&stm_thread_local);' % (arg0, arg1, arg2, arg3))

def stm_hashtable_write_entry(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    arg2 = funcgen.expr(op.args[2])
    return ('stm_hashtable_write_entry((object_t *)%s, %s, (object_t *)%s);' % (
        arg0, arg1, arg2))

def stm_hashtable_lookup(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    arg2 = funcgen.expr(op.args[2])
    result = funcgen.expr(op.result)
    return '%s = stm_hashtable_lookup((object_t *)%s, %s, %s);' % (
        result, arg0, arg1, arg2)

def stm_hashtable_length_upper_bound(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    result = funcgen.expr(op.result)
    return '%s = stm_hashtable_length_upper_bound(%s);' % (
        result, arg0)

def stm_hashtable_list(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    arg2 = funcgen.expr(op.args[2])
    result = funcgen.expr(op.result)
    return '%s = stm_hashtable_list((object_t *)%s, %s, %s);' % (
        result, arg0, arg1, arg2)

def stm_hashtable_tracefn(funcgen, op):
    arg0 = funcgen.expr(op.args[0])
    arg1 = funcgen.expr(op.args[1])
    return 'stm_hashtable_tracefn((stm_hashtable_t *)%s, %s);' % (arg0, arg1)
