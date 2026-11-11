#pragma once
#ifndef FPTN_KEENETIC_HEALTH_H
#define FPTN_KEENETIC_HEALTH_H
// A tiny data-plane canary. No routes, resolver settings or kernel TUN writes.
#include "keenetic-server-policy.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <random>
#include <arpa/inet.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>

namespace fptn::keenetic {
class TunnelHealth {
 public:
  using Bytes = std::vector<std::uint8_t>;
  using Clock = std::chrono::steady_clock;
  using Time = Clock::time_point;
  using Sender = std::function<bool(const Bytes&)>;
 private:
  std::mutex mutex_;
  Sender send_;
  std::atomic<bool> listening_{false};
  int interval_=0, timeout_=5, limit_=3, failures_=0, endpoint_=0, fd_=-1;
  bool pending_=false, reply_=false, direct_=false;
  std::uint16_t id_=0, port_=0;
  std::uint32_t source_=0;
  Time next_{}, deadline_{};
  std::string uplink_, path_;
  static constexpr std::array<std::uint32_t,2> destinations_{0x01010101,0x08080808};
  template<class Buffer> static std::uint16_t Word(const Buffer& b,std::size_t p) {return (std::uint16_t(b[p])<<8)|b[p+1];}
  static void Put(Bytes& b,std::size_t p,std::uint16_t n) {b[p]=n>>8;b[p+1]=n&255;}
  Bytes Query() const {
    Bytes q(17);Put(q,0,id_);q[2]=1;q[5]=1; // RD, one question: root NS / IN
    q[14]=2;q[16]=1;return q;
  }
  bool Answer(const Bytes& b) const {
    return b.size()>=29 && Word(b,0)==id_ && (b[2]&0xf8)==0x80 &&
      (b[3]&15)==0 && Word(b,4)==1 && b[12]==0 && Word(b,13)==2 && Word(b,15)==1 &&
      (Word(b,6)>0 || Word(b,8)>0);
  }
  void Close() {if(fd_>=0){::close(fd_);fd_=-1;}}
  void Publish(const std::string& state,Time now) {
    auto wait=std::chrono::duration_cast<std::chrono::seconds>((pending_?deadline_:next_)-now).count();
    auto wall=std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    WritePrivateJson(path_,"{\"state\":\""+state+"\",\"checkedAt\":"+std::to_string(wall)+
      ",\"nextCheckAt\":"+std::to_string(wall+std::max<std::int64_t>(0,wait))+
      ",\"failures\":"+std::to_string(failures_)+",\"threshold\":"+std::to_string(limit_)+"}");
  }
  void BeginDirect(Time now,int endpoint=0) {
    direct_=true;listening_=false;endpoint_=endpoint;Close();
    fd_=::socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    if(fd_>=0 && !uplink_.empty() && ::setsockopt(fd_,SOL_SOCKET,SO_BINDTODEVICE,
        uplink_.c_str(),static_cast<socklen_t>(uplink_.size()+1))==0) {
      sockaddr_in address{};address.sin_family=AF_INET;address.sin_port=htons(53);
      address.sin_addr.s_addr=htonl(destinations_[endpoint_]);
      auto q=Query();
      if(::connect(fd_,reinterpret_cast<sockaddr*>(&address),sizeof(address))!=0 ||
          ::send(fd_,q.data(),q.size(),0)!=static_cast<ssize_t>(q.size())) Close();
    } else Close();
    deadline_=now+std::chrono::seconds(timeout_);Publish("checking-uplink",now);
  }
  void Send(Time now,std::unique_lock<std::mutex>& lock) {
    auto q=Query();Bytes b(28+q.size());b[0]=0x45;Put(b,2,static_cast<std::uint16_t>(b.size()));
    b[8]=64;b[9]=17;
    for(int n=0;n<4;++n){b[12+n]=(source_>>(24-8*n))&255;b[16+n]=(destinations_[endpoint_]>>(24-8*n))&255;}
    Put(b,20,port_);Put(b,22,53);Put(b,24,static_cast<std::uint16_t>(q.size()+8));
    std::copy(q.begin(),q.end(),b.begin()+28);
    std::uint32_t sum=0;for(int p=0;p<20;p+=2)sum+=Word(b,p);
    while(sum>>16)sum=(sum&65535)+(sum>>16);
    Put(b,10,static_cast<std::uint16_t>(~sum)); // UDP checksum zero is legal for IPv4
    deadline_=now+std::chrono::seconds(timeout_);Publish("checking",now);
    // Never call the original transport while holding the probe mutex: a very
    // fast incoming reply (or a strategy callback) can re-enter Receive.
    const auto sender=send_;
    lock.unlock();
    try {if(sender)sender(b);}catch(...) { /* timeout handles failed sends */ }
    lock.lock();
  }
 public:
  ~TunnelHealth(){Close();}
  void Configure(int interval,int timeout,int limit,const std::string& source,
      const std::string& uplink,const std::string& path,Time now=Clock::now()) {
    const std::lock_guard<std::mutex> lock(mutex_);
    if((interval!=0 && (interval<30 || interval>3600)) || timeout<2 || timeout>15 || limit<2 || limit>10)
      throw std::runtime_error("Invalid tunnel health settings");
    in_addr ip{};
    if(::inet_pton(AF_INET,source.c_str(),&ip)!=1 || uplink.empty() || uplink.size()>15 ||
        uplink.rfind("opkgtun",0)==0 || uplink.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")!=std::string::npos)
      throw std::runtime_error("Invalid tunnel health interface/address");
    interval_=interval;timeout_=timeout;limit_=limit;source_=ntohl(ip.s_addr);uplink_=uplink;path_=path;
    failures_=0;pending_=reply_=direct_=false;listening_=false;Close();next_=now+std::chrono::seconds(interval_);
    Publish(interval_?"scheduled":"disabled",now);
  }
  void SetSender(Sender sender) {const std::lock_guard<std::mutex> lock(mutex_);send_=std::move(sender);}
  // Called before incoming packets reach the OS; consume only our exact tuple/question.
  template<class Buffer> bool Receive(const Buffer& b) {
    if(!listening_.load(std::memory_order_relaxed)) return false;
    const std::lock_guard<std::mutex> lock(mutex_);
    if(!pending_ || direct_ || b.size()<45 || b[0]!=0x45 || b[9]!=17 ||
        (Word(b,6)&0x3fff)!=0 || Word(b,2)!=b.size() || Word(b,20)!=53 || Word(b,22)!=port_ ||
        Word(b,24)!=b.size()-20) return false;
    for(int n=0;n<4;++n)if(b[12+n]!=((destinations_[endpoint_]>>(24-8*n))&255) ||
        b[16+n]!=((source_>>(24-8*n))&255))return false;
    if(!Answer(Bytes(b.begin()+28,b.end())))return false;
    reply_=true;return true;
  }
  bool Tick(bool connected,Time now=Clock::now()) {
    std::unique_lock<std::mutex> lock(mutex_);
    if(!interval_)return false;
    if(!connected){pending_=reply_=direct_=false;listening_=false;Close();next_=now+std::chrono::seconds(interval_);return false;}
    if(!pending_){
      if(now<next_)return false;
      std::random_device random;id_=static_cast<std::uint16_t>(random());port_=40000+random()%20000;
      pending_=true;listening_=true;reply_=false;direct_=false;endpoint_=0;Send(now,lock);return false;
    }
    if(direct_){
      std::array<std::uint8_t,2048> buf{};auto size = fd_<0 ? -1 : ::recv(fd_,buf.data(),buf.size(),0);
      if(size>0 && Answer(Bytes(buf.begin(),buf.begin()+size))){
        Close();pending_=false;Publish("failed",now);return true;
      }
    }else if(reply_){
      failures_=0;pending_=false;listening_=false;reply_=false;next_=now+std::chrono::seconds(interval_);Publish("ok",now);return false;
    }
    if(now<deadline_)return false;
    if(direct_){
      if(endpoint_==0){BeginDirect(now,1);return false;}
      Close();pending_=false;next_=now+std::chrono::seconds(interval_);
      Publish("inconclusive",now);return false; // WAN or probe service failure: never rotate blindly
    }
    if(endpoint_==0){endpoint_=1;Send(now,lock);return false;}
    ++failures_;
    if(failures_>=limit_){BeginDirect(now);return false;}
    pending_=false;listening_=false;next_=now+std::chrono::seconds(interval_);Publish("missed",now);return false;
  }
};
inline TunnelHealth tunnel_health;
} // namespace fptn::keenetic

#endif
