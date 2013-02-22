import thread, weakref
from rpython.translator.stm import stmgcintf
from rpython.rlib.debug import ll_assert, fatalerror
from rpython.rlib.objectmodel import keepalive_until_here, specialize
from rpython.rlib.objectmodel import we_are_translated
from rpython.rlib.rposix import get_errno, set_errno
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi, rclass, rstr
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.annlowlevel import cast_instance_to_base_ptr, llhelper
from rpython.rtyper.annlowlevel import cast_base_ptr_to_instance
from rpython.rtyper.extregistry import ExtRegistryEntry


def is_inevitable():
    return we_are_translated() and stmgcintf.StmOperations.is_inevitable()

def become_inevitable():
    llop.stm_become_inevitable(lltype.Void)

def should_break_transaction():
    return we_are_translated() and (
        stmgcintf.StmOperations.should_break_transaction())

def set_transaction_length(length):
    stmgcintf.StmOperations.set_transaction_length(length)

def increment_atomic():
    stmgcintf.StmOperations.add_atomic(+1)

def decrement_atomic():
    stmgcintf.StmOperations.add_atomic(-1)

def is_atomic():
    return stmgcintf.StmOperations.get_atomic()

def abort_info_push(instance, fieldnames):
    "Special-cased below."

def abort_info_pop(count):
    if we_are_translated():
        stmgcintf.StmOperations.abort_info_pop(count)

def charp_inspect_abort_info():
    return stmgcintf.StmOperations.inspect_abort_info()

def abort_and_retry():
    stmgcintf.StmOperations.abort_and_retry()

def before_external_call():
    if not is_atomic():
        e = get_errno()
        llop.stm_stop_transaction(lltype.Void)
        stmgcintf.StmOperations.commit_transaction()
        set_errno(e)
before_external_call._dont_reach_me_in_del_ = True
before_external_call._transaction_break_ = True

def after_external_call():
    if not is_atomic():
        e = get_errno()
        stmgcintf.StmOperations.begin_inevitable_transaction()
        llop.stm_start_transaction(lltype.Void)
        set_errno(e)
after_external_call._dont_reach_me_in_del_ = True
after_external_call._transaction_break_ = True

def enter_callback_call():
    token = stmgcintf.StmOperations.descriptor_init()
    if token != 1:
        after_external_call()
    else:
        ll_assert(not is_atomic(), "new thread: is_atomic() != 0")
        stmgcintf.StmOperations.begin_inevitable_transaction()
        # the StmGCTLS is not built yet.  leave it to gc_thread_start()
    return token
enter_callback_call._dont_reach_me_in_del_ = True
enter_callback_call._transaction_break_ = True

def leave_callback_call(token):
    if token != 1:
        before_external_call()
    else:
        # the StmGCTLS is already destroyed, done by gc_thread_die()
        # (we don't care if is_atomic() or not, we'll commit now)
        stmgcintf.StmOperations.commit_transaction()
        stmgcintf.StmOperations.descriptor_done()
leave_callback_call._dont_reach_me_in_del_ = True
leave_callback_call._transaction_break_ = True

# ____________________________________________________________

def make_perform_transaction(func, CONTAINERP):
    #
    def _stm_callback(llcontainer, retry_counter):
        if not is_atomic():
            llop.stm_start_transaction(lltype.Void)
        llcontainer = rffi.cast(CONTAINERP, llcontainer)
        try:
            res = func(llcontainer, retry_counter)
        except Exception, e:
            res = 0     # stop perform_transaction() and returns
            lle = cast_instance_to_base_ptr(e)
            llcontainer.got_exception = lle
        if not is_atomic():
            llop.stm_stop_transaction(lltype.Void)
        return res
    #
    def perform_transaction(llcontainer):
        before_external_call()
        adr_of_top = llop.gc_adr_of_root_stack_top(llmemory.Address)
        llcallback = llhelper(stmgcintf.StmOperations.CALLBACK_TX,
                              _stm_callback)
        stmgcintf.StmOperations.perform_transaction(llcallback, llcontainer,
                                                    adr_of_top)
        after_external_call()
        keepalive_until_here(llcontainer)
    perform_transaction._transaction_break_ = True
    #
    return perform_transaction

# ____________________________________________________________

class AbortInfoPush(ExtRegistryEntry):
    _about_ = abort_info_push

    def compute_result_annotation(self, s_instance, s_fieldnames):
        from rpython.annotator.model import SomeInstance
        assert isinstance(s_instance, SomeInstance)
        assert s_fieldnames.is_constant()
        assert isinstance(s_fieldnames.const, tuple)  # tuple of names

    def specialize_call(self, hop):
        fieldnames = hop.args_s[1].const
        lst = []
        v_instance = hop.inputarg(hop.args_r[0], arg=0)
        STRUCT = v_instance.concretetype.TO
        for fieldname in fieldnames:
            if fieldname == '[':
                lst.append(-2)    # start of sublist
                continue
            if fieldname == ']':
                lst.append(-1)    # end of sublist
                continue
            fieldname = 'inst_' + fieldname
            TYPE = getattr(STRUCT, fieldname) #xxx check also in parent structs
            if TYPE == lltype.Signed:
                kind = 1
            elif TYPE == lltype.Unsigned:
                kind = 2
            elif TYPE == lltype.Ptr(rstr.STR):
                kind = 3
            else:
                raise NotImplementedError(
                    "abort_info_push(%s, %r): field of type %r"
                    % (STRUCT.__name__, fieldname, TYPE))
            lst.append(kind)
            lst.append(llmemory.offsetof(STRUCT, fieldname))
        lst.append(0)
        ARRAY = rffi.CArray(lltype.Signed)
        array = lltype.malloc(ARRAY, len(lst), flavor='raw', immortal=True)
        for i in range(len(lst)):
            array[i] = lst[i]
        c_array = hop.inputconst(lltype.Ptr(ARRAY), array)
        hop.exception_cannot_occur()
        hop.genop('stm_abort_info_push', [v_instance, c_array])

# ____________________________________________________________

class ThreadLocalReference(object):
    _COUNT = 0

    def __init__(self, Cls):
        "NOT_RPYTHON: must be prebuilt"
        self.Cls = Cls
        self.local = thread._local()      # <- NOT_RPYTHON
        self.unique_id = ThreadLocalReference._COUNT
        ThreadLocalReference._COUNT += 1

    def _freeze_(self):
        return True

    @specialize.arg(0)
    def get(self):
        if we_are_translated():
            ptr = llop.stm_threadlocalref_get(rclass.OBJECTPTR, self.unique_id)
            return cast_base_ptr_to_instance(self.Cls, ptr)
        else:
            return getattr(self.local, 'value', None)

    @specialize.arg(0)
    def set(self, value):
        assert isinstance(value, self.Cls) or value is None
        if we_are_translated():
            ptr = cast_instance_to_base_ptr(value)
            llop.stm_threadlocalref_set(lltype.Void, self.unique_id, ptr)
        else:
            self.local.value = value
