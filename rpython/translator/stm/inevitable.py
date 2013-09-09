from rpython.rtyper.lltypesystem import lltype, lloperation, rclass
from rpython.translator.stm.writebarrier import is_immutable
from rpython.flowspace.model import SpaceOperation, Constant
from rpython.translator.unsimplify import varoftype


ALWAYS_ALLOW_OPERATIONS = set([
    'force_cast', 'keepalive', 'cast_ptr_to_adr',
    'cast_adr_to_int',
    'debug_print', 'debug_assert', 'cast_opaque_ptr', 'hint',
    'stack_current', 'gc_stack_bottom',
    'cast_current_ptr_to_int',   # this variant of 'cast_ptr_to_int' is ok
    'jit_force_virtual', 'jit_force_virtualizable',
    'jit_force_quasi_immutable', 'jit_marker', 'jit_is_virtual',
    'jit_record_known_class',
    'gc_identityhash', 'gc_id', 'gc_can_move', 'gc__collect',
    'gc_adr_of_root_stack_top',
    'weakref_create', 'weakref_deref',
    'stm_threadlocalref_get', 'stm_threadlocalref_set',
    'stm_threadlocalref_count', 'stm_threadlocalref_addr',
    ])
ALWAYS_ALLOW_OPERATIONS |= set(lloperation.enum_tryfold_ops())

for opname, opdesc in lloperation.LL_OPERATIONS.iteritems():
    if opname.startswith('stm_'):
        ALWAYS_ALLOW_OPERATIONS.add(opname)

GETTERS = set(['getfield', 'getarrayitem', 'getinteriorfield', 'raw_load'])
SETTERS = set(['setfield', 'setarrayitem', 'setinteriorfield', 'raw_store'])
MALLOCS = set(['malloc', 'malloc_varsize',
               'malloc_nonmovable', 'malloc_nonmovable_varsize'])
# ____________________________________________________________

def should_turn_inevitable_getter_setter(op, fresh_mallocs):
    # Getters and setters are allowed if their first argument is a GC pointer.
    # If it is a RAW pointer, and it is a read from a non-immutable place,
    # and it doesn't use the hint 'stm_dont_track_raw_accesses', then they
    # turn inevitable.
    TYPE = op.args[0].concretetype
    if not isinstance(TYPE, lltype.Ptr):
        return True     # raw_load or raw_store with a number or address
    S = TYPE.TO
    if S._gckind == 'gc':
        return False
    if is_immutable(op):
        return False
    if S._hints.get('stm_dont_track_raw_accesses', False):
        return False
    return not fresh_mallocs.is_fresh_malloc(op.args[0])

def should_turn_inevitable(op, block, fresh_mallocs):
    # Always-allowed operations never cause a 'turn inevitable'
    if op.opname in ALWAYS_ALLOW_OPERATIONS:
        return False
    #
    # Getters and setters
    if op.opname in GETTERS:
        if op.result.concretetype is lltype.Void:
            return False
        return should_turn_inevitable_getter_setter(op, fresh_mallocs)
    if op.opname in SETTERS:
        if op.args[-1].concretetype is lltype.Void:
            return False
        return should_turn_inevitable_getter_setter(op, fresh_mallocs)
    #
    # Mallocs & Frees
    if op.opname in MALLOCS:
        # flags = op.args[1].value
        # return flags['flavor'] != 'gc'
        return False # XXX: Produces memory leaks on aborts
    if op.opname == 'free':
        # We can only run a CFG in non-inevitable mode from start
        # to end in one transaction (every free gets called once
        # for every fresh malloc). No need to turn inevitable.
        # If the transaction is splitted, the remaining parts of the
        # CFG will always run in inevitable mode anyways.
        return not fresh_mallocs.is_fresh_malloc(op.args[0])
    #
    if op.opname == 'raw_malloc':
        return False # XXX: Produces memory leaks on aborts
    if op.opname == 'raw_free':
        return not fresh_mallocs.is_fresh_malloc(op.args[0])

    #
    # Function calls
    if op.opname == 'direct_call':
        funcptr = op.args[0].value._obj
        if not hasattr(funcptr, "external"):
            return False
        if getattr(funcptr, "transactionsafe", False):
            return False
        try:
            return funcptr._name + '()'
        except AttributeError:
            return True

    if op.opname == 'indirect_call':
        tographs = op.args[-1].value
        if tographs is not None:
            # Set of RPython functions
            return False
        # special-case to detect 'instantiate'
        v_func = op.args[0]
        for op1 in block.operations:
            if (v_func is op1.result and
                op1.opname == 'getfield' and
                op1.args[0].concretetype == rclass.CLASSTYPE and
                op1.args[1].value == 'instantiate'):
                return False
        # unknown function
        return True

    #
    # Entirely unsupported operations cause a 'turn inevitable'
    return True


def turn_inevitable_op(info):
    c_info = Constant(info, lltype.Void)
    return SpaceOperation('stm_become_inevitable', [c_info],
                          varoftype(lltype.Void))

def insert_turn_inevitable(graph):
    from rpython.translator.backendopt.writeanalyze import FreshMallocs
    fresh_mallocs = FreshMallocs(graph)
    for block in graph.iterblocks():
        for i in range(len(block.operations)-1, -1, -1):
            op = block.operations[i]
            inev = should_turn_inevitable(op, block, fresh_mallocs)
            if inev:
                if not isinstance(inev, str):
                    inev = op.opname
                inev_op = turn_inevitable_op(inev)
                block.operations.insert(i, inev_op)
