import py
from rpython.rlib import rstm, rgc, objectmodel
from rpython.rlib.debug import debug_print
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.rclass import OBJECTPTR
from rpython.rtyper.annlowlevel import cast_instance_to_gcref
from rpython.rtyper.annlowlevel import cast_gcref_to_instance
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
                llop.stm_start_if_not_atomic(lltype.Void)
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
            rstm.hint_commit_soon()
            print '<', int(rstm.should_break_transaction(True)), '>'
            return 0
        t, cbuilder = self.compile(entry_point)
        data = cbuilder.cmdexec('')
        assert '< 0 >\n' in data

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
        class X:
            def __init__(self, count):
                self.count = count
        def g():
            x = X(1000)
            rgc.collect(0)
            return x
        def entry_point(argv):
            x = X(len(argv))
            y = g()
            print '<', x.count, y.count, '>'
            return 0
        #
        t, cbuilder = self.compile(entry_point, backendopt=True)
        data = cbuilder.cmdexec('a b c d')
        assert '< 5 1000 >' in data, "got: %r" % (data,)

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
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert '42\n' in data, "got: %r" % (data,)

    def test_threadlocalref(self):
        from rpython.rlib import rthread
        class FooBar(object):
            pass
        t = rthread.ThreadLocalReference(FooBar)
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

        def main(argv):
            # make sure perform_transaction breaks the transaction:
            rstm.hint_commit_soon()
            start = rstm.stm_count() + 1
            rstm.break_transaction()
            retry_counter = rstm.stm_count() - start
            check(None, retry_counter)
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

    def test_gc_writebarrier_and_misc(self):
        X = lltype.GcStruct('X', ('foo', lltype.Signed))
        prebuilt = lltype.malloc(X, immortal=True)
        prebuilt.foo = 42

        def main(argv):
            llop.gc_writebarrier(lltype.Void, prebuilt)
            debug_print(objectmodel.current_object_addr_as_int(prebuilt))
            prebuilt.foo = 43
            debug_print(objectmodel.current_object_addr_as_int(prebuilt))
            llop.get_write_barrier_failing_case(rffi.VOIDP)
            llop.gc_adr_of_root_stack_top(llmemory.Address)
            assert llop.gc_can_move(lltype.Bool, prebuilt) == False
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

    def first_block(self, graph):
        block = graph.startblock
        if not block.operations:
            [exitlink] = block.exits
            block = exitlink.target
        return block

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
        block = self.first_block(t.graphs[0])
        opnames = [op.opname for op in block.operations]
        assert opnames[:6] == ['stm_ignored_start',
                               'bare_setfield',    # with no stm_write
                               'stm_ignored_stop',
                               'stm_ignored_start',
                               'getfield',         # with no stm_read
                               'stm_ignored_stop']
        data = cbuilder.cmdexec('')
        assert 'did not crash 84\n' in data

    def test_stm_write_card(self):
        LST = lltype.GcArray(lltype.Signed)
        lst = lltype.malloc(LST, 1000, immortal=True)
        LST2 = lltype.GcArray(lltype.Ptr(LST))
        lst2 = lltype.malloc(LST2, 1000, immortal=True)
        def main(argv):
            lst[42] = 43
            lst2[999] = lst
            llop.stm_commit_if_not_atomic(lltype.Void)
            llop.stm_start_if_not_atomic(lltype.Void)
            print 'did not crash', lst2[999][42]
            return 0

        t, cbuilder = self.compile(main)
        block = self.first_block(t.graphs[0])
        first_op = block.operations[0]
        assert first_op.opname == 'stm_write'
        assert first_op.args[1].value == 42
        data = cbuilder.cmdexec('')
        assert 'did not crash 43\n' in data

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

    def test_raw_load_store_on_gc(self):
        X = lltype.GcStruct('X', ('foo', lltype.Signed))
        prebuilt = lltype.malloc(X, immortal=True)
        prebuilt.foo = 42
        ofs_foo = llmemory.offsetof(X, 'foo')

        def main(argv):
            p = lltype.cast_opaque_ptr(llmemory.GCREF, prebuilt)
            llop.raw_store(lltype.Void, p, ofs_foo, -84)
            print prebuilt.foo
            prebuilt.foo = -1298
            print llop.raw_load(lltype.Signed, p, ofs_foo)
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert '-84\n' in data
        assert '-1298\n' in data

    def test_pypy_marker(self):
        class Code(object):
            pass
        class PyCode(Code):
            def __init__(self, co_filename, co_name,
                         co_firstlineno, co_lnotab):
                self.co_filename = co_filename
                self.co_name = co_name
                self.co_firstlineno = co_firstlineno
                self.co_lnotab = co_lnotab

        def run_interpreter(pycode):
            print 'starting', pycode.co_name
            rstm.push_marker(1, pycode)
            for i in range(10):
                p = llop.stm_expand_marker(rffi.CCHARP)
                print rffi.charp2str(p)
                rstm.update_marker_num((i+1) * 2 + 1)
            rstm.pop_marker()
            print 'stopping', pycode.co_name

        def main(argv):
            pycode1 = PyCode("/tmp/foobar.py", "baz", 40, "\x00\x01\x05\x01")
            pycode2 = PyCode("/tmp/foobaz.py", "bar", 70, "\x00\x01\x04\x02")
            pycode3 = PyCode(
                "/tmp/some/where/very/very/long/path/bla/br/project/foobaz.py",
                "some_extremely_longish_and_boring_function_name",
                80, "\x00\x01\x04\x02")
            Code().co_name = "moved up"
            llop.stm_setup_expand_marker_for_pypy(
                lltype.Void, pycode1,
                "co_filename", "co_name", "co_firstlineno", "co_lnotab")

            run_interpreter(pycode1)
            run_interpreter(pycode2)
            run_interpreter(pycode3)
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert ('starting baz\n'
                'File "/tmp/foobar.py", line 41, in baz\n'
                'File "/tmp/foobar.py", line 41, in baz\n'
                'File "/tmp/foobar.py", line 41, in baz\n'
                'File "/tmp/foobar.py", line 41, in baz\n'
                'File "/tmp/foobar.py", line 41, in baz\n'
                'File "/tmp/foobar.py", line 42, in baz\n'
                'File "/tmp/foobar.py", line 42, in baz\n'
                'File "/tmp/foobar.py", line 42, in baz\n'
                'File "/tmp/foobar.py", line 42, in baz\n'
                'File "/tmp/foobar.py", line 42, in baz\n'
                'stopping baz\n') in data
        assert ('starting bar\n'
                'File "/tmp/foobaz.py", line 71, in bar\n'
                'File "/tmp/foobaz.py", line 71, in bar\n'
                'File "/tmp/foobaz.py", line 71, in bar\n'
                'File "/tmp/foobaz.py", line 71, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'File "/tmp/foobaz.py", line 73, in bar\n'
                'stopping bar\n') in data
        assert ('starting some_extremely_longish_and_boring_function_name\n'
                'File "<bla/br/project/foobaz.py", line 81,'
                ' in some_extremely_longish_a>\n') in data

    def test_finalizer(self):
        class Counter:
            num = 0
        g_counter = Counter()
        class X:
            def __del__(self):
                g_counter.num += 1

        def g():
            X()

        def main(argv):
            x1 = X()
            g()
            rgc.collect()
            print 'destructors called:', g_counter.num
            objectmodel.keepalive_until_here(x1)
            return 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert 'destructors called: 1\n' in data

    def test_light_finalizer(self):
        class X:
            @rgc.must_be_light_finalizer
            def __del__(self):
                debug_print("<del>")
        def g():
            X()
        def main(argv):
            g()
            rgc.collect(0)
            return 0

        t, cbuilder = self.compile(main)
        data, err = cbuilder.cmdexec('', err=True)
        assert '<del>' in err

    def test_hashtable(self):
        py.test.skip("missing: custom tracer on Hashtable")

        class X(object):
            pass

        def main(argv):
            h = rstm.create_hashtable()
            p = h.get(-1234)
            assert p == lltype.nullptr(llmemory.GCREF.TO)
            #
            x1 = X()
            p1 = cast_instance_to_gcref(x1)
            h.set(-1234, p1)
            #
            p2 = h.get(-1234)
            x2 = cast_gcref_to_instance(X, p2)
            assert x2 is x1
            #
            rgc.collect()
            #
            p2 = h.get(-1234)
            x2 = cast_gcref_to_instance(X, p2)
            assert x2 is x1
            #
            print "ok!"
            return 0

        res = main([])      # direct run
        assert res == 0

        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert 'ok!\n' in data
