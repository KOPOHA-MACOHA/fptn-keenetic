#!/usr/bin/env python3
"""Merge an upstream revision in isolation; never execute candidate build code."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

OWNED = ('README.md', '.gitignore', '.dockerignore', '.github/workflows')
UPSTREAM_URL = 'https://github.com/fptn-project/fptn.git'
spec = importlib.util.spec_from_file_location('dependency_reuse', Path(__file__).with_name('dependency-reuse.py'))
reuse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reuse)


def git(root, *args, check=True):
    env = {**os.environ, 'GIT_TERMINAL_PROMPT':'0', 'GIT_LFS_SKIP_SMUDGE':'1',
           'GIT_CONFIG_NOSYSTEM':'1'}
    result = subprocess.run(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null',
                             '-c', 'core.autocrlf=false', *args], env=env,
                            capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError('Git operation failed: ' + args[0] + '\n' + result.stderr.decode(errors='replace')[:2000])
    return result


def json_text(value):
    return json.dumps(value, indent=2)+'\n'


def merge_entries(root, main, candidate, tag, upstream_url=UPSTREAM_URL):
    """Produce API tree entries from a real two-parent merge, without moving main."""
    root = Path(root).resolve()
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout:
        raise ValueError('Checkout has local changes; refusing to prepare an update')
    git(root, 'fetch', '--no-tags', upstream_url, candidate)
    git(root, 'merge-base', '--is-ancestor', candidate, candidate)
    before = json.loads(git(root, 'show', main+':sources.lock.json').stdout)
    git(root, 'merge-base', '--is-ancestor', before['fptn'], candidate)
    with tempfile.TemporaryDirectory(prefix='fptn-upstream-') as tmp:
        work = Path(tmp)/'merge'
        git(root, 'worktree', 'add', '--detach', str(work), main)
        try:
            base_contract = reuse.contract(work)
            baseline = reuse.cached_sources(work).decode()
            git(work, '-c', 'user.name=FPTN upstream checker', '-c',
                'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                'merge', '--no-commit', '--no-ff', candidate, check=False)
            # Original workflows must not activate self-hosted jobs or use secrets
            # in this fork. Keep our active workflows, archive the upstream files.
            listing = git(work, 'ls-tree', '-r', '-z', candidate, '--', '.github/workflows').stdout
            for row in listing.split(b'\0'):
                if not row:
                    continue
                meta, name = row.split(b'\t', 1)
                mode, kind, sha = meta.decode().split()
                if mode != '100644' or kind != 'blob':
                    raise ValueError('Unexpected upstream workflow file mode')
                relative = name.decode().removeprefix('.github/workflows/')
                target = work/'.github/upstream-workflows'/(relative+'.disabled')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git(work, 'cat-file', 'blob', sha).stdout)
            for name in OWNED:
                git(work, 'rm', '-rf', '--ignore-unmatch', '--', name)
                if git(work, 'cat-file', '-e', main+':'+name, check=False).returncode == 0:
                    git(work, 'checkout', main, '--', name)
            unresolved = git(work, 'diff', '--name-only', '--diff-filter=U').stdout.decode().splitlines()
            if unresolved:
                raise ValueError('Upstream merge needs review; main was not changed: ' + ', '.join(unresolved[:30]))
            if git(work, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode:
                raise ValueError('Upstream merge did not produce a merge state')
            changed_dependencies = base_contract != reuse.contract(work)
            updated = {**before, 'fptn':candidate}
            (work/'sources.lock.json').write_text(json_text(updated))
            plan_path = work/'build-locks/source-reuse.json'
            if changed_dependencies:
                plan_path.unlink(missing_ok=True)
            else:
                plan_path.write_text(json_text({'schema':1,
                    'sourcesSha256':reuse.digest((work/'sources.lock.json').read_bytes()),
                    'dependencySources':baseline, 'dependencyContract':base_contract}))
                reuse.cached_sources(work)  # Verify before any GitHub write.
            metadata = {'schema':1, 'repository':'fptn-project/fptn', 'release':tag,
                        'revision':candidate, 'buildReady':not changed_dependencies,
                        'dependencyReview':'unchanged' if not changed_dependencies else 'refresh-required'}
            (work/'sources.upstream.json').write_text(json_text(metadata))
            git(work, 'add', '-A')
            if git(work, 'diff', '--cached', '--check', check=False).returncode:
                raise ValueError('Merged sources contain whitespace errors or conflict markers; review required')
            index = {}
            for row in git(work, 'ls-files', '--stage', '-z').stdout.split(b'\0'):
                if row:
                    meta, name = row.split(b'\t', 1)
                    mode, sha, stage = meta.decode().split()
                    if stage != '0':
                        raise ValueError('Unresolved merge stage')
                    index[name] = (mode, sha)
            entries = []
            for name in git(work, 'diff', '--cached', '--name-only', '-z', main).stdout.split(b'\0'):
                if not name:
                    continue
                if name not in index:
                    entries.append({'path':name.decode(), 'mode':'100644', 'type':'blob', 'sha':None})
                else:
                    mode, sha = index[name]
                    if mode not in ('100644', '100755', '120000'):
                        raise ValueError('Unsupported merged file mode')
                    entries.append({'path':name.decode(), 'mode':mode, 'type':'blob',
                                    'data':base64.b64encode(git(work, 'cat-file', 'blob', sha).stdout).decode()})
            return entries, not changed_dependencies
        finally:
            git(root, 'worktree', 'remove', '--force', str(work), check=False)
