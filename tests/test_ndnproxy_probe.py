"""Exercise process lifecycle and failure paths using file-backed IPC mocks.

The fake resolver sends a fixture, not an implementation of the private protocol.
Only a router run can establish ndnproxy config compatibility and message format.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/probe-ndnproxy-mirror.sh'
MOCK = r'''#!/usr/bin/env python3
import os,signal,sys,time
from pathlib import Path
root=Path(os.environ['MIRROR_TEST_ROOT'])
name=Path(sys.argv[0]).name
port=int(os.environ['MIRROR_TEST_PORT'])
if name=='id': print('0')
elif name=='netstat':
    if (root/'listening').exists() or os.environ.get('MIRROR_BUSY'):
        print(f'udp 0 0 0.0.0.0:{port} 0.0.0.0:* 123/ndnproxy')
elif name=='socat':
    path=next(a.split(':',1)[1] for a in sys.argv if a.startswith('UNIX-RECVFROM:'))
    assert path.startswith(str(root)+'/fqdn-probe.')
    Path(path).touch()
    while not (root/'message').exists(): time.sleep(.02)
    sys.stdout.buffer.write((root/'message').read_bytes()); sys.stdout.buffer.flush()
elif name=='nslookup':
    assert sys.argv[1:]==['api.ipify.org',f'127.0.0.1:{port}']
    (root/'query').touch()
    print('Name: api.ipify.org\nAddress 1: 192.0.2.123')
elif name=='ndnproxy':
    config=Path(sys.argv[sys.argv.index('-c')+1])
    data=dict(line.split(' = ',1) for line in config.read_text().splitlines())
    assert data['dns_server']=='127.0.0.1:53 .'
    assert data['fqdn_mirror_path']==str(config.parent/'mirror.sock')
    assert data['dns_udp_port']==str(port)
    assert data['rpc_port']!= '54321'
    assert data['rr_port']!= '40901'
    for key in ['stat_file','fqdn_mirror_path']:
        assert data[key].startswith(str(config.parent)+'/')
    if os.environ.get('MIRROR_START_FAIL'): sys.exit(2)
    if os.environ.get('MIRROR_DAEMON') and os.fork(): sys.exit(0)
    pid=Path(sys.argv[sys.argv.index('-p')+1])
    if not os.environ.get('MIRROR_NO_PID'): pid.write_text(str(os.getpid()))
    (root/'resolver.pid').write_text(str(os.getpid()))
    def stop(*args):
        (root/'stopped').write_text('yes'); sys.exit(0)
    signal.signal(signal.SIGTERM,stop)
    (root/'listening').touch()
    while not (root/'query').exists(): time.sleep(.02)
    if not os.environ.get('MIRROR_SKIP_MESSAGE'):
        (root/'message').write_bytes(b'fixture\x00api.ipify.org\x00192.0.2.123')
    while True: signal.pause()
'''


class MirrorProbeTest(unittest.TestCase):
    def run_probe(self, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); commands=root/'bin'; commands.mkdir()
            port=15453
            for name in ['ndnproxy','socat','nslookup','netstat','id']:
                path=commands/name; path.write_text(MOCK); path.chmod(0o700)
            # Redirect only the private test root and test ports on the host.
            script=SCRIPT.read_text().replace('/opt/tmp',str(root)).replace('15453',str(port))
            # This sandbox forbids bind(), including Unix-domain sockets.
            script=script.replace('-S "$WORK/mirror.sock"','-f "$WORK/mirror.sock"')
            local=root/'probe.sh'; local.write_text(script)
            result=subprocess.run(['sh',str(local)],text=True,capture_output=True,timeout=25,
                env={**os.environ,'PATH':str(commands)+':'+os.environ['PATH'],
                     'MIRROR_TEST_ROOT':str(root),'MIRROR_TEST_PORT':str(port),**(extra or {})})
            self.assertEqual(list(root.glob('fqdn-probe.*')),[],result.stdout+result.stderr)
            if (root/'resolver.pid').exists():
                self.assertTrue((root/'stopped').exists(),result.stdout+result.stderr)
                pid=(root/'resolver.pid').read_text()
                proc=Path('/proc')/pid/'cmdline'
                self.assertFalse(proc.exists() and b'probe.conf' in proc.read_bytes())
            return result

    def test_foreground_resolver_capture_and_cleanup(self):
        result=self.run_probe()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Получен образец сообщения',result.stdout)

    def test_daemonized_resolver_is_also_stopped(self):
        result=self.run_probe({'MIRROR_DAEMON':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_daemonized_resolver_without_pidfile_is_also_stopped(self):
        result=self.run_probe({'MIRROR_DAEMON':'1','MIRROR_NO_PID':'1'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_dns_answer_without_mirror_message_is_not_success(self):
        result=self.run_probe({'MIRROR_SKIP_MESSAGE':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Сообщение зеркалирования не получено',result.stderr)

    def test_busy_port_does_not_launch_resolver(self):
        result=self.run_probe({'MIRROR_BUSY':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('занят',result.stderr)

    def test_start_failure_leaves_no_temporary_capture(self):
        result=self.run_probe({'MIRROR_START_FAIL':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('не начал слушать',result.stderr)
