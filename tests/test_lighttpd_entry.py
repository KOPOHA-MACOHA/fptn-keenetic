"""Run the actual installer vhost, static entry and authentication CGI under lighttpd."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

class LighttpdEntryTest(unittest.TestCase):
    def test_real_static_root_and_auth_api_with_installer_configuration(self):
        lighttpd=shutil.which('lighttpd')
        if not lighttpd or not shutil.which('php-cgi'):
            self.skipTest('lighttpd/PHP CGI unavailable locally; installed in CI')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);web=root/'web';shutil.copytree(ROOT/'web',web)
            conf=root/'etc/fptn';conf.mkdir(parents=True)
            lib=root/'libexec/fptn';lib.mkdir(parents=True)
            for ext in ['sh','php']:shutil.copyfile(ROOT/f'scripts/fptn-web-auth.{ext}',lib/f'web-auth.{ext}')
            shutil.copyfile(ROOT/'scripts/fptn-web-operations.php',lib/'web-operations.php')
            shutil.copyfile(ROOT/'scripts/fptn-transport-plan.php',lib/'transport-plan.php')
            for entry in ['api.cgi','index.cgi']:(web/entry).chmod(0o700)
            port=free_port();main_port=free_port()
            while main_port==port:main_port=free_port()
            origin=f'http://127.0.0.1:{port}';(conf/'web-origin').write_text(origin+'\n')
            source=(ROOT/'scripts/install-web-ui.sh').read_text()
            fragment=source.split('cat > "$FRAGMENT" <<EOF\n',1)[1].split('\nEOF',1)[0]
            for key,value in {'$SETENV_MODULE':'','$CGI_MODULE':'','$LAN':'127.0.0.1','$PORT':str(port),'$WEB':str(web),'\\$SERVER':'$SERVER'}.items():fragment=fragment.replace(key,value)
            configuration=root/'lighttpd.conf'
            configuration.write_text(f'''server.modules = ( "mod_indexfile", "mod_setenv", "mod_cgi", "mod_staticfile" )
mimetype.assign = ( ".html" => "text/html", ".css" => "text/css", ".js" => "text/javascript" )
server.bind = "127.0.0.1"
server.port = {main_port}
server.document-root = "{web}"
server.errorlog = "{root}/error.log"
server.pid-file = "{root}/server.pid"
setenv.add-environment = ( "FPTN_PREFIX" => "{root}" )
''' + fragment+'\n')
            checked=subprocess.run([lighttpd,'-tt','-f',str(configuration)],capture_output=True,text=True)
            self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
            with (root/'console.log').open('w') as log:
                server=subprocess.Popen([lighttpd,'-D','-f',str(configuration)],stdout=log,stderr=log)
                try:
                    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    deadline=time.monotonic()+10;response=None
                    while time.monotonic()<deadline:
                        try:response=opener.open(origin+'/',timeout=1);break
                        except (urllib.error.URLError,ConnectionError):time.sleep(.1)
                    self.assertIsNotNone(response,(root/'console.log').read_text())
                    with response:
                        body=response.read().decode();self.assertEqual(response.status,200)
                        self.assertIn('data-ui-version="0.16.0"',body)
                        self.assertIn('no-store',response.headers.get('Cache-Control',''))
                    import json
                    with opener.open(origin+'/api.cgi?auth-info',timeout=5) as response:
                        info=json.load(response);self.assertFalse(info['configured']);self.assertEqual(info['uiVersion'],'0.16.0')
                    with self.assertRaises(urllib.error.HTTPError) as error:opener.open(origin+'/api.cgi?status',timeout=5)
                    self.assertEqual(error.exception.code,401)
                finally:
                    server.terminate()
                    try:server.wait(timeout=5)
                    except subprocess.TimeoutExpired:server.kill();server.wait()
