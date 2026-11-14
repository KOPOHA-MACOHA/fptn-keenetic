"""Run the actual installer vhost, static entry and authentication CGI under lighttpd."""
import os
from pathlib import Path
import shutil
import socket
import os
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
            shutil.copyfile(ROOT/'scripts/fptn-private-state.php',lib/'private-state.php')
            shutil.copyfile(ROOT/'scripts/fptn-transport-plan.php',lib/'transport-plan.php')
            for entry in ['api.cgi','index.cgi']:(web/entry).chmod(0o700)
            port=free_port();main_port=free_port()
            while main_port==port:main_port=free_port()
            origin=f'http://127.0.0.1:{port}';(conf/'web-origin').write_text(origin+'\n')
            source=(ROOT/'scripts/install-web-ui.sh').read_text()
            fragment=source.split('cat > "$FRAGMENT" <<EOF\n',1)[1].split('\nEOF',1)[0]
            for key,value in {'$SETENV_MODULE':'','$CGI_MODULE':'','$LAN':'127.0.0.1','$PORT':str(port),'$WEB':str(web),'\\$SERVER':'$SERVER','\\$HTTP':'$HTTP'}.items():fragment=fragment.replace(key,value)
            configuration=root/'lighttpd.conf'
            configuration.write_text(f'''server.modules = ( "mod_indexfile", "mod_setenv", "mod_cgi", "mod_staticfile" )
mimetype.assign = ( ".html" => "text/html", ".css" => "text/css", ".js" => "text/javascript" )
server.bind = "127.0.0.1"
server.port = {main_port}
server.document-root = "{web}"
server.errorlog = "{root}/error.log"
server.pid-file = "{root}/server.pid"
setenv.add-environment = ( "FPTN_PREFIX" => "{root}", "FPTN_PHP" => "{shutil.which('php-cgi')}" )
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
                        self.assertIn('data-ui-version="0.18.13"',body)
                        self.assertIn('no-store',response.headers.get('Cache-Control',''))
                    import json
                    with opener.open(origin+'/favicon.svg',timeout=5) as icon:
                        self.assertEqual(icon.headers.get_content_type(),'image/svg+xml')
                        self.assertIn(b'<svg',icon.read())
                    with opener.open(origin+'/api.cgi?auth-info',timeout=5) as response:
                        info=json.load(response);self.assertFalse(info['configured']);self.assertEqual(info['uiVersion'],'0.18.13')
                    with self.assertRaises(urllib.error.HTTPError) as error:opener.open(origin+'/api.cgi?status',timeout=5)
                    self.assertEqual(error.exception.code,401)
                    # A user's HTTPS-only curlrc must not disable this local HTTP probe.
                    curl=shutil.which('curl')
                    self.assertIsNotNone(curl)
                    curl_home=root/'curl-home';curl_home.mkdir();(curl_home/'.curlrc').write_text('proto = "=https"\n')
                    environment={**os.environ,'CURL_HOME':str(curl_home)}
                    rejected=subprocess.run([curl,'--noproxy','*','-fsS',origin+'/'],env=environment,capture_output=True,text=True,timeout=5)
                    self.assertNotEqual(rejected.returncode,0)
                    probe=root/'probe';probe.mkdir()
                    body=source.split('check_panel() {\n',1)[1].split('\n}\n',1)[0]
                    checked=subprocess.run(['sh','-c','set -eu\ncheck_panel() {\n'+body+'\n}\ncheck_panel\n'],
                        env={**environment,'STAGE':str(probe),'ORIGIN':origin},capture_output=True,text=True,timeout=10)
                    self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr+(probe/'curl-error').read_text())

                finally:
                    server.terminate()
                    try:server.wait(timeout=5)
                    except subprocess.TimeoutExpired:server.kill();server.wait()

    def test_primary_port_conflict_is_fixed_by_the_actual_bootstrap_fragment(self):
        lighttpd=shutil.which('lighttpd')
        if not lighttpd:self.skipTest('real lighttpd installed in CI')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);web=root/'web';web.mkdir();(web/'index.html').write_text('FPTN panel')
            fragments=root/'conf.d';fragments.mkdir()
            panel=free_port()
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1',0));occupied.listen()
                primary=occupied.getsockname()[1]
                config=root/'lighttpd.conf'
                config.write_text(f'''server.document-root = "{web}"
server.bind = "127.0.0.1"
server.port = {primary}
index-file.names = ( "index.html" )
$SERVER["socket"] == "127.0.0.1:{panel}" {{ }}
include "{fragments}/*.conf"
''')
                broken=subprocess.run([lighttpd,'-D','-f',str(config)],capture_output=True,text=True,timeout=5)
                self.assertNotEqual(broken.returncode,0)
                self.assertIn('Address already in use',broken.stderr)
                # Run the real bootstrap creation block, not a handwritten repaired vhost.
                source=(ROOT/'install.sh').read_text()
                start=source.index('if [ "$NEW_HTTP" = yes ]; then\n  # An untouched')
                block=source[start:source.index('\nfi\n',start)+4]
                block=block.replace('/opt/etc/lighttpd/conf.d/05-fptn-bootstrap.conf',str(fragments/'05-fptn-bootstrap.conf')).replace('/opt/etc/lighttpd/lighttpd.conf',str(config)).replace('/opt/etc/lighttpd/conf.d',str(fragments))
                done=subprocess.run(['sh','-c', 'fail() { echo "$*" >&2; exit 1; }; NEW_HTTP=yes; PORT='+str(panel)+'; '+block],capture_output=True,text=True)
                self.assertEqual(done.returncode,0,done.stdout+done.stderr)
                with (root/'console.log').open('w') as log:
                    server=subprocess.Popen([lighttpd,'-D','-f',str(config)],stdout=log,stderr=log)
                    try:
                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+5
                        while True:
                            try:
                                with opener.open(f'http://127.0.0.1:{panel}/',timeout=1) as response:
                                    self.assertEqual(response.status,200);self.assertEqual(response.read(),b'FPTN panel')
                                break
                            except (urllib.error.URLError,ConnectionError):
                                if time.monotonic()>deadline:self.fail((root/'console.log').read_text())
                                time.sleep(.1)
                        self.assertEqual(config.read_text().count('server.port'),1)
                        # The foreign primary listener survived the repair untouched.
                        with socket.create_connection(('127.0.0.1',primary),timeout=1):pass
                    finally:
                        server.terminate()
                        try:server.wait(timeout=5)
                        except subprocess.TimeoutExpired:server.kill();server.wait()
