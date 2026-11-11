#define FPTN_VERSION "memory-bench"
#include LOGGER_HEADER
#include <fstream>
#include <sys/resource.h>
#include <chrono>
#include <thread>
#include <cassert>
long rss(){std::ifstream f("/proc/self/status");std::string k,l;long n;while(f>>k){if(k=="VmRSS:"){f>>n;return n;}std::getline(f,l);}return -1;}
int main(int argc,char** argv){
 const std::string app=argc>1?argv[1]:"fptn-client-cli";
 const long before=rss();auto started=std::chrono::steady_clock::now();
 assert(fptn::logger::init(app));
 auto pool=spdlog::thread_pool();const long initialized=rss();
 SPDLOG_WARN("Memory test address: 192.0.2.1");
 for(int i=0;i<512;i++)SPDLOG_INFO("MEMORY_BENCH sequence={}",i);
 auto logger=spdlog::default_logger();logger->flush();
 for(int i=0;i<100 && pool->queue_size();i++)std::this_thread::sleep_for(std::chrono::milliseconds(1));
 const long after=rss();const auto dropped=pool->overrun_counter();
 // Drop our pool owner before shutdown so the real queue is drained and joined.
 pool.reset();logger.reset();spdlog::shutdown();
 std::ifstream f(std::string(getenv("FPTN_BENCH_LOG_DIR"))+"/"+app+".log");std::string line;int recorded=0;bool masked=false;while(std::getline(f,line)){if(line.find("MEMORY_BENCH sequence=")!=std::string::npos)recorded++;if(line.find("Memory test address: ***.***.***.1")!=std::string::npos)masked=true;assert(line.find("192.0.2.1")==std::string::npos);}
 assert(recorded==512 && dropped==0 && masked);
 struct rusage usage{};getrusage(RUSAGE_SELF,&usage);
 std::cerr<<"{\"app\":\""<<app<<"\",\"beforeRssKiB\":"<<before<<",\"initializedRssKiB\":"<<initialized<<",\"afterRssKiB\":"<<after<<",\"slotBytes\":"<<sizeof(spdlog::details::async_msg)<<",\"userCpuSeconds\":"<<(usage.ru_utime.tv_sec+usage.ru_utime.tv_usec/1e6)<<",\"systemCpuSeconds\":"<<(usage.ru_stime.tv_sec+usage.ru_stime.tv_usec/1e6)<<",\"recorded\":"<<recorded<<",\"dropped\":"<<dropped<<",\"seconds\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count()<<"}\n";
}
