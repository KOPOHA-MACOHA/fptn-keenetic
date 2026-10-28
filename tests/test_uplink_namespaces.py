"""Exercise real Linux routes/addresses entirely inside disposable network namespaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]


class KernelUplinkTest(unittest.TestCase):
    def test_route_failover_dhcp_renewal_and_gateway_free_link(self):
        if os.geteuid()!=0 or not shutil.which('ip'):
            self.skipTest('Root/iproute2 required in Actions')
        name='fptn-test-'+uuid.uuid4().hex[:10]
        created=subprocess.run(['ip','netns','add',name],capture_output=True,text=True)
        if created.returncode:
            if os.environ.get('FPTN_REQUIRE_NETNS')=='1':self.fail(created.stderr)
            self.skipTest('Network namespaces unavailable: '+created.stderr)
        self.addCleanup(lambda:subprocess.run(['ip','netns','del',name],capture_output=True))
        def ip(*args):subprocess.run(['ip','-n',name,*args],check=True,capture_output=True)
        def discover(selected=''):
            result=subprocess.run(['ip','netns','exec',name,'sh',str(ROOT/'scripts/fptn-uplink.sh'),selected],text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        ip('link','set','lo','up')
        for iface,address in [('wan_a','192.0.2.2/24'),('wan_b','198.51.100.2/24')]:
            ip('link','add',iface,'type','dummy');ip('addr','add',address,'dev',iface);ip('link','set',iface,'up')
        ip('route','add','default','via','192.0.2.1','dev','wan_a','metric','10')
        ip('route','add','default','via','198.51.100.1','dev','wan_b','metric','20')
        first=discover();self.assertEqual(first['interface'],'wan_a')
        ip('link','set','wan_a','down')
        second=discover();self.assertEqual(second['interface'],'wan_b');self.assertNotEqual(second['signature'],first['signature'])
        ip('addr','del','198.51.100.2/24','dev','wan_b');ip('addr','add','198.51.100.3/24','dev','wan_b')
        ip('route','replace','default','via','198.51.100.1','dev','wan_b','metric','20')
        self.assertEqual(discover()['address'],'198.51.100.3')
        ip('route','del','default','via','198.51.100.1','dev','wan_b','metric','20')
        ip('route','add','default','dev','wan_b','metric','1')
        data=discover();self.assertEqual(data['gateway'],'');self.assertEqual(data['interface'],'wan_b')
