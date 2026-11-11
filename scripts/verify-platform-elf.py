#!/usr/bin/env python3
"""Reject shared executables, wrong endianness and unsupported ARM ABIs."""
import pathlib,struct,subprocess,sys

def verify(path):
    data=pathlib.Path(path).read_bytes()
    if data[:4]!=b'\x7fELF' or data[5]!=1:raise ValueError('Expected little-endian ELF')
    bits=data[4];machine=struct.unpack_from('<H',data,18)[0]
    if struct.unpack_from('<H',data,16)[0]!=2:raise ValueError('Expected static executable')
    if (bits,machine)==(2,183):
        platform='aarch64';phoff=struct.unpack_from('<Q',data,32)[0];size,count=struct.unpack_from('<HH',data,54)
    elif (bits,machine)==(1,40):
        platform='armv7-softfloat';phoff=struct.unpack_from('<I',data,28)[0];size,count=struct.unpack_from('<HH',data,42)
        flags=struct.unpack_from('<I',data,36)[0]
        if flags&0x400 or flags&0xff000000!=0x05000000:raise ValueError('Expected ARM EABI5 with base/soft-float calling convention')
        attrs=subprocess.check_output(['readelf','-A',str(path)],text=True)
        if 'Tag_CPU_arch: v7' not in attrs or 'Tag_ABI_VFP_args: VFP registers' in attrs:raise ValueError('Wrong ARMv7 attributes')
    else:raise ValueError('Unsupported target ELF')
    if count<1 or size<(56 if bits==2 else 32):raise ValueError('Invalid program headers')
    for i in range(count):
        off=phoff+i*size
        if off+size>len(data):raise ValueError('Truncated program header')
        if struct.unpack_from('<I',data,off)[0] in (2,3):raise ValueError('Dynamic dependency/interpreter is forbidden')
    print(str(path)+': '+platform+' / static / little-endian: PASS')
    return platform
if __name__=='__main__':verify(sys.argv[1])
