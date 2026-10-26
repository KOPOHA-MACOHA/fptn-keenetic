"""Execute the installer transaction with isolated paths and substituted services."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
MOCK=r'''#!/usr/bin/env python3
import json,os,pathlib,shutil,subprocess,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']);args=sys.argv[1:];command=pathlib.Path(sys.argv[0]).name
if command=='id': print(0)
elif command=='php-cgi':
 if (root/'fail-php').exists():sys.exit(1)
 print(json.dumps({'configured':True,'mode':'panel','rootAvailable':True}))
elif command=='ip':print('    inet 192.168.1.1/24 scope global br0')
elif command=='netstat':print('Active Internet connections (only servers)')
elif command=='lighttpd':
 if '-p' in args:print('server.modules = ( "mod_cgi" )')
elif command=='curl':
 url=next(a for a in args if a.startswith('http'))
 out=pathlib.Path(args[args.index('-o')+1]) if '-o' in args else None
 payload=root.parent/'payload'
 if 'raw.githubusercontent.com' in url:
  path=url.split('/'+'a'*40+'/',1)[1];shutil.copyfile(payload/path,out)
 elif '/releases/download/' in url:shutil.copyfile(payload/'client'/url.rsplit('/',1)[1],out)
 elif '-w' in args:print('401',end='')
 elif 'api.cgi?' in url:
  if '-K' in args:sys.stdin.read()
  print(json.dumps({'uiVersion':'0.6.0','service':{'state':'stopped'},'busy':False,'status':'stopped','configured':True,'rootAvailable':True}))
 else:
  result=subprocess.run(['sh',str(root/'share/fptn-web/index.cgi')],capture_output=True,check=True)
  header,body=result.stdout.split(b'\r\n\r\n',1)
  if '-D' in args:pathlib.Path(args[args.index('-D')+1]).write_bytes(header+b'\r\n')
  if out:out.write_bytes(body)
  else:sys.stdout.buffer.write(body)
else:sys.exit(3)
'''


class WebInstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'opt';self.payload=self.base/'payload'
        for name in ['bin','libexec/fptn','etc/fptn','etc/lighttpd/conf.d','etc/init.d','share/fptn-web','var/run/fptn-keenetic']:
            (self.root/name).mkdir(parents=True,exist_ok=True)
        self.trace=self.base/'trace'
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'FPTN_TEST_TRACE':str(self.trace)}
        for name in ['id','ip','netstat','lighttpd','curl','php-cgi']:
            path=self.root/'bin'/name;path.write_text(MOCK);path.chmod(0o700)
        (self.root/'etc/fptn/web-auth.json').write_text('{"username":"admin","hash":"test"}')
        for key in ['token','server']:(self.root/'etc/fptn'/key).write_text('dummy\n')
        (self.root/'etc/lighttpd/lighttpd.conf').write_text('original main')
        (self.root/'etc/lighttpd/conf.d/95-fptn.conf').write_text('# Managed by fptn-web-ui\noriginal fragment\n')
        (self.root/'share/fptn-web/.fptn-web-owned').touch()
        (self.root/'share/fptn-web/index.html').write_text('original UI')
        init=self.root/'etc/init.d/S80lighttpd';init.write_text('''#!/bin/sh
echo lighttpd-restart >> "$FPTN_TEST_TRACE"
if [ -f "$FPTN_PREFIX/fail-restart" ]; then rm "$FPTN_PREFIX/fail-restart"; exit 1; fi
''');init.chmod(0o700)
        def controller(label):return '''#!/bin/sh
echo LABEL-$1 >> "$FPTN_TEST_TRACE"
case "$1" in
 get-settings) echo '{"bypass":"obfuscation","strategy":"dual-rolling-tunnel","sni":"google.com"}' ;;
 stop) [ ! -f "$FPTN_PREFIX/fail-stop" ] || exit 1; rm -rf "$FPTN_PREFIX/var/run/fptn-keenetic" ;;
 start) mkdir -p "$FPTN_PREFIX/var/run/fptn-keenetic" ;;
 *) exit 1 ;;
esac
'''.replace('LABEL',label)
        self.old_controller=controller('old');(self.root/'bin/fptnctl').write_text(self.old_controller);(self.root/'bin/fptnctl').chmod(0o700)
        self.old_binary='#!/bin/sh\ncase "$1" in --help) echo old-client;; --version) echo 0.3.0;; esac\n'
        lib=self.root/'libexec/fptn'
        (lib/'fptn-client-cli').write_text(self.old_binary);(lib/'fptn-client-cli').chmod(0o700)
        (lib/'brotli').write_text('dummy')
        self.old_sums=''.join(hashlib.sha256((lib/p).read_bytes()).hexdigest()+'  '+p+'\n' for p in ['fptn-client-cli','brotli'])
        (lib/'SHA256SUMS').write_text(self.old_sums)
        paths=['web/index.html','web/index.cgi','web/style.css','web/app.js','web/api.cgi','scripts/fptn-native-service.sh','scripts/fptn-web-auth.sh','scripts/fptn-web-auth.php']
        for name in paths:
            target=self.payload/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(controller('staged') if name=='scripts/fptn-native-service.sh' else (REPO/name).read_text())
        (self.payload/'web/SHA256SUMS').write_text(''.join(hashlib.sha256((self.payload/p).read_bytes()).hexdigest()+'  '+p+'\n' for p in paths))
        (self.payload/'client').mkdir()
        new=self.payload/'client/fptn-client-cli';new.write_text('#!/bin/sh\ncase "$1" in --help) echo --keenetic-probe-only;; --version) echo 0.4.0;; esac\n')
        (self.payload/'client/SHA256SUMS').write_text(hashlib.sha256(new.read_bytes()).hexdigest()+'  fptn-client-cli\n')
        self.installer=self.base/'installer.sh'
        # Only translate its absolute /opt filesystem paths; execute all transaction code.
        self.installer.write_text((REPO/'scripts/install-web-ui.sh').read_text().replace('/opt',str(self.root)))

    def execute(self):
        return subprocess.run(['sh',str(self.installer),'install','a'*40,'192.168.1.1','b'*40],env=self.env,text=True,capture_output=True,timeout=20)

    def assert_original_files(self):
        self.assertEqual((self.root/'bin/fptnctl').read_text(),self.old_controller)
        self.assertEqual((self.root/'libexec/fptn/fptn-client-cli').read_text(),self.old_binary)
        self.assertEqual((self.root/'libexec/fptn/SHA256SUMS').read_text(),self.old_sums)
        self.assertEqual((self.root/'share/fptn-web/index.html').read_text(),'original UI')

    def test_first_stop_failure_never_mutates_or_recursively_rolls_back(self):
        (self.root/'fail-stop').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original_files()
        trace=self.trace.read_text().splitlines()
        self.assertEqual(trace.count('staged-stop'),1)
        self.assertNotIn('old-stop',trace);self.assertNotIn('lighttpd-restart',trace)
        self.assertNotIn('Восстанавливаю файлы',result.stderr)
        self.assertFalse((self.root/'var/run/fptn-web/action').exists())

    def test_missing_php_stops_before_replacing_or_stopping_anything(self):
        (self.root/'fail-php').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assert_original_files()
        self.assertFalse(self.trace.exists())
        self.assertNotIn('Новый пароль',result.stdout+result.stderr)

    def test_successful_upgrade_uses_staged_stop_and_restores_running_state(self):
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        trace=self.trace.read_text().splitlines()
        self.assertIn('staged-stop',trace);self.assertIn('staged-start',trace)
        self.assertNotIn('old-stop',trace)
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())
        self.assertFalse((self.root/'var/run/fptn-web/action').exists())
        self.assertEqual((self.root/'libexec/fptn/fptn-client-cli').read_bytes(),(self.payload/'client/fptn-client-cli').read_bytes())

    def test_failed_web_restart_restores_files_and_previous_service(self):
        (self.root/'fail-restart').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original_files()
        trace=self.trace.read_text().splitlines()
        self.assertIn('old-start',trace)
        self.assertEqual(trace.count('staged-stop'),1)
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())
        self.assertFalse((self.root/'var/run/fptn-web/action').exists())

    def test_failure_immediately_after_binary_rename_restores_previous_files(self):
        original=shutil.which('mv')
        wrapper=self.root/'bin/mv'
        wrapper.write_text('''#!/usr/bin/env python3
import os,pathlib,subprocess,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']);real=REAL
result=subprocess.run([real,*sys.argv[1:]])
marker=root/'fail-swap'
if sys.argv[-1]==str(root/'libexec/fptn/fptn-client-cli') and sys.argv[-2].endswith('.new') and marker.exists():
 marker.unlink();sys.exit(1)
sys.exit(result.returncode)
'''.replace('REAL',repr(original)));wrapper.chmod(0o700)
        (self.root/'fail-swap').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original_files()
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())
