#!/usr/bin/env python3
"""Build deterministic Entware packages from an already verified runtime, no compiler."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

PLATFORMS={'mipsel-mips32r2-softfloat':('mipsel-3.4','mipselsf-k3.4'),
           'aarch64':('aarch64-3.10','aarch64-k3.10'),
           'armv7-softfloat':('armv7-3.2','armv7sf-k3.2')}
PACKAGE='fptn-keenetic'


def sha(data): return hashlib.sha256(data).hexdigest()


def tar_bytes(files,sort=True):
    stream=io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as zipped:
        with tarfile.open(fileobj=zipped,mode='w') as archive:
            for name,(data,mode) in (sorted(files.items()) if sort else files.items()):
                member=tarfile.TarInfo(name); member.mode=mode;member.size=len(data)
                archive.addfile(member,io.BytesIO(data))
    return stream.getvalue()


def inspect_runtime(path,revision,platform):
    with tarfile.open(path,'r:gz') as archive:
        files={}
        for member in archive.getmembers():
            p=PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0]!='fptn-keenetic' or not (member.isdir() or member.isfile()):
                raise ValueError('Unsafe runtime archive member')
            if member.isfile():
                if member.name in files: raise ValueError('Duplicate runtime file')
                files[member.name]=archive.extractfile(member).read()
    prefix='fptn-keenetic/'
    if files[prefix+'release-revision'].decode().strip()!=revision:raise ValueError('Wrong runtime revision')
    rows={}
    for line in files[prefix+'SHA256SUMS'].decode().splitlines():
        match=re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9_./-]+)',line)
        if not match: raise ValueError('Bad runtime checksums')
        expected,name=match.groups()
        if name in rows or '..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute():raise ValueError('Unsafe runtime checksum path')
        if sha(files[prefix+name])!=expected:raise ValueError('Corrupt runtime payload')
        rows[name]=expected
    if {p[len(prefix):] for p in files} != set(rows)|{'SHA256SUMS'}:raise ValueError('Unlisted runtime files')
    record=json.loads(files[prefix+'client-release.json'])
    if platform!=record['platform']:
        record=json.loads(files[prefix+'platform-releases.json'])['platforms'][platform]['client']
    if record['platform']!=platform or sha(files[prefix+'client/fptn-client-cli'])!=record['sha256']:raise ValueError('Wrong runtime client')
    html=files[prefix+'web/index.html'].decode()
    match=re.search(r'data-ui-version="(\d+\.\d+\.\d+)"',html)
    if not match:raise ValueError('Missing panel version')
    return match[1],record


def control_script(action,platform):
    check='''ROOT=${FPTN_PREFIX:-/opt}
# opkg --offline-root stages files only; deployment runs on the target explicitly.
[ -z "${IPKG_INSTROOT:-}" ] || exit 0
'''
    if action=='preinst':
        body='''[ ! -e "$ROOT/share/fptn-package" ] || [ -f "$ROOT/share/fptn-package/.owned" ] || exit 1
[ ! -e "$ROOT/bin/fptn-package" ] || grep -Fq '# opkg owns the immutable payload' "$ROOT/bin/fptn-package" || exit 1
if [ -e "$ROOT/bin/fptnctl" ]; then
  [ -f "$ROOT/libexec/fptn/.runtime-managed" ] || grep -Fq '# FPTN transport only.' "$ROOT/bin/fptnctl" || exit 1
fi
command -v ndmc >/dev/null 2>&1 || exit 1
[ -c /dev/net/tun ] || exit 1
'''
    elif action=='postinst':body='exec "$ROOT/bin/fptn-package" deploy\n'
    elif action=='prerm':body='case "${1:-remove}" in remove|deconfigure) exec "$ROOT/bin/fptn-package" remove ;; upgrade) exit 0 ;; *) exit 1 ;; esac\n'
    else:body='case "${1:-}" in remove) rm -f "$ROOT/etc/fptn/opkg-managed.json" ;; esac\n'
    return ('#!/bin/sh\nset -eu\n'+check+body).encode()


def build(root,runtime,out,platform,revision,serial):
    if platform not in PLATFORMS or not re.fullmatch('[0-9a-f]{40}',revision) or type(serial)!=int or serial<1:
        raise ValueError('Invalid package identity')
    version,client=inspect_runtime(runtime,revision,platform)
    arch,feed=PLATFORMS[platform];version+='-'+str(serial)
    meta={'schema':1,'package':PACKAGE,'version':version,'platform':platform,'architecture':arch,
          'revision':revision,'clientVersion':client['clientVersion'],'archiveSha256':sha(runtime.read_bytes())}
    data={'opt/bin/fptn-package':((root/'packaging/fptn-package.sh').read_bytes(),0o700),
          'opt/share/fptn-package/.owned':(b'Managed by fptn-keenetic opkg package\n',0o600),
          'opt/share/fptn-package/runtime.tar.gz':(runtime.read_bytes(),0o600),
          'opt/share/fptn-package/package.json':((json.dumps(meta,indent=2)+'\n').encode(),0o600),
          'opt/share/fptn-package/platform.sh':((root/'scripts/fptn-platform.sh').read_bytes(),0o700)}
    control=(f'Package: {PACKAGE}\nVersion: {version}\nArchitecture: {arch}\n'
             'Maintainer: FPTN Keenetic contributors\nSection: net\nPriority: optional\nLicense: See included upstream license files\n'
             'Depends: curl, jq, coreutils-timeout, ip-full, lighttpd, lighttpd-mod-cgi, lighttpd-mod-setenv, php8-cgi\n'
             f'Installed-Size: {sum(len(d) for d,_ in data.values())}\n'
             'Description: FPTN client and web panel for Keenetic Entware\n'
             ' Transactional runtime deployment; settings and shared services are preserved.\n')
    controls={'control':(control.encode(),0o644),**{name:(control_script(name,platform),0o755) for name in ['preinst','postinst','prerm','postrm']}}
    outer=tar_bytes({'debian-binary':(b'2.0\n',0o644),'control.tar.gz':(tar_bytes(controls),0o644),'data.tar.gz':(tar_bytes(data),0o644)},sort=False)
    out.mkdir(parents=True,exist_ok=True)
    path=out/f'{PACKAGE}_{version}_{arch}.ipk';path.write_bytes(outer)
    index=control+f'Filename: {path.name}\nSize: {len(outer)}\nSHA256sum: {sha(outer)}\n\n'
    (out/'Packages').write_text(index)
    (out/'Packages.gz').write_bytes(gzip.compress(index.encode(),mtime=0))
    (out/'package.json').write_text(json.dumps({**meta,'filename':path.name,'sha256':sha(outer),'feed':feed},indent=2)+'\n')
    return path


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path);p.add_argument('runtime',type=Path);p.add_argument('output',type=Path)
    p.add_argument('platform',choices=PLATFORMS);p.add_argument('revision');p.add_argument('serial',type=int)
    a=p.parse_args();print(build(a.root,a.runtime,a.output,a.platform,a.revision,a.serial))
