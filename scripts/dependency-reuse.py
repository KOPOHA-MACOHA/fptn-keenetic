#!/usr/bin/env python3
"""Audit source-only upstream updates against an unchanged dependency SDK."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SOURCE_PATHS = ('CMakeLists.txt', 'conanfile.py', 'LICENSE', '.conan', 'depends', 'src', 'deploy', 'tests')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dependency_path(name):
    return (name == 'conanfile.py' or name.startswith(('.conan/', 'depends/')) or
            (name.split('/')[0] in SOURCE_PATHS and
             (Path(name).name == 'CMakeLists.txt' or name.endswith('.cmake'))))


def tracked_dependencies(root):
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'], text=True).split('\0')
    return sorted(n for n in names if dependency_path(n))


def contract(root, names=None):
    root = Path(root)
    names = tracked_dependencies(root) if names is None else sorted(names)
    result = {}
    for name in names:
        if not dependency_path(name) or '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError('Invalid dependency contract path')
        path = root/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Dependency contract requires regular files: ' + name)
        result[name] = digest(path.read_bytes())
    if not {'CMakeLists.txt', 'conanfile.py'} <= result.keys():
        raise ValueError('Incomplete dependency contract')
    return result


def cached_sources(root):
    """Return the original exact lock bytes only for a validated source-only plan."""
    root = Path(root)
    current = (root/'sources.lock.json').read_bytes()
    path = root/'build-locks/source-reuse.json'
    if not path.exists():
        return current
    plan = json.loads(path.read_text())
    baseline = plan['dependencySources'].encode()
    old, new = json.loads(baseline), json.loads(current)
    if (plan.get('schema') != 1 or plan['sourcesSha256'] != digest(current) or
            digest(baseline) != json.loads((root/'build-locks/metadata.json').read_text())['sourcesSha256'] or
            {k:v for k,v in old.items() if k != 'fptn'} != {k:v for k,v in new.items() if k != 'fptn'} or
            plan['dependencyContract'] != contract(root)):
        raise ValueError('Source-only SDK reuse no longer matches sources or dependency recipes; refresh locks explicitly')
    return baseline


def audit(work):
    """Run inside the restored image before discarding its original dependency source."""
    work = Path(work)
    old = (work/'sources.lock.json').read_bytes()
    new = (work/'candidate.sources.lock.json').read_bytes()
    locks, candidate = work/'build-locks', work/'candidate-build-locks'
    # The candidate cannot silently change any recipe, graph, tool or SDK lock.
    lock_names = ('conan.lock', 'graph.json', 'metadata.json', 'python.txt', 'apt.txt',
                  'build-profile', 'ubuntu.sources', 'shared-build-cache.json')
    old_files = {name:(locks/name).read_bytes() for name in lock_names}
    new_files = {name:(candidate/name).read_bytes() for name in lock_names}
    if old_files != new_files or json.loads(old_files['metadata.json'])['sourcesSha256'] != digest(old):
        raise ValueError('Prepared SDK and candidate dependency locks differ')
    if (work/'profile').read_bytes() != (work/'candidate.profile').read_bytes():
        raise ValueError('Prepared SDK ABI profile differs')
    if old != new:
        plan = json.loads((candidate/'source-reuse.json').read_text())
        if (plan.get('schema') != 1 or plan['dependencySources'].encode() != old or
                plan['sourcesSha256'] != digest(new) or
                {k:v for k,v in json.loads(old).items() if k != 'fptn'} !=
                {k:v for k,v in json.loads(new).items() if k != 'fptn'}):
            raise ValueError('Source update is not authorized to reuse this dependency image')
    source = work/'candidate-source'
    names = json.loads((source/'FORK-SOURCE.json').read_text())['dependencyPaths']
    baseline_names = tracked_dependencies(work/'upstream')
    if names != baseline_names or contract(source, names) != contract(work/'upstream', baseline_names):
        raise ValueError('Prepared dependency recipes or CMake inputs changed; SDK refresh required')
    # Bind the unchanged reviewed lock set to the audited client sources, only
    # inside this build. The committed metadata still identifies the cached SDK.
    meta = json.loads((candidate/'metadata.json').read_text())
    meta['sourcesSha256'] = digest(new)
    (candidate/'metadata.json').write_text(json.dumps(meta, indent=2)+'\n')
    print('Audited unchanged SDK recipes, CMake inputs, locks and ABI profile')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'audit':
        audit(Path(sys.argv[2]))
    else:
        sys.exit('usage: dependency-reuse.py audit WORK_DIRECTORY')
