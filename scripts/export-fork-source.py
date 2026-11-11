#!/usr/bin/env python3
"""Export the committed fork sources for the client stage, reusing locked dependencies."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

SOURCE_PATHS = ('CMakeLists.txt', 'conanfile.py', 'LICENSE', '.conan', 'depends', 'src', 'deploy', 'tests')
DEPENDENCY_PATHS = ('conanfile.py', '.conan', 'depends')


def export(root, destination):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    revision = git('rev-parse', 'HEAD').decode().strip()
    lock = json.loads(git('show', revision + ':sources.lock.json'))
    upstream = lock['fptn']
    git('merge-base', '--is-ancestor', upstream, revision)
    # A source-only change may reuse dependencies. Recipe/build dependency changes
    # require advancing the source lock and refreshing its reviewed lock set first.
    git('diff', '--exit-code', upstream, revision, '--', *DEPENDENCY_PATHS)
    git('diff', '--exit-code', 'HEAD', '--', *SOURCE_PATHS, 'sources.lock.json')
    for name in ('CMakeLists.txt', 'conanfile.py', 'src/fptn-client/fptn-client-cli.cpp'):
        git('cat-file', '-e', revision + ':' + name)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(git('archive', revision, '--', *SOURCE_PATHS))) as archive:
        archive.extractall(destination, filter='data')
    (destination/'FORK-SOURCE.json').write_text(json.dumps({
        'schema': 1, 'repositoryRevision': revision, 'upstreamRevision': upstream,
    }, indent=2) + '\n')
    print('Exported committed fork sources: ' + revision)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('usage: export-fork-source.py DESTINATION')
    export(Path(__file__).resolve().parents[1], Path(sys.argv[1]))
