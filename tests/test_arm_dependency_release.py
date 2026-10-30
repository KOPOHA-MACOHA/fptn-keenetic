"""Persistent snapshots survive Actions-cache loss without a dependency compiler."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
MOCK=r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,sys
root=pathlib.Path(os.environ['CACHE_TEST_ROOT']);a=sys.argv[1:]
with (root/'calls').open('a') as f:f.write(json.dumps(a)+'\n')
if a[0]=='api':
 status=int((root/'status').read_text())
 print('HTTP/2.0 '+str(status)+' status\nContent-Type: application/json\n')
 print(json.dumps({'draft':False,'assets':[{'name':'dependencies.tar'},{'name':'metadata.json'}]}))
 sys.exit(0 if status==200 else 1)
if a[:2]==['release','download']:
 name=a[a.index('--pattern')+1];dest=pathlib.Path(a[a.index('--dir')+1]);shutil.copyfile(root/'remote'/name,dest/name)
elif a[:2]==['release','create']:
 target=root/'uploaded';target.mkdir()
 for arg in a:
  if '#' in arg:
   path=pathlib.Path(arg.split('#')[0]);shutil.copyfile(path,target/path.name)
else:sys.exit(2)
'''

class DependencyReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'bin').mkdir();(self.root/'remote').mkdir()
        gh=self.root/'bin/gh';gh.write_text(MOCK);gh.chmod(0o700)
        self.key='a'*64;self.archive=self.root/'local/dependencies.tar';self.archive.parent.mkdir()
        self.output=self.root/'output';self.data=b'isolated test OCI bytes'
        (self.root/'remote/dependencies.tar').write_bytes(self.data)
        self.meta={'schema':1,'platform':'aarch64','key':self.key,'sha256':hashlib.sha256(self.data).hexdigest(),'size':len(self.data)}
        self.save_metadata();(self.root/'status').write_text('200')
        self.env={**os.environ,'PATH':str(self.root/'bin')+':'+os.environ['PATH'],'CACHE_TEST_ROOT':str(self.root),'GH_REPO':'KOPOHA-MACOHA/fptn-keenetic','GITHUB_SHA':'b'*40,'GITHUB_OUTPUT':str(self.output)}
    def save_metadata(self):(self.root/'remote/metadata.json').write_text(json.dumps(self.meta))
    def run_helper(self,action):return subprocess.run(['python3',str(ROOT/'scripts/arm-dependency-release.py'),action,'aarch64',self.key,str(self.archive)],env=self.env,text=True,capture_output=True,timeout=5)
    def calls(self):return [json.loads(x) for x in (self.root/'calls').read_text().splitlines()]
    def test_restore_after_actions_cache_loss_verifies_bytes_and_records_success(self):
        r=self.run_helper('restore');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.archive.read_bytes(),self.data);self.assertEqual(self.output.read_text(),'restored=true\n')
        self.assertEqual([c[1] for c in self.calls()[1:]],['download','download'])
    def test_only_explicit_404_allows_one_cold_build(self):
        (self.root/'status').write_text('404');r=self.run_helper('restore');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.output.read_text(),'restored=false\n');self.assertFalse(self.archive.exists());self.assertEqual(len(self.calls()),1)
        for status in [401,403,429,500]:
            with self.subTest(status=status):
                self.output.unlink();(self.root/'status').write_text(str(status));r=self.run_helper('restore')
                self.assertNotEqual(r.returncode,0);self.assertFalse(self.output.exists());self.assertFalse(self.archive.exists())
                self.output.touch()
    def test_corruption_or_wrong_platform_never_replaces_an_existing_archive(self):
        self.archive.write_bytes(b'existing snapshot')
        for field,value in [('sha256','0'*64),('size',999),('platform','armv7-softfloat'),('key','c'*64)]:
            with self.subTest(field=field):
                original=self.meta[field];self.meta[field]=value;self.save_metadata()
                r=self.run_helper('restore');self.assertNotEqual(r.returncode,0);self.assertFalse(self.output.exists())
                self.assertEqual(self.archive.read_bytes(),b'existing snapshot');self.meta[field]=original
    def test_publish_binds_the_existing_archive_to_platform_key_and_hash(self):
        (self.root/'status').write_text('404');self.archive.write_bytes(self.data)
        r=self.run_helper('publish');self.assertEqual(r.returncode,0,r.stderr)
        uploaded=self.root/'uploaded';self.assertEqual(json.loads((uploaded/'metadata.json').read_text()),self.meta)
        self.assertEqual((uploaded/'dependencies.tar').read_bytes(),self.data)
        create=self.calls()[-1];self.assertIn('deps-aarch64-'+self.key,create);self.assertNotIn('--clobber',create)
    def test_published_snapshot_is_never_overwritten(self):
        self.archive.write_bytes(b'different local output')
        r=self.run_helper('publish');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(len(self.calls()),1);self.assertFalse((self.root/'uploaded').exists())
    def test_fresh_runner_workflow_cannot_compile_or_fall_back_to_actions_cache(self):
        workflow=(ROOT/'.github/workflows/arm-dependency-cache.yml').read_text()
        verifier=workflow.split('  verify-fresh-runner:',1)[1]
        self.assertIn('arm-dependency-release.py restore',verifier)
        self.assertIn('dependency-image.py unpack',verifier)
        for forbidden in ['actions/cache','docker/build-push','conan install','cmake --build']:self.assertNotIn(forbidden,verifier)
        build=(ROOT/'.github/workflows/build-platforms.yml').read_text()
        self.assertNotIn('echo "uri=$(python3',build)
        self.assertLess(build.index('uri=$(python3'),build.index('uses: actions/cache/save@v4'))
        self.assertIn("steps.restore.outputs.cache-hit != 'true' && steps.release-dependencies.outputs.restored != 'true'",build)

if __name__=='__main__':unittest.main()
