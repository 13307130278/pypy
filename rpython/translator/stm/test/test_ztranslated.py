import py
from rpython.rlib import rstm, rgc
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.annlowlevel import cast_instance_to_base_ptr
from rpython.translator.stm.test.support import NoGcCompiledSTMTests
from rpython.translator.stm.test.support import CompiledSTMTests
from rpython.translator.stm.test import targetdemo2


class TestNoGcSTMTranslated(NoGcCompiledSTMTests):
    def test_nogc_targetdemo(self):
        t, cbuilder = self.compile(targetdemo2.entry_point)
        data, dataerr = cbuilder.cmdexec('4 100', err=True)
        assert 'check ok!' in data


class TestSTMTranslated(CompiledSTMTests):

    def test_targetdemo(self):
        t, cbuilder = self.compile(targetdemo2.entry_point)
        data, dataerr = cbuilder.cmdexec('4 5000', err=True,
                                         env={'PYPY_GC_DEBUG': '1'})
        assert 'check ok!' in data

    def test_bug1(self):
        #
        class Foobar:
            pass
        def check(foobar, retry_counter):
            rgc.collect(0)
            return 0
        #
        class X:
            def __init__(self, count):
                self.count = count
        def g():
            x = X(1000)
            rstm.perform_transaction(check, Foobar, Foobar())
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

    def test_bug2(self):
        #
        class Foobar:
            pass
        def check(foobar, retry_counter):
            return 0    # do nothing
        #
        class X2:
            pass
        prebuilt2 = [X2(), X2()]
        #
        def bug2(count):
            x = prebuilt2[count]
            x.foobar = 2                    # 'x' becomes a local
            #
            rstm.perform_transaction(check, Foobar, Foobar())
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
        t, cbuilder = self.compile(entry_point, backendopt=True)
        data = cbuilder.cmdexec('')
        assert '12\n12\n' in data, "got: %r" % (data,)

    def test_prebuilt_nongc(self):
        class Foobar:
            pass
        def check(foobar, retry_counter):
            return 0    # do nothing
        from rpython.rtyper.lltypesystem import lltype
        R = lltype.GcStruct('R', ('x', lltype.Signed))
        S1 = lltype.Struct('S1', ('r', lltype.Ptr(R)))
        s1 = lltype.malloc(S1, immortal=True, flavor='raw')
        #S2 = lltype.Struct('S2', ('r', lltype.Ptr(R)),
        #                   hints={'stm_thread_local': True})
        #s2 = lltype.malloc(S2, immortal=True, flavor='raw')
        def do_stuff():
            rstm.perform_transaction(check, Foobar, Foobar())
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
        class FooBar(object):
            pass
        t = rstm.ThreadLocalReference(FooBar)
        def main(argv):
            x = FooBar()
            assert t.get() is None
            t.set(x)
            assert t.get() is x
            assert llop.stm_threadlocalref_llcount(lltype.Signed) == 1
            p = llop.stm_threadlocalref_lladdr(llmemory.Address, 0)
            adr = p.address[0]
            adr2 = cast_instance_to_base_ptr(x)
            adr2 = llmemory.cast_ptr_to_adr(adr2)
            assert adr == adr2
            print "ok"
            return 0
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('')
        assert 'ok\n' in data

    def test_abort_info(self):
        from rpython.rtyper.lltypesystem.rclass import OBJECTPTR

        class Foobar(object):
            pass
        globf = Foobar()

        def check(_, retry_counter):
            globf.xy = 100 + retry_counter
            rstm.abort_info_push(globf, ('xy', '[', 'yx', ']'))
            if retry_counter < 3:
                rstm.abort_and_retry()
            #
            print rffi.charp2str(rstm.charp_inspect_abort_info())
            #
            rstm.abort_info_pop(2)
            return 0

        PS = lltype.Ptr(lltype.GcStruct('S', ('got_exception', OBJECTPTR)))
        perform_transaction = rstm.make_perform_transaction(check, PS)

        def main(argv):
            globf.yx = 'hi there %d' % len(argv)
            perform_transaction(lltype.nullptr(PS.TO))
            return 0
        t, cbuilder = self.compile(main)
        data = cbuilder.cmdexec('a b')
        assert 'li102el10:hi there 3ee\n' in data
