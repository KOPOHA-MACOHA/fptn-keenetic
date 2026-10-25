"""Validate the binary XML produced by the shell writer, not router acceptance."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/probe-smartdns-native-writer.sh'
MOCK=r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
root=Path(os.environ['WRITER_TEST_ROOT']); name=Path(sys.argv[0]).name
if name=='id': print('0')
elif name=='ndmc':
    assert sys.argv[1:]==['-c','show object-group fqdn domain-list9']
    with (root/'reads').open('a') as f: f.write('show\n')
elif name=='nslookup':
    assert sys.argv[1:]==['api.ipify.org','127.0.0.1']
    if os.environ.get('WRITER_BAD_ANSWER'):
        print('Server: 127.0.0.1\nAddress 1: 127.0.0.1\nName: api.ipify.org\nAddress 1: 999.1.2.3')
    else:
        print('Server: 127.0.0.1\nAddress 1: 127.0.0.1\nName: api.ipify.org\nAddress 1: 8.47.69.0\nAddress 2: 8.6.112.0')
elif name=='socat':
    assert sys.argv[1:]==['-u','STDIN','UNIX-SENDTO:'+str(root/'native.sock')]
    (root/'sent.bin').write_bytes(sys.stdin.buffer.read())
'''


def decode_document(data):
    """Independent tree reader for libndm's node/sibling/attribute/end encoding."""
    pos=0; nodes=[]; current=None; root=None
    def string():
        nonlocal pos
        if pos+4>len(data): raise ValueError('truncated length')
        length=int.from_bytes(data[pos:pos+4],'big'); pos+=4
        if pos+length>len(data): raise ValueError('truncated string')
        value=data[pos:pos+length].decode('ascii'); pos+=length
        return value
    while pos<len(data):
        control=data[pos]>>6; kind=data[pos]&63; pos+=1
        if control in (0,2):
            name,value=string(),string()
            parent=current if control==0 else current['parent']
            node=dict(kind=kind,name=name,value=value,parent=parent,children=[])
            if parent is not None: parent['children'].append(node)
            if root is None: root=node
            current=node; nodes.append(node)
        elif control==1:
            raise ValueError('attributes not expected in mirror fixture')
        else:
            stop=current is root or current['parent'] is root
            current=current['parent']
            if stop: break
    if pos!=len(data): raise ValueError('unconsumed bytes')
    for node in nodes: node.pop('parent')
    return root


class NativeWriterTest(unittest.TestCase):
    def run_writer(self, bad=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); commands=root/'bin'; commands.mkdir()
            for name in ['id','ndmc','nslookup','socat']:
                p=commands/name; p.write_text(MOCK); p.chmod(0o700)
            (root/'native.sock').write_bytes(b'live socket sentinel')
            script=SCRIPT.read_text().replace('/opt/tmp',str(root))
            script=script.replace('/var/run/fqdn-dns-mirror.sock',str(root/'native.sock'))
            script=script.replace('-S "$NATIVE_SOCKET"','-f "$NATIVE_SOCKET"')
            local=root/'probe.sh'; local.write_text(script)
            result=subprocess.run(['sh',str(local)],text=True,capture_output=True,timeout=10,
                env={**os.environ,'PATH':str(commands)+':'+os.environ['PATH'],
                     'WRITER_TEST_ROOT':str(root),'WRITER_BAD_ANSWER':'1' if bad else ''})
            self.assertEqual((root/'native.sock').read_bytes(),b'live socket sentinel')
            self.assertEqual(list(root.glob('fqdn-writer.*')),[])
            sent=root/'sent.bin'
            return result,sent.read_bytes() if sent.exists() else b''

    def test_full_reply_tree_has_fresh_answer_addresses_ttl_and_no_qid(self):
        result,data=self.run_writer()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        doc=decode_document(data)
        self.assertEqual(doc['kind'],0)
        reply=doc['children'][0]
        self.assertEqual(reply['name'],'reply')
        expected=[]
        for section in reply['children']:
            self.assertEqual(section['name'],'section')
            self.assertEqual(section['children'][0]['value'],'api.ipify.org')
            ip=section['children'][1]
            self.assertEqual(ip['name'],'ipv4')
            self.assertEqual([(x['name'],x['value']) for x in ip['children']][1],('t','3'))
            expected.append(ip['children'][0]['value'])
        self.assertEqual(expected,['8.47.69.0','8.6.112.0'])
        self.assertNotIn(b'qid',data)
        self.assertNotIn(b'127.0.0.1',data)

    def test_invalid_answer_does_not_send_native_message(self):
        result,data=self.run_writer(bad=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data,b'')
