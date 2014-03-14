from rpython.flowspace.model import SpaceOperation, Constant, Variable
from rpython.translator.unsimplify import varoftype
from rpython.rtyper.lltypesystem import lltype


READ_OPS = set(['getfield', 'getarrayitem', 'getinteriorfield', 'raw_load'])


def is_gc_ptr(T):
    return isinstance(T, lltype.Ptr) and T.TO._gckind == 'gc'

def unwraplist(list_v):
    for v in list_v:
        if isinstance(v, Constant):
            yield v.value
        elif isinstance(v, Variable):
            yield None    # unknown
        else:
            raise AssertionError(v)

def is_immutable(op):
    if op.opname in ('getfield', 'setfield'):
        STRUCT = op.args[0].concretetype.TO
        return STRUCT._immutable_field(op.args[1].value)
    if op.opname in ('getarrayitem', 'setarrayitem'):
        ARRAY = op.args[0].concretetype.TO
        return ARRAY._immutable_field()
    if op.opname == 'getinteriorfield':
        OUTER = op.args[0].concretetype.TO
        return OUTER._immutable_interiorfield(unwraplist(op.args[1:]))
    if op.opname == 'setinteriorfield':
        OUTER = op.args[0].concretetype.TO
        return OUTER._immutable_interiorfield(unwraplist(op.args[1:-1]))
    if op.opname in ('raw_load', 'raw_store'):
        return False


def insert_stm_read_barrier(transformer, graph):
    # We need to put enough 'stm_read' in the graph so that any
    # execution of a READ_OP on some GC object is guaranteed to also
    # execute either 'stm_read' or 'stm_write' on the same GC object
    # during the same transaction.
    #
    # XXX this can be optimized a lot, but for now we go with the
    # simplest possible solution...
    #
    gcremovetypeptr = transformer.translator.config.translation.gcremovetypeptr

    for block in graph.iterblocks():
        if not block.operations:
            continue
        newops = []
        stm_ignored = False
        for op in block.operations:
            is_getter = (op.opname in READ_OPS and
                         op.result.concretetype is not lltype.Void and
                         is_gc_ptr(op.args[0].concretetype))

            if (gcremovetypeptr and op.opname in ('getfield', 'setfield') and
                op.args[1].value == 'typeptr' and
                op.args[0].concretetype.TO._hints.get('typeptr')):
                # typeptr is always immutable
                pass
            elif ((op.opname in ('getarraysize', 'getinteriorarraysize', 'weakref_deref') and
                  is_gc_ptr(op.args[0].concretetype)) or
                  (is_getter and is_immutable(op))):
                # immutable getters
                pass
            elif is_getter:
                if not stm_ignored:
                    v_none = varoftype(lltype.Void)
                    newops.append(SpaceOperation('stm_read',
                                                 [op.args[0]], v_none))
                    transformer.read_barrier_counts += 1
            elif op.opname == 'stm_ignored_start':
                assert stm_ignored == False
                stm_ignored = True
            elif op.opname == 'stm_ignored_stop':
                assert stm_ignored == True
                stm_ignored = False
            newops.append(op)
        assert stm_ignored == False
        block.operations = newops
