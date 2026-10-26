"""Authenticated asynchronous update API with real operation ownership."""
import hashlib,json,shutil,unittest
from pathlib import Path
import test_web_ui
ROOT=Path(__file__).resolve().parents[1]
class UpdateApiTest(unittest.TestCase):
    setUp=test_web_ui.WebUiTest.setUp
    request=test_web_ui.WebUiTest.request
    wait_worker=test_web_ui.WebUiTest.wait_worker
    def prepare(self):
        lib=self.root/'libexec/fptn';shutil.copyfile(ROOT/'scripts/fptn-web-update.sh',lib/'web-update.sh')
        installer=self.root/'mock-installer';installer.write_text('''#!/bin/sh
set -eu
[ "$FPTN_OPERATION_ID" = "$(jq -r .id "$FPTN_PREFIX/var/run/fptn-web/action/owner.json")" ]
[ "$(cat "$FPTN_PREFIX/var/run/fptn-web/action/action")" = update ]
echo installed > "$FPTN_PREFIX/installed"
''')
        catalog={'schema':1,'version':'0.9.0','revision':'a'*40,'installerSha256':hashlib.sha256(installer.read_bytes()).hexdigest(),'clientRevision':'c'*40,'clientVersion':'0.4.0'}
        (self.root/'channel').write_text(json.dumps(catalog))
        curl=self.bin/'curl';curl.write_text('''#!/usr/bin/env python3
import os,pathlib,shutil,sys
root=pathlib.Path(os.environ['FPTN_PREFIX']);args=sys.argv[1:];url=next(x for x in args if x.startswith('https://'))
shutil.copyfile(root/('channel' if url.endswith('update.json') else 'mock-installer'),args[args.index('-o')+1])
''');curl.chmod(0o700)
    def test_check_then_install_through_authenticated_background_worker(self):
        self.prepare()
        status,_=self.request(body={'action':'check-update'});self.assertEqual(status,202)
        self.assertTrue(self.wait_worker()['ok'])
        status,data=self.request('updates');self.assertEqual(status,200);self.assertTrue(data['available'])
        status,_=self.request(body={'action':'update'});self.assertEqual(status,202)
        result=self.wait_worker();self.assertTrue(result['ok'],(self.run/'action.log').read_text())
        self.assertEqual((self.root/'installed').read_text().strip(),'installed')
        self.assertEqual((self.conf/'token').read_text().strip(),self.token)
    def test_anonymous_client_cannot_check_or_install(self):
        self.prepare()
        for action in ['check-update','update']:
            status,_=self.request(body={'action':action},HTTP_AUTHORIZATION='');self.assertEqual(status,401)
        status,_=self.request('updates',HTTP_AUTHORIZATION='');self.assertEqual(status,401)
        self.assertFalse((self.run/'update-available.json').exists());self.assertFalse((self.root/'installed').exists())
