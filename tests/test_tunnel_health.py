"""Compile the real probe state machine with socket syscalls replaced: no network."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
CPP=r'''
#include "keenetic-health.h"
#include <cassert>
#include <cstring>
using Bytes=fptn::keenetic::TunnelHealth::Bytes;
static Bytes direct;
static bool direct_ok=false;
static int opened=0,closed=0;
extern "C" int __wrap_socket(int,int,int){++opened;return 100;}
extern "C" int __wrap_setsockopt(int,int,int,const void*,socklen_t){return 0;}
extern "C" int __wrap_connect(int,const sockaddr*,socklen_t){return 0;}
extern "C" ssize_t __wrap_send(int,const void*p,size_t n,int){direct.assign((const uint8_t*)p,(const uint8_t*)p+n);return n;}
extern "C" ssize_t __wrap_recv(int,void*p,size_t,int){if(!direct_ok)return -1;auto a=direct;a[2]=0x81;a[3]=0x80;a[7]=1;a.resize(a.size()+12);memcpy(p,a.data(),a.size());return a.size();}
extern "C" int __wrap_close(int){++closed;return 0;}
static Bytes answer(Bytes a){
 for(int n=0;n<4;++n)std::swap(a[12+n],a[16+n]);
 for(int n=0;n<2;++n)std::swap(a[20+n],a[22+n]);
 a[30]=0x81;a[31]=0x80;a[35]=1;a.resize(a.size()+12);a[3]=a.size();a[25]=a.size()-20;return a;
}
int main(int argc,char**argv){
 assert(argc==2);using H=fptn::keenetic::TunnelHealth;auto now=H::Time{};
 H health;Bytes packet;int sends=0;
 health.SetSender([&](const Bytes&b){packet=b;++sends;return true;});
 health.Configure(60,5,3,"10.0.0.1","eth2.2",argv[1],now);
 assert(!health.Tick(true,now+std::chrono::seconds(59)));assert(sends==0);
 now+=std::chrono::seconds(60);assert(!health.Tick(true,now));assert(sends==1&&packet.size()==45);
 assert(packet[12]==10&&packet[15]==1&&packet[16]==1&&packet[19]==1);
 auto good=answer(packet),bad=good;bad[28]^=1;assert(!health.Receive(bad));
 bad=good;bad[22]^=1;assert(!health.Receive(bad));
 bad=good;bad[12]=8;assert(!health.Receive(bad));
 bad=good;bad[31]|=3;assert(!health.Receive(bad)); // NXDOMAIN isn't the root NS success
 bad=good;bad[6]=0x20;assert(!health.Receive(bad));
 bad=good;bad.resize(32);assert(!health.Receive(bad));
 assert(health.Receive(good));assert(!health.Tick(true,now));assert(!health.Receive(good));
 // One failed endpoint tries the other; a valid reply there resets the failure streak.
 now+=std::chrono::seconds(60);health.Tick(true,now);now+=std::chrono::seconds(5);health.Tick(true,now);
 assert(packet[16]==8&&packet[19]==8);assert(health.Receive(answer(packet)));health.Tick(true,now);
 // Three full failed cycles, including both endpoints, before checking the uplink.
 for(int n=0;n<3;++n){now+=std::chrono::seconds(60);assert(!health.Tick(true,now));now+=std::chrono::seconds(5);assert(!health.Tick(true,now));now+=std::chrono::seconds(5);assert(!health.Tick(true,now));}
 assert(opened==1);direct_ok=true;assert(health.Tick(true,now));assert(closed==1);
 // No uplink response is inconclusive, not a trigger for endless VPN failovers.
 direct_ok=false;health.Configure(30,2,2,"10.0.0.1","eth2.2",argv[1],now);
 for(int n=0;n<2;++n){now+=std::chrono::seconds(30);health.Tick(true,now);now+=std::chrono::seconds(2);health.Tick(true,now);now+=std::chrono::seconds(2);assert(!health.Tick(true,now));}
 now+=std::chrono::seconds(2);assert(!health.Tick(true,now));now+=std::chrono::seconds(2);assert(!health.Tick(true,now));
 assert(opened==3&&closed==3);
 // Disabled/reconnecting instances must not emit probes or keep a socket open.
 int before=sends;health.Configure(0,5,3,"10.0.0.1","eth2.2",argv[1],now);health.Tick(true,now+std::chrono::hours(24));assert(sends==before);
 health.Configure(60,5,3,"10.0.0.1","eth2.2",argv[1],now);health.Tick(false,now+std::chrono::seconds(90));health.Tick(true,now+std::chrono::seconds(91));assert(sends==before);
 bool denied=false;try{health.Configure(1,5,3,"10.0.0.1","eth2.2",argv[1]);}catch(...){denied=true;}assert(denied);
 denied=false;try{health.Configure(60,5,3,"10.0.0.1","opkgtun7",argv[1]);}catch(...){denied=true;}assert(denied);
}
'''
class TunnelHealthTest(unittest.TestCase):
 def test_canary_matching_timeouts_backup_threshold_uplink_and_disable(self):
  compiler=shutil.which('g++')
  if not compiler:self.skipTest('C++ compiler unavailable')
  with tempfile.TemporaryDirectory() as folder:
   r=Path(folder);(r/'test.cpp').write_text(CPP)
   build=subprocess.run([compiler,'-std=c++20','-pthread','-Wall','-Wextra','-Werror','-I'+str(ROOT/'src'),str(r/'test.cpp'),'-o',str(r/'probe'),*[f'-Wl,--wrap={s}' for s in ['socket','connect','setsockopt','send','recv','close']]],capture_output=True,text=True)
   self.assertEqual(build.returncode,0,build.stderr)
   test=subprocess.run([str(r/'probe'),str(r/'status.json')],capture_output=True,text=True,timeout=10)
   self.assertEqual(test.returncode,0,test.stdout+test.stderr)
