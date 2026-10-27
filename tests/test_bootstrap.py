"""Bootstrap installs only missing dependencies and verifies the runtime installer."""
import hashlib,json,os,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOCK='''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['BOOT_ROOT']);name=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
if name=='id':print(0)
elif name=='pidof':sys.exit(1)
elif name=='opkg':
 with (root/'opkg.log').open('a') as f:f.write(' '.join(args)+'\\n')
 if args and args[0]=='install' and 'lighttpd' in args:
  p=root/'etc/lighttpd/lighttpd.conf';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('default package config')
  p=root/'bin/lighttpd';p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o700)
elif name=='curl':
 out=pathlib.Path(args[args.index('-o')+1]);url=next(a for a in args if a.startswith('http'))
 out.write_bytes((root/('channel.json' if url.endswith('web/update.json') else 'remote-installer')).read_bytes())
else:pass
'''
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'opt'
        for directory in ['bin','sbin','etc/lighttpd','lib/lighttpd']:(self.root/directory).mkdir(parents=True,exist_ok=True)
        self.env={**os.environ,'BOOT_ROOT':str(self.root)}
        for name in ['python3','awk','mkdir','mktemp','readlink','dirname','sha256sum','jq','sh','rm','cat']:(self.root/'bin'/name).symlink_to(shutil.which(name))
        for name in ['id','opkg','curl','ip','lighttpd','ndmc','pidof','netstat']:
            p=self.root/'bin'/name;p.write_text(MOCK);p.chmod(0o700)
        (self.root/'sbin/ip').write_text('#!/bin/sh\n');(self.root/'sbin/ip').chmod(0o700)
        for module in ['cgi','setenv']:(self.root/'lib/lighttpd'/('mod_'+module+'.so')).touch()
        (self.root/'etc/entware_release').write_text('arch=mipsel\ncpu_type=mips32r2\nfloat=soft\n')
        (self.root/'etc/lighttpd/lighttpd.conf').write_text('existing config')
        (self.root/'tun').touch()
        payload='#!/bin/sh\nprintf "%s\\n" "$*" > "$BOOT_ROOT/invoked"\n'
        (self.root/'remote-installer').write_text(payload)
        self.channel={'schema':1,'revision':'a'*40,'installerSha256':hashlib.sha256(payload.encode()).hexdigest()}
        self.save_channel()
        self.script=self.root/'bootstrap.sh'
        script=(ROOT/'install.sh').read_text().replace('/opt',str(self.root)).replace('[ -c /dev/net/tun ]','[ -f "'+str(self.root/'tun')+'" ]')
        # No host PHP/lighttpd may satisfy fixture dependency discovery, even in Actions.
        script=re.sub(r'^PATH=.*$', 'PATH='+str(self.root/'bin'), script, flags=re.MULTILINE)
        self.script.write_text(script)
    def save_channel(self):(self.root/'channel.json').write_text(json.dumps(self.channel))
    def execute(self):return subprocess.run(['sh',str(self.script),'install','192.168.1.1'],env=self.env,text=True,capture_output=True,timeout=10)
    def test_missing_php_is_installed_then_exact_revision_is_used(self):
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        log=(self.root/'opkg.log').read_text();self.assertIn('update\n',log);self.assertIn('install php8-cgi\n',log)
        self.assertNotIn('openssl',log);self.assertNotIn('upgrade',log)
        self.assertEqual((self.root/'invoked').read_text().strip(),'install '+'a'*40+' 192.168.1.1')
    def test_existing_dependencies_do_not_run_opkg_update_or_install(self):
        p=self.root/'bin/php-cgi';p.write_text(MOCK);p.chmod(0o700)
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.root/'opkg.log').exists())
    def test_bad_platform_never_installs_or_executes_anything(self):
        (self.root/'etc/entware_release').write_text('arch=mips\ncpu_type=mips32r2\nfloat=soft\n')
        result=self.execute();self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'opkg.log').exists());self.assertFalse((self.root/'invoked').exists())
    def test_unverified_installer_or_invalid_revision_never_executes(self):
        (self.root/'remote-installer').write_text('#!/bin/sh\ntouch "$BOOT_ROOT/invoked"\n')
        self.assertNotEqual(self.execute().returncode,0);self.assertFalse((self.root/'invoked').exists())
        self.channel['revision']='main; echo anything';self.save_channel()
        self.assertNotEqual(self.execute().returncode,0);self.assertFalse((self.root/'invoked').exists())

    def test_new_webserver_primary_listener_is_loopback_not_firmware_port(self):
        (self.root/'etc/lighttpd/lighttpd.conf').unlink();(self.root/'bin/lighttpd').unlink()
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        base=self.root/'etc/lighttpd/conf.d/05-fptn-bootstrap.conf'
        self.assertIn('server.bind := "127.0.0.1"',base.read_text());self.assertIn('server.port := 1110',base.read_text())
        self.assertEqual((self.root/'etc/lighttpd/lighttpd.conf').read_text(),'default package config')
        self.assertIn('lighttpd',(self.root/'opkg.log').read_text())
