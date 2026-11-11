"""Architecture rendering and dispatch without executing foreign target binaries."""
import hashlib,importlib.util,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class PlatformPortsTest(unittest.TestCase):
    def test_profiles_keep_abi_flags_and_only_arm32_demotes_specific_boringssl_warning(self):
        original=(ROOT/'Dockerfile').read_bytes()
        for platform,arch,expected,emulator in [('aarch64','armv8','-march=armv8-a','qemu-aarch64'),('armv7-softfloat','armv7','-mfloat-abi=soft','qemu-arm')]:
            with self.subTest(platform=platform),tempfile.TemporaryDirectory() as directory:
                out=Path(directory)
                subprocess.run(['python3',str(ROOT/'scripts/render-platform.py'),str(ROOT),platform,str(out)],check=True,capture_output=True)
                profile=(out/'profile').read_text();docker=(out/'Dockerfile').read_text()
                self.assertIn('\narch='+arch+'\n',profile);self.assertIn(expected,profile)
                for bad in ['mips','-mabi=32','armv832','armv732']:self.assertNotIn(bad,profile)
                self.assertIn(emulator+' /out/fptn-platform-probe',docker);self.assertNotIn('timeout 60 qemu-mipsel',docker);self.assertIn('sed -i s/qemu-mipsel/'+emulator+'/g',docker)
                self.assertIn('CMAKE_C_FLAGS=',docker);self.assertNotIn('@@FLAGS@@',docker)
                self.assertEqual('openssl/*:tools.build:cxxflags=' in profile,platform=='armv7-softfloat')
                self.assertNotIn('-Wno-stringop-overflow',profile)
        self.assertEqual((ROOT/'Dockerfile').read_bytes(),original)

    def test_detects_little_endian_arm_kernel_and_rejects_mismatched_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'etc').mkdir();(root/'bin').mkdir()
            uname=root/'bin/uname';uname.write_text('#!/bin/sh\ncat "$FPTN_PREFIX/machine"\n');uname.chmod(0o700)
            env={**os.environ,'FPTN_PREFIX':str(root),'PATH':str(root/'bin')+':'+os.environ['PATH']}
            def detect(arch,machine):
                (root/'etc/entware_release').write_text('arch='+arch+'\ncpu_type=generic\nfloat=soft\n')
                (root/'machine').write_text(machine+'\n')
                return subprocess.run(['sh',str(ROOT/'scripts/fptn-platform.sh')],env=env,capture_output=True,text=True)
            for arch,machine,expected in [('aarch64','aarch64','aarch64'),('arm','armv7l','armv7-softfloat')]:
                r=detect(arch,machine);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),expected)
            for arch,machine in [('arm','armv7b'),('arm','armv6l'),('aarch64','x86_64'),('mips','mips')]:self.assertNotEqual(detect(arch,machine).returncode,0)
