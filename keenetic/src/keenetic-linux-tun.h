/*=============================================================================
Copyright (c) 2024-2026 Pavel Shpilev
Copyright (c) 2024-2026 Stas Skokov

Distributed under the MIT License (https://opensource.org/licenses/MIT)
=============================================================================*/

#pragma once

#include <atomic>
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <net/if.h>
#include <stdexcept>
#include <sys/socket.h>
#include <unistd.h>
#include <linux/if_tun.h>  // NOLINT(build/include_order)
#include <memory>
#include <string>
#include <sys/ioctl.h>  // NOLINT(build/include_order)
#include <tuntap++.hh>  // NOLINT(build/include_order)

namespace fptn::common::network {

class LinuxTunDevice {
 public:
  ~LinuxTunDevice() { Close(); }

  bool Open(const std::string& name) {
    // Native mode attaches to Keenetic-owned persistent TUN. Never rename it.
    if (name.rfind("opkgtun", 0) == 0) {
      const auto suffix = name.substr(7);
      if (name.size() >= IFNAMSIZ || suffix.empty() ||
          suffix.find_first_not_of("0123456789") != std::string::npos ||
          (suffix.size() > 1 && suffix.front() == '0')) return false;
      return OpenExisting(name);
    }
    tun_ = std::make_unique<tuntap::tun>();
    tun_->name(name);
    name_ = tun_->name();
    DisableKernelMessages();
    return true;
  }

  void Close() {
    if (native_fd_ >= 0) { ::close(native_fd_); native_fd_ = -1; }
    if (tun_) {
      tun_->nonblocking(true);
      tun_.reset();
    }
  }

  [[nodiscard]] const std::string& GetName() const { return name_; }

  bool ConfigureIPv4(const std::string& addr, int mask) const {
    if (native_fd_ >= 0) {
      if (mask != 32) return false;
      struct ifreq req {};
      struct in_addr expected {};
      if (::inet_pton(AF_INET, addr.c_str(), &expected) != 1 ||
          !Query(SIOCGIFADDR, req)) return false;
      const auto actual = reinterpret_cast<const struct sockaddr_in*>(&req.ifr_addr)->sin_addr;
      if (actual.s_addr != expected.s_addr || !Query(SIOCGIFNETMASK, req)) return false;
      return reinterpret_cast<const struct sockaddr_in*>(&req.ifr_netmask)->sin_addr.s_addr == htonl(0xffffffffu);
    }
    tun_->ip(addr, mask);
    return true;
  }

  bool ConfigureIPv6(const std::string& addr, int mask) const {
    if (native_fd_ >= 0) return true;  // No IPv6 configuration changes in native mode.
    tun_->ip(addr, mask);
    return true;
  }

  void SetNonBlocking(bool enabled) const {
    if (native_fd_ >= 0) {
      const int flags = ::fcntl(native_fd_, F_GETFL);
      if (flags < 0 || ::fcntl(native_fd_, F_SETFL,
          enabled ? flags | O_NONBLOCK : flags & ~O_NONBLOCK) < 0)
        throw std::runtime_error("Cannot set TUN fd nonblocking mode");
      return;
    }
    tun_->nonblocking(enabled);
  }

  void SetMTU(int mtu) const {
    if (native_fd_ >= 0) {
      struct ifreq req {};
      if (!Query(SIOCGIFMTU, req) || req.ifr_mtu != mtu)
        throw std::runtime_error("Configure the FPTN OpkgTun MTU in KeeneticOS before starting FPTN");
      return;
    }
    tun_->mtu(mtu);
  }

  bool BringUp() const {
    if (native_fd_ >= 0) {
      struct ifreq req {};
      return Query(SIOCGIFFLAGS, req) && (req.ifr_flags & IFF_UP) != 0;
    }
    tun_->up();
    return true;
  }

  int Read(void* buffer, int size) const {
    if (native_fd_ >= 0) return static_cast<int>(::read(native_fd_, buffer, static_cast<std::size_t>(size)));
    for (int i = 0; i < 4; i++) {
      const int res = tun_->read(buffer, static_cast<std::size_t>(size));
      if (res > 0) return res;
    }
    return 0;
  }

  int Write(const void* data, int size) const {
    if (native_fd_ >= 0) return static_cast<int>(::write(native_fd_, data, static_cast<std::size_t>(size)));
    return tun_->write(const_cast<void*>(data), static_cast<std::size_t>(size));
  }

  // cppcheck-suppress functionStatic
  static void SetStopFlag(const std::atomic<bool>* /*running*/) {}

 protected:
  void DisableKernelMessages() const {
    const int fd = tun_->native_handle();
    if (fd >= 0) {
      ::ioctl(fd, TUNSETDEBUG, 0);
    }
  }

 private:
  bool OpenExisting(const std::string& name) {
    const unsigned index = ::if_nametoindex(name.c_str());
    if (index == 0) return false;
    const int fd = ::open("/dev/net/tun", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return false;
    struct ifreq req {};
    std::strncpy(req.ifr_name, name.c_str(), IFNAMSIZ - 1);
    req.ifr_flags = IFF_TUN | IFF_NO_PI;
    int result = ::ioctl(fd, TUNSETIFF, &req);
    if (result < 0 && errno == EINVAL) {
      // Some firmware builds create the persistent device with MULTI_QUEUE.
      req.ifr_flags = IFF_TUN | IFF_NO_PI | IFF_MULTI_QUEUE;
      result = ::ioctl(fd, TUNSETIFF, &req);
    }
    if (result < 0 || ::if_nametoindex(name.c_str()) != index) {
      ::close(fd);  // Also disposes a newly created device in a delete/create race.
      return false;
    }
    native_fd_ = fd;
    name_ = name;
    return true;
  }

  bool Query(unsigned long request, struct ifreq& req) const {
    const int sock = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (sock < 0) return false;
    std::strncpy(req.ifr_name, name_.c_str(), IFNAMSIZ - 1);
    const bool ok = ::ioctl(sock, request, &req) == 0;
    ::close(sock);
    return ok;
  }

  int native_fd_ = -1;
  std::unique_ptr<tuntap::tun> tun_;
  std::string name_;
};

}  // namespace fptn::common::network
