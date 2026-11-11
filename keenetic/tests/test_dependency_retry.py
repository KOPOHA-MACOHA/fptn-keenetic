"""Only transient downloads retry, retaining successful package state in one RUN."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class DependencyRetryTest(unittest.TestCase):
    def simulate(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'conan').write_text('''#!/bin/sh
n=0
[ ! -f "$RETRY_TEST_ROOT/count" ] || n=$(cat "$RETRY_TEST_ROOT/count")
n=$((n+1)); echo "$n" > "$RETRY_TEST_ROOT/count"
if [ "$RETRY_TEST_MODE" = recover ] && [ "$n" -gt 1 ]; then
    test -f "$RETRY_TEST_ROOT/completed-package" || exit 99
    exit 0
fi
touch "$RETRY_TEST_ROOT/completed-package"
if [ "$RETRY_TEST_MODE" = compiler ]; then
    echo 'ERROR: Error in build(): compiler error'; exit 17
fi
if [ "$RETRY_TEST_MODE" = missing ]; then
    echo 'ConanException: Error downloading file https://example.test/a: HTTP 404'; exit 18
fi
echo "ConanException: Error downloading file https://example.test/a: too many 504 error responses"; exit 19
''')
            (root/'sleep').write_text('#!/bin/sh\nexit 0\n')
            for p in [root/'conan',root/'sleep']:p.chmod(0o700)
            result=subprocess.run(['bash',str(ROOT/'scripts/install-locked-dependencies.sh')],env={**os.environ,'PATH':str(root)+':'+os.environ['PATH'],'RETRY_TEST_ROOT':str(root),'RETRY_TEST_MODE':mode},text=True,capture_output=True,timeout=5)
            return result,int((root/'count').read_text())
    def test_transient_http_failure_preserves_completed_packages(self):
        result,count=self.simulate('recover');self.assertEqual(result.returncode,0,result.stdout);self.assertEqual(count,2)
    def test_persistent_network_failure_stops_after_three_attempts(self):
        result,count=self.simulate('network');self.assertEqual(result.returncode,19);self.assertEqual(count,3)
    def test_compile_errors_and_missing_sources_do_not_retry(self):
        for mode,code in [('compiler',17),('missing',18)]:
            result,count=self.simulate(mode);self.assertEqual(result.returncode,code);self.assertEqual(count,1)
