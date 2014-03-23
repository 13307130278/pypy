import py
from rpython.rlib import rstm, rgc, objectmodel
from rpython.rlib.debug import debug_print
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rtyper.lltypesystem.rclass import OBJECTPTR
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.translator.stm.test.support import CompiledSTMTests
from rpython.translator.stm.test import targetdemo2


class TestSTMTranslated(CompiledSTMTests):

    def test_malloc(self):
        class Foo:
            pass
        def entry_point(argv):
            lst = []
            for i in range(int(argv[1])):
                lst.append(Foo())
            print '<', len(lst), '>'
            return 0
        #
        t, cbuilder = self.compile(entry_point, backendopt=True)
        data = cbuilder.cmdexec('5')
        assert '< 5 >' in data, "got: %r" % (data,)
        data = cbuilder.cmdexec('42')
        assert '< 42 >' in data, "got: %r" % (data,)
        data = cbuilder.cmdexec('260')
        assert '< 260 >' in data, "got: %r" % (data,)

    def test_hash_id(self):
        from rpython.rlib.objectmodel import compute_identity_hash
        from rpython.rlib.objectmodel import compute_unique_id
        FOO = lltype.GcStruct('FOO')
        prebuilt = lltype.malloc(FOO)
        prebuilt_hash = lltype.identityhash(prebuilt)
        #
        def w(num, x):
            print '%d>>>' % num, compute_identity_hash(x), compute_unique_id(x)
        #
        def entry_point(argv):
            w(1, prebuilt)
            w(2, lltype.malloc(FOO))
            return 0
        #
        t, cbuilder = self.compile(entry_point)
        assert prebuilt_hash == lltype.identityhash(prebuilt)
        data = cbuilder.cmdexec('')
        data = data.split()
        i1 = data.index('1>>>')
        i2 = data.index('2>>>')
        hash1 = int(data[i1 + 1])
        id1   = int(data[i1 + 2])
        hash2 = int(data[i2 + 1])
        id2   = int(data[i2 + 2])
        assert hash1 == prebuilt_hash
        assert hash2 != 0
        assert id1 != id2

    def test_start_thread(self):
        from rpython.rlib import rthread
        class Global:
            value = 1
            seen = None
        glob = Global()
        #
        def threadfn():
            x = Global()
            x.value = 0
            glob.seen = x
        def entry_point(argv):
            glob.seen = None
            rthread.start_new_thread(threadfn, ())
            while glob.seen is None:
                llop.stm_commit_if_not_atomic(lltype.Void)
                llop.stm_start_inevitable_if_not_atomic(lltype.Void)
            return glob.seen.value
        #
        t, cbuilder = self.compile(entry_point)
        cbuilder.cmdexec('')
        # assert did not crash

    def test_become_inevitable(self):
        def entry_point(argv):
            rstm.become_inevitable()
            return 0
        t, cbuilder = self.compile(entry_point)
        cbuilder.cmdexec('')
        # assert did not crash

    def test_should_break_transaction(self):
        def entry_point(argv):
            print '<', int(rstm.should_break_transaction()), '>'
            return 0
        t, cbuilder = self.compile(entry_point)
        data = cbuilder.cmdexec('')
        assert '< 1 >\n' in data

    def test_set_transaction_length(self):
        def entry_point(argv):
            rstm.set_transaction_length(0.123)
            return 0
        t, cbuilder = self.compile(entry_point)
        cbuilder.cmdexec('')
        # assert did not crash

    def test_stm_atomic(self):
        def entry_point(argv):
            assert not rstm.is_atomic()
            rstm.increment_atomic()
            assert rstm.is_atomic()
            rstm.decrement_atomic()
            assert not rstm.is_atomic()
            return 0
        t, cbuilder = self.compile(entry_point)
        cbuilder.cmdexec('')
        # assert did not crash

    def test_collect(self):
        def entry_point(argv):
            rgc.collect(int(argv[1]))
            return 0
        t, cbuilder = self.compile(entry_point)
        cbuilder.cmdexec('0')
        cbuilder.cmdexec('1')
        # assert did not crash

    def test_targetdemo(self):
        t, cbuilder = self.compile(targetdemo2.entry_point)
        data, dataerr = cbuilder.cmdexec('4 5000', err=True)
        assert 'check ok!' in data

    def test_bug1(self):
        #
        def check(foobar, retry_counter):
            rgc.collect(0)
            return 0
        #
        S = lltype.GcStruct('S', ('got_exception', OBJECTPTR))
        PS = lltype.Ptr(S)
        perform_transaction = rstm.make_perform_transaction(check, PS)
        class X:
            def __init__(self, count):
                self.count = count
        def g():
            x = X(1000)
            perform_transaction(lltype.malloc(S))
            return x
        def entry_point(argv):
            x = X(len(argv))
            y = g()
            print '<', x.count, y.count, '>'
            return 0
        #
        perform_transaction = rstm.make_perform_transaction(check, PS)
        t, cbuilder = self.compile(entry_point, backendopt=True)
        data = cbuilder.cmdexec('a b c d')
        assert '< 5 1000 >' in data, "got: %r" % (data,)

    def test_bug2(self):
        #
        def check(foobar, retry_counter):
            return 0    # do nothing
        #
        class X2:
            pass
        prebuilt2 = [X2(), X2()]
        #
        S = lltype.GcStruct('S', ('got_exception', OBJECTPTR))
        PS = lltype.Ptr(S)
        perform_transaction = rstm.make_perform_transaction(check, PS)
        def bug2(count):
            x = prebuilt2[count]
            x.foobar = 2                    # 'x' becomes a local
            #
            perform_transaction(lltype.malloc(S))
                                            # 'x' becomes the global again
            #
            y = prebuilt2[count]            # same prebuilt obj
            y.foobar += 10                  # 'y' becomes a local
            return x.foobar                 # read from the global, thinking
        bug2._dont_inline_ = True           #    that it is still a local
        def entry_point(argv):
            print bug2(0)
            print bug2(1)
            return 0
        #
        perform_transaction = rstm.make_perform_transaction(check, PS)
        t, cbuilder = self.compile(entry_point, backendopt=True)
        data = cbuilder.cmdexec('')
        assert '12\n12\n' in data, "got: %r" % (data,)

    def test_prebuilt_nongc(self):
        py.test.skip("stmframework: GC pointer written into a non-GC location")
        def check(foobar, retry_counter):
            return 0    # do nothing
        from rpython.rtyper.lltypesystem import lltype
        S = lltype.GcStruct('S', ('got_exception', OBJECTPTR))
        PS = lltype.Ptr(S)
        perform_transaction = rstm.make_perform_transaction(check, PS)
        
        from rpython.rtyper.lltypesystem import lltype
        R = lltype.GcStruct('R', ('x', lltype.Signed))
        S1 = lltype.Struct('S1', ('r', lltype.Ptr(R)))
        s1 = lltype.malloc(S1, immortal=True, flavor='raw')
        #S2 = lltype.Struct('S2', ('r', lltype.Ptr(R)),
        #                   hints={'stm_thread_local': True})
        #s2 = lltype.malloc(S2, immortal=True, flavor='raw')
        def do_stuff():
            perform_transaction(lltype.malloc(S))
            print s1.r.x
            #print s2.r.x
        do_stuff._dont_inline_ = True
        def main(argv):
            s1.r = lltype.malloc(R)
            s1.r.x = 42
            #s2.r = lltype.malloc(R)
            #s2.r.x = 43
            do_stuff()
            return 0
        #
        perform_transaction = rstm.make_perform_transaction(check, PS)
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert '42\n' in data, "got: %r" % (data,)

    def test_threadlocalref(self):
        class FooBar(object):
            pass
        t = rstm.ThreadLocalReference(FooBar)
        def main(argv):
            x = FooBar()
            assert t.get() is None
            t.set(x)
            assert t.get() is x
            print "ok"
            return 0
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert 'ok\n' in data

    def test_abort_info(self):
        class Parent(object):
            pass
        class Foobar(Parent):
            pass
        globf = Foobar()

        def setxy(globf, retry_counter):
            if retry_counter > 1:
                globf.xy = 100 + retry_counter

        def check(_, retry_counter):
            rstm.abort_info_push(globf, ('[', 'xy', ']', 'yx'))
            setxy(globf, retry_counter)
            if retry_counter < 3:
                rstm.abort_and_retry()
            #
            last = rstm.charp_inspect_abort_info()
            if last:
                print rffi.charp2str(last)
            else:
                print 'got abort_info=NULL!'
            print int(bool(rstm.charp_inspect_abort_info()))
            #
            rstm.abort_info_pop(2)
            return 0

        PS = lltype.Ptr(lltype.GcStruct('S', ('got_exception', OBJECTPTR)))
        perform_transaction = rstm.make_perform_transaction(check, PS)

        def main(argv):
            Parent().xy = 0
            globf.xy = -2
            globf.yx = 'hi there %d' % len(argv)
            perform_transaction(lltype.nullptr(PS.TO))
            return 0
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('a b')
        assert 'li102ee10:hi there 3e\n0\n' in data

    def test_weakref(self):
        import weakref
        class Foo(object):
            pass

        def f(argv):
            foo = Foo()
            foo.n = argv
            w = weakref.ref(foo)
            assert w() is foo
            objectmodel.keepalive_until_here(foo)
            return w
        f._dont_inline_ = True

        def main(argv):
            w = f(argv)
            assert w() is not None
            assert len(w().n) == len(argv)
            rgc.collect()
            assert w() is None
            print 'test ok'
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('a b')
        assert 'test ok\n' in data

    def test_prebuilt_weakref(self):
        import weakref
        class Foo(object):
            pass
        foo = Foo(); foo.n = 42
        wr = weakref.ref(foo)

        def main(argv):
            wr().n += 1
            print '<', wr().n, '>'
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert '< 43 >\n' in data

    def test_stm_pointer_equal(self):
        class Foo:
            pass
        prebuilt_foo = Foo()
        def make(n):
            foo1 = Foo()
            foo2 = Foo()
            if n < 100:
                return foo1, foo2, foo1, None
            return None, None, None, foo1     # to annotate as "can be none"
        def main(argv):
            foo1, foo2, foo3, foo4 = make(len(argv))
            assert foo1 is not prebuilt_foo
            assert foo1 is not foo2
            assert foo1 is foo3
            assert foo4 is None
            assert foo1 is not None
            assert prebuilt_foo is not foo1
            assert None is not foo1
            assert None is foo4
            print 'test ok'
            return 0

        main([])
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert 'test ok\n' in data

    def test_raw_malloc_no_leak(self):
        FOOARRAY = lltype.Array(lltype.Signed)

        def check(_, retry_counter):
            x = lltype.malloc(FOOARRAY, 100000, flavor='raw')
            if retry_counter < 1000:
                if (retry_counter & 3) == 0:
                    lltype.free(x, flavor='raw')
                debug_print(rffi.cast(lltype.Signed, x))
                rstm.abort_and_retry()
            lltype.free(x, flavor='raw')
            return 0

        PS = lltype.Ptr(lltype.GcStruct('S', ('got_exception', OBJECTPTR)))
        perform_transaction = rstm.make_perform_transaction(check, PS)

        def main(argv):
            perform_transaction(lltype.nullptr(PS.TO))
            return 0

        t, cbuilder = self.compile(main)
        data, dataerr = cbuilder.cmdexec('', err=True)
        lines = dataerr.split('\n')
        assert len(lines) > 1000
        addresses = map(int, lines[:1000])
        assert len(addresses) == 1000
        assert len(set(addresses)) < 500    # should ideally just be a few
        import re
        match = re.search(r"(\d+) mallocs left", dataerr)
        assert match
        assert int(match.group(1)) < 20

    def test_gc_writebarrier(self):
        X = lltype.GcStruct('X', ('foo', lltype.Signed))
        prebuilt = lltype.malloc(X, immortal=True)
        prebuilt.foo = 42

        def main(argv):
            llop.gc_writebarrier(lltype.Void, prebuilt)
            debug_print(objectmodel.current_object_addr_as_int(prebuilt))
            prebuilt.foo = 43
            debug_print(objectmodel.current_object_addr_as_int(prebuilt))
            return 0

        t, cbuilder = self.compile(main)
        data, dataerr = cbuilder.cmdexec('', err=True)
        lines = dataerr.split('\n')
        assert lines[0] == lines[1]

    def test_dtoa(self):
        def main(argv):
            a = len(argv) * 0.2
            b = len(argv) * 0.6
            debug_print(str(a))
            debug_print(str(b))
            return 0

        t, cbuilder = self.compile(main)
        data, dataerr = cbuilder.cmdexec('a', err=True)
        lines = dataerr.split('\n')
        assert lines[0] == ' 0.400000'
        assert lines[1] == ' 1.200000'

    def test_stm_ignored(self):
        class X:
            foo = 84
        prebuilt = X()
        prebuilt2 = X()
        def main(argv):
            with objectmodel.stm_ignored:
                prebuilt.foo = 42
            with objectmodel.stm_ignored:
                x = prebuilt2.foo
            print 'did not crash', x
            return 0

        t, cbuilder = self.compile(main)
        opnames = [op.opname for op in t.graphs[0].startblock.operations]
        assert opnames[:6] == ['stm_ignored_start',
                               'bare_setfield',    # with no stm_write
                               'stm_ignored_stop',
                               'stm_ignored_start',
                               'getfield',         # with no stm_read
                               'stm_ignored_stop']
        data = cbuilder.cmdexec('')
        assert 'did not crash 84\n' in data

    def test_float_inf_nan_in_struct(self):
        mylist = [float("inf"), float("-inf"), float("nan")]
        def main(argv):
            print ':', mylist[int(argv[1])]
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('0')
        assert ': inf\n' in data
        data = cbuilder.cmdexec('1')
        assert ': -inf\n' in data
        data = cbuilder.cmdexec('2')
        assert ': nan\n' in data

    def test_static_root_in_nongc(self):
        class A:
            def __init__(self, n):
                self.n = n
        class B:
            def _freeze_(self):
                return True
        b1 = B(); b1.a = A(42)
        b2 = B(); b2.a = A(84)
        def dump(b):
            print '<', b.a.n, '>'
        def main(argv):
            dump(b1)
            dump(b2)
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert '< 42 >\n< 84 >\n' in data
