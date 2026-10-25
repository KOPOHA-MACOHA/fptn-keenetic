import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CPP = r'''
#include "keenetic-server-policy.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <map>
#include <set>
#include <string>
#include <thread>
struct Server {std::string name; int ms; bool works=true;};
struct Result {std::string name;};
int main(int argc,char**argv){
  using namespace fptn::keenetic;
  assert(argc==2);assert(WritePrivateJson(argv[1],"{\"state\":\"connected\"}"));
  struct stat info{};assert(stat(argv[1],&info)==0);assert((info.st_mode&0777)==0600);
  std::mutex mutex;std::set<std::string> calls;std::atomic<int> active{0},peak{0};
  auto probe=[&](const Server& s)->std::optional<Result>{
    int n=++active;int previous=peak.load();while(n>previous&&!peak.compare_exchange_weak(previous,n)){}
    {std::lock_guard<std::mutex> lock(mutex);calls.insert(s.name);}
    std::this_thread::sleep_for(std::chrono::milliseconds(s.ms));--active;
    if(s.name=="throws") throw std::runtime_error("unavailable");
    if(!s.works)return std::nullopt;
    return Result{s.name};
  };
  std::vector<Server> servers{{"normal",1},{"fav-slow",40},{"fav-fast",8},{"excluded",1}};
  auto result=SelectServer<Result>(ServerTiers(servers,{"fav-slow","fav-fast","excluded"},{" EXCLUDED "},{},""),probe);
  assert(result&&result->name=="fav-fast");assert((calls==std::set<std::string>{"fav-slow","fav-fast"}));assert(peak<=2);
  calls.clear();servers[1].works=false;servers[2].works=false;
  result=SelectServer<Result>(ServerTiers(servers,{"fav-slow","fav-fast"},{"excluded"},{},""),probe);
  assert(result&&result->name=="normal");assert(!calls.contains("excluded"));assert(calls.size()==3);
  calls.clear();servers[1].works=true;servers[2].works=true;
  result=SelectServer<Result>(ServerTiers(servers,{"fav-slow","fav-fast"},{"excluded"},{"fav-fast"},""),probe);
  assert(result&&result->name=="fav-slow");assert(calls.size()==1);
  calls.clear();servers[0].works=false;
  result=SelectServer<Result>(ServerTiers(servers,{"fav-fast"},{"excluded"},{},"normal"),probe);
  assert(result&&result->name=="fav-fast");assert((calls==std::set<std::string>{"normal","fav-fast"}));
  calls.clear();
  std::vector<Server> many{{"one",30},{"two",30},{"three",30},{"four",30},{"five",30},{"fastest-last",2},{"throws",2}};
  result=SelectServer<Result>(ServerTiers(many,{}, {},{},""),probe);
  assert(result&&result->name=="fastest-last");assert(calls.size()==7);assert(peak<=2);
  assert(ServerTiers(servers,{}, {"normal","fav-slow","fav-fast","excluded"},{},"").empty());
  std::vector<Server> excessive;for(int i=0;i<65;i++)excessive.push_back({std::to_string(i),1});
  bool refused=false;try{ServerTiers(excessive,{}, {},{},"");}catch(const std::runtime_error&){refused=true;}assert(refused);
  LossWindow loss;const auto now=std::chrono::steady_clock::now();
  assert(!loss.Expired(false,now));assert(!loss.Expired(true,now));
  assert(!loss.Expired(true,now+std::chrono::seconds(14)));assert(loss.Expired(true,now+std::chrono::seconds(15)));
  assert(!loss.Expired(false,now+std::chrono::seconds(16)));assert(!loss.Expired(true,now+std::chrono::seconds(17)));
  assert(!loss.Expired(true,now+std::chrono::seconds(30)));assert(loss.Expired(true,now+std::chrono::seconds(32)));
}
'''


class ServerPolicyTest(unittest.TestCase):
    def test_real_cpp_selection_priorities_exclusion_concurrency_and_loss_window(self):
        compiler=shutil.which('g++')
        if not compiler:self.skipTest('g++ unavailable')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'duplicate.h').write_text((ROOT/'src/keenetic-server-policy.h').read_text())
            (root/'test.cpp').write_text(CPP.replace('#include "keenetic-server-policy.h"', '#include "keenetic-server-policy.h"\n#include "duplicate.h"'))
            compiled=subprocess.run([compiler,'-std=c++20','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'src'),str(root/'test.cpp'),'-o',str(root/'test')],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            result=subprocess.run([str(root/'test'),str(root/'report.json')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_changed_upstream_is_rejected_before_writes(self):
        spec=importlib.util.spec_from_file_location('serverpolicy',ROOT/'scripts/prepare-server-policy.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cli=root/'src/fptn-client/fptn-client-cli.cpp';cli.parent.mkdir(parents=True)
            loop=root/'src/fptn-client/utils/signal/main_loop.h';loop.parent.mkdir(parents=True)
            cli.write_text('changed upstream');loop.write_text('unchanged loop')
            with self.assertRaises(ValueError):module.prepare(root,ROOT/'src/keenetic-server-policy.h')
            self.assertEqual(cli.read_text(),'changed upstream');self.assertEqual(loop.read_text(),'unchanged loop')
