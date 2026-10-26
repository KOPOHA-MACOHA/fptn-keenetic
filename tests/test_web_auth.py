"""Real crypt hashes, CGI login, session expiry/revocation and bounded bad logins."""
import hashlib
import json
import os
import pty
import select
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class WebAuthTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which('openssl') or not shutil.which('jq'): self.skipTest('openssl/jq unavailable')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.conf=self.root/'etc/fptn'; self.conf.mkdir(parents=True)
        self.run=self.root/'var/run/fptn-web'
        lib=self.root/'libexec/fptn';lib.mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/fptn-web-auth.sh',lib/'web-auth.sh')
        self.password='test password!123'
        hashed=subprocess.run(['openssl','passwd','-6','-salt','testsalt','-stdin'],input=self.password+'\n',text=True,capture_output=True,check=True).stdout.strip()
        (self.conf/'web-auth.json').write_text(json.dumps({'username':'admin','hash':hashed}))
        (self.conf/'web-origin').write_text('http://192.168.1.1:1111\n')
        bin=self.root/'bin';bin.mkdir();(bin/'id').write_text('#!/bin/sh\necho 0\n');(bin/'id').chmod(0o700)
        self.env={**os.environ,'FPTN_PREFIX':str(self.root),'PATH':str(bin)+':'+os.environ['PATH'],'FPTN_TEST_PATH':str(bin)+':'+os.environ['PATH']}

    def auth(self,action,body=''):
        return subprocess.run(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),action],env=self.env,input=body,text=True,capture_output=True,timeout=10)

    def login(self,password=None):
        return self.auth('login',json.dumps({'username':'admin','password':self.password if password is None else password}))

    def test_password_login_expiry_and_logout_revoke_session(self):
        result=self.login(); self.assertEqual(result.returncode,0,result.stderr)
        token=json.loads(result.stdout)['key']; self.assertEqual(len(token),64)
        self.assertEqual(self.auth('verify',token+'\n').returncode,0)
        self.assertEqual(self.auth('verify',self.password+'\n').returncode,1)
        path=self.run/'sessions'/hashlib.sha256(token.encode()).hexdigest()
        self.assertNotIn(token,path.read_text()); self.assertEqual(path.stat().st_mode&0o777,0o600)
        path.write_text(str(int(time.time())-1)+'\n')
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)
        token=json.loads(self.login().stdout)['key']
        self.assertEqual(self.auth('logout',token+'\n').returncode,0)
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)

    def test_credential_change_invalidates_even_an_unremoved_old_session(self):
        token=json.loads(self.login().stdout)['key']
        credentials=self.conf/'web-auth.json'; data=json.loads(credentials.read_text())
        data['username']='different'; credentials.write_text(json.dumps(data))
        self.assertNotEqual(self.auth('verify',token+'\n').returncode,0)

    def test_five_wrong_passwords_delay_further_attempts(self):
        for _ in range(5):self.assertNotEqual(self.login('wrong').returncode,0)
        self.assertNotEqual(self.login().returncode,0)
        (self.run/'auth-delay').write_text('0\n')
        self.assertEqual(self.login().returncode,0)
        self.assertFalse((self.run/'auth-failures').exists())

    def test_cgi_login_checks_origin_and_never_accepts_old_access_key(self):
        def request(query,body,**headers):
            encoded=json.dumps(body).encode()
            env={**self.env,'HTTP_HOST':'192.168.1.1:1111','REQUEST_METHOD':'POST','QUERY_STRING':query,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(encoded)),**headers}
            result=subprocess.run(['sh',str(ROOT/'web/api.cgi')],env=env,input=encoded,capture_output=True,timeout=10)
            header,data=result.stdout.decode().split('\r\n\r\n',1)
            return int(header.split()[1]),json.loads(data)
        body={'username':'admin','password':self.password}
        self.assertEqual(request('login',body,HTTP_ORIGIN='http://evil.example')[0],403)
        self.assertEqual(request('login',body,CONTENT_TYPE='text/plain')[0],415)
        status,data=request('login',body);self.assertEqual(status,200,data)
        token=data['key']
        self.assertNotIn(self.password,json.dumps(data))
        self.assertEqual(request('logout',{},HTTP_AUTHORIZATION='Bearer '+token)[0],200)
        self.assertEqual(request('logout',{},HTTP_AUTHORIZATION='Bearer '+token)[0],401)
        (self.conf/'web-key').write_text('a'*32)
        self.assertEqual(request('logout',{},HTTP_AUTHORIZATION='Bearer '+'a'*32)[0],401)

    def test_interactive_password_setup_hides_and_hashes_password(self):
        master,slave=pty.openpty()
        process=subprocess.Popen(['sh',str(ROOT/'scripts/fptn-web-auth.sh'),'passwd'],env=self.env,stdin=slave,stdout=slave,stderr=slave)
        os.close(slave); transcript=b''
        try:
            for prompt,answer in [('Логин панели','customuser'),('Новый пароль панели','new secret password123'),('Повторите пароль','new secret password123')]:
                deadline=time.monotonic()+5
                while prompt.encode() not in transcript:
                    self.assertGreater(deadline,time.monotonic(),transcript.decode(errors='replace'))
                    if select.select([master],[],[],.1)[0]:transcript+=os.read(master,8192)
                os.write(master,(answer+'\n').encode())
            self.assertEqual(process.wait(timeout=5),0)
            while select.select([master],[],[],.1)[0]:
                try:transcript+=os.read(master,8192)
                except OSError:break
            self.assertNotIn(b'new secret password123',transcript)
            data=json.loads((self.conf/'web-auth.json').read_text())
            self.assertEqual(data['username'],'customuser');self.assertTrue(data['hash'].startswith('$6$'))
            self.assertEqual((self.conf/'web-auth.json').stat().st_mode&0o777,0o600)
            result=self.auth('login',json.dumps({'username':'customuser','password':'new secret password123'}))
            self.assertEqual(result.returncode,0,result.stderr)
        finally:
            if process.poll() is None:process.kill();process.wait()
            os.close(master)

    def test_password_setup_refuses_noninteractive_input(self):
        result=self.auth('passwd','admin\npassword123\npassword123\n')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('интерактивном',result.stderr)
