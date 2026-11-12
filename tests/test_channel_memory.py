"""Temporary component benchmark; no monitoring code enters the installed service."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChannelMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        boost = Path(os.environ.get('FPTN_BOOST_INCLUDE', '/usr/include'))
        spdlog = Path(os.environ.get('FPTN_SPDLOG_INCLUDE', '/usr/include'))
        if not shutil.which('g++') or not (boost/'boost/version.hpp').exists() or not (spdlog/'spdlog/spdlog.h').exists():
            raise unittest.SkipTest('Host Boost and spdlog headers are required in Actions')
        cls.tmp = tempfile.TemporaryDirectory(); cls.root = Path(cls.tmp.name)
        cls.addClassCleanup(cls.tmp.cleanup)
        for variant, header in [('before', ROOT/'tests/fixtures/channel/channel.h'),
                                ('after', ROOT/'src/common/data/channel.h')]:
            args = ['g++', '-std=c++20', '-O2', '-pthread', '-DSPDLOG_ACTIVE_LEVEL=6',
                    '-I'+str(boost), '-I'+str(spdlog), '-I'+str(ROOT/'src'),
                    '-DCHANNEL_HEADER="'+str(header)+'"', str(ROOT/'tests/benchmark_channel_memory.cpp'),
                    '-o', str(cls.root/variant)]
            tweak = spdlog/'spdlog/tweakme.h'
            if tweak.exists() and re.search(r'^\s*#\s*define\s+SPDLOG_FMT_EXTERNAL(?:\s|$)', tweak.read_text(), re.M):
                args.append('-lfmt')
            built = subprocess.run(args, text=True, capture_output=True, timeout=120)
            if built.returncode: raise AssertionError(built.stderr)

    def run_case(self, variant, scenario):
        run = subprocess.run([str(self.root/variant), scenario], text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        return json.loads(run.stdout)

    def test_batch_bounds_fifo_recovery_and_concurrent_producers(self):
        run = subprocess.run([str(self.root/'after'), 'check'], text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('PASS', run.stdout)

    def test_normal_traffic_keeps_all_packets_and_reports_cpu(self):
        rows = {'before': [], 'after': []}
        for _ in range(5):
            for variant in rows:
                row = self.run_case(variant, 'normal'); rows[variant].append(row)
                self.assertEqual(row['accepted'], 640000); self.assertEqual(row['drained'], 640000)
        # Record CPU rather than making a flaky speed promise on a shared CI runner.
        print('CHANNEL_NORMAL_RESULT '+json.dumps(rows, sort_keys=True), flush=True)

    def test_congested_queue_stays_bounded_and_uses_less_memory(self):
        rows = {'before': [], 'after': []}
        for _ in range(3):
            for variant in rows: rows[variant].append(self.run_case(variant, 'congested'))
        for before, after in zip(rows['before'], rows['after']):
            self.assertEqual(before['drained'], 20479); self.assertEqual(after['drained'], 4095)
            self.assertGreater(before['retainedRssKiB']-after['retainedRssKiB'], 16*1024)
        print('CHANNEL_CONGESTED_RESULT '+json.dumps(rows, sort_keys=True), flush=True)
