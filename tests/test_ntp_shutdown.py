"""Compile the patched pinned TimeProvider with a delayed local NTP substitute."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
NTP=r'''#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <optional>
#include <string>
#include <thread>
namespace ntp {
inline std::atomic<bool> started=false;
class NTPClient {public:
 NTPClient(const std::string&,std::uint16_t){}
 std::uint64_t request_time(){started=true;std::this_thread::sleep_for(std::chrono::milliseconds(25));return 0;}
};}
'''
LOGGER=r'''#pragma once
#include <atomic>
#include <cassert>
inline std::atomic<bool> logger_closed=false;
inline std::atomic<int> messages=0;
template<class... Args> void Log(Args&&...){assert(!logger_closed);++messages;}
#define SPDLOG_INFO(...) Log(__VA_ARGS__)
#define SPDLOG_WARN(...) Log(__VA_ARGS__)
#define SPDLOG_ERROR(...) Log(__VA_ARGS__)
'''
MAIN=r'''
#include "fptn-protocol-lib/time/time_provider.h"
#include <ntp_client.hpp>
#include <spdlog/spdlog.h>
int main(){
 auto* provider=fptn::time::TimeProvider::Instance();
 while(!ntp::started)std::this_thread::yield();
 provider->Stop();
 assert(messages>0);
 logger_closed=true;
 provider->Stop(); // destructor will call Stop once more; joining is idempotent
}
'''


class NtpShutdownTest(unittest.TestCase):
    def test_background_logging_finishes_before_logger_shutdown(self):
        compiler=shutil.which('g++')
        if not compiler:self.skipTest('g++ unavailable')
        spec=importlib.util.spec_from_file_location('policy',ROOT/'scripts/prepare-server-policy.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fixture=ROOT/'tests/fixtures/ntp'
        header,source=module.patch_ntp((fixture/'time_provider.h').read_text(),(fixture/'time_provider.cpp').read_text())
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for path,text in {'fptn-protocol-lib/time/time_provider.h':header,'time.cpp':source,'ntp_client.hpp':NTP,'spdlog/spdlog.h':LOGGER,'main.cpp':MAIN}.items():
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            compiled=subprocess.run([compiler,'-std=c++20','-pthread','-Wall','-Wextra','-Werror','-I'+str(root),str(root/'time.cpp'),str(root/'main.cpp'),'-o',str(root/'test')],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            result=subprocess.run([str(root/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
