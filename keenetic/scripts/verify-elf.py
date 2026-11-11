#!/usr/bin/env python3
"""Reject dynamically linked or wrong-ABI artifacts before device testing."""
import pathlib
import struct
import subprocess
import sys


def verify(path):
    data = pathlib.Path(path).read_bytes()
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("expected ELF32 little-endian")
    if struct.unpack_from("<H", data, 18)[0] != 8:
        raise ValueError("expected MIPS machine")
    if struct.unpack_from("<H", data, 16)[0] != 2:
        raise ValueError("expected executable ELF")
    flags = struct.unpack_from("<I", data, 36)[0]
    if flags & 0xF0000000 != 0x70000000 or flags & 0xF000 != 0x1000:
        raise ValueError(f"expected MIPS32r2 o32 flags, got {flags:#x}")
    phoff = struct.unpack_from("<I", data, 28)[0]
    phsize, phnum = struct.unpack_from("<HH", data, 42)
    if phsize < 32 or phnum == 0:
        raise ValueError("invalid program header table")
    for i in range(phnum):
        offset = phoff + i * phsize
        if offset + phsize > len(data):
            raise ValueError("truncated program header table")
        kind = struct.unpack_from("<I", data, offset)[0]
        if kind in (2, 3):
            raise ValueError("PT_DYNAMIC / PT_INTERP: binary is not fully static")
    attrs = subprocess.check_output(["readelf", "-A", str(path)], text=True)
    if "Soft float" not in attrs:
        raise ValueError("ELF attributes do not confirm soft-float ABI")
    print(f"{path}: ELF32 LE / MIPS32r2 o32 / static / soft-float: PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-elf.py BINARY")
    verify(sys.argv[1])
