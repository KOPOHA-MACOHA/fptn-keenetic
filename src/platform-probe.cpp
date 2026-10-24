#include <atomic>
#include <coroutine>
#include <cstdint>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <thread>

#if !defined(__mips__) || !defined(__MIPSEL__) || !defined(__mips_soft_float)
#error This probe is intended for MIPS little-endian soft-float
#endif
static_assert(sizeof(void*) == 4, "Expected 32-bit ABI");

struct Task {
    struct promise_type {
        int value = 0;
        Task get_return_object() {
            return {std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        std::suspend_never initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend() noexcept { return {}; }
        void return_value(int n) noexcept { value = n; }
        void unhandled_exception() { std::terminate(); }
    };
    std::coroutine_handle<promise_type> handle;
    ~Task() { handle.destroy(); }
};
Task answer() { co_return 42; }

int main() {
    try {
        std::atomic<std::uint64_t> counter{0};
        std::thread worker([&] { counter.fetch_add(42); });
        worker.join();
        auto task = answer();
        if (counter.load() != 42 || task.handle.promise().value != 42)
            throw std::runtime_error("C++ runtime check failed");
        bool exception_ok = false;
        try { throw std::runtime_error("test"); }
        catch (const std::runtime_error&) { exception_ok = true; }
        if (!exception_ok) return 1;
        std::cout << "MIPS32 little-endian soft-float, static musl, C++20\n"
                  << "threads / atomic64 / exceptions / coroutines: OK\n"
                  << "probe: PASS\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "probe: FAIL: " << e.what() << '\n';
        return 1;
    }
}
