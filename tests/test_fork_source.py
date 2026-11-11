"""The compiler must consume this fork's committed sources without changing cache keys."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fork_source', ROOT/'scripts/export-fork-source.py')
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class ForkSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'repo'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.put('CMakeLists.txt', 'project(fptn)\n')
        self.put('conanfile.py', '# original recipe\n')
        self.put('LICENSE', '# upstream license\n')
        for name in ('.conan/fixture', 'depends/fixture', 'deploy/fixture', 'tests/fixture'):
            self.put(name, '# fixture\n')
        self.put('src/fptn-client/fptn-client-cli.cpp', '// upstream\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'upstream')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put('sources.lock.json', json.dumps({'fptn': self.base}))
        self.put('src/fptn-client/fptn-client-cli.cpp', '// fork adaptation\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'adaptation')
        self.destination = Path(self.tmp.name)/'source'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True)

    def put(self, name, content):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_exports_fork_changes_and_omits_untracked_files(self):
        self.put('src/local-secret.txt', 'must not be exported')
        source.export(self.root, self.destination)
        self.assertEqual((self.destination/'src/fptn-client/fptn-client-cli.cpp').read_text(), '// fork adaptation\n')
        self.assertFalse((self.destination/'src/local-secret.txt').exists())
        metadata = json.loads((self.destination/'FORK-SOURCE.json').read_text())
        self.assertEqual(metadata['upstreamRevision'], self.base)
        self.assertEqual(metadata['repositoryRevision'], self.git('rev-parse', 'HEAD').strip())

    def test_dependency_changes_require_a_new_reviewed_baseline(self):
        self.put('conanfile.py', '# changed recipe\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'dependency change')
        with self.assertRaises(subprocess.CalledProcessError):
            source.export(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_uncommitted_source_changes_are_rejected(self):
        self.put('src/fptn-client/fptn-client-cli.cpp', '// dirty source\n')
        with self.assertRaises(subprocess.CalledProcessError):
            source.export(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_dependency_prefix_is_identical_to_the_imported_version(self):
        marker = '# END DEPENDENCY IMAGE INPUTS'
        current = (ROOT/'Dockerfile').read_text().split(marker)[0]
        # The imported baseline is still available locally and on the migration
        # branch, independently of the original repository's future name.
        original = subprocess.check_output(['git', '-C', str(ROOT), 'show',
            'efbf0d550f9a03c437ff03714cbecc9d6f5b6f22:Dockerfile'], text=True)
        self.assertEqual(current, original.split(marker)[0])
