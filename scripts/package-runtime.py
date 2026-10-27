#!/usr/bin/env python3
"""Bundle verified existing binaries and the exact runtime revision; no compiler."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, client, decoder, output, revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Full runtime revision required')
    release = json.loads((root/'client-release.json').read_text())
    assets = json.loads((root/'runtime-assets.json').read_text())
    if release['platform'] != assets['platform']:
        raise ValueError('Binary and decoder platforms differ')
    inputs = {
        'fptn-client-cli': (client, release['sha256']),
        'sources.lock.json': (client, release['sourceLockSha256']),
        'LICENSE-FPTN': (client, assets['clientLicenseSha256']),
        'brotli': (decoder, assets['decoderSha256']),
        'LICENSE-BROTLI': (decoder, assets['decoderLicenseSha256']),
        'BROTLI-SOURCE.txt': (decoder, assets['decoderSourceSha256']),
    }
    for name, (directory, expected) in inputs.items():
        if digest(directory/name) != expected:
            raise ValueError('Wrong binary/source/license: '+name)
    if json.loads((client/'sources.lock.json').read_text())['fptn'] != release['upstreamRevision']:
        raise ValueError('Wrong original source revision')
    directory = output/'fptn-keenetic'
    directory.mkdir(parents=True)  # Reject an existing stage instead of mixing releases.
    for line in (root/'web/SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or digest(root/path) != expected:
            raise ValueError('Invalid runtime manifest: '+name)
        target = directory/path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/path, target)
    for name in ['web/SHA256SUMS', 'scripts/install-web-ui.sh', 'install.sh']:
        target = directory/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root/name, target)
    (directory/'release-revision').write_text(revision+'\n')
    (directory/'client').mkdir()
    for name, (source, _) in inputs.items():
        shutil.copyfile(source/name, directory/'client'/name)
    paths = sorted(p for p in directory.rglob('*') if p.is_file())
    (directory/'SHA256SUMS').write_text(''.join(digest(p)+'  '+p.relative_to(directory).as_posix()+'\n' for p in paths))
    archive = output/('fptn-keenetic-'+release['platform']+'.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(directory, arcname='fptn-keenetic')
    (output/'SHA256SUMS').write_text(digest(archive)+'  '+archive.name+'\n')
    return archive


if __name__ == '__main__':
    print(build(*(Path(p) for p in sys.argv[1:5]), sys.argv[5]))
