"""Exercise service policy and rollback using stateful ip/iptables substitutes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/fptn-service.sh'
MOCK = r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
path=Path(os.environ['FPTN_MOCK_STATE'])
s=json.loads(path.read_text()); a=sys.argv[1:]; cmd=Path(sys.argv[0]).name
s['trace'].append([cmd,*a])
def done(code=0, output=''):
    path.write_text(json.dumps(s)); print(output, end=''); sys.exit(code)
def mutation():
    s['mutations']+=1
    if s['mutations']==int(os.environ.get('FPTN_FAIL_AT','-1')): done(2)
if cmd=='ip':
    if 'link' in a:
        done(1 if 'fptn0' in a and not Path(os.environ.get('FPTN_MOCK_MARKER','/nonexistent')).exists() else 0)
    if a[1:3]==['rule','show']:
        done(output='0: from all lookup local\n100: from all fwmark 0x1 lookup 4096\n'+''.join(f"61: from all to {n} iif br0 lookup 30091\n" for n in s['rules']))
    if a[1:3]==['route','show']:
        done(output='\n'.join(s['routes'])+ ('\n' if s['routes'] else ''))
    mutation()
    if a[1]=='rule':
        net=a[a.index('to')+1]
        if a[2]=='add':
            if net in s['rules']: done(2)
            s['rules'].append(net)
        else:
            if net not in s['rules']: done(2)
            s['rules'].remove(net)
    elif a[1]=='route':
        net='default' if 'default' in a else a[a.index('30091')+1]
        if a[2]=='add':
            if net in s['routes']: done(2)
            s['routes'].append(net)
        else:
            if net not in s['routes']: done(2)
            s['routes'].remove(net)
    else: done(99)
    done()
if cmd=='iptables':
    table=a[1]; op=a[2]; chain=a[3]; chains=s['tables'][table]
    if op=='-S': done(0 if chain in chains else 1)
    if op=='-C': done(0 if chain in chains and a[4:] in chains[chain] else 1)
    mutation()
    if op=='-N':
        if chain in chains: done(1)
        chains[chain]=[]
    elif op in ('-A','-I'):
        if chain not in chains: done(1)
        # POSTROUTING cannot match incoming interface. Catch this kernel error.
        if table=='nat' and '-i' in a: done(2)
        chains[chain].append(a[5:] if op=='-I' else a[4:])
    elif op=='-D':
        if a[4:] not in chains.get(chain,[]): done(1)
        chains[chain].remove(a[4:])
    elif op=='-F': chains[chain]=[]
    elif op=='-X':
        if chains[chain] or any(['-j',chain] in rules for key,rules in chains.items() if key!=chain): done(1)
        del chains[chain]
    else: done(99)
    done()
done(99)
'''


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_path = self.root / 'state.json'
        self.initial = {'rules': [], 'routes': [], 'tables': {
            'filter': {'FORWARD': [['-j','KEENETIC']], 'KEENETIC': [['-j','RETURN']]},
            'nat': {'POSTROUTING': [['-j','KEENETIC']], 'KEENETIC': [['-j','RETURN']]}},
            'mutations': 0, 'trace': []}
        self.state_path.write_text(json.dumps(self.initial))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        p=self.bin/'id'; p.write_text('#!/bin/sh\necho 0\n'); p.chmod(0o700)
        real_cat=shutil.which('cat')
        p=self.bin/'cat'; p.write_text('#!/bin/sh\nif [ "$*" = /proc/sys/net/ipv4/ip_forward ]; then echo 1; else exec '+real_cat+' "$@"; fi\n'); p.chmod(0o700)
        for name in ['ip','iptables']:
            p=self.bin/name; p.write_text(MOCK); p.chmod(0o700)
        self.env = {**os.environ, 'FPTN_PREFIX': str(self.root/'opt'),
                    'FPTN_MOCK_STATE':str(self.state_path), 'PATH':str(self.bin)+':'+os.environ['PATH']}

    def shell(self, body, extra=None):
        # Load the actual function definitions, excluding CLI dispatch.
        definitions=SCRIPT.read_text().rsplit('\nneed_root\n',1)[0]
        return subprocess.run(['sh','-c', definitions+'\n'+body, str(SCRIPT)],
            env={**self.env, **(extra or {})}, text=True, capture_output=True, timeout=20)

    def state(self): return json.loads(self.state_path.read_text())

    def test_normalizes_networks_and_ignores_duplicates_comments(self):
        result=self.shell("printf '%s\\n' '8.8.8.9/24,1.1.1.1 # x' '8.8.8.0/24' | normalize_cidrs")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'8.8.8.0/24\n1.1.1.1/32\n')

    def test_rejects_private_broad_invalid_and_injected_cidrs(self):
        for net in ['192.168.1.0/24','10.0.0.0/8','100.64.0.0/10','127.0.0.1','169.254.1.1',
                    '172.16.0.0/12','224.0.0.0/4','8.0.0.0/4','0.0.0.0/0','8.8.8.8/33',
                    '8.8.8.999','8.8.8.8/','8.8.8.8;id']:
            with self.subTest(net=net):
                result=self.shell('printf "%s\\n" "$NET" | normalize_cidrs',{'NET':net})
                self.assertNotEqual(result.returncode,0)

    def test_apply_and_cleanup_preserve_existing_firewall(self):
        result=self.shell('mkdir -p "$RUN"; printf "8.8.8.0/24\\n1.1.1.1/32\\n" > "$RUN/cidrs"; apply_network; cleanup_network; cleanup_network')
        self.assertEqual(result.returncode,0,result.stderr)
        state=self.state()
        self.assertEqual(state['rules'],[]); self.assertEqual(state['routes'],[])
        self.assertEqual(state['tables'],self.initial['tables'])
        writes=[a for a in state['trace'] if a[:4]==['ip','-4','rule','add']]
        self.assertEqual(len(writes),2)
        self.assertTrue(all('br0' in a and '30091' in a for a in writes))
        self.assertFalse(any('flush' in a or 'replace' in a for a in state['trace']))

    def test_failed_setup_at_each_mutation_rolls_back_only_own_changes(self):
        # One destination: 10 mutations including default, chains and jumps.
        for step in range(1,11):
            with self.subTest(step=step):
                self.state_path.write_text(json.dumps(self.initial))
                run=self.root/'opt/var/run/fptn-keenetic'
                shutil.rmtree(run,ignore_errors=True)
                result=self.shell('mkdir -p "$RUN"; echo 1.1.1.1/32 > "$RUN/cidrs"; trap cleanup_network EXIT; apply_network',
                                  {'FPTN_FAIL_AT':str(step)})
                self.assertNotEqual(result.returncode,0)
                state=self.state()
                self.assertEqual(state['rules'],[])
                self.assertEqual(state['routes'],[])
                self.assertEqual(state['tables'],self.initial['tables'])

    def test_preexisting_chain_is_refused_without_mutation(self):
        state=self.initial.copy(); state['tables']['filter']['FPTN_KN_F']=[]
        self.state_path.write_text(json.dumps(state))
        result=self.shell('preflight')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.state()['mutations'],0)
        self.assertIn('Цепочка FPTN_KN_F', result.stderr)

    def test_stale_pid_identity_is_not_alive(self):
        result=self.shell('mkdir -p "$RUN"; echo "1 999999999999" > "$RUN/supervisor.pid"; alive "$RUN/supervisor.pid"')
        self.assertNotEqual(result.returncode,0)

    def test_configure_menu_keeps_secrets_private_and_does_not_change_network(self):
        root=self.root/'opt'; lib=root/'libexec/fptn'; conf=root/'etc/fptn'
        lib.mkdir(parents=True); conf.mkdir(parents=True)
        brotli=lib/'brotli'; brotli.write_text('#!/bin/sh\ncat\n'); brotli.chmod(0o700)
        for name,content in {'stty':'#!/bin/sh\n[ "$1" != -g ] || echo saved\nexit 0\n',
                             'curl':'#!/bin/sh\nprintf 8.8.8.8\n'}.items():
            path=self.bin/name; path.write_text(content); path.chmod(0o700)
        token='fptnb:'+base64.b64encode(json.dumps({'password':'secret-password','servers':[{'name':'NL'},{'name':'CZ'}]}).encode()).decode()
        result=subprocess.run(['sh',str(SCRIPT),'configure'],input=token+'\n2\n\n',text=True,capture_output=True,env=self.env,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((conf/'server').read_text(),'CZ\n')
        self.assertEqual((conf/'cidrs').read_text(),'8.8.8.8/32\n')
        self.assertEqual((conf/'token').stat().st_mode&0o777,0o600)
        self.assertEqual(conf.stat().st_mode&0o777,0o700)
        self.assertNotIn(token,result.stdout+result.stderr)
        self.assertNotIn('secret-password',result.stdout+result.stderr)
        self.assertEqual(self.state()['mutations'],0)


    def test_cleanup_failure_can_be_retried_from_same_journal(self):
        result=self.shell('mkdir -p "$RUN"; echo 1.1.1.1/32 > "$RUN/cidrs"; apply_network; if cleanup_network; then exit 99; fi; cleanup_network',
                          {'FPTN_FAIL_AT':'11'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.state()['rules'],[])
        self.assertEqual(self.state()['routes'],[])
        self.assertEqual(self.state()['tables'],self.initial['tables'])

    def test_real_supervisor_start_status_and_stop_with_mock_elf_client(self):
        cc=shutil.which('cc')
        if not cc: self.skipTest('native C compiler unavailable')
        root=self.root/'opt'; lib=root/'libexec/fptn'; conf=root/'etc/fptn'
        lib.mkdir(parents=True); conf.mkdir(parents=True)
        code=self.root/'client.c'
        code.write_text(r'''#include <stdio.h>
#include <signal.h>
#include <unistd.h>
#include <stdlib.h>
volatile sig_atomic_t stopped=0;
void stop(int sig) { stopped=1; }
int main(void) {
  signal(SIGTERM,stop); signal(SIGINT,stop);
  const char *marker=getenv("FPTN_MOCK_MARKER");
  FILE *f=fopen(marker,"w"); if(!f) return 2; fclose(f);
  puts("Connection #1 READY"); puts("Connection #2 READY"); fflush(stdout);
  while(!stopped) sleep(1);
  unlink(marker); return 0;
}
''')
        subprocess.run([cc,str(code),'-o',str(lib/'fptn-client-cli')],check=True,capture_output=True)
        (lib/'brotli').write_text('not used')
        (lib/'SHA256SUMS').write_text(''.join(hashlib.sha256((lib/f).read_bytes()).hexdigest()+'  '+f+'\n' for f in ['fptn-client-cli','brotli']))
        for f,data in {'token':'secret-token','server':'CZ','cidrs':'1.1.1.1/32'}.items(): (conf/f).write_text(data+'\n')
        # A main-table gateway is required by the actual supervisor.
        mock=(self.bin/'ip').read_text().replace("    mutation()\n    if a[1]=='rule':", "    if a[1:3]==['route','show']: done(output='default via 100.64.0.2 dev eth2.2\\n')\n    mutation()\n    if a[1]=='rule':")
        # Existing route-show branch needs to distinguish main from test table.
        mock=mock.replace("if a[1:3]==['route','show']:","if a[1:3]==['route','show'] and '30091' in a:",1)
        (self.bin/'ip').write_text(mock)
        env={**self.env,'FPTN_MOCK_MARKER':str(self.root/'client-marker')}
        def command(cmd):
            return subprocess.run(['sh',str(SCRIPT),cmd],env=env,text=True,capture_output=True,timeout=25)
        try:
            result=command('start')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('служба запущена',result.stdout)
            self.assertNotIn('secret-token',result.stdout+result.stderr)
            self.assertEqual(self.state()['rules'],['1.1.1.1/32'])
            self.assertIn('служба запущена',command('status').stdout)
        finally:
            stopped=command('stop')
        self.assertEqual(stopped.returncode,0,stopped.stdout+stopped.stderr)
        self.assertFalse((root/'var/run/fptn-keenetic').exists())
        self.assertEqual(self.state()['tables'],self.initial['tables'])
        self.assertEqual(self.state()['rules'],[])


    def test_preexisting_priority_or_table_is_refused_without_writes(self):
        for occupied in ['rules','routes']:
            with self.subTest(occupied=occupied):
                state=json.loads(json.dumps(self.initial))
                state[occupied]=['9.9.9.9/32']
                self.state_path.write_text(json.dumps(state))
                result=self.shell('preflight')
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(self.state()['mutations'],0)
                self.assertEqual(self.state()[occupied],['9.9.9.9/32'])
