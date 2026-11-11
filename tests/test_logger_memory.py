"""Compare actual upstream logger before/after, using real spdlog and private files.

Tools and fixtures under tests/ are excluded from installed runtime packages.
Local: FPTN_SPDLOG_INCLUDE=/path/to/spdlog/include python3 -m unittest discover -s tests -p test_logger_memory.py -v
"""
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('transport_patch',ROOT/'scripts/prepare-native-transport.py')
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
FIXTURE=ROOT/'tests/fixtures/logger/logger.h'


class LoggerPatchTest(unittest.TestCase):
    def test_unknown_or_already_changed_queue_fails_closed(self):
        text=FIXTURE.read_text()
        for candidate in [text.replace('kLogQueueSize = 32768','kLogQueueSize = 8192'),text+text,patch.compact_client_logger(text)]:
            with self.assertRaises(ValueError):patch.compact_client_logger(candidate)


class LoggerMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.include=Path(os.environ.get('FPTN_SPDLOG_INCLUDE','/usr/include'))
        if not (cls.include/'spdlog/async.h').exists() or not shutil.which('g++'):
            raise unittest.SkipTest('Host spdlog development headers required in Actions')
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        original=FIXTURE.read_text()
        variants={'before':original,'after':patch.compact_client_logger(original)}
        for name,text in variants.items():
            # Only redirect the test sink; all formatter, async queue and shutdown code is actual upstream.
            old='const std::filesystem::path log_dir = "/var/log/fptn/";'
            if text.count(old)!=1:raise AssertionError('logger fixture log directory changed')
            text=text.replace(old,'const std::filesystem::path log_dir = std::string(getenv("FPTN_BENCH_LOG_DIR"));')
            (cls.root/(name+'.h')).write_text(text)
            cmd=['g++','-std=c++20','-O2','-pthread','-I'+str(cls.include),'-I'+str(cls.root),f'-DLOGGER_HEADER="{name}.h"',str(ROOT/'tests/benchmark_logger_memory.cpp'),'-o',str(cls.root/name)]
            if (cls.include/'spdlog/tweakme.h').exists() and re.search(r'^\s*#\s*define\s+SPDLOG_FMT_EXTERNAL(?:\s|$)',(cls.include/'spdlog/tweakme.h').read_text(),re.M):cmd+=['-lfmt']
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            if result.returncode:raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'tmp'):cls.tmp.cleanup()

    def run_case(self,variant,app='fptn-client-cli'):
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([str(self.root/variant),app],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,env={**os.environ,'FPTN_BENCH_LOG_DIR':folder},timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            row=json.loads(result.stderr)
            self.assertEqual(row['recorded'],512)
            self.assertEqual(row['dropped'],0)
            return row

    def test_client_rss_reduction_real_logger_and_clean_shutdown(self):
        rows={name:[self.run_case(name) for _ in range(3)] for name in ['before','after']}
        for before,after in zip(rows['before'],rows['after']):
            # Ignore process baseline/ASLR variation; compare eager resident growth.
            old=before['initializedRssKiB']-before['beforeRssKiB']
            new=after['initializedRssKiB']-after['beforeRssKiB']
            self.assertGreater(old-new,4096)
        print('LOGGER_MEMORY_RESULT '+json.dumps(rows,sort_keys=True),flush=True)

    def test_other_programs_keep_original_queue(self):
        before=self.run_case('before','fptn-server');after=self.run_case('after','fptn-server')
        self.assertLess(abs((before['initializedRssKiB']-before['beforeRssKiB'])-(after['initializedRssKiB']-after['beforeRssKiB'])),2048)
