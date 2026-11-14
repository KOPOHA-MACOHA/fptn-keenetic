"""Bootstrap installs only missing dependencies and verifies the runtime installer."""
import hashlib,importlib.util,json,os,re,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MOCK='''#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['BOOT_ROOT']);name=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
if name=='id':print(0)
elif name=='pidof':sys.exit(0 if (root/'running-http').exists() else 1)
elif name=='uname':print('armv7l')
elif name=='opkg':
 if args==['status','lighttpd']:
  vendor=root/'vendor-md5'
  if vendor.exists():print('Conffiles:\\n '+str(root/'etc/lighttpd/lighttpd.conf')+' '+vendor.read_text().strip())
  sys.exit(0)
 with (root/'opkg.log').open('a') as f:f.write(' '.join(args)+'\\n')
 if args and args[0]=='install' and 'lighttpd' in args:
  p=root/'etc/lighttpd/lighttpd.conf';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('default package config')
  p=root/'bin/lighttpd';p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o700)
 if args and args[0]=='install':
  providers=json.loads((root/'providers.json').read_text());commands=json.loads((root/'real-commands.json').read_text())
  for command,package in providers.items():
   if package in args and not (command=='timeout' and (root/'omit-timeout').exists()):
    p=root/'bin'/command
    if p.exists():continue
    if commands[command]:p.symlink_to(commands[command])
    else:p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o700)
elif name=='lighttpd':
 if '-p' in args and (root/'effective-http').exists():print((root/'effective-http').read_text())
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
        for name in ['python3', 'sh', 'awk', 'cat', 'chmod', 'cp', 'date', 'dirname', 'grep', 'head', 'id', 'mkdir', 'mv', 'pidof', 'rm', 'rmdir', 'sleep', 'tail', 'touch', 'tr', 'uname', 'wc', 'curl', 'jq', 'lighttpd', 'timeout', 'nice', 'nohup', 'base64', 'stty', 'stat', 'readlink', 'mktemp', 'sha256sum', 'md5sum', 'netstat', 'tar', 'gzip']:
            if name not in ['id','uname','pidof','netstat','lighttpd','curl']:(self.root/'bin'/name).symlink_to(shutil.which(name))
        for name in ['id','opkg','curl','ip','lighttpd','ndmc','pidof','netstat','uname']:
            p=self.root/'bin'/name;p.write_text(MOCK);p.chmod(0o700)
        (self.root/'providers.json').write_text(json.dumps({'curl': 'curl', 'jq': 'jq', 'lighttpd': 'lighttpd', 'timeout': 'coreutils-timeout', 'nice': 'coreutils-nice', 'nohup': 'coreutils-nohup', 'base64': 'coreutils-base64', 'stty': 'coreutils-stty', 'stat': 'coreutils-stat', 'readlink': 'coreutils-readlink', 'mktemp': 'coreutils-mktemp', 'sha256sum': 'coreutils-sha256sum', 'md5sum': 'coreutils-md5sum', 'netstat': 'net-tools-netstat', 'tar': 'tar', 'gzip': 'gzip'}))
        (self.root/'real-commands.json').write_text(json.dumps({name:shutil.which(name) for name in ['curl', 'jq', 'lighttpd', 'timeout', 'nice', 'nohup', 'base64', 'stty', 'stat', 'readlink', 'mktemp', 'sha256sum', 'md5sum', 'netstat', 'tar', 'gzip']}))
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
    def test_explicit_port_is_forwarded_and_invalid_port_does_not_install_packages(self):
        result=subprocess.run(['sh',str(self.script),'install','192.168.1.1','3456'],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'invoked').read_text().strip(),'install '+'a'*40+' 192.168.1.1  3456')
        (self.root/'invoked').unlink();(self.root/'opkg.log').unlink(missing_ok=True)
        result=subprocess.run(['sh',str(self.script),'install','192.168.1.1','65536'],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.root/'opkg.log').exists());self.assertFalse((self.root/'invoked').exists())

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

    def test_arm_without_timeout_installs_it_before_verified_runtime(self):
        (self.root/'etc/entware_release').write_text('arch=arm\ncpu_type=armv7\nfloat=soft\n')
        (self.root/'bin/timeout').unlink()
        p=self.root/'bin/php-cgi';p.write_text(MOCK);p.chmod(0o700)
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('install coreutils-timeout\n',(self.root/'opkg.log').read_text())
        self.assertTrue((self.root/'bin/timeout').is_file());self.assertTrue((self.root/'invoked').exists())

    def test_unavailable_timeout_after_package_install_stops_before_runtime(self):
        (self.root/'bin/timeout').unlink();(self.root/'omit-timeout').touch()
        result=self.execute();self.assertNotEqual(result.returncode,0)
        self.assertIn('Не найдена команда timeout',result.stderr)
        self.assertFalse((self.root/'invoked').exists())

    def test_each_runtime_utility_is_installed_when_absent_from_firmware(self):
        providers=json.loads((self.root/'providers.json').read_text())
        # Install PHP once so each following request isolates one missing utility.
        p=self.root/'bin/php-cgi';p.write_text(MOCK);p.chmod(0o700)
        for command in ['nice','nohup','base64','stty','stat','readlink','mktemp','sha256sum','md5sum','netstat','tar','gzip']:
            with self.subTest(command=command):
                (self.root/'bin'/command).unlink();(self.root/'opkg.log').unlink(missing_ok=True)
                result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('install '+providers[command]+'\n',(self.root/'opkg.log').read_text())
                self.assertTrue((self.root/'bin'/command).exists())

    def test_missing_firmware_baseline_fails_before_packages_or_file_changes(self):
        (self.root/'bin/awk').unlink()
        result=self.execute();self.assertNotEqual(result.returncode,0)
        self.assertIn('Нет базовой команды KeeneticOS: awk',result.stderr)
        self.assertFalse((self.root/'opkg.log').exists());self.assertFalse((self.root/'invoked').exists())

    def test_bootstrap_and_ipk_dependency_maps_cannot_drift(self):
        spec=importlib.util.spec_from_file_location('ipk_deps',ROOT/'scripts/package-ipk.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        source=(ROOT/'install.sh').read_text()
        pairs=re.search(r'for pair in ([^;]+); do',source).group(1).split()
        self.assertEqual(dict(pair.split(':') for pair in pairs),module.COMMAND_PACKAGES)
        baseline=re.search(r'for cmd in ([^;]+); do',source).group(1).split()
        self.assertEqual(baseline,module.BASE_COMMANDS)
        self.assertNotIn('ip6tables',module.COMMAND_PACKAGES)

    def test_runtime_preflight_commands_all_have_a_provider_or_explicit_platform_requirement(self):
        spec=importlib.util.spec_from_file_location('ipk_runtime_deps',ROOT/'scripts/package-ipk.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        covered=set(module.COMMAND_PACKAGES)|set(module.BASE_COMMANDS)|{'ip','ndmc','opkg'}
        for name in ['fptn-native-service.sh','install-web-ui.sh']:
            source=(ROOT/'scripts'/name).read_text()
            for commands in re.findall(r'for cmd in ([^;]+); do',source):
                self.assertLessEqual(set(commands.split()),covered,name)

    def test_short_loader_checks_prerequisites_before_creating_its_stage(self):
        env={**self.env,'PATH':str(self.root/'bin'),'TMPDIR':str(self.root)}
        result=subprocess.run(['sh',str(ROOT/'get.sh')],env=env,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue((self.root/'invoked').exists());self.assertFalse(list(self.root.glob('fptn-setup.*')))
        (self.root/'invoked').unlink();(self.root/'bin/mktemp').unlink()
        result=subprocess.run(['sh',str(ROOT/'get.sh')],env=env,text=True,capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0);self.assertIn('mktemp',result.stderr)
        self.assertFalse((self.root/'invoked').exists());self.assertFalse(list(self.root.glob('fptn-setup.*')))

    def vendor_http(self, effective):
        main=self.root/'etc/lighttpd/lighttpd.conf'
        (self.root/'vendor-md5').write_text(hashlib.md5(main.read_bytes()).hexdigest())
        (self.root/'effective-http').write_text(effective)

    def test_preinstalled_vendor_http_with_implicit_port_80_gets_private_listener(self):
        self.vendor_http('config {\n server.document-root = "/opt/share/www"\n}\n')
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        base=self.root/'etc/lighttpd/conf.d/05-fptn-bootstrap.conf'
        self.assertIn('server.port := 1110',base.read_text())
        self.assertEqual((self.root/'etc/lighttpd/lighttpd.conf').read_text(),'existing config')

    def test_custom_main_running_http_and_nondefault_primary_port_are_not_reconfigured(self):
        for case in ['custom-main','running','custom-port','conditional-port']:
            with self.subTest(case=case):
                self.vendor_http('config {\n server.port = 80\n}\n')
                if case=='custom-main':(self.root/'vendor-md5').write_text('0'*32)
                elif case=='running':(self.root/'running-http').touch()
                elif case=='custom-port':(self.root/'effective-http').write_text('config {\n server.port = 8088\n}\n')
                else:(self.root/'effective-http').write_text('config {\n server.port = 8088\n if $SERVER["socket"] == ":9090" {\n server.port = 80\n }\n}\n')
                result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertFalse((self.root/'etc/lighttpd/conf.d/05-fptn-bootstrap.conf').exists())
                (self.root/'running-http').unlink(missing_ok=True)
