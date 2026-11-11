"""Use real kernel TCP sockets to verify binding survives endpoint fallback."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]
CPP=r'''
#include "keenetic-transport.h"
#include <boost/asio/co_spawn.hpp>
#include <boost/asio/detached.hpp>
#include <boost/asio/this_coro.hpp>
#include <boost/beast/core/tcp_stream.hpp>
#include <cassert>
#include <iostream>
#include <vector>
using boost::asio::ip::tcp;
void bound(tcp::socket& socket) {
 char name[IFNAMSIZ]{};socklen_t size=sizeof(name);
 assert(getsockopt(socket.native_handle(),SOL_SOCKET,SO_BINDTODEVICE,name,&size)==0);
 assert(std::string(name)=="lo");
}
int main(){
 boost::asio::io_context context;
 fptn::keenetic::ConfigureTransport("lo");
 for(const char* name:{"", "opkgtun0", "-bad", "lo;id", "missingif0"}) {
  bool threw=false;try{fptn::keenetic::ConfigureTransport(name);}catch(const std::runtime_error&){threw=true;}
  assert(threw);
 }
 bool failed=false;try{fptn::keenetic::BindTransport(-1);}catch(const std::runtime_error&){failed=true;}assert(failed);
 for(bool ipv6:{false,true}){
  auto address=boost::asio::ip::make_address(ipv6?"::1":"127.0.0.1");
  tcp::acceptor listener(context,tcp::endpoint(address,0));
  tcp::acceptor unused(context,tcp::endpoint(address,0));
  auto rejected=unused.local_endpoint();unused.close();
  std::vector<tcp::endpoint> endpoints{rejected,listener.local_endpoint()};
  auto results=tcp::resolver::results_type::create(endpoints.begin(),endpoints.end(),"loopback","0");
  boost::beast::tcp_stream stream(context);
  assert(fptn::keenetic::Connect(stream,results)==listener.local_endpoint());bound(stream.socket());
  bool done=false;
  boost::asio::co_spawn(context,[&]() -> boost::asio::awaitable<void>{
    boost::beast::tcp_stream connection(co_await boost::asio::this_coro::executor);
    boost::system::error_code error;
    co_await fptn::keenetic::AsyncConnect(connection,results,error);
    assert(!error);bound(connection.socket());done=true;
  },boost::asio::detached);
  context.run();assert(done);context.restart();
 }
 std::cout<<"real IPv4/IPv6 socket binding and sync/async endpoint fallback: PASS\n";
}
'''


class NativeTransportTest(unittest.TestCase):
    def test_real_kernel_sockets_and_sync_async_fallback(self):
        if not pathlib.Path('/usr/include/boost/beast/core/tcp_stream.hpp').exists():
            self.skipTest('Host Boost headers required in Actions')
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);(root/'test.cpp').write_text(CPP)
            built=subprocess.run(['g++','-std=c++20','-DBOOST_ERROR_CODE_HEADER_ONLY','-pthread','-I'+str(ROOT/'src'),str(root/'test.cpp'),'-o',str(root/'test')],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([str(root/'test')],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS',result.stdout)
