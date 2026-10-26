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
elif command=='sleep':pass
elif command=='date':
 clock=root/'clock';now=int(clock.read_text()) if clock.exists() else 1000
 clock.write_text(str(now+1));print(now)
elif command=='php-cgi':
 if (root/'fail-php').exists():sys.exit(1)
 if os.environ.get('FPTN_AUTH_ACTION')=='op-state':print(json.dumps({'busy':True}))
 elif os.environ.get('FPTN_AUTH_ACTION')=='op-reserve':
  lock=root/'var/run/fptn-web/action';lock.mkdir();(lock/'action').write_text('update\n');print(json.dumps({'id':'a'*32}))
 else:print(json.dumps({'configured':True,'mode':'panel','rootAvailable':True}))
elif command=='ip':print('    inet 192.168.1.1/24 scope global br0')
elif command=='netstat':print('Active Internet connections (only servers)')
elif command=='lighttpd':
 if '-p' in args:print('server.modules = ( "mod_cgi" )')
elif command=='curl':
 url=next(a for a in args if a.startswith('http'))
 if url.startswith('http://192.168.1.1:1111'):
  with (root/'http-probes').open('a') as f:f.write(url+'\n')
  if (root/'web-down').exists():sys.exit(7)
  warm=root/'web-warmup'
  if warm.exists():
   left=int(warm.read_text())
   if left>0:warm.write_text(str(left-1));sys.exit(7)
   warm.unlink()
 out=pathlib.Path(args[args.index('-o')+1]) if '-o' in args else None
 payload=root.parent/'payload'
 if 'raw.githubusercontent.com' in url:
  path=url.split('/'+'a'*40+'/',1)[1];shutil.copyfile(payload/path,out)
 elif '/releases/download/' in url:shutil.copyfile(payload/'client'/url.rsplit('/',1)[1],out)
 elif '-w' in args:print('401',end='')
 elif 'api.cgi?' in url:
  if 'auth-info' in url:
   with (root.parent/'trace').open('a') as f:f.write('http-auth-info\n')
  if '-K' in args:sys.stdin.read()
  info={'uiVersion':'old' if (root/'wrong-api').exists() else '0.9.0','service':{'state':'stopped'},'busy':False,'status':'stopped','configured':True,'rootAvailable':True}
  text=json.dumps(info)
  if out:out.write_text(text)
  else:print(text)
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
        for name in ['id','ip','netstat','lighttpd','curl','php-cgi','date','sleep']:
            path=self.root/'bin'/name;path.write_text(MOCK);path.chmod(0o700)
        (self.root/'etc/fptn/web-auth.json').write_text('{"username":"admin","hash":"test"}')
        for key in ['token','server']:(self.root/'etc/fptn'/key).write_text('dummy\n')
        (self.root/'etc/lighttpd/lighttpd.conf').write_text('original main')
        (self.root/'etc/lighttpd/conf.d/95-fptn.conf').write_text('# Managed by fptn-web-ui\noriginal fragment\n')
        (self.root/'share/fptn-web/.fptn-web-owned').touch()
        (self.root/'share/fptn-web/index.html').write_text('original UI')
        init=self.root/'etc/init.d/S80lighttpd';init.write_text('''#!/bin/sh
echo lighttpd-restart >> "$FPTN_TEST_TRACE"
if [ -f "$FPTN_PREFIX/fail-restart" ]; then rm "$FPTN_PREFIX/fail-restart"; touch "$FPTN_PREFIX/web-down"; exit 1; fi
rm -f "$FPTN_PREFIX/web-down"
if [ -f "$FPTN_PREFIX/delayed-restart" ]; then printf '2\n' > "$FPTN_PREFIX/web-warmup"; exit 1; fi
if [ -f "$FPTN_PREFIX/false-failed-restart" ]; then exit 1; fi
''');init.chmod(0o700)
        def controller(label):return '''#!/bin/sh
echo LABEL-$1 >> "$FPTN_TEST_TRACE"
case "$1" in
 get-settings) echo '{"bypass":"obfuscation","strategy":"dual-rolling-tunnel","sni":"google.com"}' ;;
 stop) [ ! -f "$FPTN_PREFIX/fail-stop" ] || exit 1; rm -rf "$FPTN_PREFIX/var/run/fptn-keenetic" ;;
 start) python3 -c 'import signal,sys;sys.exit(signal.getsignal(signal.SIGTERM)==signal.SIG_IGN)' || exit 1; mkdir -p "$FPTN_PREFIX/var/run/fptn-keenetic" ;;
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
        paths=['web/index.html','web/index.cgi','web/style.css','web/app.js','web/api.cgi','scripts/fptn-native-service.sh','scripts/fptn-web-auth.sh','scripts/fptn-web-auth.php','scripts/fptn-web-operations.php','scripts/fptn-transport-plan.php','scripts/fptn-web-update.sh']
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

    def test_stop_failure_restores_web_without_repeating_stop_or_replacing_binary(self):
        (self.root/'fail-stop').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original_files()
        trace=self.trace.read_text().splitlines()
        self.assertEqual(trace.count('staged-stop'),1)
        self.assertNotIn('old-stop',trace);self.assertEqual(trace.count('lighttpd-restart'),2)
        self.assertIn('Восстанавливаю файлы',result.stderr)
        self.assertFalse((self.root/'var/run/fptn-web/action').exists())

    def test_empty_entry_response_stops_before_touching_live_service(self):
        entry=self.payload/'web/index.cgi';entry.write_text("#!/bin/sh\nprintf 'Content-Type: text/html\\r\\n\\r\\n'\n")
        names=[line.split('  ',1)[1] for line in (self.payload/'web/SHA256SUMS').read_text().splitlines()]
        (self.payload/'web/SHA256SUMS').write_text(''.join(hashlib.sha256((self.payload/p).read_bytes()).hexdigest()+'  '+p+'\n' for p in names))
        result=self.execute();self.assertNotEqual(result.returncode,0)
        self.assert_original_files();self.assertFalse(self.trace.exists())
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())

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
        self.assertLess(trace.index('http-auth-info'),trace.index('staged-stop'))
        self.assertNotIn('old-stop',trace)
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())
        self.assertFalse((self.root/'var/run/fptn-web/action').exists())
        self.assertEqual((self.root/'libexec/fptn/fptn-client-cli').read_bytes(),(self.payload/'client/fptn-client-cli').read_bytes())

    def test_failed_restart_return_code_with_healthy_http_does_not_roll_back(self):
        (self.root/'false-failed-restart').touch();result=self.execute()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.trace.read_text().splitlines().count('lighttpd-restart'),1)
        self.assertIn('staged-start',self.trace.read_text())
        self.assertNotIn('Восстанавливаю файлы',result.stderr)
        self.assertIn('api.cgi?auth-info',(self.root/'http-probes').read_text())

    def test_failed_restart_with_delayed_daemon_waits_for_actual_readiness(self):
        (self.root/'delayed-restart').touch();result=self.execute()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertGreaterEqual((self.root/'http-probes').read_text().splitlines().count('http://192.168.1.1:1111/'),3)
        self.assertEqual(self.trace.read_text().splitlines().count('lighttpd-restart'),1)
        self.assertNotIn('Восстанавливаю файлы',result.stderr)

    def test_responsive_page_with_wrong_api_version_is_not_treated_as_ready(self):
        (self.root/'wrong-api').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0)
        self.assert_original_files()
        self.assertIn('API настройки входа',result.stderr)
        self.assertTrue((self.root/'var/run/fptn-keenetic').exists())

    def test_failed_web_restart_restores_files_and_previous_service(self):
        (self.root/'fail-restart').touch();result=self.execute()
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assert_original_files()
        trace=self.trace.read_text().splitlines()
        self.assertNotIn('old-start',trace)
        self.assertNotIn('staged-stop',trace)
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

    def test_web_worker_lock_is_reused_and_not_removed_by_installer(self):
        import json
        lock=self.root/'var/run/fptn-web/action';lock.mkdir(parents=True)
        (lock/'action').write_text('update\n');(lock/'owner.json').write_text(json.dumps({'id':'d'*32}))
        self.env['FPTN_OPERATION_ID']='d'*32
        result=self.execute();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(lock.exists());self.assertTrue((lock/'install-backup').exists())

    def test_web_worker_cannot_borrow_another_operation(self):
        import json
        lock=self.root/'var/run/fptn-web/action';lock.mkdir(parents=True)
        (lock/'action').write_text('save\n');(lock/'owner.json').write_text(json.dumps({'id':'e'*32}))
        self.env['FPTN_OPERATION_ID']='d'*32
        result=self.execute();self.assertNotEqual(result.returncode,0)
        self.assert_original_files();self.assertTrue(lock.exists())
