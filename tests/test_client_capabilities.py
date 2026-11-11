"""Discover capabilities from the executable; caching never depends on UI lists."""
import json,os,shlex,subprocess,tempfile,time,unittest
from pathlib import Path
import client_fixture
ROOT=Path(__file__).resolve().parents[1]
class CapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        client_fixture.install(self.root);self.lib=self.root/'libexec/fptn';self.client=self.lib/'fptn-client-cli'
        self.bin=self.root/'bin';self.bin.mkdir();p=self.bin/'id';p.write_text('#!/bin/sh\necho 0\n');p.chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'PATH':str(self.bin)+':'+os.environ['PATH']}
    def execute(self):return subprocess.run(['sh',str(ROOT/'scripts/fptn-client-capabilities.sh')],env=self.env,text=True,capture_output=True,timeout=12)
    def discover(self):
        r=self.execute();self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
    def replace_client(self,help_text,version='0.4.0'):
        self.help=self.root/('help-'+str(time.time_ns()));self.help.write_text(help_text)
        code='#!/bin/sh\necho "$1" >> '+shlex.quote(str(self.root/'calls'))+'\ncase "$1" in\n --help) cat '+shlex.quote(str(self.help))+' ;;\n --version) echo '+shlex.quote(version)+' ;;\n *) exit 1 ;;\nesac\n'
        new=self.lib/'next';new.write_text(code);new.chmod(0o700);new.replace(self.client)
    def minimal_busybox_stat(self):
        stat=self.bin/'stat';stat.write_text('''#!/usr/bin/env python3
import os,pathlib,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']);args=sys.argv[1:]
assert len(args)==3 and args[:2]==['-L','-t'], 'minimal BusyBox stat accepts only -L/-t'
p=args[2];s=os.stat(p)
f=root/'stat-reads';n=int(f.read_text())+1 if f.exists() else 1;f.write_text(str(n))
# Exact FEATURE_STAT_FORMAT=n terse layout; access time changes on each read.
print(p,s.st_size,s.st_blocks,format(s.st_mode,'x'),s.st_uid,s.st_gid,format(s.st_dev,'x'),s.st_ino,s.st_nlink,'0','0',n,int(s.st_mtime),int(s.st_ctime),s.st_blksize)
''');stat.chmod(0o700)
    def test_minimal_busybox_stat_caches_ignores_atime_and_detects_replacement(self):
        self.minimal_busybox_stat();self.replace_client(client_fixture.help_text());first=self.discover()
        self.assertEqual(self.discover(),first)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['--help','--version'])
        self.replace_client('obfuscation sni-spoofing-chrome-150 dual-rolling-tunnel\n')
        second=self.discover();self.assertNotEqual(first['sha256'],second['sha256']);self.assertIn('sni-spoofing-chrome-150',second['methods'])
    def test_minimal_busybox_stat_handles_filename_with_spaces(self):
        import shutil
        self.minimal_busybox_stat();target=self.lib/'client with spaces';shutil.copyfile(self.client,target);target.chmod(0o700)
        r=subprocess.run(['sh',str(ROOT/'scripts/fptn-client-capabilities.sh'),str(target)],env=self.env,text=True,capture_output=True,timeout=12)
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('obfuscation',json.loads(r.stdout)['methods'])
    def test_malformed_terse_metadata_is_not_accepted(self):
        stat=self.bin/'stat';stat.write_text('#!/bin/sh\necho broken\n');stat.chmod(0o700)
        self.assertNotEqual(self.execute().returncode,0)

    def test_actual_help_parsed_and_only_readonly_flags_are_executed(self):
        self.replace_client(client_fixture.help_text());data=self.discover()
        self.assertEqual(data['schema'],1);self.assertEqual(data['clientVersion'],'0.4.0');self.assertEqual(len(data['sha256']),64)
        self.assertIn('sni-spoofing-chrome-149',data['methods']);self.assertIn('triple-rolling-tunnel',data['strategies'])
        self.assertNotIn('fingerprint',data);self.assertEqual((self.root/'calls').read_text().splitlines(),['--help','--version'])
    def test_cache_avoids_repeated_executions(self):
        self.replace_client(client_fixture.help_text());first=self.discover();self.assertEqual(self.discover(),first)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['--help','--version'])
    def test_replacement_with_same_version_invalidates_cache_and_accepts_future_profiles(self):
        first=self.discover();self.replace_client('obfuscation sni-spoofing-edge-152 sni-spoofing-chrome-150\ndual-rolling-tunnel quad-rolling-tunnel\n');second=self.discover()
        self.assertEqual(first['clientVersion'],second['clientVersion']);self.assertNotEqual(first['sha256'],second['sha256'])
        self.assertEqual(second['methods'],['obfuscation','sni-spoofing-edge-152','sni-spoofing-chrome-150'])
        settings={'bypass':'sni-spoofing-edge-152','strategy':'quad-rolling-tunnel','sni':'example.com'}
        r=subprocess.run(['sh',str(ROOT/'scripts/fptn-native-service.sh'),'validate-settings'],env=self.env,input=json.dumps(settings),text=True,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr)
        settings['bypass']='sni-spoofing-chrome-149'
        r=subprocess.run(['sh',str(ROOT/'scripts/fptn-native-service.sh'),'validate-settings'],env=self.env,input=json.dumps(settings),text=True,capture_output=True,timeout=10)
        self.assertNotEqual(r.returncode,0)
    def test_corrupt_cache_regenerates_from_client(self):
        self.discover();(self.root/'var/run/fptn-capabilities/current.json').write_text('broken')
        self.assertIn('obfuscation',self.discover()['methods'])
    def test_empty_changed_or_oversized_help_fails_without_reusing_old_capabilities(self):
        self.discover()
        for help_text in ['no known enums','obfuscation only','obfuscation sni-spoofing-chrome-canary-150 dual-rolling-tunnel',client_fixture.help_text()+'x'*140000]:
            self.replace_client(help_text);self.assertNotEqual(self.execute().returncode,0)
    def test_hanging_help_is_bounded(self):
        self.client.write_text('#!/bin/sh\nsleep 30\n');self.client.chmod(0o700)
        start=time.monotonic();self.assertNotEqual(self.execute().returncode,0);self.assertLess(time.monotonic()-start,9)
    def test_defaults_follow_supported_strategy(self):
        self.replace_client('obfuscation sni-spoofing-edge-152\nquad-rolling-tunnel\n')
        r=subprocess.run(['sh',str(ROOT/'scripts/fptn-native-service.sh'),'get-settings'],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['strategy'],'quad-rolling-tunnel')
