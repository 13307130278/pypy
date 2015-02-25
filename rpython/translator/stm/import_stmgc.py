#!/usr/bin/env python
""" A simple tool for importing the current version of the external stmgc
repository into pypy; should sync whatever version you provide.  Usage:

import_stmgc.py <path-to-stmgc-working-copy>

The working copy comes from:  hg clone https://bitbucket.org/pypy/stmgc
"""

import sys, py, subprocess, os

def mangle(lines):
    yield "/* Imported by rpython/translator/stm/import_stmgc.py */\n"
    kill_first_empty_line = True
    for line in lines:
        if kill_first_empty_line and line.strip() == '':
            kill_first_empty_line = False
            continue
        yield line

def main(stmgc_dir):
    stmgc_dir = py.path.local(stmgc_dir).join('c8')
    popen = subprocess.Popen(['hg', 'id', '-i'], cwd=str(stmgc_dir),
                             stdout=subprocess.PIPE)
    rev = popen.stdout.read().strip()
    popen.wait()
    #
    stmgc_dest = py.path.local(__file__).join('..', 'src_stm')
    plist = stmgc_dir.visit(rec=lambda p: p.basename == 'stm')
    for p in sorted(plist):
        if not (p.basename.endswith('.c') or p.basename.endswith('.h')):
            continue
        if p.basename.startswith('.'):
            continue        
        if p.basename.startswith('demo'):
            continue
        path = stmgc_dest.join(p.relto(stmgc_dir))
        print path
        path.join('..').ensure(dir=1)
        if path.check():
            path.remove()
        path.write(''.join(mangle(p.readlines())))
        path.chmod(0444)
    #
    stmgc_dest.join('revision').write('%s\n' % rev)
    print rev
    #
    g = os.popen("bash -c 'diff <(cd '%s' && hg status -macn stm/ | sort)"
                 "              <(cd '%s' && hg status -macn stm/ | sort)'"
        % (stmgc_dest, stmgc_dir), 'r')
    diff = g.read()
    g.close()
    if diff:
        print
        print 'WARNING: The differences between which files are tracked are:'
        print diff
        print 'Use "hg add" or "hg remove".'

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print __doc__
        sys.exit(2)
    main(sys.argv[1])
