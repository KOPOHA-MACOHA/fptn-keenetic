"""Platform probes must not rebuild the locked Ubuntu toolchain on normal CI."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class PreparedProbeTest(unittest.TestCase):
    def test_prepared_probe_stage_uses_the_verified_toolchain(self):
        docker = (ROOT/'Dockerfile').read_text()
        stage = docker.split('FROM $'+'{DEPENDENCIES_IMAGE} AS prepared-probe\n', 1)[1]
        stage = stage.split('FROM scratch AS prepared-probe-export', 1)[0]
        self.assertIn('mipsel-openwrt-linux-musl-g++', stage)
        self.assertIn('qemu-mipsel /out/fptn-platform-probe', stage)
        self.assertNotIn('apt-get', stage)
        self.assertNotIn('conan install', stage)
        build = yaml.safe_load((ROOT/'.github/workflows/build.yml').read_text())
        steps = build['jobs']['probe']['steps']
        restore = next(i for i, s in enumerate(steps) if s.get('id') == 'probe-dependencies')
        probe = next(i for i, s in enumerate(steps) if s.get('with', {}).get('target') == 'prepared-probe-export')
        self.assertLess(restore, probe)
        self.assertEqual(steps[restore]['with']['restore-only'], 'true')
        self.assertIn('steps.probe-dependencies.outputs.context', steps[probe]['with']['build-contexts'])

    def test_arm_probe_runs_only_after_restoring_and_verifying_the_snapshot(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/build-platforms.yml').read_text())
        steps = workflow['jobs']['compile']['steps']
        context = next(i for i, s in enumerate(steps) if s.get('id') == 'context')
        probe = next(i for i, s in enumerate(steps) if s.get('with', {}).get('target') == 'prepared-probe-export')
        self.assertLess(context, probe)
        self.assertIn('steps.context.outputs.uri', steps[probe]['with']['build-contexts'])
        action = yaml.safe_load((ROOT/'.github/actions/dependency-image/action.yml').read_text())
        self.assertIn('restore-only', action['inputs'])
        phases = action['runs']['steps']
        restore = next(i for i, s in enumerate(phases) if s.get('id') == 'release-dependencies')
        guard = next(i for i, s in enumerate(phases) if s.get('name', '').startswith('Refuse a cold rebuild'))
        seed = next(i for i, s in enumerate(phases) if s.get('id') == 'seed')
        self.assertLess(restore, guard)
        self.assertLess(guard, seed)

    def test_arm_platform_export_never_reaches_the_cold_toolchain(self):
        for platform in ['aarch64', 'armv7-softfloat']:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                subprocess.run(['python3', str(ROOT/'scripts/render-platform.py'),
                                str(ROOT), platform, str(output)], check=True, capture_output=True)
                stages = {}
                current = None
                for line in (output/'Dockerfile').read_text().splitlines():
                    found = re.match(r'^FROM(?:\s+--platform=\S+)?\s+(\S+)\s+AS\s+(\S+)', line, re.I)
                    if found:
                        base, current = found.groups()
                        stages[current] = {base}
                    elif current:
                        copied = re.match(r'^COPY\s+--from=(\S+)', line)
                        if copied:
                            stages[current].add(copied.group(1))
                pending = ['platform-export']
                reached = set()
                while pending:
                    stage = pending.pop()
                    if stage in reached:
                        continue
                    reached.add(stage)
                    pending.extend(parent for parent in stages[stage] if parent in stages)
                self.assertIn('client', reached)
                self.assertIn('token-decoder', reached)
                self.assertNotIn('toolchain', reached)
                self.assertNotIn('dependencies', reached)
        workflow = yaml.safe_load((ROOT/'.github/workflows/build-platforms.yml').read_text())
        client = next(s for s in workflow['jobs']['compile']['steps']
                      if s.get('with', {}).get('target') == 'platform-export')
        self.assertIn('DEPENDENCIES_IMAGE=prepared-dependencies', client['with']['build-args'])
