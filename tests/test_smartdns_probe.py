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
    s['native_edits']+=1; p.write_text(json.dumps(s)); sys.exit(99)
elif command=='nslookup':
    assert sys.argv[1:]==['api.ipify.org','127.0.0.1:15353']
    s['queries']+=1; p.write_text(json.dumps(s))
    if os.environ.get('PROBE_FAIL_QUERY')=='1': sys.exit(2)
    if os.environ.get('PROBE_SKIP_IPSET')!='1':
        s['sets'][next(iter(s['sets']))].append('9.9.9.9'); p.write_text(json.dumps(s))
    print('Name: api.ipify.org\nAddress 1: 9.9.9.9')
elif command=='ipset':
    op,name=sys.argv[1:3]; assert name.startswith('fptn_probe_4_@')
    if op=='list':
        if os.environ.get('PROBE_COLLISION')=='1': sys.exit(0)
        if name not in s['sets']: sys.exit(1)
        print('Name: '+name+'\nMembers:\n'+'\n'.join(s['sets'][name]))
    elif op=='create':
        assert name not in s['sets']
        s['sets'][name]=['9.9.9.9'] if os.environ.get('PROBE_PREPOPULATE')=='1' else []
    elif op=='destroy': del s['sets'][name]
    elif op=='save':
        print('create '+name+' hash:net family inet hashsize 64 maxelem 64')
        for ip in s['sets'][name]: print('add '+name+' '+ip)
    else: sys.exit(99)
    p.write_text(json.dumps(s))
elif command=='netstat': print('Active Internet connections')
elif command=='id': print('0')
'''

class SmartDNSProbeTest(unittest.TestCase):
    def run_probe(self,extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); commands=root/'bin'; commands.mkdir()
            state=root/'state'; initial=['8.6.112.0','8.47.69.0']
            state.write_text(json.dumps({'native':initial,'native_edits':0,'sets':{},'queries':0}))
            for name in ['smartdns','ndmc','nslookup','ipset','netstat','id']:
                path=commands/name; path.write_text(MOCK); path.chmod(0o700)
            result=subprocess.run(['sh',str(SCRIPT)],text=True,capture_output=True,timeout=10,
                env={**os.environ,'PATH':str(commands)+':'+os.environ['PATH'],'TMPDIR':str(root),
                     'PROBE_MOCK_STATE':str(state),**(extra or {})})
            final=json.loads(state.read_text())
            self.assertEqual(final['native'],initial)
            self.assertEqual(final['native_edits'],0)
            self.assertEqual(final['sets'],{})
            self.assertEqual(list(root.glob('smartdns-ipset-probe.*')),[])
            return result,final

    def test_success_requires_new_answer_in_previously_empty_set(self):
        result,final=self.run_probe()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('PASS:',result.stdout)
        self.assertEqual(final['queries'],1)

    def test_failed_query_deletes_only_own_temporary_set(self):
        result,final=self.run_probe({'PROBE_FAIL_QUERY':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('PASS:',result.stdout)

    def test_failed_probe_start_does_not_touch_native_set(self):
        result,final=self.run_probe({'PROBE_FAIL_START':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(final['queries'],0)

    def test_answer_without_kernel_write_is_not_success(self):
        result,final=self.run_probe({'PROBE_SKIP_IPSET':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('PASS:',result.stdout)
        self.assertIn('не подтверждена',result.stderr)

    def test_prepopulated_set_cannot_produce_false_success(self):
        result,final=self.run_probe({'PROBE_PREPOPULATE':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(final['queries'],0)
        self.assertIn('не пуст',result.stderr)

    def test_name_collision_aborts_before_query(self):
        result,final=self.run_probe({'PROBE_COLLISION':'1'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(final['queries'],0)
