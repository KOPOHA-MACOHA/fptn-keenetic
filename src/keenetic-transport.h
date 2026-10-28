#pragma once
// Linux transport adapter only. The original TLS, API and packet protocol stay intact.
#include <cerrno>
#include <cstring>
#include <net/if.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <utility>
#include <boost/asio/awaitable.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/redirect_error.hpp>
#include <boost/asio/use_awaitable.hpp>
#include <boost/system/system_error.hpp>

namespace fptn::keenetic {
// Set once, before worker threads start. Empty preserves legacy test behavior.
inline std::string transport_interface;
inline void ConfigureTransport(const std::string& name) {
  if (name.empty() || name.size() >= IFNAMSIZ || name == "opkgtun0" ||
      name.front() == '-' || name.find_first_not_of(
          "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-") != std::string::npos)
    throw std::runtime_error("Invalid FPTN uplink interface");
  if (::if_nametoindex(name.c_str()) == 0)
    throw std::runtime_error("FPTN uplink interface does not exist");
  transport_interface = name;
}
inline void BindTransport(int fd) {
  if (transport_interface.empty()) return;
  if (::setsockopt(fd, SOL_SOCKET, SO_BINDTODEVICE, transport_interface.c_str(),
          static_cast<socklen_t>(transport_interface.size() + 1)) != 0)
    throw std::runtime_error(std::string("Cannot bind FPTN uplink: ") + std::strerror(errno));
}
template<class Stream, class Results>
boost::asio::ip::tcp::endpoint Connect(Stream& stream, const Results& results) {
  if (transport_interface.empty()) return stream.connect(results);
  boost::system::error_code error = boost::asio::error::host_not_found;
  for (const auto& entry : results) {
    const auto endpoint = entry.endpoint();
    stream.socket().close(error);
    stream.socket().open(endpoint.protocol(), error);
    if (error) continue;
    BindTransport(stream.socket().native_handle());
    // Single-endpoint connect preserves the socket binding; range connect may reopen it.
    stream.connect(endpoint, error);
    if (!error) return endpoint;
    if (error == boost::asio::error::timed_out || error == boost::asio::error::operation_aborted) break;
  }
  throw boost::system::system_error(error);
}
template<class Stream, class Results>
boost::asio::awaitable<void> AsyncConnect(Stream& stream, const Results& results,
    boost::system::error_code& error) {
  if (transport_interface.empty()) {
    co_await stream.async_connect(results,
        boost::asio::redirect_error(boost::asio::use_awaitable, error));
    co_return;
  }
  error = boost::asio::error::host_not_found;
  for (const auto& entry : results) {
    stream.socket().close(error);
    stream.socket().open(entry.endpoint().protocol(), error);
    if (error) continue;
    BindTransport(stream.socket().native_handle());
    co_await stream.async_connect(entry.endpoint(),
        boost::asio::redirect_error(boost::asio::use_awaitable, error));
    if (!error || error == boost::asio::error::timed_out ||
        error == boost::asio::error::operation_aborted) co_return;
  }
}
}  // namespace fptn::keenetic
