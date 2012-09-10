from pypy.rpython.lltypesystem import lltype, llmemory, llarena, llgroup
from pypy.rpython.lltypesystem.lloperation import llop
from pypy.rpython.lltypesystem.llmemory import raw_malloc_usage, raw_memcopy
from pypy.rpython.memory.gc.base import GCBase, MovingGCBase
from pypy.rpython.memory.support import mangle_hash
from pypy.rpython.annlowlevel import llhelper
from pypy.rlib.rarithmetic import LONG_BIT, r_uint
from pypy.rlib.debug import ll_assert, debug_start, debug_stop, fatalerror
from pypy.rlib.debug import debug_print
from pypy.module.thread import ll_thread


WORD = LONG_BIT // 8
NULL = llmemory.NULL

first_gcflag = 1 << (LONG_BIT//2)

# Documentation:
# https://bitbucket.org/pypy/extradoc/raw/extradoc/talk/stm2012/stmimpl.rst
#
# Terminology used here:
#
#   - Objects can be LOCAL or GLOBAL.  This is what STM is based on.
#     Local objects are not visible to any other transaction, whereas
#     global objects are, and are read-only.
#
#   - Each object lives either in the shared area, or in a thread-local
#     nursery.  The shared area contains:
#       - the prebuilt objects
#       - the small objects allocated via minimarkpage.py (XXX so far,
#         just with malloc)
#       - the non-small raw-malloced objects
#
#   - The GLOBAL objects are all located in the shared area.
#     All GLOBAL objects are non-movable.
#
#   - The LOCAL objects might be YOUNG or OLD depending on whether they
#     already survived a collection.  YOUNG LOCAL objects are either in
#     the nursery or, if they are big, raw-malloced.  OLD LOCAL objects
#     are in the shared area.  Getting the write barrier right for both
#     this and the general STM mechanisms is tricky, so for now this GC
#     is not actually generational (slow when running long transactions
#     or before running transactions at all).  (XXX fix me)
#
#   - So far, the GC is always running in "transactional" mode.  Later,
#     it would be possible to speed it up in case there is only one
#     (non-blocked) thread.
#
# GC Flags on objects:
#
#   - GCFLAG_GLOBAL: identifies GLOBAL objects.  All prebuilt objects
#     start as GLOBAL; conversely, all freshly allocated objects start
#     as LOCAL, and become GLOBAL if they survive an end-of-transaction.
#     All objects that are GLOBAL are immortal and leaking for now
#     (global_collect() will be done later).
#
#   - GCFLAG_POSSIBLY_OUTDATED: see stmimpl.rst.  Used by C.
#   - GCFLAG_NOT_WRITTEN: see stmimpl.rst.  Used by C.
#   - GCFLAG_LOCAL_COPY: see stmimpl.rst.  Used by C.
#     Note that GCFLAG_LOCAL_COPY and GCFLAG_GLOBAL are exclusive.
#
#   - GCFLAG_VISITED: used temporarily to mark local objects found to be
#     surviving during a collection.  Between collections, it is set on
#     the LOCAL COPY objects, but only on them.
#
#   - GCFLAG_HASH_FIELD: the object contains an extra field added at the
#     end, with the hash value
#
# Invariant: between two transactions, all objects visible from the current
# thread are always GLOBAL.  In particular:
#
#   - The LOCAL objects of a thread are not visible at all from other threads.
#     This means that there is *no* pointer from a GLOBAL object directly to
#     a LOCAL object.  At most, there can be pointers from a GLOBAL object to
#     another GLOBAL object that itself has a LOCAL COPY --- or, of course,
#     pointers from a LOCAL object to anything.
#
# Collection: for now we have only local_collection(), which ignores all
# GLOBAL objects.
#
#   - To find the roots, we take the list (maintained by the C code)
#     of the LOCAL COPY objects of the current transaction, together with
#     the stack.  GLOBAL objects can be ignored because they have no
#     pointer to any LOCAL object at all.
#
#   - A special case is the end-of-transaction collection, done by the same
#     local_collection() with a twist: all surviving objects (after being
#     copied out of the nursery) receive the flags GLOBAL and NOT_WRITTEN.
#     At the end, we are back to the situation where this thread sees only
#     GLOBAL objects.  This is PerformLocalCollect() in stmimpl.rst.
#     It is done just before CommitTransaction(), implemented in C.
#
# All objects have an address-sized 'revision' field in their header.
# It is generally used by the C code (marked with (*) below), but it
# is also (ab)used by the GC itself (marked with (!) below).
#
#   - for local objects with GCFLAG_LOCAL_COPY, it points to the GLOBAL
#     original (*).
#
#   - it contains the 'next' object of the 'sharedarea_tls.chained_list'
#     list, which describes all LOCAL objects malloced outside the
#     nursery (!).
#
#   - during collection, the nursery objects that are copied outside
#     the nursery grow GCFLAG_VISITED and their 'revision' points
#     to the new copy (!).
#
#   - on any GLOBAL object, 'revision' is managed by C code (*).
#     It must be initialized to 1 when the GC code turns a LOCAL object
#     into a GLOBAL one.
#

GCFLAG_GLOBAL            = first_gcflag << 0     # keep in sync with et.h
GCFLAG_POSSIBLY_OUTDATED = first_gcflag << 1     # keep in sync with et.h
GCFLAG_NOT_WRITTEN       = first_gcflag << 2     # keep in sync with et.h
GCFLAG_LOCAL_COPY        = first_gcflag << 3     # keep in sync with et.h
GCFLAG_VISITED           = first_gcflag << 4     # keep in sync with et.h
GCFLAG_HASH_FIELD        = first_gcflag << 5     # keep in sync with et.h

GCFLAG_PREBUILT          = GCFLAG_GLOBAL | GCFLAG_NOT_WRITTEN
REV_INITIAL              = r_uint(1)
REV_FLAG_NEW_HASH        = r_uint(2)


def always_inline(fn):
    fn._always_inline_ = True
    return fn
def dont_inline(fn):
    fn._dont_inline_ = True
    return fn


class StmGC(MovingGCBase):
    _alloc_flavor_ = "raw"
    inline_simple_malloc = True
    inline_simple_malloc_varsize = True
    prebuilt_gc_objects_are_static_roots = False
    malloc_zero_filled = True

    HDR = lltype.Struct('header', ('tid', lltype.Signed),
                                  ('revision', lltype.Unsigned))
    typeid_is_in_field = 'tid'
    withhash_flag_is_in_field = 'tid', GCFLAG_HASH_FIELD

    TRANSLATION_PARAMS = {
        'stm_operations': 'use_real_one',
        'nursery_size': 32*1024*1024,           # 32 MB

        #"page_size": 1024*WORD,                 # copied from minimark.py
        #"arena_size": 65536*WORD,               # copied from minimark.py
        #"small_request_threshold": 35*WORD,     # copied from minimark.py
    }

    def __init__(self, config,
                 stm_operations='use_emulator',
                 nursery_size=1024,
                 #page_size=16*WORD,
                 #arena_size=64*WORD,
                 #small_request_threshold=5*WORD,
                 #ArenaCollectionClass=None,
                 **kwds):
        MovingGCBase.__init__(self, config, multithread=True, **kwds)
        #
        if isinstance(stm_operations, str):
            assert stm_operations == 'use_real_one', (
                "XXX not provided so far: stm_operations == %r" % (
                stm_operations,))
            from pypy.translator.stm.stmgcintf import StmOperations
            stm_operations = StmOperations()
        #
        from pypy.rpython.memory.gc import stmshared
        self.stm_operations = stm_operations
        self.nursery_size = nursery_size
        self.sharedarea = stmshared.StmGCSharedArea(self)
        #
        def _stm_duplicate(obj):     # indirection to hide 'self'
            return self.stm_duplicate(obj)
        self._stm_duplicate = _stm_duplicate

    def setup(self):
        """Called at run-time to initialize the GC."""
        #
        # Hack: MovingGCBase.setup() sets up stuff related to id(), which
        # we implement differently anyway.  So directly call GCBase.setup().
        GCBase.setup(self)
        #
        # The following line causes the _stm_getsize() function to be
        # generated in the C source with a specific signature, where it
        # can be called by the C code.
        llop.nop(lltype.Void, llhelper(self.stm_operations.DUPLICATE,
                                       self._stm_duplicate))
        #
        self.sharedarea.setup()
        #
        self.stm_operations.descriptor_init()
        self.stm_operations.begin_inevitable_transaction()
        self.setup_thread()

    def setup_thread(self):
        """Build the StmGCTLS object and start a transaction at the level
        of the GC.  The C-level transaction should already be started."""
        ll_assert(self.stm_operations.in_transaction(),
                  "setup_thread: not in a transaction")
        from pypy.rpython.memory.gc.stmtls import StmGCTLS
        stmtls = StmGCTLS(self)
        stmtls.start_transaction()

    def teardown_thread(self):
        """Stop the current transaction, commit it at the level of
        C code, and tear down the StmGCTLS object.  For symmetry, this
        ensures that the level of C has another (empty) transaction
        started."""
        ll_assert(bool(self.stm_operations.in_transaction()),
                  "teardown_thread: not in a transaction")
        stmtls = self.get_tls()
        stmtls.stop_transaction()
        self.stm_operations.commit_transaction()
        self.stm_operations.begin_inevitable_transaction()
        stmtls.delete()

    @always_inline
    def get_tls(self):
        from pypy.rpython.memory.gc.stmtls import StmGCTLS
        tls = self.stm_operations.get_tls()
        return StmGCTLS.cast_address_to_tls_object(tls)

    @staticmethod
    def JIT_max_size_of_young_obj():
        return None

    @staticmethod
    def JIT_minimal_size_in_nursery():
        return 0

    JIT_WB_IF_FLAG = GCFLAG_GLOBAL

    # ----------

    def malloc_fixedsize_clear(self, typeid, size,
                               needs_finalizer=False,
                               is_finalizer_light=False,
                               contains_weakptr=False):
        #assert not needs_finalizer, "XXX" --- finalizer is just ignored
        #
        # Get the memory from the thread-local nursery.
        size_gc_header = self.gcheaderbuilder.size_gc_header
        totalsize = size_gc_header + size
        tls = self.get_tls()
        result = tls.allocate_bump_pointer(totalsize)
        #
        # Build the object.
        llarena.arena_reserve(result, totalsize)
        obj = result + size_gc_header
        self.init_gc_object(result, typeid, flags=0)
        if contains_weakptr:   # check constant-folded
            tls.fresh_new_weakref(obj)
        #
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)


    def malloc_varsize_clear(self, typeid, length, size, itemsize,
                             offset_to_length):
        # XXX blindly copied from malloc_fixedsize_clear() for now.
        # XXX Be more subtle, e.g. detecting overflows, at least
        size_gc_header = self.gcheaderbuilder.size_gc_header
        nonvarsize = size_gc_header + size
        totalsize = nonvarsize + itemsize * length
        totalsize = llarena.round_up_for_allocation(totalsize)
        result = self.get_tls().allocate_bump_pointer(totalsize)
        llarena.arena_reserve(result, totalsize)
        obj = result + size_gc_header
        self.init_gc_object(result, typeid, flags=0)
        (obj + offset_to_length).signed[0] = length
        return llmemory.cast_adr_to_ptr(obj, llmemory.GCREF)


    def collect(self, gen=1):
        self.get_tls().local_collection()
        if gen > 0:
            debug_print("XXX not implemented: global collect()")

    def start_transaction(self):
        self.get_tls().start_transaction()

    def stop_transaction(self):
        self.get_tls().stop_transaction()


    @always_inline
    def get_type_id(self, obj):
        tid = self.header(obj).tid
        return llop.extract_ushort(llgroup.HALFWORD, tid)

    def get_size_incl_hash(self, obj):
        size = self.get_size(obj)
        hdr = self.header(obj)
        if hdr.tid & GCFLAG_HASH_FIELD:
            size += llmemory.sizeof(lltype.Signed)
        return size

    @always_inline
    def combine(self, typeid16, flags):
        return llop.combine_ushort(lltype.Signed, typeid16, flags)

    @always_inline
    def init_gc_object(self, addr, typeid16, flags=0):
        hdr = llmemory.cast_adr_to_ptr(addr, lltype.Ptr(self.HDR))
        hdr.tid = self.combine(typeid16, flags)

    def init_gc_object_immortal(self, addr, typeid16, flags=0):
        flags |= GCFLAG_PREBUILT
        self.init_gc_object(addr, typeid16, flags)
        hdr = llmemory.cast_adr_to_ptr(addr, lltype.Ptr(self.HDR))
        hdr.revision = REV_INITIAL

    def obj_revision(self, obj):
        return hdr_revision(self.header(obj))

    def set_obj_revision(self, obj, nrevision):
        set_hdr_revision(self.header(obj), nrevision)

    def stm_duplicate(self, obj):
        tls = self.get_tls()
        try:
            localobj = tls.duplicate_obj(obj, self.get_size(obj))
            tls.copied_local_objects.append(localobj)     # XXX KILL
        except MemoryError:
            # should not really let the exception propagate.
            # XXX do something slightly better, like abort the transaction
            # and raise a MemoryError when retrying
            fatalerror("FIXME: MemoryError in stm_duplicate")
            return llmemory.NULL
        #
        hdr = self.header(localobj)
        hdr.tid &= ~(GCFLAG_GLOBAL | GCFLAG_POSSIBLY_OUTDATED)
        hdr.tid |= (GCFLAG_VISITED | GCFLAG_LOCAL_COPY)
        return localobj

    # ----------
    # id() and identityhash() support

    def id(self, gcobj):
        """NOT IMPLEMENTED! XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"""
        return self.identityhash(gcobj)

    def identityhash(self, gcobj):
        obj = llmemory.cast_ptr_to_adr(gcobj)
        obj = self.stm_operations.stm_HashObject(obj)
        return self._get_object_hash(obj)

    def _get_object_hash(self, obj):
        if self.header(obj).tid & GCFLAG_HASH_FIELD:
            objsize = self.get_size(obj)
            obj = llarena.getfakearenaaddress(obj)
            return (obj + objsize).signed[0]
        else:
            # XXX improve hash(nursery_object)
            return mangle_hash(llmemory.cast_adr_to_int(obj))

    def can_move(self, obj):
        tls = self.get_tls()
        if tls.is_in_nursery(addr):
            return True
        else:
            # XXX for now a pointer to a non-nursery object is
            # always valid, as long as we don't have global collections
            return False

# ____________________________________________________________
# helpers

def hdr_revision(hdr):
    return llmemory.cast_int_to_adr(hdr.revision)

def set_hdr_revision(hdr, nrevision):
    hdr.revision = llmemory.cast_adr_to_uint_symbolic(nrevision)
