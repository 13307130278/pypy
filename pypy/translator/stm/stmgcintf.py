import py
from pypy.tool.autopath import pypydir
from pypy.rpython.lltypesystem import lltype, llmemory, rffi
from pypy.translator.tool.cbuild import ExternalCompilationInfo
from pypy.rlib.rarithmetic import LONG_BIT


cdir = py.path.local(pypydir) / 'translator' / 'stm'
cdir2 = py.path.local(pypydir) / 'translator' / 'c'

eci = ExternalCompilationInfo(
    include_dirs = [cdir, cdir2],
    includes = ['src_stm/et.h'],
    pre_include_bits = ['#define PYPY_LONG_BIT %d' % LONG_BIT,
                        '#define RPY_STM 1'],
)

def _llexternal(name, args, result, **kwds):
    return rffi.llexternal(name, args, result, compilation_info=eci,
                           _nowrapper=True, **kwds)

def smexternal(name, args, result):
    return staticmethod(_llexternal(name, args, result))

# ____________________________________________________________


class StmOperations(object):

    CALLBACK_TX     = lltype.Ptr(lltype.FuncType([rffi.VOIDP, lltype.Signed],
                                                 lltype.Signed))
    DUPLICATE       = lltype.Ptr(lltype.FuncType([llmemory.Address],
                                                 llmemory.Address))
    CALLBACK_ENUM   = lltype.Ptr(lltype.FuncType([llmemory.Address]*2,
                                                 lltype.Void))

    def _freeze_(self):
        return True

    # C part of the implementation of the pypy.rlib.rstm module
    in_transaction = smexternal('stm_in_transaction', [], lltype.Signed)
    is_inevitable = smexternal('stm_is_inevitable', [], lltype.Signed)
    should_break_transaction = smexternal('stm_should_break_transaction',
                                          [], lltype.Signed)
    add_atomic = smexternal('stm_add_atomic', [lltype.Signed], lltype.Void)
    get_atomic = smexternal('stm_get_atomic', [], lltype.Signed)
    descriptor_init = smexternal('DescriptorInit', [], lltype.Signed)
    descriptor_done = smexternal('DescriptorDone', [], lltype.Void)
    begin_inevitable_transaction = smexternal(
        'BeginInevitableTransaction', [], lltype.Void)
    commit_transaction = smexternal(
        'CommitTransaction', [], lltype.Void)
    perform_transaction = smexternal('stm_perform_transaction',
                                     [CALLBACK_TX, rffi.VOIDP, llmemory.Address],
                                     lltype.Void)

    # for the GC: store and read a thread-local-storage field
    set_tls = smexternal('stm_set_tls', [llmemory.Address], lltype.Void)
    get_tls = smexternal('stm_get_tls', [], llmemory.Address)
    del_tls = smexternal('stm_del_tls', [], lltype.Void)

    # calls FindRootsForLocalCollect() and invokes for each such root
    # the callback set in CALLBACK_ENUM.
    tldict_enum = smexternal('stm_tldict_enum', [], lltype.Void)

    # sets the transaction length, after which should_break_transaction()
    # returns True
    set_transaction_length = smexternal('stm_set_transaction_length',
                                        [lltype.Signed], lltype.Void)

    # for testing
    abort_and_retry  = smexternal('stm_abort_and_retry', [], lltype.Void)
