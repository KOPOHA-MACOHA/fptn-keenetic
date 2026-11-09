"""Build locks reject drift without contacting a remote or compiling a package."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/name)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


locks = module('dependency-lock.py')
image = module('dependency-image.py')
imports = module('import-dependency-image.py')


class DependencyLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lock = self.root/'build-locks'
        shutil.copytree(ROOT/'build-locks', self.lock)

    def test_complete_checked_in_graph_matches_exact_recipe_locks_and_sources(self):
        meta, graph = locks.read_lock(self.lock)
        locks.verify_sources(self.lock, ROOT/'sources.lock.json')
        self.assertGreater(len(graph), 20)
        self.assertEqual(meta['tools']['conan'], 'Conan version 2.34.0')
        for identity, edges in graph:
            self.assertIn(identity.split(':')[0], ('host', 'build'))
            for edge in edges:
                self.assertIn(edge, {row[0] for row in graph})

    def test_upstream_change_requires_explicit_lock_refresh(self):
        sources = self.root/'sources.json'
        data = json.loads((ROOT/'sources.lock.json').read_text()); data['fptn'] = 'f'*40
        sources.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'refresh dependency locks'):
            locks.verify_sources(self.lock, sources)

    def test_missing_recipe_or_floating_reference_is_rejected(self):
        path = self.lock/'conan.lock'; original = json.loads(path.read_text())
        for replacement in ('zlib/1.3.2', 'zlib/[>=1.0]', 'zlib/1.3.2#'+'a'*32+'%123'):
            data = copy.deepcopy(original); data['requires'][0] = replacement; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'Unpinned'):
                locks.read_lock(self.lock)
        data = copy.deepcopy(original); data['requires'].pop(); path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'graph disagree'):
            locks.read_lock(self.lock)

    def test_graph_tracks_context_and_edges_but_ignores_transient_paths_and_timestamps(self):
        ref = 'zlib/1.3.2#'+'a'*32
        graph = {'graph': {'nodes': {'0': {'ref':'consumer/0', 'recipe':'Consumer'},
            '1': {'ref':ref, 'context':'host', 'dependencies':{}, 'package_folder':'/one'},
            '2': {'ref':ref, 'context':'build', 'dependencies':{'1':{}}, 'package_folder':'/two'}}}}
        expected = locks.graph_contract(graph)
        graph['graph']['nodes']['1']['package_folder'] = '/another'
        graph['graph']['nodes']['1']['ref'] += '%123.45'
        self.assertEqual(expected, locks.graph_contract(graph))
        graph['graph']['nodes']['2']['dependencies'] = {}
        self.assertNotEqual(expected, locks.graph_contract(graph))

    def tool_answer(self, *args):
        meta = json.loads((self.lock/'metadata.json').read_text())['tools']
        if args[:3] == ('python','-m','pip'): return '\n'.join(meta['pip'])
        if args[0] == 'dpkg-query': return (self.lock/'apt.txt').read_text().strip()
        return meta[args[0]]

    def test_tool_and_ubuntu_drift_are_rejected(self):
        with patch.object(locks, 'run', side_effect=self.tool_answer):
            locks.verify_tools(self.lock)
        for affected in ('conan','cmake','ninja','python','dpkg-query'):
            with self.subTest(tool=affected):
                def answer(*args):
                    return 'changed' if args[0] == affected else self.tool_answer(*args)
                with patch.object(locks, 'run', side_effect=answer), self.assertRaises(ValueError):
                    locks.verify_tools(self.lock)

    def test_all_lock_inputs_invalidate_mips_and_arm_keys(self):
        files = ['Dockerfile', *image.INPUTS]
        for name in files:
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, path)
        before = image.cache_key(self.root)
        for name in locks.LOCK_FILES:
            path = self.lock/name; original = path.read_bytes(); path.write_bytes(original+b'\n')
            self.assertNotEqual(before, image.cache_key(self.root), name)
            path.write_bytes(original)
        arm = (ROOT/'scripts/platform-dependency-key.py').read_text()
        for name in locks.LOCK_FILES:
            self.assertIn("'"+name+"'", arm)

    def test_legacy_import_is_exact_key_platform_and_digest_only(self):
        records = json.loads((ROOT/'build-locks/cache-imports.json').read_text())
        for platform, entries in records.items():
            for key, record in entries.items():
                self.assertEqual(imports.select(platform,key),record)
                self.assertIsNone(imports.select(platform,'0'*64))
                self.assertIsNone(imports.select('mipsel-mips32r2-softfloat',key))

    def test_docker_enforces_pins_and_checks_restored_images_before_compilation(self):
        docker = (ROOT/'Dockerfile').read_text()
        self.assertIn('--require-hashes --no-build-isolation', docker)
        self.assertNotIn('conan>=',docker)
        self.assertIn('COPY --from=sdk /etc/ssl/certs/ca-certificates.crt', docker)
        self.assertIn('https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/', (self.lock/'ubuntu.sources').read_text())
        self.assertIn('--lockfile=/work/build-locks/conan.lock',docker)
        self.assertNotIn('--lockfile-partial',docker)
        client = docker.split('FROM ${DEPENDENCIES_IMAGE} AS client')[1]
        self.assertLess(client.index('dependency-lock.py check'),client.index('prepare-connect-test.py'))
        for line in (self.lock/'apt.txt').read_text().splitlines():
            self.assertRegex(line, r'^[a-z0-9][a-z0-9+.-]*=[^\s=]+$')

    def test_graph_command_is_offline_locked_and_cannot_install_or_build(self):
        def fake_run(args, stdout, check):
            self.assertEqual(args[:3], ['conan','graph','info'])
            self.assertIn('--no-remote',args)
            self.assertIn('--lockfile='+str(self.lock/'conan.lock'),args)
            stdout.write('{"graph":{"nodes":{"0":{"ref":"new/1#aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","context":"host"}}}}')
        with patch.object(locks.subprocess,'run',side_effect=fake_run), self.assertRaisesRegex(ValueError,'differs'):
            locks.verify_graph(self.lock,self.root)
