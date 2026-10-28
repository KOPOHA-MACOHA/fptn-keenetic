"""Accelerated lifecycle soak; tests supervisor leaks, not live VPN throughput."""
import json,os,time,unittest
from pathlib import Path
import test_wan_failover
class WanSoakTest(unittest.TestCase):
    setUp=test_wan_failover.WanFailoverTest.setUp
    cmd=test_wan_failover.WanFailoverTest.cmd
    prepare_native_client=test_wan_failover.WanFailoverTest.prepare_native_client
    wait_for=test_wan_failover.WanFailoverTest.wait_for
    prepare=test_wan_failover.WanFailoverTest.prepare
    save=test_wan_failover.WanFailoverTest.save
    def test_twenty_dhcp_reconnects_keep_one_child_and_bounded_supervisor_resources(self):
        root=self.prepare()
        sleeper=self.bin/'sleep';sleeper.write_text('#!/usr/bin/env python3\nimport sys,time\ntime.sleep(.05 if sys.argv[1]=="1" else .02)\n');sleeper.chmod(0o700)
        result=self.cmd('start');self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        run=root/'var/run/fptn-keenetic';supervisor=int((run/'supervisor.pid').read_text().split()[0]);fdroot=Path('/proc')/str(supervisor)/'fd'
        initial=len(list(fdroot.iterdir()));baseline_rss=int(next(line.split()[1] for line in (fdroot.parent/'status').read_text().splitlines() if line.startswith('VmRSS:')))
        records=[]
        for index in range(20):
            old=(run/'client.pid').read_text();records.append(old)
            self.snapshot['addresses']['wan_a']=f'2: wan_a inet 192.0.2.{index+20}/24 scope global wan_a';self.save()
            self.wait_for(lambda:(run/'client.pid').exists() and (run/'client.pid').read_text()!=old and (run/'ready').exists(),timeout=8)
            self.assertEqual(int((run/'supervisor.pid').read_text().split()[0]),supervisor)
            self.assertLessEqual(len(list(fdroot.iterdir())),initial+4)
            rss=int(next(line.split()[1] for line in (fdroot.parent/'status').read_text().splitlines() if line.startswith('VmRSS:')))
            self.assertLessEqual(rss,baseline_rss+4096)
        self.assertEqual(self.cmd('stop').returncode,0);self.assertFalse(run.exists())
        for record in records:
            pid,stamp,*_=record.split();stat=Path('/proc')/pid/'stat'
            if stat.exists():self.assertNotEqual(stat.read_text().rsplit(') ',1)[1].split()[19],stamp)
