#!/usr/bin/env python3
"""Small fail-closed adaptations on top of a pinned upstream checkout."""
import gzip
import json
import pathlib
import re
import sys


def replace_once(root, path, old, new):
    file = root / path
    text = file.read_text()
    if text.count(old) != 1:
        raise ValueError(f"upstream changed: expected exactly one match in {path}: {old!r}")
    file.write_text(text.replace(old, new, 1))


def prepare(root, lock):
    if not all(re.fullmatch(r"[0-9a-f]{40}", v) for v in lock.values()):
        raise ValueError("source refs must be full commit SHAs")
    replacements = [
        (".conan/recipes/boringssl/conanfile.py",
         "https://github.com/batchar2/boringssl/archive/refs/heads/working-russia-tls-handshake.tar.gz",
         f"https://github.com/fptn-project/boringssl/archive/{lock['boringssl']}.tar.gz"),
        (".conan/recipes/yaff/conanfile.py",
         "https://github.com/fptn-project/yaff/archive/refs/heads/main.zip",
         f"https://github.com/fptn-project/yaff/archive/{lock['yaff']}.zip"),
        ("depends/cmake/CamouflageTLS.cmake",
         "https://github.com/fptn-project/camouflage-tls/archive/refs/heads/main.zip",
         f"https://github.com/fptn-project/camouflage-tls/archive/{lock['camouflage_tls']}.zip"),
        ("depends/cmake/NtpClient.cmake",
         "https://github.com/batchar2/NTP-client/archive/refs/heads/master.zip",
         f"https://github.com/fptn-project/NTP-client/archive/{lock['ntp_client']}.zip"),
    ]
    for path, old, new in replacements:
        replace_once(root, path, old, new)

    # Parsing --help / --version exits inside argparse. Every path that would
    # initialize networking is blocked BEFORE logger, NTP, TUN or routing setup.
    replace_once(root, "src/fptn-client/fptn-client-cli.cpp",
                 '    if (fptn::logger::init("fptn-client-cli")) {',
                 '    std::cerr << "Keenetic prototype: connection is disabled; "\n'
                 '                 "only --help and --version are supported.\\n";\n'
                 '    return EXIT_FAILURE;\n\n'
                 '    if (fptn::logger::init("fptn-client-cli")) {')

    # No multi-megabyte adblock list in a router compatibility prototype.
    # Upstream's embedding generator accepts an existing deterministic gzip.
    blocklist = root / "keenetic-empty-blocklist.gz"
    blocklist.write_bytes(gzip.compress(b"# Empty in the Keenetic prototype\n", mtime=0))
    replace_once(root, "src/fptn-client/CMakeLists.txt",
                 'set(ADBLOCK_BLOCKLIST_GZ "${CMAKE_CURRENT_BINARY_DIR}/blocklist.gz")',
                 'set(ADBLOCK_BLOCKLIST_GZ "${CMAKE_SOURCE_DIR}/keenetic-empty-blocklist.gz")')
    print("Pinned moving sources; disabled connections; omitted large adblock list")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: prepare-source.py UPSTREAM_DIR LOCK_JSON")
    prepare(pathlib.Path(sys.argv[1]), json.loads(pathlib.Path(sys.argv[2]).read_text()))
