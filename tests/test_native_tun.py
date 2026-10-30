import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
STUB=r'''#pragma once
#include <string>
namespace tuntap { inline int creations=0; class tun {
 std::string value;
 public: tun(){++creations;} void name(const std::string& n){value=n;}
 const std::string& name()const{return value;} void nonblocking(bool){}
 void ip(const std::string&,int){} void mtu(int){} void up(){}
 int read(void*,std::size_t){return 0;} int write(void*,std::size_t n){return n;}
 int native_handle()const{return -1;}
};}
'''
CPP=r'''
#include "keenetic-linux-tun.h"
#include <cassert>
#include <cstdarg>
#include <cstdio>
static bool exists=true, fail_attach=false, replaced=false, multiqueue=false;
static int opens=0, closes=0, attaches=0;
extern "C" unsigned __wrap_if_nametoindex(const char*) noexcept {
 return exists ? (replaced && attaches ? 10 : 9) : 0;
}
extern "C" int __wrap_open(const char* path,int flags,...) {
 assert(std::string(path)=="/dev/net/tun");
 assert((flags&O_NONBLOCK)!=0 && (flags&O_CLOEXEC)!=0); ++opens; return 10;
}
extern "C" int __wrap_close(int fd) {assert(fd==10 || fd==11); if(fd==10) ++closes; return 0;}
extern "C" int __wrap_socket(int,int,int) noexcept {return 11;}
extern "C" int __wrap_fcntl(int,int,...) {return 0;}
extern "C" int __wrap_ioctl(int fd,unsigned long request,...) noexcept {
 va_list ap; va_start(ap,request); auto* r=va_arg(ap,ifreq*); va_end(ap);
 assert(std::string(r->ifr_name)=="opkgtun7");
 if(request==TUNSETIFF) {assert(fd==10); ++attaches;
   if(fail_attach){errno=EPERM; return -1;}
   if(multiqueue && !(r->ifr_flags&IFF_MULTI_QUEUE)){errno=EINVAL; return -1;}
   return 0;
 }
 assert(fd==11);
 if(request==SIOCGIFADDR) {auto* a=reinterpret_cast<sockaddr_in*>(&r->ifr_addr); inet_pton(AF_INET,"10.0.0.1",&a->sin_addr); return 0;}
 if(request==SIOCGIFNETMASK) {auto* a=reinterpret_cast<sockaddr_in*>(&r->ifr_netmask); a->sin_addr.s_addr=htonl(0xffffffffu); return 0;}
 if(request==SIOCGIFFLAGS){r->ifr_flags=IFF_UP; return 0;}
 if(request==SIOCGIFMTU){r->ifr_mtu=1420; return 0;}
 assert(false && "Unexpected mutation ioctl"); return -1;
}
extern "C" ssize_t __wrap_read(int fd,void* buffer,size_t size) {assert(fd==10 && size>=4); memset(buffer,0x45,4); return 4;}
extern "C" ssize_t __wrap_write(int fd,const void*,size_t size) {assert(fd==10); return size;}
int main(){
 using fptn::common::network::LinuxTunDevice;
 for(const char* bad:{"opkgtun", "opkgtun00", "opkgtun-1", "opkgtun1x", "opkgtun123456789"}) {
  LinuxTunDevice d; assert(!d.Open(bad)); assert(tuntap::creations==0);
 }
 {exists=false; LinuxTunDevice d; assert(!d.Open("opkgtun7")); assert(opens==0); exists=true;}
 {LinuxTunDevice d; assert(d.Open("opkgtun7")); assert(tuntap::creations==0);
  assert(d.GetName()=="opkgtun7"); assert(d.ConfigureIPv4("10.0.0.1",32));
  assert(!d.ConfigureIPv4("10.0.0.2",32)); assert(!d.ConfigureIPv4("10.0.0.1",24));
  assert(d.ConfigureIPv6("fd00::1",126)); d.SetNonBlocking(true); d.SetMTU(1420); assert(d.BringUp());
  bool thrown=false; try{d.SetMTU(1280);}catch(const std::runtime_error&){thrown=true;} assert(thrown);
  char b[8]; assert(d.Read(b,8)==4); assert(d.Write(b,4)==4); d.Close(); assert(closes==1);
 } assert(closes==1);
 {fail_attach=true; LinuxTunDevice d; assert(!d.Open("opkgtun7")); fail_attach=false;} assert(closes==2);
 {attaches=0; replaced=true; LinuxTunDevice d; assert(!d.Open("opkgtun7")); replaced=false;} assert(closes==3);
 {attaches=0; multiqueue=true; LinuxTunDevice d; assert(d.Open("opkgtun7")); assert(attaches==2); multiqueue=false;} assert(closes==4);
 {LinuxTunDevice d; assert(d.Open("fptn0")); assert(tuntap::creations==1); d.Close();}
 puts("native TUN attach/close/validation/packets: PASS");
}
'''

class NativeTunTest(unittest.TestCase):
    def test_real_header_with_wrapped_syscalls(self):
        compiler=shutil.which('g++')
        if not compiler: self.skipTest('host C++ compiler unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'tuntap++.hh').write_text(STUB); (root/'test.cpp').write_text(CPP)
            cmd=[compiler,'-std=c++20','-Wall','-Wextra','-Werror','-I'+str(root),'-I'+str(ROOT/'src'),str(root/'test.cpp'),'-o',str(root/'test')]
            cmd+=['-Wl,--wrap='+name for name in ['if_nametoindex','open','close','socket','fcntl','ioctl','read','write']]
            built=subprocess.run(cmd,text=True,capture_output=True)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([str(root/'test')],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('PASS',result.stdout)

    def test_native_patch_requires_matching_layout_and_keeps_optin(self):
        spec=importlib.util.spec_from_file_location('native',ROOT/'scripts/prepare-native-tun.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cli=root/'src/fptn-client/fptn-client-cli.cpp'; cli.parent.mkdir(parents=True)
            cli.write_text('    args.add_argument("--keenetic-connect");\n'
                'if (!args.get<bool>("--keenetic-connect")) return EXIT_FAILURE;\n'
                '    if (args.get<std::string>("--tun-interface-name") != "fptn0") {\n'
                '      std::cerr << "Tunnel test requires --tun-interface-name fptn0.\\n";\n'
                '      return EXIT_FAILURE;\n    }')
            tun=root/'src/common/network/tun/linux_tun_device.h'; tun.parent.mkdir(parents=True)
            tun.write_text('class LinuxTunDevice {\ntun_ = std::make_unique<tuntap::tun>();\n};')
            module.prepare(root,ROOT/'src/keenetic-linux-tun.h')
            self.assertIn('--keenetic-native',cli.read_text())
            self.assertIn('!args.get<bool>("--keenetic-connect")',cli.read_text())
            self.assertIn('(!keenetic_native && keenetic_tun != "fptn0")',cli.read_text())
            self.assertEqual(tun.read_text(),(ROOT/'src/keenetic-linux-tun.h').read_text())
            with self.assertRaises(ValueError): module.prepare(root,ROOT/'src/keenetic-linux-tun.h')
