#!/usr/bin/env python3
"""Reuse audited host-tool packages, leaving target selection to locked Conan IDs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def lock_digest(root):
    digest = hashlib.sha256()
    for name in ('conan.lock', 'graph.json', 'metadata.json', 'python.txt', 'apt.txt', 'build-profile'):
        digest.update(name.encode()+b'\0'+(root/name).read_bytes())
    return digest.hexdigest()


def main():
    archive = Path(sys.argv[1])
    record = json.loads((ROOT/'build-locks/shared-build-cache.json').read_text())
    if not record or record['locksSha256'] != lock_digest(ROOT/'build-locks'):
        print('No matching audited shared build cache; use an empty Conan cache')
        return
    subprocess.run(['python3', str(ROOT/'scripts/arm-dependency-release.py'), 'restore',
                    'aarch64', record['key'], str(archive)], check=True)
    if not archive.is_file():
        print('Audited shared build cache is absent; use an empty Conan cache')
        return
    spec = importlib.util.spec_from_file_location('image', ROOT/'scripts/dependency-image.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    layout = archive.parent/'seed-layout'
    module.unpack(archive, layout)
    if module.image_digest(layout) != record['imageDigest']:
        raise ValueError('Shared build cache digest differs')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write('context=oci-layout://'+str(layout.resolve())+'@'+record['imageDigest']+'\n')


if __name__ == '__main__':
    main()
