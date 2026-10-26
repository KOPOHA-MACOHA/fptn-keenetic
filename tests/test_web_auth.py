"""Browser first-run, existing Entware root credentials and real PHP CGI hashing."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class WebAuthTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which('php-cgi') and not shutil.which('php'):
            self.skipTest('PHP runtime unavailable locally; required in CI')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.conf=self.root/'etc/fptn';self.conf.mkdir(parents=True)
        self.run=self.root/'var/run/fptn-web';lib=self.root/'libexec/fptn';lib.mkdir(parents=True)
        for ext in ['sh','php']:shutil.copyfile(ROOT/f'scripts/fptn-web-auth.{ext}',lib/f'web-auth.{ext}')
        self.password='test password!123'
        bin=self.root/'bin';bin.mkdir();(bin/'id').write_text('#!/bin/sh\necho 0\n');(bin/'id').chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'PATH':str(bin)+':'+os.environ['PATH'],'FPTN_TEST_PATH':str(bin)+':'+os.environ['PATH']}
        (self.conf/'web-origin').write_text('http://192.168.1.1:1111\n')
        result=self.auth('setup',json.dumps({'mode':'panel','username':'admin','password':self.password,'confirm':self.password}))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def auth(self,action,body=''):
        return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),action],env=self.env,input=body,text=True,capture_output=True,timeout=15)

    def login(self,password=None):
        return self.auth('login',json.dumps({'username':'admin','password':self.password if password is None else password}))

    def cgi(self,query,body=None,**headers):
        encoded=json.dumps(body).encode() if body is not None else b''
        env={**self.env,'HTTP_HOST':'192.168.1.1:1111','REQUEST_METHOD':'POST' if body is not None else 'GET','QUERY_STRING':query,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(encoded)),'SCRIPT_FILENAME':str(ROOT/'web/api.cgi'),'SERVER_SOFTWARE':'lighttpd','SERVER_NAME':'192.168.1.1','GATEWAY_INTERFACE':'CGI/1.1',**headers}
        result=subprocess.run(['sh',str(ROOT/'web/api.cgi')],env=env,input=encoded,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        header,data=result.stdout.decode().split('\r\n\r\n',1)
        return int(header.split()[1]),json.loads(data)

    def test_browser_first_run_create_then_login_and_cannot_replace_existing_account(self):
        (self.conf/'web-auth.json').unlink()
        status,info=self.cgi('auth-info');self.assertEqual(status,200);self.assertFalse(info['configured'])
        body={'mode':'panel','username':'mine','password':'abcd','confirm':'wrong'}
        self.assertEqual(self.cgi('setup',body)[0],400);self.assertFalse((self.conf/'web-auth.json').exists())
        body['confirm']='abcd';status,data=self.cgi('setup',body)
        self.assertEqual(status,200,data);self.assertNotIn('key',data)
        saved=(self.conf/'web-auth.json').read_bytes();self.assertNotIn(b'abcd',saved)
        self.assertEqual((self.conf/'web-auth.json').stat().st_mode&0o777,0o600)
        self.assertEqual(self.cgi('setup',{**body,'username':'attacker'})[0],400)
        self.assertEqual((self.conf/'web-auth.json').read_bytes(),saved)
        status,data=self.cgi('login',{'username':'mine','password':'abcd'});self.assertEqual(status,200,data)
        self.assertEqual(len(data['key']),64);self.assertNotIn('hash',data)

    def test_existing_entware_root_password_and_password_changes_revoke_session(self):
        panel=json.loads((self.conf/'web-auth.json').read_text());(self.conf/'web-auth.json').unlink()
        shadow=self.root/'etc/shadow';shadow.write_text('root:'+panel['hash']+':0:0:99999:7:::\n')
        self.assertTrue(self.cgi('auth-info')[1]['rootAvailable'])
        self.assertEqual(self.cgi('setup',{'mode':'entware','password':'wrong'})[0],400)
        status,data=self.cgi('setup',{'mode':'entware','password':self.password});self.assertEqual(status,200,data)
        self.assertEqual(shadow.read_text(),'root:'+panel['hash']+':0:0:99999:7:::\n')
        saved=json.loads((self.conf/'web-auth.json').read_text());self.assertEqual(saved,{'mode':'entware','username':'root'})
        token=data['key'];self.assertEqual(self.auth('verify',token+'\n').returncode,0)
        self.assertEqual(self.cgi('login',{'username':'root','password':self.password})[0],200)
        self.assertEqual(self.cgi('login',{'username':'admin','password':self.password})[0],401)
        shadow.write_text('root:!:0:0:99999:7:::\n')
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)
        self.assertEqual(self.cgi('login',{'username':'root','password':self.password})[0],401)

    def test_passwd_fallback_for_entware_without_shadow_and_locked_root_refused(self):
        hashed=json.loads((self.conf/'web-auth.json').read_text())['hash']
        (self.conf/'web-auth.json').unlink();passwd=self.root/'etc/passwd'
        passwd.write_text('root:'+hashed+':0:0:root:/opt/root:/bin/sh\n')
        self.assertTrue(self.cgi('auth-info')[1]['rootAvailable'])
        self.assertEqual(self.cgi('setup',{'mode':'entware','password':self.password})[0],200)
        self.auth('reset');(self.root/'etc/shadow').write_text('root:!:0:0:99999:7:::\n')
        self.assertFalse(self.cgi('auth-info')[1]['rootAvailable'])
        self.assertEqual(self.cgi('setup',{'mode':'entware','password':self.password})[0],400)

    def test_password_login_expiry_and_logout_revoke_session(self):
        result=self.login();self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        token=json.loads(result.stdout)['key'];self.assertEqual(len(token),64)
        self.assertEqual(self.auth('verify',token+'\n').returncode,0)
        self.assertEqual(self.auth('verify',self.password+'\n').returncode,1)
        path=self.run/'sessions'/hashlib.sha256(token.encode()).hexdigest()
        self.assertNotIn(token,path.read_text());self.assertEqual(path.stat().st_mode&0o777,0o600)
        stamp=path.read_text().split()[1];path.write_text(str(int(time.time())-1)+' '+stamp+'\n')
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)
        token=json.loads(self.login().stdout)['key']
        self.assertEqual(self.auth('logout',token+'\n').returncode,0)
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)

    def test_credential_change_invalidates_even_an_unremoved_old_session(self):
        token=json.loads(self.login().stdout)['key']
        credentials=self.conf/'web-auth.json';data=json.loads(credentials.read_text())
        data['username']='different';credentials.write_text(json.dumps(data))
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)

    def test_five_wrong_passwords_delay_further_attempts(self):
        for _ in range(5):self.assertNotEqual(self.login('wrong').returncode,0)
        self.assertNotEqual(self.login().returncode,0)
        (self.run/'auth-delay').write_text('0\n');self.assertEqual(self.login().returncode,0)
        self.assertFalse((self.run/'auth-failures').exists())

    def test_cgi_login_checks_origin_and_never_accepts_old_access_key(self):
        body={'username':'admin','password':self.password}
        self.assertEqual(self.cgi('login',body,HTTP_ORIGIN='http://evil.example')[0],403)
        self.assertEqual(self.cgi('setup',body,HTTP_ORIGIN='http://evil.example')[0],403)
        self.assertEqual(self.cgi('login',body,CONTENT_TYPE='text/plain')[0],415)
        status,data=self.cgi('login',body);self.assertEqual(status,200,data);token=data['key']
        self.assertNotIn(self.password,json.dumps(data))
        self.assertEqual(self.cgi('logout',{},HTTP_AUTHORIZATION='Bearer '+token)[0],200)
        self.assertEqual(self.cgi('logout',{},HTTP_AUTHORIZATION='Bearer '+token)[0],401)
        (self.conf/'web-key').write_text('a'*32)
        self.assertEqual(self.cgi('logout',{},HTTP_AUTHORIZATION='Bearer '+'a'*32)[0],401)

    def test_reset_only_resets_panel_configuration_not_entware(self):
        token=json.loads(self.login().stdout)['key'];shadow=self.root/'etc/shadow';shadow.write_text('root:!:unchanged\n')
        result=self.auth('reset');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse(self.cgi('auth-info')[1]['configured']);self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)
        self.assertEqual(shadow.read_text(),'root:!:unchanged\n')

    def test_concurrent_setup_has_one_winner(self):
        (self.conf/'web-auth.json').unlink();body=json.dumps({'mode':'panel','username':'one','password':'test','confirm':'test'})
        children=[subprocess.Popen(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),'setup'],env=self.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(3)]
        for child in children:child.stdin.write(body);child.stdin.close()
        codes=[]
        for child in children:
            codes.append(child.wait(timeout=15));child.stdout.close();child.stderr.close()
        self.assertEqual(codes.count(0),1,codes)
        self.assertEqual(json.loads((self.conf/'web-auth.json').read_text())['username'],'one')

    def test_legacy_sha512_credentials_continue_to_work_without_openssl(self):
        data={'username':'admin','hash':'$6$abcdefghijklmnop$Lcb0CtDeG8B7IawT1d3JyzZtU/ipjaxhQn6287FkM9XKViTiU.e44LkUVOKMD9bT3uqodzwqb5.rv3Mkxh5xw/'}
        (self.conf/'web-auth.json').write_text(json.dumps(data))
        self.assertEqual(self.login('testpass123').returncode,0)
