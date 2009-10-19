from pypy.conftest import gettestobjspace, option
from pypy.tool.udir import udir
import py
from py.test import skip
import sys, os

class BytecodeTrace(list):
    def get_opnames(self, prefix=""):
        return [op.getopname() for op in self
                    if op.getopname().startswith(prefix)]

ZERO_OP_BYTECODES = [
    'POP_TOP',
    'ROT_TWO',
    'ROT_THREE',
    'DUP_TOP',
    'ROT_FOUR',
    'NOP',
    'DUP_TOPX',
    'LOAD_CONST',
    'JUMP_FORWARD',
    #'JUMP_ABSOLUTE' in theory, but contains signals stuff
    #'LOAD_FAST' should be here, but currently needs a guard for nonzeroness
    'STORE_FAST',
    ]

class PyPyCJITTests(object):
    def run_source(self, source, *testcases):
        source = py.code.Source(source)
        filepath = self.tmpdir.join('case%d.py' % self.counter)
        logfilepath = filepath.new(ext='.log')
        self.__class__.counter += 1
        f = filepath.open('w')
        print >> f, source
        # some support code...
        print >> f, py.code.Source("""
            import sys, pypyjit
            pypyjit.set_param(threshold=3)

            def check(args, expected):
                print >> sys.stderr, 'trying:', args
                result = main(*args)
                print >> sys.stderr, 'got:', repr(result)
                assert result == expected
                assert type(result) is type(expected)
        """)
        for testcase in testcases * 2:
            print >> f, "check(%r, %r)" % testcase
        print >> f, "print 'OK :-)'"
        f.close()

        # we don't have os.popen() yet on pypy-c...
        if sys.platform.startswith('win'):
            py.test.skip("XXX this is not Windows-friendly")
        child_stdin, child_stdout = os.popen2('PYPYJITLOG="%s" "%s" "%s"' % (
            logfilepath, self.pypy_c, filepath))
        child_stdin.close()
        result = child_stdout.read()
        child_stdout.close()
        assert result
        assert result.splitlines()[-1].strip() == 'OK :-)'
        assert logfilepath.check()
        opslogfile = logfilepath.new(ext='.log.ops')
        self.parse_loops(opslogfile)

    def parse_loops(self, opslogfile):
        from pypy.jit.metainterp.test.oparser import parse, split_logs_into_loops
        assert opslogfile.check()
        logs = opslogfile.read()
        parts = split_logs_into_loops(logs)
        # skip entry bridges, they can contain random things
        self.loops = [parse(part, no_namespace=True) for part in parts
                          if "entry bridge" not in part]
        self.sliced_loops = [] # contains all bytecodes of all loops
        for loop in self.loops:
            for op in loop.operations:
                if op.getopname() == "debug_merge_point":
                    sliced_loop = BytecodeTrace()
                    sliced_loop.bytecode = op.args[0]._get_str().rsplit(" ", 1)[1]
                    self.sliced_loops.append(sliced_loop)
                else:
                    sliced_loop.append(op)
        self.check_0_op_bytecodes()

    def check_0_op_bytecodes(self):
        for bytecodetrace in self.sliced_loops:
            if bytecodetrace.bytecode not in ZERO_OP_BYTECODES:
                continue
            assert not bytecodetrace

    def get_by_bytecode(self, name):
        return [ops for ops in self.sliced_loops if ops.bytecode == name]

    def test_f(self):
        self.run_source("""
            def main(n):
                for i in range(3):
                    return (n+5)+6
        """,
                   ([100], 111),
                    ([-5], 6),
                    ([sys.maxint], sys.maxint+11),
                    ([-sys.maxint-5], long(-sys.maxint+6)),
                    )

    def test_f1(self):
        self.run_source('''
            def main(n):
                "Arbitrary test function."
                i = 0
                x = 1
                while i<n:
                    j = 0   #ZERO
                    while j<=i:
                        j = j + 1
                        x = x + (i&j)
                    i = i + 1
                return x
        ''',
                   ([2117], 1083876708))

    def test_factorial(self):
        self.run_source('''
            def main(n):
                r = 1
                while n > 1:
                    r *= n
                    n -= 1
                return r
        ''',
                   ([5], 120),
                    ([20], 2432902008176640000L))

    def test_factorialrec(self):
        skip("does not make sense yet")        
        self.run_source('''
            def main(n):
                if n > 1:
                    return n * main(n-1)
                else:
                    return 1
        ''',
                   ([5], 120),
                    ([20], 2432902008176640000L))

    def test_richards(self):
        self.run_source('''
            import sys; sys.path[:] = %r
            from pypy.translator.goal import richards

            def main():
                return richards.main(iterations = 1)
        ''' % (sys.path,),
                   ([], 42))

    def test_simple_call(self):
        self.run_source('''
            def f(i):
                return i + 1
            def main(n):
                i = 0
                while i < n:
                    i = f(f(i))
                return i
        ''',
                   ([20], 20),
                    ([31], 32))
        ops = self.get_by_bytecode("LOAD_GLOBAL")
        assert len(ops) == 2
        assert ops[0].get_opnames() == ["getfield_gc", "getarrayitem_gc",
                                        "getfield_gc", "ooisnull",
                                        "guard_false"]
        assert not ops[1] # second LOAD_GLOBAL folded away
        ops = self.get_by_bytecode("CALL_FUNCTION")
        assert len(ops) == 2
        for bytecode in ops:
            assert not bytecode.get_opnames("call")
            assert not bytecode.get_opnames("new")
            assert len(bytecode.get_opnames("guard")) <= 10
       


class AppTestJIT(PyPyCJITTests):
    def setup_class(cls):
        if not option.runappdirect:
            py.test.skip("meant only for pypy-c")
        # the next line skips stuff if the pypy-c is not a jit build
        cls.space = gettestobjspace(usemodules=['pypyjit'])
        cls.tmpdir = udir.join('pypy-jit')
        cls.tmpdir.ensure(dir=1)
        cls.counter = 0
        cls.pypy_c = sys.executable

class TestJIT(PyPyCJITTests):
    def setup_class(cls):
        if option.pypy_c is None:
            py.test.skip("pass --pypy-c!")
        cls.tmpdir = udir.join('pypy-jit')
        cls.tmpdir.ensure(dir=1)
        cls.counter = 0
        cls.pypy_c = option.pypy_c
