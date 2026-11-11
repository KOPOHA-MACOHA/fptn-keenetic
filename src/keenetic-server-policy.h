#pragma once
#ifndef FPTN_KEENETIC_SERVER_POLICY_H
#define FPTN_KEENETIC_SERVER_POLICY_H

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <cctype>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>
#include <sys/stat.h>

namespace fptn::keenetic {
inline bool WritePrivateJson(const std::string& path, const std::string& json) {
  if (path.empty()) return true;
  const auto temporary = path + ".new";
  std::ofstream file(temporary, std::ios::binary | std::ios::trunc);
  if (!file) return false;
  file << json << '\n'; file.close();
  if (!file || chmod(temporary.c_str(), 0600) != 0 || std::rename(temporary.c_str(), path.c_str()) != 0) {
    std::remove(temporary.c_str()); return false;
  }
  return true;
}
inline std::string ServerKey(std::string name) {
  const auto first = name.find_first_not_of(" \r\n\t");
  if (first == std::string::npos) return {};
  name = name.substr(first, name.find_last_not_of(" \r\n\t") - first + 1);
  for (auto& ch : name) if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch - 'A' + 'a');
  return name;
}

template<class Server>
std::vector<std::vector<Server>> ServerTiers(const std::vector<Server>& servers,
    const std::vector<std::string>& favorites, const std::vector<std::string>& excluded,
    const std::vector<std::string>& temporarily_failed, const std::string& manual) {
  std::unordered_set<std::string> favorite, denied, seen;
  for (const auto& name : favorites) favorite.insert(ServerKey(name));
  for (const auto& name : excluded) denied.insert(ServerKey(name));
  for (const auto& name : temporarily_failed) denied.insert(ServerKey(name));
  std::vector<Server> chosen, preferred, other;
  for (const auto& server : servers) {
    const auto name = ServerKey(server.name);
    if (denied.contains(name) || !seen.insert(name).second) continue;
    if (!manual.empty() && name == ServerKey(manual)) chosen.push_back(server);
    else if (favorite.contains(name)) preferred.push_back(server);
    else other.push_back(server);
  }
  if (seen.size() > 64) throw std::runtime_error("At most 64 eligible servers are supported on this router; exclude unused servers");
  std::vector<std::vector<Server>> tiers;
  if (!chosen.empty()) tiers.push_back(std::move(chosen));
  if (!preferred.empty()) tiers.push_back(std::move(preferred));
  if (!other.empty()) tiers.push_back(std::move(other));
  return tiers;
}

// Probe every member of a tier; minimize successful login latency, not throughput.
// A fixed worker count bounds transient TLS allocations on a 256 MiB router.
template<class Result, class Server, class Probe>
std::optional<Result> SelectServer(const std::vector<std::vector<Server>>& tiers, Probe probe) {
  for (const auto& tier : tiers) {
    std::mutex mutex;
    std::size_t next = 0;
    std::optional<Result> best;
    auto best_time = std::chrono::steady_clock::duration::max();
    auto work = [&]() {
      while (true) {
        std::size_t index;
        { const std::lock_guard<std::mutex> lock(mutex);
          if (next == tier.size()) return;
          index = next++;
        }
        const auto start = std::chrono::steady_clock::now();
        std::optional<Result> result;
        try { result = probe(tier[index]); } catch (...) { /* unavailable */ }
        const auto elapsed = std::chrono::steady_clock::now() - start;
        const std::lock_guard<std::mutex> lock(mutex);
        if (result && (!best || elapsed < best_time)) { best = std::move(result); best_time = elapsed; }
      }
    };
    // jthread also joins on partial construction failure; no detached callbacks.
    std::vector<std::jthread> workers;
    for (std::size_t n = 0; n < std::min<std::size_t>(2, tier.size()); ++n) workers.emplace_back(work);
    workers.clear();
    if (best) return best;
  }
  return std::nullopt;
}

class LossWindow {
  std::optional<std::chrono::steady_clock::time_point> since_;
 public:
  bool Expired(bool reconnecting, std::chrono::steady_clock::time_point now) {
    if (!reconnecting) { since_.reset(); return false; }
    if (!since_) since_ = now;
    return now - *since_ >= std::chrono::seconds(15);
  }
};
}  // namespace fptn::keenetic
#endif
