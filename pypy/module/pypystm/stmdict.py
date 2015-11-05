"""
The class pypystm.stmdict, giving a part of the regular 'dict' interface
"""

from pypy.interpreter.error import OperationError
from pypy.interpreter.baseobjspace import W_Root
from pypy.interpreter.typedef import TypeDef
from pypy.interpreter.gateway import interp2app, unwrap_spec, WrappedDefault

from rpython.rlib import rstm, jit, rgc
from rpython.rlib.objectmodel import specialize, we_are_translated
from rpython.rtyper.annlowlevel import cast_gcref_to_instance
from rpython.rtyper.annlowlevel import cast_instance_to_gcref
from rpython.rtyper.lltypesystem import lltype, llmemory

ARRAY = lltype.GcArray(llmemory.GCREF)
PARRAY = lltype.Ptr(ARRAY)


# XXX: should have identity-dict strategy
def really_find_equal_item(space, h, w_key):
    hkey = space.hash_w(w_key)
    entry = h.lookup(hkey)
    array = lltype.cast_opaque_ptr(PARRAY, entry.object)
    if not array:
        return (entry, array, -1)
    if space.type(w_key).compares_by_identity():
        # fastpath
        return (entry, array, _find_equal_item(space, array, w_key))
    # slowpath
    return _really_find_equal_item_loop(space, h, w_key, entry, array, hkey)

@jit.dont_look_inside
def _really_find_equal_item_loop(space, h, w_key, entry, array, hkey):
    assert not space.type(w_key).compares_by_identity() # assume it stays that way
    while True:
        assert array
        i = _find_equal_item(space, array, w_key)
        # custom __eq__ may have been called in _find_equal_item()
        #
        # Only if entry.object changed during the call to _find_equal_item()
        # we have to re-lookup the entry. This is ok since entry.object=array!=NULL
        # when we enter here and therefore, entry can only be thrown out of
        # the hashtable if it gets NULLed somehow, thus, changing entry.object.
        array2 = lltype.cast_opaque_ptr(PARRAY, entry.object)
        if array != array2:
            # re-get entry (and array)
            entry = h.lookup(hkey)
            array = lltype.cast_opaque_ptr(PARRAY, entry.object)
            if not array:
                return (entry, array, -1)
            continue

        return (entry, array, i)


def _find_equal_item(space, array, w_key):
    # result by this function is based on 'array'. If the entry
    # changes, the result is stale.
    w_item = cast_gcref_to_instance(W_Root, array[0])
    if space.eq_w(w_key, w_item):
        return 0
    if len(array) > 2:
        return _run_next_iterations(space, array, w_key)
    return -1


@jit.dont_look_inside
def _run_next_iterations(space, array, w_key):
    i = 2
    limit = len(array) # fixed size
    while True:
        w_item = cast_gcref_to_instance(W_Root, array[i])
        if space.eq_w(w_key, w_item):
            return i
        i += 2
        if i >= limit:
            return -1

def ll_arraycopy(source, dest, source_start, dest_start, length):
    if we_are_translated():
        rgc.ll_arraycopy(source, dest, source_start, dest_start, length)
    else:
        for i in range(length):
            dest[dest_start + i] = source[source_start + i]

def pop_from_entry(h, space, w_key):
    entry, array, i = really_find_equal_item(space, h, w_key)
    if i < 0: # or not array
        return None
    # found
    w_value = cast_gcref_to_instance(W_Root, array[i + 1])
    L = len(array) - 2
    if L == 0:
        narray = lltype.nullptr(ARRAY)
    else:
        narray = lltype.malloc(ARRAY, L)
        ll_arraycopy(array, narray, 0, 0, i)
        ll_arraycopy(array, narray, i + 2, i, L - i)
    h.writeobj(entry, lltype.cast_opaque_ptr(llmemory.GCREF, narray))
    return w_value



class W_STMDict(W_Root):

    def __init__(self):
        self.h = rstm.create_hashtable()

    def getitem_w(self, space, w_key):
        entry, array, i = really_find_equal_item(space, self.h, w_key)
        if array and i >= 0:
            return cast_gcref_to_instance(W_Root, array[i + 1])
        space.raise_key_error(w_key)

    def setitem_w(self, space, w_key, w_value):
        entry, array, i = really_find_equal_item(space, self.h, w_key)
        if array:
            if i >= 0:
                # already there, update the value
                array[i + 1] = cast_instance_to_gcref(w_value)
                return
            L = len(array)
            narray = lltype.malloc(ARRAY, L + 2)
            ll_arraycopy(array, narray, 0, 0, L)
        else:
            narray = lltype.malloc(ARRAY, 2)
            L = 0
        narray[L] = cast_instance_to_gcref(w_key)
        narray[L + 1] = cast_instance_to_gcref(w_value)
        self.h.writeobj(entry, lltype.cast_opaque_ptr(llmemory.GCREF, narray))

    def delitem_w(self, space, w_key):
        if pop_from_entry(self.h, space, w_key) is None:
            space.raise_key_error(w_key)

    def contains_w(self, space, w_key):
        entry, array, i = really_find_equal_item(space, self.h, w_key)
        if array and i >= 0:
            return space.w_True
        return space.w_False

    @unwrap_spec(w_default=WrappedDefault(None))
    def get_w(self, space, w_key, w_default):
        entry, array, i = really_find_equal_item(space, self.h, w_key)
        if array and i >= 0:
            return cast_gcref_to_instance(W_Root, array[i + 1])
        return w_default

    def pop_w(self, space, w_key, w_default=None):
        w_value = pop_from_entry(self.h, space, w_key)
        if w_value is not None:
            return w_value
        elif w_default is not None:
            return w_default
        else:
            space.raise_key_error(w_key)

    @unwrap_spec(w_default=WrappedDefault(None))
    def setdefault_w(self, space, w_key, w_default):
        entry, array, i = really_find_equal_item(space, self.h, w_key)
        if array:
            if i >= 0:
                # already there, return the existing value
                return cast_gcref_to_instance(W_Root, array[i + 1])
            L = len(array)
            narray = lltype.malloc(ARRAY, L + 2)
            ll_arraycopy(array, narray, 0, 0, L)
        else:
            narray = lltype.malloc(ARRAY, 2)
            L = 0
        narray[L] = cast_instance_to_gcref(w_key)
        narray[L + 1] = cast_instance_to_gcref(w_default)
        self.h.writeobj(entry, lltype.cast_opaque_ptr(llmemory.GCREF, narray))
        return w_default


    def get_length(self):
        array, count = self.h.list()
        total_length_times_two = 0
        for i in range(count):
            subarray = lltype.cast_opaque_ptr(PARRAY, array[i].object)
            assert subarray
            total_length_times_two += len(subarray)
        return total_length_times_two >> 1

    def get_keys_values_w(self, offset):
        array, count = self.h.list()
        result_list_w = []
        for i in range(count):
            subarray = lltype.cast_opaque_ptr(PARRAY, array[i].object)
            assert subarray
            j = offset
            limit = len(subarray)
            while j < limit:
                w_item = cast_gcref_to_instance(W_Root, subarray[j])
                result_list_w.append(w_item)
                j += 2
        return result_list_w

    def get_items_w(self, space):
        array, count = self.h.list()
        result_list_w = []
        for i in range(count):
            subarray = lltype.cast_opaque_ptr(PARRAY, array[i].object)
            assert subarray
            j = 0
            limit = len(subarray)
            while j < limit:
                w_key = cast_gcref_to_instance(W_Root, subarray[j])
                w_value = cast_gcref_to_instance(W_Root, subarray[j + 1])
                result_list_w.append(space.newtuple([w_key, w_value]))
                j += 2
        return result_list_w

    def len_w(self, space):
        return space.wrap(self.get_length())

    def keys_w(self, space):
        return space.newlist(self.get_keys_values_w(offset=0))

    def values_w(self, space):
        return space.newlist(self.get_keys_values_w(offset=1))

    def items_w(self, space):
        return space.newlist(self.get_items_w(space))

    def iterkeys_w(self, space):
        return W_STMDictIterKeys(self.h)

    def itervalues_w(self, space):
        return W_STMDictIterValues(self.h)

    def iteritems_w(self, space):
        return W_STMDictIterItems(self.h)


class W_BaseSTMDictIter(W_Root):
    _immutable_fields_ = ["hiter"]
    next_from_same_hash = 0

    def __init__(self, hobj):
        self.hiter = hobj.iterentries()

    def descr_iter(self, space):
        return self

    def descr_length_hint(self, space):
        # xxx estimate: doesn't remove the items already yielded,
        # and uses the faster len_estimate(); on the other hand,
        # counts only one for every 64-bit hash value
        return space.wrap(self.hiter.hashtable.len_estimate())

    def descr_next(self, space):
        if self.next_from_same_hash == 0:      # common case
            try:
                entry = self.hiter.next()
            except StopIteration:
                raise OperationError(space.w_StopIteration, space.w_None)
            index = 0
            array = lltype.cast_opaque_ptr(PARRAY, entry.object)
        else:
            index = self.next_from_same_hash
            array = self.next_array
            self.next_from_same_hash = 0
            self.next_array = lltype.nullptr(ARRAY)
        #
        if len(array) > index + 2:      # uncommon case
            self.next_from_same_hash = index + 2
            self.next_array = array
        #
        return self.get_final_value(space, array, index)

    def _cleanup_(self):
        raise Exception("seeing a prebuilt %r object" % (
            self.__class__,))

class W_STMDictIterKeys(W_BaseSTMDictIter):
    def get_final_value(self, space, array, index):
        return cast_gcref_to_instance(W_Root, array[index])

class W_STMDictIterValues(W_BaseSTMDictIter):
    def get_final_value(self, space, array, index):
        return cast_gcref_to_instance(W_Root, array[index + 1])

class W_STMDictIterItems(W_BaseSTMDictIter):
    def get_final_value(self, space, array, index):
        return space.newtuple([
            cast_gcref_to_instance(W_Root, array[index]),
            cast_gcref_to_instance(W_Root, array[index + 1])])


def W_STMDict___new__(space, w_subtype):
    r = space.allocate_instance(W_STMDict, w_subtype)
    r.__init__()
    return space.wrap(r)

W_STMDict.typedef = TypeDef(
    'pypystm.stmdict',
    __new__ = interp2app(W_STMDict___new__),
    __getitem__ = interp2app(W_STMDict.getitem_w),
    __setitem__ = interp2app(W_STMDict.setitem_w),
    __delitem__ = interp2app(W_STMDict.delitem_w),
    __contains__ = interp2app(W_STMDict.contains_w),
    get = interp2app(W_STMDict.get_w),
    pop = interp2app(W_STMDict.pop_w),
    setdefault = interp2app(W_STMDict.setdefault_w),

    __len__  = interp2app(W_STMDict.len_w),
    keys     = interp2app(W_STMDict.keys_w),
    values   = interp2app(W_STMDict.values_w),
    items    = interp2app(W_STMDict.items_w),

    __iter__   = interp2app(W_STMDict.iterkeys_w),
    iterkeys   = interp2app(W_STMDict.iterkeys_w),
    itervalues = interp2app(W_STMDict.itervalues_w),
    iteritems  = interp2app(W_STMDict.iteritems_w),
    )

W_BaseSTMDictIter.typedef = TypeDef(
    "stmdict_iter",
    __iter__ = interp2app(W_BaseSTMDictIter.descr_iter),
    next = interp2app(W_BaseSTMDictIter.descr_next),
    __length_hint__ = interp2app(W_BaseSTMDictIter.descr_length_hint),
    )
