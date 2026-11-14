"""IPK bytes, target ABI selection, deployment lifecycle and a real opkg database."""
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import threading
import http.server
import unittest
import test_runtime_package

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/name);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
ipk=load('package-ipk.py');feed=load('publish-opkg-feed.py')

class IpkTest(unittest.TestCase):
 def setUp(self):
  self.fixture=test_runtime_package.RuntimePackageTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
  f=self.fixture;self.base=f.base;self.root=f.root
  for name in ['packaging/fptn-package.sh','scripts/fptn-platform.sh']:
   dest=self.root/name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(ROOT/name,dest)
  release=json.loads((self.root/'client-release.json').read_text());release['clientVersion']='0.8.0';(self.root/'client-release.json').write_text(json.dumps(release))
  (self.root/'web/index.html').write_text('<html data-ui-version="0.18.4">')
  # Synthetic installer never touches host paths or a network. Actual installer
  # rollback is exercised separately by test_web_installer/test_install_selection.
  (self.root/'install.sh').write_text('''#!/bin/sh
set -eu
[ ! -f "$FPTN_PREFIX/fail-install" ] || exit 12
printf '%s:%s' "$2" "$3" > "$FPTN_PREFIX/endpoint"
printf '{"revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' > "$FPTN_PREFIX/etc/fptn/web-meta.json"
''')
  names=['client-release.json','runtime-assets.json','web/index.html']
  import hashlib
  (self.root/'web/SHA256SUMS').write_text(''.join(hashlib.sha256((self.root/p).read_bytes()).hexdigest()+'  '+p+'\n' for p in names))
  self.runtime=f.build();self.platform='mipsel-mips32r2-softfloat';self.revision='a'*40
  self.out=self.base/'ipk';self.package=ipk.build(self.root,self.runtime,self.out,self.platform,self.revision,1)
 def outer(self):return tarfile.open(self.package,'r:gz')
 def data(self):
  with self.outer() as outer:return outer.extractfile('data.tar.gz').read()
 def deploy_root(self):
  root=self.base/'opt'
  with tarfile.open(fileobj=io.BytesIO(self.data()),mode='r:gz') as data:data.extractall(self.base,filter='data')
  (root/'etc/fptn').mkdir(parents=True);(root/'etc/entware_release').write_text('arch=mipsel\ncpu_type=mips32r2\nfloat=soft\n')
  (root/'bin/id').write_text('#!/bin/sh\necho 0\n');(root/'bin/id').chmod(0o700)
  (root/'bin/fptnctl').write_text('#!/bin/sh\nexit 0\n');(root/'bin/fptnctl').chmod(0o700)
  (root/'etc/fptn/token').write_text('private-token');(root/'etc/fptn/web-auth.json').write_text('private-auth')
  (root/'etc/fptn/web-origin').write_text('http://192.168.55.1:1115')
  return root
 def command(self,root,action):
  return subprocess.run(['sh',str(root/'bin/fptn-package'),action],env={**os.environ,'FPTN_PREFIX':str(root)},capture_output=True,text=True,timeout=10)
 def test_deterministic_entware_archive_and_checksummed_index(self):
  with self.outer() as outer:
   self.assertEqual(outer.getnames(),['debian-binary','control.tar.gz','data.tar.gz'])
   control=tarfile.open(fileobj=io.BytesIO(outer.extractfile('control.tar.gz').read()),mode='r:gz')
   metadata=control.extractfile('control').read().decode()
   self.assertIn('Architecture: mipsel-3.4',metadata)
   dependencies=next(line for line in metadata.splitlines() if line.startswith('Depends: '))
   self.assertIn('coreutils-timeout',dependencies.split(': ',1)[1].split(', '))
  self.assertEqual(gzip.decompress((self.out/'Packages.gz').read_bytes()).decode(),(self.out/'Packages').read_text())
  again=ipk.build(self.root,self.runtime,self.base/'again',self.platform,self.revision,1)
  self.assertEqual(again.read_bytes(),self.package.read_bytes())
 def test_corrupt_runtime_wrong_target_and_wrong_revision_are_rejected(self):
  for platform,revision in [('aarch64',self.revision),(self.platform,'b'*40)]:
   with self.assertRaises((ValueError,KeyError)):ipk.build(self.root,self.runtime,self.base/'bad',platform,revision,1)
 def test_deploy_preserves_credentials_and_endpoint_and_records_only_success(self):
  root=self.deploy_root();r=self.command(root,'deploy');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  self.assertEqual((root/'endpoint').read_text(),'192.168.55.1:1115')
  self.assertEqual((root/'etc/fptn/token').read_text(),'private-token');self.assertEqual((root/'etc/fptn/web-auth.json').read_text(),'private-auth')
  self.assertEqual(json.loads((root/'etc/fptn/opkg-managed.json').read_text())['revision'],self.revision)
  before=(root/'etc/fptn/opkg-managed.json').read_bytes();(root/'fail-install').touch()
  r=self.command(root,'deploy');self.assertNotEqual(r.returncode,0);self.assertEqual((root/'etc/fptn/opkg-managed.json').read_bytes(),before)
 def test_failed_migration_remove_does_not_uninstall_manual_runtime(self):
  root=self.deploy_root();ctl=root/'bin/fptnctl';ctl.write_text('#!/bin/sh\nexit 90\n');ctl.chmod(0o700)
  self.assertEqual(self.command(root,'remove').returncode,0);self.assertTrue(ctl.exists())
 def test_prerm_refuses_mismatched_deployment_and_keeps_config(self):
  root=self.deploy_root();self.assertEqual(self.command(root,'deploy').returncode,0)
  (root/'etc/fptn/web-meta.json').write_text('{"revision":"different"}')
  self.assertNotEqual(self.command(root,'remove').returncode,0);self.assertTrue((root/'etc/fptn/token').exists())
 def test_feed_evidence_rejects_downgrade_identity_and_tampering(self):
  self.assertLess(feed.version('0.18.4-1'),feed.version('0.18.4-2'))
  with self.assertRaises(ValueError):feed.version('latest')
  with self.assertRaises(ValueError):feed.verify_evidence({'schema':1,'runtimeRevision':'b'*40,'platforms':{}},self.revision,{'assets':[]})
 @unittest.skipUnless(os.environ.get('FPTN_TEST_OPKG'),'real host opkg is built in Actions')
 def test_real_opkg_install_upgrade_architecture_and_remove(self):
  binary=os.environ['FPTN_TEST_OPKG'];dest=self.base/'opkg-root';dest.mkdir()
  config=self.base/'opkg.conf';config.write_text('dest root /\narch all 1\narch mipsel-3.4 150\noption check_signature 0\n')
  def run(*args):
   return subprocess.run([binary,'-f',str(config),'--offline-root',str(dest),'--force-depends',*args],capture_output=True,text=True,timeout=20)
  first=run('install',str(self.package));self.assertEqual(first.returncode,0,first.stdout+first.stderr)
  self.assertTrue((dest/'opt/share/fptn-package/runtime.tar.gz').is_file())
  config_dir=dest/'opt/etc/fptn';config_dir.mkdir(parents=True);(config_dir/'token').write_text('keep')
  newer=ipk.build(self.root,self.runtime,self.base/'newer',self.platform,self.revision,2)
  upgrade=run('install',str(newer));self.assertEqual(upgrade.returncode,0,upgrade.stdout+upgrade.stderr)
  info=run('status','fptn-keenetic');self.assertIn('Version: 0.18.4-2',info.stdout)
  remove=run('remove','fptn-keenetic');self.assertEqual(remove.returncode,0,remove.stdout+remove.stderr)
  self.assertFalse((dest/'opt/bin/fptn-package').exists());self.assertEqual((config_dir/'token').read_text(),'keep')
