"""Release migration must not silently replace assets or write the original repo."""
import importlib.util
import os
import io
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mirror', ROOT/'.github/scripts/mirror-releases.py')
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)


class ReleaseMigrationTest(unittest.TestCase):
    def test_upload_streams_to_the_fork_with_exact_length(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GH_TOKEN': 'fixture-not-a-real-token'}):
            path = Path(directory)/'asset.tar'
            path.write_bytes(b'checked bytes')
            def opened(request, timeout):
                self.assertTrue(request.full_url.startswith('https://uploads.github.com/repos/'+mirror.TARGET+'/'))
                self.assertTrue(request.full_url.endswith('name=asset%20name.tar'))
                self.assertEqual(request.get_header('Content-length'), str(path.stat().st_size))
                self.assertEqual(request.data.read(), b'checked bytes')
                return io.BytesIO(b'{"id": 42}')
            with patch.object(mirror.urllib.request, 'urlopen', side_effect=opened):
                self.assertEqual(mirror.Api().upload(1, 'asset name.tar', path), {'id': 42})

    def test_invalid_asset_names_and_missing_digests_are_rejected(self):
        good = {'name': 'dependencies.tar', 'digest': 'sha256:'+'a'*64, 'size': 42}
        self.assertEqual(set(mirror.checked_assets({'assets': [good]})), {'dependencies.tar'})
        for change in [{'name': '../escape'}, {'digest': None}, {'size': -1}]:
            with self.assertRaises((ValueError, TypeError)):
                mirror.checked_assets({'assets': [{**good, **change}]})
        with self.assertRaises(ValueError):
            mirror.checked_assets({'assets': [good, good]})

    def test_original_repository_cannot_be_written(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-not-a-real-token'}):
            api = mirror.Api()
            for method in ['POST', 'PATCH', 'DELETE']:
                with self.assertRaises(ValueError):
                    api.call(method, mirror.SOURCE, '/releases')

    def test_default_branch_gate_precedes_tags_and_release_writes(self):
        class Fake:
            def call(self, method, repo, endpoint=''):
                self_test.assertEqual(method, 'GET')
                if repo == mirror.SOURCE:
                    return {'id': mirror.SOURCE_ID}
                return {'id': mirror.TARGET_ID, 'fork': True, 'default_branch': 'master',
                        'parent': {'full_name': 'fptn-project/fptn'}}
        self_test = self
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': mirror.TARGET}), patch.object(mirror, 'execute') as execute:
            with self.assertRaises(ValueError):
                mirror.mirror(Fake(), {'releases': []}, ROOT)
            execute.assert_not_called()
