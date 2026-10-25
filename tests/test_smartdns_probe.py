import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/probe-smartdns-ipset.sh'
MOCK=r'''#!/usr/bin/env python3
import json,os,signal,sys
from pathlib import Path
p=Path(os.environ['PROBE_MOCK_STATE']); s=json.loads(p.read_text()); command=Path(sys.argv[0]).name
if command=='smartdns':
    if os.environ.get('PROBE_FAIL_START')=='1': sys.exit(1)
    config=Path(sys.argv[sys.argv.index('-c')+1]).read_text()
    assert 'bind 127.0.0.1:15353' in config
    assert 'server 127.0.0.1:53' in config
    assert 'cache-size 0' in config
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
    while True: signal.pause()
elif command=='ndmc':
    arg=sys.argv[2]; ip=arg.split()[-1].split('/')[0]
    assert 'domain-list9' in arg
    s['changes']+=1
    if 'no include' in arg: s['static']=[x for x in s['static'] if x!=ip]
    elif ip not in s['static']: s['static'].append(ip)
    s['members']=s['static'][:]
    p.write_text(json.dumps(s))
elif command=='nslookup':
    assert sys.argv[1:]==['api.ipify.org','127.0.0.1:15353']
    if os.environ.get('PROBE_FAIL_QUERY')=='1': sys.exit(2)
    s['members'].append('9.9.9.9'); p.write_text(json.dumps(s))
    print('Name: api.ipify.org\nAddress 1: 9.9.9.9')
elif command=='ipset':
    print('Name: _NDM_OGDN_4_@domain-list9\nMembers:')
    print('\n'.join(s['members']))
elif command=='netstat': print('Active Internet connections')
elif command=='id': print('0')
'''

class SmartDNSProbeTest(unittest.TestCase):
    def run_probe(self,extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); commands=root/'bin'; commands.mkdir()
            state=root/'state'; initial=['8.6.112.0','8.47.69.0']
            state.write_text(json.dumps({'static':initial,'members':initial,'changes':0}))
            for name in ['smartdns','ndmc','nslookup','ipset','netstat','id']:
                path=commands/name; path.write_text(MOCK); path.chmod(0o700)
            result=subprocess.run(['sh',str(SCRIPT)],text=True,capture_output=True,timeout=10,
                env={**os.environ,'PATH':str(commands)+':'+os.environ['PATH'],'TMPDIR':str(root),
                     'PROBE_MOCK_STATE':str(state),**(extra or {})})
            final=json.loads(state.read_text())
            self.assertEqual(set(final['static']),set(initial))
            self.assertEqual(set(final['members']),set(initial))
            self.assertEqual(list(root.glob('smartdns-ipset-probe.*')),[])
            return result,final

    def test_success_restores_original_native_entries(self):
        result,final=self.run_probe()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('9.9.9.9',result.stdout)
        self.assertEqual(final['changes'],4)

    def test_failed_query_still_restores_entries(self):
        result,final=self.run_probe({'PROBE_FAIL_QUERY':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(final['changes'],4)

    def test_failed_probe_start_never_edits_native_list(self):
        result,final=self.run_probe({'PROBE_FAIL_START':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(final['changes'],0)
