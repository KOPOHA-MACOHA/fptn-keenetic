#!/usr/bin/env python3
"""Content key and integrity checks for the reusable dependency OCI image."""
import hashlib
import json
import pathlib
import re
import sys
import tarfile

MARKER = '# END DEPENDENCY IMAGE INPUTS'
INPUTS = ('sources.lock.json', 'profiles/mipsel-softfloat', 'scripts/prepare-source.py',
          'scripts/fix-static-libgcc.py', 'scripts/verify-elf.py')


def cache_key(root):
    dockerfile = (root / 'Dockerfile').read_text()
    if dockerfile.count(MARKER) != 1:
        raise ValueError('Missing/duplicate dependency image boundary')
    prefix = dockerfile.split(MARKER)[0].encode()
    digest = hashlib.sha256(b'fptn-dependency-image-v1\0')
    for name, data in [('Dockerfile.dependencies', prefix), *[(name, (root/name).read_bytes()) for name in INPUTS]]:
        digest.update(name.encode() + b'\0' + len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


def unpack(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        for member in members:
            name = member.name.removeprefix('./').rstrip('/')
            allowed = name in ('', '.', 'oci-layout', 'index.json', 'blobs', 'blobs/sha256') or re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', name)
            if not allowed or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unexpected OCI archive member: {member.name}')
        source.extractall(destination, members=members, filter='data')


def image_digest(layout):
    if json.loads((layout/'oci-layout').read_text()).get('imageLayoutVersion') != '1.0.0':
        raise ValueError('Unsupported OCI layout')
    def blob(descriptor):
        digest = descriptor['digest']
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('Unsupported digest')
        path = layout/'blobs/sha256'/digest.split(':')[1]
        if path.stat().st_size != descriptor['size']:
            raise ValueError(f'Wrong blob size: {digest}')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest.split(':')[1]:
            raise ValueError(f'Corrupt blob: {digest}')
        return path
    def select(descriptor, depth=0):
        if depth > 4:
            raise ValueError('OCI index nesting too deep')
        document = json.loads(blob(descriptor).read_text())
        if 'manifests' in document:
            for child in document['manifests']:
                found = select(child, depth+1)
                if found: return found
        elif 'config' in document and 'layers' in document:
            config = json.loads(blob(document['config']).read_text())
            if config.get('os') == 'linux' and config.get('architecture') == 'amd64':
                for layer in document['layers']: blob(layer)
                return descriptor['digest']
        return None
    index = json.loads((layout/'index.json').read_text())
    for descriptor in index['manifests']:
        found = select(descriptor)
        if found: return found
    raise ValueError('No linux/amd64 dependency image')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'key':
        print(cache_key(pathlib.Path(sys.argv[2])))
    elif len(sys.argv) == 4 and sys.argv[1] == 'unpack':
        destination = pathlib.Path(sys.argv[3]).resolve()
        unpack(pathlib.Path(sys.argv[2]), destination)
        print(f'oci-layout://{destination}@{image_digest(destination)}')
    else:
        sys.exit('usage: dependency-image.py key ROOT | unpack ARCHIVE DIRECTORY')
