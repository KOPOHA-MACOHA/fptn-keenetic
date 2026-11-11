#!/usr/bin/env python3
"""Describe the actual built artifact and pinned original sources for publication."""
import hashlib,json,re,sys
from pathlib import Path

def generate(root, directory, revision):
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('Full build revision required')
    lock=json.loads((directory/'sources.lock.json').read_text())
    expected=json.loads((root/'sources.lock.json').read_text())
    upstream=json.loads((root/'sources.upstream.json').read_text())
    if lock!=expected or upstream['revision']!=lock['fptn']:raise ValueError('Artifact sources do not match the checked-out build')
    versions=re.findall(r'-DFPTN_VERSION=([0-9]+\.[0-9]+\.[0-9]+)',(root/'Dockerfile').read_text())
    if len(versions)!=1:raise ValueError('Missing or ambiguous client build version')
    original=upstream['release'].removeprefix('v')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',original):raise ValueError('Invalid original release version')
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    checksums={}
    for line in (directory/'SHA256SUMS').read_text().splitlines():
        value,separator,name=line.partition('  ')
        if name in ('fptn-client-cli','sources.lock.json'):
            if name in checksums or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Invalid or duplicated artifact checksum')
            checksums[name]=value
    for name in ('fptn-client-cli','sources.lock.json'):
        if checksums.get(name)!=digest(directory/name):raise ValueError('Artifact checksum mismatch: '+name)
    result={'schema':1,'platform':'mipsel-mips32r2-softfloat','buildRevision':revision,
            'sha256':digest(directory/'fptn-client-cli'),'clientVersion':versions[0],
            'upstreamRevision':lock['fptn'],'upstreamVersion':original,
            'sourceLockSha256':digest(directory/'sources.lock.json')}
    target=directory/'client-release.json';target.write_text(json.dumps(result,indent=2)+'\n')
    sums=directory/'SHA256SUMS';lines=[line for line in sums.read_text().splitlines() if not line.endswith('  client-release.json')]
    sums.write_text('\n'.join([*lines,digest(target)+'  client-release.json'])+'\n')
    return result

if __name__=='__main__':
    if len(sys.argv)!=4:sys.exit('usage: package-client-release.py ROOT ARTIFACT_DIR BUILD_SHA')
    print(json.dumps(generate(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3])))
