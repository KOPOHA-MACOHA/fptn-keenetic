#!/usr/bin/env python3
"""Persistent, input-keyed ARM OCI snapshots; only used on GitHub Actions runners."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

MAX_SIZE = 2 * 1024**3 - 1  # GitHub's per-release-asset limit; never change billing.


def gh(*args, check=True):
    r = subprocess.run(['gh', *args], text=True, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or 'GitHub request failed')
    return r


def lookup(repo, tag):
    r = gh('api', '--include', f'repos/{repo}/releases/tags/{tag}', check=False)
    match = re.match(r'HTTP/\S+ (\d{3})\b', r.stdout)
    if not match:
        raise RuntimeError('Cannot determine GitHub HTTP status; refusing a dependency rebuild')
    status = int(match[1])
    if status == 404:
        return None
    if status != 200 or r.returncode:
        raise RuntimeError(f'GitHub returned HTTP {status}; refusing a dependency rebuild')
    body = re.split(r'\r?\n\r?\n', r.stdout, maxsplit=1)
    release = json.loads(body[1])
    if release.get('draft'):
        raise RuntimeError('Dependency release is still a draft; do not rebuild or overwrite it')
    names = {a['name'] for a in release['assets']}
    if not {'dependencies.tar', 'metadata.json'} <= names:
        raise RuntimeError('Incomplete dependency release; do not rebuild or overwrite it')
    return release


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def metadata(path, platform, key):
    if path.stat().st_size > 4096:
        raise ValueError('Oversized snapshot metadata')
    m = json.loads(path.read_text())
    if (m.get('schema') != 1 or m.get('platform') != platform or m.get('key') != key or
            not isinstance(m.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', m['sha256']) or
            type(m.get('size')) is not int or not 0 < m['size'] <= MAX_SIZE):
        raise ValueError('Wrong snapshot metadata or dependency inputs')
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['restore', 'publish'])
    p.add_argument('platform', choices=['aarch64', 'armv7-softfloat'])
    p.add_argument('key')
    p.add_argument('archive', type=Path)
    a = p.parse_args()
    if not re.fullmatch('[0-9a-f]{64}', a.key):
        p.error('Expected the full dependency input key')
    if a.action == 'publish' and a.archive.name != 'dependencies.tar':
        p.error('The published archive must be named dependencies.tar')
    repo = os.environ.get('GH_REPO', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        p.error('GH_REPO must identify this workflow repository')
    tag = f'deps-{a.platform}-{a.key}'
    release = lookup(repo, tag)
    a.archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dependency-release-', dir=a.archive.parent) as tmp:
        stage = Path(tmp)
        if a.action == 'restore':
            if release is None:
                print(f'No persistent snapshot for {a.platform} / {a.key}')
                result = 'false'
            else:
                gh('release', 'download', tag, '--repo', repo, '--pattern', 'metadata.json', '--dir', str(stage))
                m = metadata(stage/'metadata.json', a.platform, a.key)
                gh('release', 'download', tag, '--repo', repo, '--pattern', 'dependencies.tar', '--dir', str(stage))
                archive = stage/'dependencies.tar'
                if archive.stat().st_size != m['size'] or digest(archive) != m['sha256']:
                    raise ValueError('Corrupt dependency archive; refusing a dependency rebuild')
                # OCI members and every blob are verified separately by dependency-image.py.
                archive.replace(a.archive)
                result = 'true'
                print(f'Restored verified persistent snapshot: {tag}')
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                    output.write('restored='+result+'\n')
        elif release is not None:
            print(f'Persistent snapshot already published; keeping it immutable: {tag}')
        else:
            size = a.archive.stat().st_size
            if not 0 < size <= MAX_SIZE:
                raise ValueError('Dependency archive exceeds the GitHub asset limit')
            revision = os.environ.get('GITHUB_SHA', '')
            if not re.fullmatch('[0-9a-f]{40}', revision):
                p.error('GITHUB_SHA must identify the source commit')
            # The caller must first run the existing OCI integrity checker.
            m = {'schema': 1, 'platform': a.platform, 'key': a.key,
                 'sha256': digest(a.archive), 'size': size}
            (stage/'metadata.json').write_text(json.dumps(m, indent=2)+'\n')
            gh('release', 'create', tag, str(a.archive)+'#dependencies.tar',
               str(stage/'metadata.json')+'#metadata.json', '--repo', repo,
               '--target', revision, '--prerelease', '--latest=false',
               '--title', f'ARM build dependencies: {a.platform} ({a.key[:12]})',
               '--notes', 'Build-time OCI snapshot, keyed by SDK, ABI and dependency inputs. '
               'Not a router installation package. Retained independently of the Actions cache.')
            print(f'Published immutable dependency snapshot: {tag}')


if __name__ == '__main__':
    main()
