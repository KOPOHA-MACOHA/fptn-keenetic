# Resident memory used by the async logger

The pinned upstream logger is copied verbatim into `fixtures/logger/logger.h`
from `fptn-project/fptn@a389bee62da930807c3bc85284ee4976e9c7e08f`,
`src/common/logger/logger.h` (MIT). The production adapter changes the queue
only for `app_name == "fptn-client-cli"`: 32768 -> 1024 messages. Other programs
keep 32768. The logger remains asynchronous, with the same sinks, formatter,
masking, flush policy and `overrun_oldest` overflow policy. At saturation, old
log messages can be overwritten sooner; packet queues and traffic are unaffected.

`test_logger_memory.py` compiles the actual original and patched logger header,
redirecting only its file path to private temporary directories. It checks
resident memory after initialization, writes 512 messages plus a masked test
address, shuts down, and verifies that all messages reached disk with zero
queue overruns and that the address was masked. A control checks that the server
keeps its original allocation. Unknown/already-patched source layouts fail closed.

Local comparison on Linux x86_64 with upstream **spdlog 1.17.0**, three fresh
processes per variant (KiB):

| Run | Original RSS after init | Patched RSS after init | Saved RSS growth, accounting for process baseline |
| --- | ---: | ---: | ---: |
| 1 | 15392 | 2732 | 12664 |
| 2 | 15380 | 2740 | 12644 |
| 3 | 15392 | 2716 | 12676 |

This is about **12.4 MiB** of resident memory in the standalone logger comparison,
not a measurement of the complete VPN client or a physical router. Entry size
and savings differ on 32-bit MIPS/ARM. Raw RSS and process CPU measurements are
in `fixtures/logger/linux-x86_64-results.json`. CPU time covers initialization,
logging, verification and shutdown; it is not a VPN throughput benchmark.

The candidate 4 MiB WebSocket reservation change was not included: a separate
flat-buffer comparison reduced reserved capacity but did not show a meaningful
RSS reduction on this host. No packet/TLS buffers or protocol limits were changed.

Reproduce using installed spdlog/fmt development headers, or point to an upstream
spdlog include directory (bundled fmt is supported):

```sh
FPTN_SPDLOG_INCLUDE=/path/to/spdlog-1.17.0/include \
  python3 -m unittest discover -s tests -p test_logger_memory.py -v
```

These files are developer checks under `tests/`. They are excluded from install
archives, the service and the panel. CI also exercises the host distribution
version of spdlog; the client builds retain the pinned Conan dependency version.
