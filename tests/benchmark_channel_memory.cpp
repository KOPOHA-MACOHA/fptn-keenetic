// Development-only benchmark. Uses the actual Channel and IPPacket implementations.
#include CHANNEL_HEADER
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/resource.h>
#include <thread>
#include <vector>

using namespace fptn::common;
using namespace std::chrono_literals;

long rss(const std::string& field = "VmRSS:") {
  std::ifstream file("/proc/self/status");
  std::string key, line; long value;
  while (file >> key) {
    if (key == field) { file >> value; return value; }
    std::getline(file, line);
  }
  return -1;
}
network::IPPacketPtr packet(std::uint32_t id = 0) {
  network::IPPacketData data(1400, 0x45);
  std::memcpy(data.data() + 1, &id, sizeof(id));
  return std::make_unique<network::IPPacket>(std::move(data), 1);
}
std::uint32_t id(const network::IPPacketPtr& p) {
  std::uint32_t value; std::memcpy(&value, p->Data().data() + 1, sizeof(value));
  return value;
}
network::BatchIPPacketPtr batch(unsigned count, unsigned first = 0) {
  network::BatchIPPacketPtr result; result.reserve(count);
  for (unsigned i = 0; i < count; ++i) result.push_back(packet(first + i));
  return result;
}
void check() {
  data::Channel channel("capacity", 4);
  assert(channel.Push(packet(0)));
  assert(channel.PushBatch(batch(3, 1))); // Exact fit.
  assert(!channel.PushBatch(batch(1, 99)));
  assert(id(channel.WaitForPacket(0ms)) == 0);
  assert(!channel.PushBatch(batch(2, 99))); // Reject the ENTIRE batch.
  for (unsigned i = 1; i < 4; ++i) assert(id(channel.WaitForPacket(0ms)) == i);
  assert(!channel.WaitForPacket(0ms));
  assert(!channel.PushBatch(batch(5))); // An empty queue also has a limit.
  assert(!channel.WaitForPacket(0ms));
  assert(channel.PushBatch(batch(4, 10))); // Recovery after rejection.
  auto packets = channel.WaitForPackets(0ms);
  assert(packets.size() == 4);
  for (unsigned i = 0; i < 4; ++i) assert(id(packets[i]) == i + 10);
  data::Channel disabled("zero", 0);
  assert(!disabled.Push(packet())); assert(!disabled.PushBatch(batch(1)));
  assert(!disabled.WaitForPacket(0ms));

  data::Channel concurrent("concurrent", 128);
  constexpr unsigned workers = 4, per_worker = 16000;
  std::atomic<unsigned> active{workers}, accepted{0};
  std::vector<std::thread> producers;
  for (unsigned worker = 0; worker < workers; ++worker) {
    producers.emplace_back([&, worker] {
      for (unsigned first = 0; first < per_worker; first += 8) {
        if (concurrent.PushBatch(batch(8, worker * per_worker + first))) accepted += 8;
      }
      --active;
    });
  }
  unsigned received = 0;
  std::array<int, workers> previous; previous.fill(-1);
  for (;;) {
    auto current = concurrent.WaitForPackets(1ms);
    if (current.empty() && active == 0) break;
    for (const auto& p : current) {
      unsigned value = id(p), worker = value / per_worker;
      assert(worker < workers && int(value % per_worker) > previous[worker]);
      previous[worker] = int(value % per_worker); ++received;
    }
  }
  for (auto& producer : producers) producer.join();
  assert(received == accepted);
  std::cout << "FIFO, exact capacity, atomic rejection, recovery, concurrent producers: PASS\n";
}
int main(int argc, char** argv) {
  const std::string scenario = argc > 1 ? argv[1] : "normal";
  if (scenario == "check") { check(); return 0; }
  const bool congested = scenario == "congested";
  data::Channel channel("benchmark", 4096);
  const long before = rss();
  unsigned accepted = 0, drained = 0;
  const auto started = std::chrono::steady_clock::now();
  if (congested) for (unsigned i = 0; i < 4095; ++i) channel.Push(packet(i));
  const unsigned rounds = congested ? 12 : 20000, width = congested ? 16384 : 32;
  for (unsigned round = 0; round < rounds; ++round) {
    auto incoming = batch(width);
    if (channel.PushBatch(std::move(incoming))) accepted += width;
    if (!congested) drained += channel.WaitForPackets(0ms).size();
  }
  const long retained = rss();
  const long peak = rss("VmHWM:");
  while (auto p = channel.WaitForPacket(0ms)) ++drained;
  rusage usage{}; getrusage(RUSAGE_SELF, &usage);
  const double cpu = usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1e6 +
      usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1e6;
  std::cout << "{\"scenario\":\"" << scenario << "\",\"baselineRssKiB\":" << before
      << ",\"retainedRssKiB\":" << retained << ",\"peakRssKiB\":" << peak
      << ",\"cpuSeconds\":" << cpu << ",\"elapsedSeconds\":"
      << std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count()
      << ",\"accepted\":" << accepted << ",\"drained\":" << drained << "}\n";
}
