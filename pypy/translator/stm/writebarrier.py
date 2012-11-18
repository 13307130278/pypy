from pypy.objspace.flow.model import SpaceOperation, Constant, Variable
from pypy.translator.unsimplify import varoftype, insert_empty_block
from pypy.rpython.lltypesystem import lltype
from pypy.translator.backendopt.writeanalyze import top_set


MALLOCS = set([
    'malloc', 'malloc_varsize',
    'malloc_nonmovable', 'malloc_nonmovable_varsize',
    ])

MORE_PRECISE_CATEGORIES = {
    'P': 'PGORLWN',
    'G': 'GN',
    'O': 'ORLWN',
    'R': 'RLWN',
    'L': 'LWN',
    'W': 'WN',
    'N': 'N'}

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
    raise AssertionError(op)


def insert_stm_barrier(stmtransformer, graph):
    graphinfo = stmtransformer.write_analyzer.compute_graph_info(graph)

    def get_category(v):
        if isinstance(v, Constant):
            if v.value:
                return 'G'
            else:
                return 'N'     # NULL
        return category.get(v, 'P')

    def renamings_get(v):
        if v not in renamings:
            return v
        v2 = renamings[v][0]
        if v2.concretetype == v.concretetype:
            return v2
        v3 = varoftype(v.concretetype)
        newoperations.append(SpaceOperation('cast_pointer', [v2], v3))
        return v3

    for block in graph.iterblocks():
        if block.operations == ():
            continue
        #
        wants_a_barrier = {}
        expand_comparison = set()
        for op in block.operations:
            if (op.opname in ('getfield', 'getarrayitem',
                              'getinteriorfield') and
                  op.result.concretetype is not lltype.Void and
                  op.args[0].concretetype.TO._gckind == 'gc' and
                  not is_immutable(op)):
                wants_a_barrier.setdefault(op, 'R')
            elif (op.opname in ('setfield', 'setarrayitem',
                                'setinteriorfield') and
                  op.args[-1].concretetype is not lltype.Void and
                  op.args[0].concretetype.TO._gckind == 'gc' and
                  not is_immutable(op)):
                wants_a_barrier[op] = 'W'
            elif (op.opname in ('ptr_eq', 'ptr_ne') and
                  op.args[0].concretetype.TO._gckind == 'gc'):
                expand_comparison.add(op)
        #
        if wants_a_barrier or expand_comparison:
            # note: 'renamings' maps old vars to new vars, but cast_pointers
            # are done lazily.  It means that the two vars may not have
            # exactly the same type.
            renamings = {}   # {original-var: [var-in-newoperations] (len 1)}
            category = {}    # {var-in-newoperations: LETTER}
            newoperations = []
            for op in block.operations:
                #
                if op.opname == 'cast_pointer':
                    v = op.args[0]
                    renamings[op.result] = renamings.setdefault(v, [v])
                    continue
                #
                to = wants_a_barrier.get(op)
                if to is not None:
                    v = op.args[0]
                    v_holder = renamings.setdefault(v, [v])
                    v = v_holder[0]
                    frm = get_category(v)
                    if frm not in MORE_PRECISE_CATEGORIES[to]:
                        c_info = Constant('%s2%s' % (frm, to), lltype.Void)
                        w = varoftype(v.concretetype)
                        newop = SpaceOperation('stm_barrier', [c_info, v], w)
                        newoperations.append(newop)
                        v_holder[0] = w
                        category[w] = to
                #
                newop = SpaceOperation(op.opname,
                                       [renamings_get(v) for v in op.args],
                                       op.result)
                newoperations.append(newop)
                #
                if op in expand_comparison:
                    cats = ''.join([get_category(v) for v in newop.args])
                    if ('N' not in cats and
                            cats not in ('LL', 'LW', 'WL', 'WW')):
                        if newop.opname == 'ptr_ne':
                            v = varoftype(lltype.Bool)
                            negop = SpaceOperation('bool_not', [v],
                                                   newop.result)
                            newoperations.append(negop)
                            newop.result = v
                        newop.opname = 'stm_ptr_eq'
                #
                effectinfo = stmtransformer.write_analyzer.analyze(
                    op, graphinfo=graphinfo)
                if effectinfo:
                    if effectinfo is top_set:
                        category.clear()
                    else:
                        types = set([entry[1] for entry in effectinfo])
                        for v in category.keys():
                            if v.concretetype in types and category[v] == 'R':
                                category[v] = 'O'
                #
                if op.opname in MALLOCS:
                    category[op.result] = 'W'

            block.operations = newoperations
            #
            for link in block.exits:
                newoperations = []
                for i, v in enumerate(link.args):
                    link.args[i] = renamings_get(v)
                if newoperations:
                    # must put them in a fresh block along the link
                    annotator = stmtransformer.translator.annotator
                    newblock = insert_empty_block(annotator, link,
                                                  newoperations)
