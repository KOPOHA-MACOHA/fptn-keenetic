#!/usr/bin/env python3
"""Restore GCC's static exception unwinder omitted by OpenWrt patch 850.

OpenWrt's LIBGCC_SPEC links -lgcc for -static, but omits -lgcc_eh.
Keep its other SDK specs (including sysroot lookup) and add the missing archive.
"""
import pathlib
import subprocess

compiler = "mipsel-openwrt-linux-musl-gcc"
path = pathlib.Path(subprocess.check_output(
    [compiler, "-print-file-name=specs"], text=True).strip())
if not path.is_absolute() or not path.is_file():
    raise SystemExit("Cannot locate the SDK's existing specs file")
specs = subprocess.check_output([compiler, "-dumpspecs"], text=True)
old = "%{static|static-libgcc:-lgcc}"
if specs.count(old) != 1:
    raise SystemExit("OpenWrt LIBGCC_SPEC changed; refusing to guess")
path.write_text(specs.replace(old, "%{static|static-libgcc:-lgcc -lgcc_eh}", 1))
print("Restored -lgcc_eh for static linking in SDK compiler specs")
