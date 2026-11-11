"""Changes to exported upstream deployment sources must invalidate build selection."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fork_build_publication', ROOT/'scripts/release-publication.py')
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


class ForkBuildInputsTest(unittest.TestCase):
    def test_deployment_source_change_invalidates_compiled_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (root/'Dockerfile').write_text('FROM scratch\n')
            source = root/'deploy/openwrt/fptn-tcp-probe/fptn-tcp-probe.cpp'
            source.parent.mkdir(parents=True)
            source.write_text('// initial deployment source\n')
            git('add', '.')
            git('commit', '-qm', 'initial')
            repo = pub.Repo(root)
            before = repo.client_key(git('rev-parse', 'HEAD'))
            source.write_text('// updated deployment source\n')
            git('add', '.')
            git('commit', '-qm', 'upstream deployment change')
            self.assertNotEqual(before, repo.client_key(git('rev-parse', 'HEAD')))

    def test_arm_workflow_watches_exported_deployment_sources(self):
        import yaml
        workflow = yaml.safe_load((ROOT/'.github/workflows/build-platforms.yml').read_text())
        events = workflow.get('on', workflow.get(True))
        self.assertIn('deploy/**', events['push']['paths'])

    def test_documentation_change_matches_the_mips_workflow_ignore_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (root/'Dockerfile').write_text('FROM scratch\n')
            document = root/'deploy/README.md'
            document.parent.mkdir()
            document.write_text('Initial deployment documentation\n')
            git('add', '.')
            git('commit', '-qm', 'initial')
            repo = pub.Repo(root)
            before = repo.client_key(git('rev-parse', 'HEAD'))
            document.write_text('Updated deployment documentation\n')
            git('add', '.')
            git('commit', '-qm', 'documentation only')
            self.assertEqual(before, repo.client_key(git('rev-parse', 'HEAD')))
