"""Run the actual passive awk reader against deterministic proc snapshots and Linux."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'scripts/fptn-native-service.sh'


def stat(pid, start, ticks, state='S'):
    fields = [state] + ['0'] * 21
    fields[11], fields[12], fields[19] = str(ticks), '0', str(start)
    return f'{pid} (odd ) process name) ' + ' '.join(fields) + '\n'


class ResourceTests(unittest.TestCase):
    def fixture(self, restart=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc, run = root/'proc', root/'run'
            run.mkdir()
            (run/'client.pid').write_text('123 91 boot\n')
            (run/'supervisor.pid').write_text('456 92 boot\n')
            for i in range(3):
                frame = root/f'frame{i}'
                (frame/'sys/kernel/random').mkdir(parents=True)
                (frame/'sys/kernel/random/boot_id').write_text('boot\n')
                (frame/'uptime').write_text(f'{10+i*2}.0 0\n')
                # 100 total ticks per frame; guest ticks must never be counted twice.
                (frame/'stat').write_text(f'cpu {20+i*20} 0 0 {80+i*80} 0 0 0 0 90000 90000\n')
                (frame/'meminfo').write_text(f'MemAvailable: {50000-i*100} kB\n')
                for pid, start, ticks in [(123, 91, i*5), (456, 92, i)]:
                    path = frame/str(pid);path.mkdir()
                    if restart and i == 1 and pid == 123:
                        start = 999  # A recycled PID is not the recorded FPTN process.
                    (path/'stat').write_text(stat(pid,start,ticks))
                    (path/'status').write_text(f'VmRSS: {18000+i*10} kB\n')
                    (path/'cmdline').write_text('SECRET_TOKEN_NOT_FOR_REPORT')
            shutil.copytree(root/'frame0',proc)
            bindir=root/'bin';bindir.mkdir()
            (root/'step').write_text('0')
            (bindir/'sleep').write_text(f'''#!/bin/sh
read -r step < '{root}/step'
step=$((step+1))
cp -r '{root}/frame'"$step"/. '{proc}/'
printf '%s\\n' "$step" > '{root}/step'
''')
            (bindir/'sleep').chmod(0o700)
            reader=SERVICE.read_text().split('# BEGIN PASSIVE RESOURCE READER\n',1)[1].split('# END PASSIVE RESOURCE READER',1)[0]
            reader=reader.replace('/proc/',str(proc)+'/')
            result=subprocess.run(['sh','-c','RUN="$1"; DURATION=4; INTERVAL=2\n'+reader,'test',str(run)],
                env={**os.environ,'PATH':str(bindir)+':'+os.environ['PATH']},capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn('SECRET',result.stdout)
            return result.stdout

    def test_cpu_guest_accounting_rss_and_weighted_summary(self):
        out=self.fixture()
        self.assertIn('общий CPU: средний 20.0% / пик 20.0%',out)
        self.assertIn('client: CPU 5.0% / пик 5.0%; максимум RSS 18020 КиБ; процессов 1',out)
        self.assertIn('supervisor: CPU 1.0% / пик 1.0%',out)
        self.assertIn('минимум MemAvailable 49800 КиБ',out)

    def test_recycled_pid_is_not_measured_and_first_interval_is_skipped(self):
        out=self.fixture(restart=True)
        self.assertIn('client: CPU н/д / пик н/д;',out)
        self.assertIn('процессов 1',out)

    def test_invalid_arguments_fail_without_creating_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args in [('0','1'),('2','0'),('3','4'),('3601','2'),('2;touch x','1'),('99999999','1')]:
                result=subprocess.run(['sh',str(SERVICE),'resources',*args],env={**os.environ,'FPTN_PREFIX':tmp},capture_output=True,text=True,timeout=3)
                self.assertNotEqual(result.returncode,0)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_live_proc_and_no_service_never_starts_or_writes_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run(['sh',str(SERVICE),'resources','2','1'],env={**os.environ,'FPTN_PREFIX':tmp},capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('[ИТОГ] общий CPU:',result.stdout)
            self.assertIn('client: CPU н/д',result.stdout)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_alive_rejects_wrong_boot_recycled_pid_and_zombie(self):
        source=SERVICE.read_text()
        functions='proc_start() {'+source.split('proc_start() {',1)[1].split('\ntrim_logs()',1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            record=Path(tmp)/'pid'
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            pid=os.getpid()
            stamp=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            def check(pid,stamp,boot):
                record.write_text(f'{pid} {stamp} {boot}\n')
                return subprocess.run(['sh','-c',functions+'\nalive "$1"','test',str(record)],capture_output=True).returncode
            self.assertEqual(check(pid,stamp,boot),0)
            self.assertNotEqual(check(pid,str(int(stamp)+1),boot),0)
            self.assertNotEqual(check(pid,stamp,'wrong-boot'),0)
            zombie=subprocess.Popen(['sh','-c','exit 0'])
            try:
                import time
                for _ in range(100):
                    data=Path(f'/proc/{zombie.pid}/stat').read_text().rsplit(') ',1)[1].split()
                    if data[0]=='Z':break
                    time.sleep(.01)
                self.assertEqual(data[0],'Z')
                self.assertNotEqual(check(zombie.pid,data[19],boot),0)
            finally:zombie.wait()


if __name__ == '__main__':
    unittest.main()
