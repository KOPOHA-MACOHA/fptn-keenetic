"""Exercise real CGI/worker with real jq and a file-backed client substitute.

No HTTP listener, VPN connection, or router network mutation is used here.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CGI = ROOT / 'web/api.cgi'
SERVICE = ROOT / 'scripts/fptn-native-service.sh'


class WebUiTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which('jq'):
            self.skipTest('jq unavailable')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'opt'
        self.conf = self.root / 'etc/fptn'
        self.run = self.root / 'var/run/fptn-web'
        self.client_run = self.root / 'var/run/fptn-keenetic'
        self.bin = self.root / 'bin'
        for folder in [self.conf, self.run, self.bin]:
            folder.mkdir(parents=True)
        (self.bin/'id').write_text('#!/bin/sh\necho 0\n')
        (self.bin/'id').chmod(0o700)
        self.key = '0123456789abcdef0123456789abcdef'*2
        (self.conf/'web-auth.json').write_text('{"username":"admin","hash":"dummy"}')
        (self.run/'sessions').mkdir()
        (self.run/'sessions'/hashlib.sha256(self.key.encode()).hexdigest()).write_text(str(int(time.time())+3600)+' '+hashlib.sha256((self.conf/'web-auth.json').read_bytes()).hexdigest()+'\n')
        (self.conf / 'web-origin').write_text('http://192.168.1.1:1111\n')
        self.names = ['Czechia-Premium', 'Сервер №2']
        config = {'servers': [{'name': name} for name in self.names]}
        self.token = 'fptn:' + base64.b64encode(json.dumps(config, ensure_ascii=False).encode()).decode()
        (self.conf / 'token').write_text(self.token+'\n')
        (self.conf / 'server').write_text(self.names[0]+'\n')
        # Pass-through decoder: prefix/decode plumbing tested, not Brotli itself.
        lib = self.root / 'libexec/fptn'
        lib.mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/fptn-web-auth.sh',lib/'web-auth.sh')
        (lib / 'brotli').write_text('#!/bin/sh\ncat\n')
        (lib / 'brotli').chmod(0o700)
        controller = self.bin / 'fptnctl'
        controller.write_text('''#!/usr/bin/env python3
import json,os,pathlib,shutil,subprocess,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']); conf=root/'etc/fptn'; run=root/'var/run/fptn-keenetic'
action=sys.argv[1]
if action in ('get-settings','validate-settings','get-server-policy','validate-server-policy'):
    sys.exit(subprocess.call(['sh',os.environ['FPTN_REAL_SERVICE'],action]))
if action=='status-json':
    live=run.exists();print(json.dumps({'state':'running' if live else 'stopped','message':'Туннель подключён' if live else 'Служба остановлена','selectedServer':'CZ' if live else '', 'connected':live,'supervisorAlive':live,'clientAlive':live,'canStart':not live,'canStop':live,'canRestart':live}));sys.exit(0)
with (root/'trace').open('a') as f: f.write(action+'\\n')
if action=='status':
    print('FPTN native: клиент запущен, READY получен при старте.' if run.exists() else 'FPTN: остановлен. Автозапуск выключен.')
elif action=='stop':
    shutil.rmtree(run,ignore_errors=True); print('Остановлен')
elif action=='start':
    settings=json.loads((conf/'connection.json').read_text()) if (conf/'connection.json').exists() else {}
    if settings.get('sni')=='fails.example':
        print('Подключение не удалось');sys.exit(1)
    run.mkdir(parents=True,exist_ok=True); print('READY')
elif action=='logs':
    print((root/'test.log').read_text() if (root/'test.log').exists() else 'Обычная запись журнала')
elif action=='check-servers':
    policy=json.loads((conf/'server-policy.json').read_text()) if (conf/'server-policy.json').exists() else {'excluded':[]}
    report={'checked_at':123,'complete':True,'scope':'all-allowed','method':'fptn-login','results':[{'name':n,'ok':True,'latency_ms':42,'code':200} for n in ['Czechia-Premium','Сервер №2'] if n not in policy['excluded']]}
    (root/'var/run/fptn-web/server-checks.json').write_text(json.dumps(report))
else: sys.exit(2)
''')
        controller.chmod(0o700)
        self.env = {**os.environ, 'FPTN_PREFIX': str(self.root),
                    'FPTN_REAL_SERVICE': str(SERVICE), 'FPTN_TEST_PATH': str(self.bin)+':'+os.environ['PATH'],
                    'HTTP_HOST': '192.168.1.1:1111',
                    'HTTP_AUTHORIZATION': 'Bearer '+self.key}

    def request(self, query='status', body=None, **overrides):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else b''
        env = {**self.env, 'REQUEST_METHOD': 'POST' if body is not None else 'GET',
               'QUERY_STRING': query if body is None else '', 'CONTENT_LENGTH': str(len(data)),
               'CONTENT_TYPE': 'application/json', **overrides}
        result = subprocess.run(['sh', str(CGI)], env=env, input=data, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        header, content = result.stdout.decode().split('\r\n\r\n', 1)
        status = int(header.splitlines()[0].split()[1])
        return status, json.loads(content)

    def wait_worker(self):
        deadline = time.monotonic()+15
        while (self.run/'action').exists() and time.monotonic()<deadline:
            time.sleep(.05)
        self.assertFalse((self.run/'action').exists(), 'worker failed to finish')
        return json.loads((self.run/'result.json').read_text())

    def settings(self, **overrides):
        return {'bypass': 'obfuscation', 'strategy': 'dual-rolling-tunnel', 'sni': 'google.com', **overrides}

    def save(self, **overrides):
        return self.request(body={'action': 'save', 'server': self.names[0],
                                  'token': '', 'connection': self.settings(), **overrides})

    def test_requires_separate_key_and_expected_origin(self):
        self.assertEqual(self.request(HTTP_AUTHORIZATION='')[0], 401)
        self.assertEqual(self.request(HTTP_AUTHORIZATION='Bearer '+self.token)[0], 401)
        self.assertEqual(self.request(HTTP_ORIGIN='http://evil.example')[0], 403)
        self.assertEqual(self.request(HTTP_HOST='evil.example:1111')[0], 403)
        self.assertEqual(self.request(HTTP_ORIGIN='http://192.168.1.1:1111')[0], 200)

    def test_settings_never_return_token_and_preserve_defaults(self):
        status, data = self.request('settings')
        self.assertEqual(status, 200)
        self.assertEqual(data['servers'], self.names)
        self.assertEqual(data['connection'], self.settings())
        self.assertNotIn(self.token, json.dumps(data))

    def test_preview_both_compressed_prefixes_and_unicode(self):
        payload = self.token.split(':', 1)[1]
        for prefix in ['fptn:', 'fptn://', 'fptnb:', 'fptnb//']:
            status, data = self.request(body={'action': 'preview', 'token': prefix+payload})
            self.assertEqual(status, 200, data)
            self.assertEqual(data, {'servers': self.names})
        self.assertEqual(self.request(body={'action': 'preview', 'token': 'fptnb:!!'})[0], 400)

    def test_invalid_settings_cannot_queue_or_execute(self):
        for changes in [{'bypass': 'x; touch /tmp/owned'}, {'strategy': 'unknown'},
                        {'sni': 'a;touch pwned'}, {'sni': '-bad.example'},
                        {'sni': 'bad-.example'}, {'sni': 'a..example'}, {'sni': 'x\ny.com'}]:
            status, data = self.save(connection=self.settings(**changes))
            self.assertEqual(status, 400, data)
            self.assertFalse((self.run/'action').exists())
        self.assertFalse((self.root/'trace').exists())

    def test_save_running_client_restarts_with_new_settings(self):
        self.client_run.mkdir()
        connection = self.settings(bypass='sni-spoofing-chrome-149', strategy='triple-rolling-tunnel', sni='example.org')
        self.assertEqual(self.save(server=self.names[1], connection=connection)[0], 202)
        self.assertTrue(self.wait_worker()['ok'])
        self.assertEqual(json.loads((self.conf/'connection.json').read_text()), connection)
        self.assertEqual((self.conf/'server').read_text().strip(), self.names[1])
        self.assertTrue(self.client_run.exists())
        self.assertEqual((self.root/'trace').read_text().splitlines(), ['stop','start'])
        self.assertEqual((self.conf/'connection.json').stat().st_mode & 0o777, 0o600)

    def test_save_stopped_does_not_start_and_invalid_server_does_not_write(self):
        self.assertEqual(self.save(server='not-in-token')[0], 202)
        self.assertFalse(self.wait_worker()['ok'])
        self.assertEqual((self.conf/'server').read_text().strip(), self.names[0])
        self.assertFalse((self.root/'trace').exists())
        self.assertEqual(self.save()[0], 202)
        self.assertTrue(self.wait_worker()['ok'])
        self.assertFalse(self.client_run.exists())

    def test_failed_apply_restores_old_configuration_and_client(self):
        self.client_run.mkdir()
        old = self.settings()
        (self.conf/'connection.json').write_text(json.dumps(old))
        self.assertEqual(self.save(connection=self.settings(sni='fails.example'))[0], 202)
        self.assertFalse(self.wait_worker()['ok'])
        self.assertEqual(json.loads((self.conf/'connection.json').read_text()), old)
        self.assertTrue(self.client_run.exists())
        self.assertEqual((self.conf/'token').read_text().strip(), self.token)

    def test_busy_lock_and_http_write_restrictions(self):
        (self.run/'action').mkdir()
        self.assertEqual(self.request(body={'action':'start'})[0], 409)
        self.assertEqual(self.request(body={'action':'start'}, CONTENT_TYPE='text/plain')[0], 415)
        self.assertEqual(self.request(body={'action':'install'})[0], 400)
        self.assertEqual(self.request(REQUEST_METHOD='DELETE')[0], 405)

    def test_logs_are_text_and_do_not_return_credentials(self):
        (self.root/'test.log').write_text('TOKEN='+self.token+'\n'+'secret '+self.token+'\n<script>x</script>\n')
        status, data = self.request('logs')
        self.assertEqual(status, 200)
        self.assertNotIn(self.token, data['log'])
        self.assertIn('<script>x</script>', data['log'])  # JS renders this with textContent.

    def test_authenticated_cgi_query_cannot_enter_worker(self):
        env = {**self.env, 'REQUEST_METHOD':'GET', 'QUERY_STRING':'--worker'}
        result = subprocess.run(['sh',str(CGI),'--worker'],env=env,capture_output=True,timeout=5)
        self.assertIn(b'404 Not Found',result.stdout)
        self.assertFalse((self.root/'trace').exists())

    def test_browser_and_shell_syntax(self):
        for path in [CGI, ROOT/'web/index.cgi', SERVICE, ROOT/'scripts/install-web-ui.sh']:
            subprocess.run(['sh','-n',str(path)],check=True,capture_output=True)
        if shutil.which('node'):
            subprocess.run(['node','--check',str(ROOT/'web/app.js')],check=True,capture_output=True)

    def test_server_policy_roundtrip_and_no_all_excluded_configuration(self):
        policy={'mode':'auto','favorites':[self.names[1]],'excluded':[self.names[0]]}
        self.assertEqual(self.save(policy=policy)[0],202)
        self.assertTrue(self.wait_worker()['ok'])
        self.assertEqual(self.request('settings')[1]['policy'],policy)
        self.assertEqual(self.save(policy={'mode':'auto','favorites':[],'excluded':self.names})[0],202)
        self.assertFalse(self.wait_worker()['ok'])
        self.assertEqual(self.request('settings')[1]['policy'],policy)

    def test_manual_excluded_server_is_not_applied_and_policy_input_is_data(self):
        self.assertEqual(self.save(policy={'mode':'manual','favorites':[],'excluded':[self.names[0]]})[0],202)
        self.assertFalse(self.wait_worker()['ok'])
        self.assertFalse((self.conf/'server-policy.json').exists())
        self.assertEqual(self.save(policy={'mode':'auto; exit','favorites':[],'excluded':[]})[0],400)
        self.assertEqual(self.save(policy={'mode':'auto','favorites':'bad','excluded':[]})[0],400)

    def test_status_exposes_explicit_state_and_versions(self):
        (self.conf/'web-meta.json').write_text(json.dumps({'clientVersion':'0.4.0','revision':'abc'}))
        status,data=self.request()
        self.assertEqual(status,200);self.assertEqual(data['uiVersion'],'0.5.0')
        self.assertEqual(data['clientVersion'],'0.4.0')
        self.assertTrue(data['service']['canStart']);self.assertFalse(data['service']['canStop'])
        self.client_run.mkdir()
        data=self.request()[1]
        self.assertFalse(data['service']['canStart']);self.assertTrue(data['service']['canStop'])

    def test_check_reports_login_latency_without_stopping_service(self):
        self.client_run.mkdir()
        (self.conf/'server-policy.json').write_text(json.dumps({'mode':'auto','favorites':[],'excluded':[self.names[1]]}))
        self.assertEqual(self.request(body={'action':'check'})[0],202)
        result=self.wait_worker();self.assertTrue(result['ok']);self.assertEqual(result['action'],'check')
        self.assertTrue(self.client_run.exists())
        data=self.request()[1]
        self.assertEqual(data['report']['results'],[{'name':self.names[0],'ok':True,'latency_ms':42,'code':200}])
        trace=(self.root/'trace').read_text().splitlines()
        self.assertNotIn('stop',trace);self.assertNotIn('start',trace)

    def test_entry_page_is_not_cached_and_assets_are_versioned(self):
        page=subprocess.run(['sh',str(ROOT/'web/index.cgi')],capture_output=True,check=True).stdout.decode()
        self.assertIn('Cache-Control: no-store',page)
        self.assertIn('data-ui-version="0.5.0"',page)
        self.assertIn('app.js?v=0.5.0',page)


if __name__ == '__main__':
    unittest.main()
