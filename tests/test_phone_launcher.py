import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PhoneLauncherTest(unittest.TestCase):
    def run_launcher(self, config, choice="", compressed=False, extra_env=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "run-connect-test.sh").write_text((ROOT / "scripts/start-from-token.sh").read_text())
            commands = root / "bin"
            commands.mkdir()
            mocks = {
                "ip": '#!/bin/sh\ncase "$*" in *"link show"*) exit 1;; *) echo "default via 100.64.0.2 dev eth2.2 metric 1000";; esac\n',
                "stty": '#!/bin/sh\n[ "$1" != "-g" ] || echo saved\nexit 0\n',
                "nice": '#!/bin/sh\nshift 2\nexec "$@"\n',
                "timeout": '#!/bin/sh\nshift 3\nexec "$@"\n',
            }
            # Entware's default jq is the noregex variant. Reject any accidental
            # use of regex functions even when tests run against Ubuntu jq-full.
            real_jq = shutil.which("jq")
            self.assertIsNotNone(real_jq)
            mocks["jq"] = ('#!/bin/sh\n'
                'case "$*" in *"test("*|*"match("*|*"sub("*) echo "regex unavailable" >&2; exit 3;; esac\n'
                'exec ' + real_jq + ' "$@"\n')
            for name, source in mocks.items():
                (commands / name).write_text(source)
                (commands / name).chmod(0o700)
            (root / "fptn-client-cli").write_text('''#!/bin/sh
if [ "$1" = --version ]; then echo 0.1.0; exit 0; fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = --preferred-server ]; then shift; printf 'MOCK_SERVER=%s\\n' "$1"; fi
  if [ "$1" = --sni ]; then shift; printf 'MOCK_SNI=%s\\n' "$1"; fi
  if [ "$1" = --bypass-method ]; then shift; printf 'MOCK_METHOD=%s\\n' "$1"; fi
  if [ "$1" = --connection-strategy ]; then shift; printf 'MOCK_STRATEGY=%s\\n' "$1"; fi
  shift
done
printf 'MOCK_LOCALE=%s\\n' "$LC_ALL"
''')
            # Covers decoder plumbing; real Brotli is tested under QEMU in CI.
            (root / "brotli").write_text("#!/bin/sh\ncat\n")
            manifest = "".join(hashlib.sha256((root / p).read_bytes()).hexdigest() + "  " + p + "\n"
                               for p in ["fptn-client-cli", "brotli"])
            (root / "SHA256SUMS").write_text(manifest)
            encoded = base64.b64encode(json.dumps(config).encode()).decode().rstrip("=")
            token = ("fptnb:" if compressed else "fptn://") + encoded
            result = subprocess.run(["sh", str(root / "run-connect-test.sh")],
                input=token + "\n" + choice, text=True, capture_output=True,
                env={**os.environ, **(extra_env or {}), "PATH": str(commands) + ":" + os.environ["PATH"]}, timeout=10)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertNotIn("secret-password", result.stdout + result.stderr)
            return result

    def test_plain_token_menu_and_selection(self):
        result = self.run_launcher({"password": "secret-password", "servers": [{"name": "NL 1"}, {"name": "Германия"}]}, "2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) NL 1", result.stdout)
        self.assertIn("2) Германия", result.stdout)
        self.assertIn("MOCK_SERVER=Германия", result.stdout)

    def test_brotli_prefix_and_single_server(self):
        result = self.run_launcher({"servers": [{"name": "NL 1"}]}, compressed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MOCK_SERVER=NL 1", result.stdout)

    def test_invalid_choice_reprompts_without_token_reentry(self):
        result = self.run_launcher({"servers": [{"name": "NL"}, {"name": "DE"}]}, "99\n1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Такого номера нет", result.stdout)
        self.assertIn("MOCK_SERVER=NL", result.stdout)

    def test_empty_or_control_character_server_list_does_not_connect(self):
        for servers in [[], [{"name": "bad\u001b[31m"}]]:
            result = self.run_launcher({"servers": servers})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("MOCK_SERVER=", result.stdout)

    def test_sni_and_method_overrides_and_portable_locale(self):
        result = self.run_launcher({"servers": [{"name": "NL"}]}, extra_env={
            "FPTN_SNI": "example.org", "FPTN_BYPASS_METHOD": "obfuscation",
            "FPTN_CONNECTION_STRATEGY": "dual-rolling-tunnel", "LC_ALL": "invalid_LOCALE"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MOCK_SNI=example.org", result.stdout)
        self.assertIn("MOCK_METHOD=obfuscation", result.stdout)
        self.assertIn("MOCK_STRATEGY=dual-rolling-tunnel", result.stdout)
        self.assertIn("MOCK_LOCALE=C", result.stdout)
