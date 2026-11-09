#!/usr/bin/env python3
"""One-time exact-key imports of already audited ARM snapshots, never fuzzy cache hits."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def select(platform, key):
    if platform not in ('aarch64', 'armv7-softfloat') or not re.fullmatch('[0-9a-f]{64}', key):
        return None
    records = json.loads((ROOT/'build-locks/cache-imports.json').read_text())
    record = records.get(platform, {}).get(key)
    if record and (not re.fullmatch('[0-9a-f]{64}', record['oldKey']) or
                   not re.fullmatch('sha256:[0-9a-f]{64}', record['imageDigest'])):
        raise ValueError('Invalid audited snapshot import')
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('platform'); p.add_argument('key'); p.add_argument('archive', type=Path)
    a = p.parse_args()
    record = select(a.platform, a.key)
    imported = False
    if record:
        subprocess.run(['python3', str(ROOT/'scripts/arm-dependency-release.py'), 'restore',
                        a.platform, record['oldKey'], str(a.archive)], check=True)
        if a.archive.exists():
            spec = importlib.util.spec_from_file_location('image', ROOT/'scripts/dependency-image.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            layout = a.archive.parent/'import-layout'
            module.unpack(a.archive, layout)
            if module.image_digest(layout) != record['imageDigest']:
                raise ValueError('Audited dependency snapshot digest differs; refusing reuse')
            imported = True
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
            out.write('restored='+str(imported).lower()+'\n')
    print('Audited dependency snapshot import: '+str(imported).lower())


if __name__ == '__main__':
    main()
