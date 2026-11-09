#!/usr/bin/env python3
"""Fail closed on source, recipe graph or build-tool drift. Build-time only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

LOCK_FILES = ('conan.lock', 'graph.json', 'metadata.json', 'python.txt', 'apt.txt', 'build-profile')


def graph_contract(document):
    nodes = document['graph']['nodes']
    def identity(node):
        return node['context'] + ':' + node['ref'].split('%')[0]
    rows = set()
    for node in nodes.values():
        if not node.get('ref') or node.get('recipe') == 'Consumer':
            continue
        ref = node['ref'].split('%')[0]
        if not re.fullmatch(r'[^#\s]+#[0-9a-f]{32}', ref):
            raise ValueError('Recipe without an exact revision: ' + ref)
        dependencies = sorted({identity(nodes[str(key)]) for key in node.get('dependencies', {})})
        rows.add(json.dumps([identity(node), dependencies], separators=(',', ':')))
    if not rows:
        raise ValueError('Empty dependency graph')
    return [json.loads(row) for row in sorted(rows)]


def read_lock(root):
    root = Path(root)
    meta = json.loads((root/'metadata.json').read_text())
    lock = json.loads((root/'conan.lock').read_text())
    if meta.get('schema') != 1 or lock.get('version') != '0.5':
        raise ValueError('Unsupported build lock schema')
    for key in ('requires', 'build_requires', 'python_requires', 'config_requires'):
        refs = lock[key]
        if len(refs) != len(set(refs)) or any(not re.fullmatch(r'[^#%\s]+#[0-9a-f]{32}', ref) for ref in refs):
            raise ValueError('Unpinned or duplicate Conan references: ' + key)
    refs = set(lock['requires'] + lock['build_requires'] + lock['python_requires'] + lock['config_requires'])
    contract = json.loads((root/'graph.json').read_text())
    if ({row[0].split(':', 1)[1] for row in contract} != refs or
            {row[0].split(':', 1)[1] for row in contract if row[0].startswith('host:')} != set(lock['requires']) or
            {row[0].split(':', 1)[1] for row in contract if row[0].startswith('build:')} != set(lock['build_requires'])):
        raise ValueError('Lockfile and complete graph disagree')
    return meta, contract


def verify_sources(root, sources):
    meta, _ = read_lock(root)
    if hashlib.sha256(Path(sources).read_bytes()).hexdigest() != meta['sourcesSha256']:
        raise ValueError('Pinned upstream sources changed: refresh dependency locks explicitly before building')


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def verify_tools(root):
    root = Path(root)
    meta, _ = read_lock(root)
    expected = meta['tools']
    actual = {'pip': run('python', '-m', 'pip', 'freeze', '--all').splitlines(),
              'conan': run('conan', '--version'),
              'cmake': run('cmake', '--version').splitlines()[0],
              'ninja': run('ninja', '--version')}
    for key in actual:
        if (sorted(actual[key]) if key == 'pip' else actual[key]) != (sorted(expected[key]) if key == 'pip' else expected[key]):
            raise ValueError('Build tool differs from lock: ' + key)
    if run('dpkg-query', '-W', '-f=${Package}=${Version}\n').splitlines() != (root/'apt.txt').read_text().splitlines():
        raise ValueError('Ubuntu build packages differ from lock')


def verify_graph(root, source):
    root, source = Path(root).resolve(), Path(source).resolve()
    _, expected = read_lock(root)
    with tempfile.TemporaryFile(mode='w+') as out:
        subprocess.run(['conan', 'graph', 'info', str(source), '--format=json', '--no-remote',
                        '--lockfile='+str(root/'conan.lock'), '--profile:build='+str(root/'build-profile'),
                        '--profile:host=/work/profile', '-s:b', 'compiler.cppstd=20',
                        '-o', 'with_gui_client=False', '-o', 'openwrt=True'], stdout=out, check=True)
        out.seek(0)
        actual = graph_contract(json.load(out))
    if actual != expected:
        raise ValueError('Resolved dependency graph differs from lock')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['sources', 'tools', 'graph', 'check'])
    parser.add_argument('lock_dir', type=Path)
    parser.add_argument('--sources', type=Path, default=Path('/work/sources.lock.json'))
    parser.add_argument('--upstream', type=Path, default=Path('/work/upstream'))
    args = parser.parse_args()
    if args.action in ('sources', 'check'):
        verify_sources(args.lock_dir, args.sources)
    if args.action in ('tools', 'check'):
        verify_tools(args.lock_dir)
    if args.action in ('graph', 'check'):
        verify_graph(args.lock_dir, args.upstream)
    print('Build locks verified: ' + args.action)


if __name__ == '__main__':
    main()
