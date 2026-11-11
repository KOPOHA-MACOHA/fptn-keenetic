#!/usr/bin/env python3
"""Restore GCC's static exception unwinder omitted by OpenWrt patch 850.

OpenWrt's LIBGCC_SPEC links -lgcc for -static, but omits -lgcc_eh.
Keep its other SDK specs (including sysroot lookup) and add missing archives.
libatomic must follow application libraries: early -latomic flags are ineffective
with static archive linking (Protobuf/Abseil uses 64-bit atomic operations).
"""
import pathlib
import subprocess

def patch_specs(specs):
    old = "%{static|static-libgcc:-lgcc}"
    if specs.count(old) != 1:
        raise ValueError("OpenWrt LIBGCC_SPEC changed; refusing to guess")
    return specs.replace(old, "%{static|static-libgcc:-lgcc -lgcc_eh -latomic}", 1)


if __name__ == "__main__":
    compiler = "mipsel-openwrt-linux-musl-gcc"
    path = pathlib.Path(subprocess.check_output(
        [compiler, "-print-file-name=specs"], text=True).strip())
    if not path.is_absolute() or not path.is_file():
        raise SystemExit("Cannot locate the SDK's existing specs file")
    specs = subprocess.check_output([compiler, "-dumpspecs"], text=True)
    path.write_text(patch_specs(specs))
    print("Restored late -lgcc_eh -latomic for static linking in SDK compiler specs")
