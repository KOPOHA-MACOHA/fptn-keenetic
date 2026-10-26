"""Fixed-channel updater: no network or real installation in these tests."""
import hashlib,json,os
from pathlib import Path
import subprocess,tempfile,time,unittest
ROOT=Path(__file__).resolve().parents[1]
class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.run=self.root/'var/run/fptn-web';self.run.mkdir(parents=True)
        self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True)
        (self.conf/'web-origin').write_text('http://192.168.1.1:1111')
        (self.conf/'web-meta.json').write_text(json.dumps({'uiVersion':'0.8.0','revision':'b'*40}))
        self.installer=self.root/'installer';self.installer.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FPTN_PREFIX/applied"\nprintf "%s" "$FPTN_TARGET_CLIENT_VERSION" > "$FPTN_PREFIX/client-target"\n')
        self.catalog={'schema':1,'version':'0.9.0','revision':'a'*40,'installerSha256':hashlib.sha256(self.installer.read_bytes()).hexdigest(),'clientRevision':'c'*40,'clientVersion':'0.4.0'}
        self.channel=self.root/'channel';self.channel.write_text(json.dumps(self.catalog))
        self.bin=self.root/'bin';self.bin.mkdir();curl=self.bin/'curl'
        curl.write_text('''#!/usr/bin/env python3
import os,pathlib,shutil,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']);args=sys.argv[1:];url=next(x for x in args if x.startswith('https://'))
with (root/'urls').open('a') as f:f.write(url+'\\n')
if (root/'offline').exists():sys.exit(7)
assert url.startswith('https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/')
shutil.copyfile(root/('channel' if url.endswith('web/update.json') else 'installer'),args[args.index('-o')+1])
''');curl.chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'PATH':str(self.bin)+':'+os.environ['PATH'],'FPTN_OPERATION_ID':'d'*32}
    def call(self,action):return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-update.sh'),action],env=self.env,text=True,capture_output=True,timeout=10)
    def check(self):
        r=self.call('check');self.assertEqual(r.returncode,0,r.stderr)
    def test_fixed_channel_installer_hash_and_arguments(self):
        self.check();info=json.loads(self.call('status').stdout);self.assertTrue(info['available'])
        r=self.call('apply');self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual((self.root/'applied').read_text().splitlines(),['install','a'*40,'192.168.1.1','c'*40])
        self.assertEqual((self.root/'client-target').read_text(),'0.4.0')
        self.assertEqual((self.root/'urls').read_text().splitlines(),['https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/main/web/update.json','https://raw.githubusercontent.com/KOPOHA-MACOHA/fptn-keenetic/'+'a'*40+'/scripts/install-web-ui.sh'])
    def test_corrupt_installer_never_executes(self):
        self.check();self.installer.write_text('echo broken\n');self.assertNotEqual(self.call('apply').returncode,0);self.assertFalse((self.root/'applied').exists())
    def test_invalid_catalog_does_not_replace_good_cache(self):
        self.check();cache=(self.run/'update-available.json').read_bytes()
        for change in [{'revision':'../../evil'},{'installerSha256':'x'*64},{'version':'9;bad.0.0'},{'clientRevision':'https://evil'}]:
            self.channel.write_text(json.dumps({**self.catalog,**change}));self.assertNotEqual(self.call('check').returncode,0);self.assertEqual((self.run/'update-available.json').read_bytes(),cache)
    def test_expired_catalog_requires_new_check(self):
        self.check();p=self.run/'update-available.json';data=json.loads(p.read_text());data['checkedAt']=int(time.time())-90000;p.write_text(json.dumps(data))
        self.assertNotEqual(self.call('apply').returncode,0);self.assertFalse((self.root/'applied').exists())
    def test_current_or_older_version_is_not_installed(self):
        self.check()
        for meta in [{'uiVersion':'0.9.0','revision':'a'*40},{'uiVersion':'1.0.0','revision':'b'*40}]:
            (self.conf/'web-meta.json').write_text(json.dumps(meta));self.assertFalse(json.loads(self.call('status').stdout)['available']);self.assertNotEqual(self.call('apply').returncode,0)
    def test_offline_check_does_not_change_installation(self):
        (self.root/'offline').touch();self.assertNotEqual(self.call('check').returncode,0);self.assertFalse((self.run/'update-available.json').exists());self.assertFalse((self.root/'applied').exists())

    def test_same_ui_revision_detects_changed_binary_identity(self):
        self.catalog.update(clientSha256='f'*64,upstreamVersion='0.4.6',upstreamRevision='e'*40)
        self.channel.write_text(json.dumps(self.catalog));(self.conf/'web-meta.json').write_text(json.dumps({'uiVersion':'0.9.0','revision':'a'*40}))
        lib=self.root/'libexec/fptn';lib.mkdir(parents=True)
        state={'clientSha256':'d'*64,'clientIdentityVerified':True,'clientBuildRevision':'c'*40,'upstreamRevision':'e'*40}
        (self.root/'identity.json').write_text(json.dumps(state))
        helper=lib/'client-identity.sh';helper.write_text('#!/bin/sh\ncat "$FPTN_PREFIX/identity.json"\n')
        self.check();data=json.loads(self.call('status').stdout);self.assertTrue(data['available']);self.assertTrue(data['clientUpdate'])
        state['clientSha256']='f'*64;(self.root/'identity.json').write_text(json.dumps(state))
        data=json.loads(self.call('status').stdout);self.assertFalse(data['available']);self.assertFalse(data['clientUpdate'])

    def test_extra_identity_fields_are_validated_and_preserved(self):
        self.catalog.update(clientSha256='f'*64,upstreamVersion='0.4.6',upstreamRevision='e'*40)
        self.channel.write_text(json.dumps(self.catalog));self.check()
        data=json.loads((self.run/'update-available.json').read_text());self.assertEqual(data['clientSha256'],'f'*64);self.assertEqual(data['upstreamVersion'],'0.4.6')
        self.channel.write_text(json.dumps({**self.catalog,'clientSha256':'broken'}));self.assertNotEqual(self.call('check').returncode,0)
