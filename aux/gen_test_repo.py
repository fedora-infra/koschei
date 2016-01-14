#!/usr/bin/python
import sys
import shutil
import os
from subprocess import check_call
from textwrap import dedent

pkgs = [
    dict(name='A', epoch=None, version='1', release='1.fc22', arch=None,
         requires=['B']),
    dict(name='B', epoch=None, version='4.1', release='2.fc22', arch='noarch',
         requires=['C']),
    dict(name='C', epoch=1, version='3', release='1.fc22', arch=None),
    dict(name='D', epoch=2, version='8.b', release='1.rc1.fc22', arch=None,
         requires=['F']),
    dict(name='E', epoch=0, version='0.1', release='1.fc22.1', arch='noarch',
         requires=['D']),
    dict(name='F', epoch=None, version='1', release='1.fc22', arch='noarch',
         requires=['B', 'C', 'E']),
    dict(name='R', epoch=None, version='3.3', release='2.fc22', arch=None,
         provides=['virtual']),
    dict(name='foo', epoch=None, version='4', release='1.fc22', arch='src',
         requires=['A', 'F']),
    dict(name='bar', epoch=1, version='2', release='2', arch='src',
         requires=['nonexistent']),
]

def generate():
    for p in pkgs:
        spec = dedent("""\
                Name:{p[name]}
                Version:{p[version]}
                Release:{p[release]}
                License:GPL
                Summary: bla bla bla
                URL: www.example.com
                """).format(p=p)

        if p['arch'] == 'noarch':
            spec += 'BuildArch:noarch\n'
        if p.get('epoch') is not None:
            spec += 'Epoch:{}\n'.format(p['epoch'])

        for dep in p.get('requires', ()):
            if p['arch'] == 'src':
                spec += 'BuildRequires:{}\n'.format(dep)
            else:
                spec += 'Requires:{}\n'.format(dep)

        for dep in p.get('provides', ()):
            spec += 'Provides:{}\n'.format(dep)

        spec += dedent("""\
                %description
                asdf

                %prep
                echo bla> bla
                %build
                :
                %install
                :
                %files
                %doc bla
                """)
        with open('tmp_rpmbuild/{}.spec'.format(p['name']), 'w') as specfile:
            specfile.write(spec)

        check_call('rpmbuild -b{} tmp_rpmbuild/{}.spec -D"_rpmdir tmp_rpmbuild" -D"_srcrpmdir tmp_rpmbuild"'\
                     .format('bs'[p['arch'] == 'src'], p['name']), shell=True)
if __name__ == '__main__':
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = os.path.join(os.path.dirname(sys.argv[0]), '..', 'test', 'test_repo')
        print(target)
    check_call('mkdir -p {} tmp_rpmbuild'.format(target), shell=True)
    if os.path.exists(target):
        shutil.rmtree(target)
    os.mkdir(target)
    generate()
    check_call(dedent("""\
            mkdir {0}/{{x86_64,src}}
            mv tmp_rpmbuild/noarch/*.rpm {0}/x86_64/
            mv tmp_rpmbuild/x86_64/*.rpm {0}/x86_64/
            mv tmp_rpmbuild/*.src.rpm {0}/src/
            createrepo_c {0}/x86_64
            createrepo_c {0}/src
            find {0} -name '*.rpm' -delete
            rm -r tmp_rpmbuild
            """).format(target), shell=True)
