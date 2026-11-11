"""Historical evidence survives repository rename without trusting new runtimes to it."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication_archive', ROOT/'scripts/release-publication.py')
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)
RUNTIME = 'a'*40


class ArchivedActionsTest(unittest.TestCase):
    def proof(self, historical=True, identity=None, conclusion='success'):
        calls = []
        run = {'id': 42, 'run_attempt': 1, 'head_sha': RUNTIME, 'head_branch': 'main',
               'head_repository': {'full_name': pub.LEGACY_REPOSITORY,
                                   'id': pub.LEGACY_REPOSITORY_ID if identity is None else identity},
               'path': '.github/workflows/check-runtime.yml', 'event': 'push',
               'status': 'completed', 'conclusion': 'success'}
        def get(path, missing=False):
            calls.append(path)
            if path.startswith('/repos/'+pub.REPOSITORY+'/'):
                return None
            if '/jobs?' in path:
                return {'jobs': [{'id': i, 'name': name, 'conclusion': conclusion}
                                 for i, name in enumerate(['test', 'package'], 1)]}
            return run
        def ancestor(older, newer):
            return historical if newer == pub.LEGACY_MAIN else older == newer
        result = pub.validation_proof(SimpleNamespace(get=get), SimpleNamespace(ancestor=ancestor), RUNTIME, 42)
        return result, calls

    def test_historical_completed_proof_remains_valid_after_rename(self):
        result, calls = self.proof()
        self.assertTrue(result)
        self.assertTrue(any('/'+pub.LEGACY_REPOSITORY+'/' in p for p in calls))

    def test_new_runtime_cannot_use_archived_evidence(self):
        result, calls = self.proof(historical=False)
        self.assertFalse(result)
        self.assertEqual(len(calls), 1)

    def test_recreated_archive_name_and_failed_jobs_are_rejected(self):
        self.assertFalse(self.proof(identity=999)[0])
        self.assertFalse(self.proof(conclusion='failure')[0])

    def test_archive_access_is_read_only_and_limited_to_actions_runs(self):
        api = pub.GitHub('fixture-not-a-real-token')
        for method, suffix in [('POST', 'actions/runs/42'), ('GET', 'contents/secrets'),
                               ('GET', 'actions/secrets'), ('PATCH', 'git/refs/heads/main')]:
            with self.assertRaises(ValueError):
                api.request(method, '/repos/'+pub.LEGACY_REPOSITORY+'/'+suffix)
