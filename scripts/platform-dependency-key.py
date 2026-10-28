#!/usr/bin/env python3
import hashlib,pathlib,sys
root=pathlib.Path('.');generated=pathlib.Path(sys.argv[1]);h=hashlib.sha256(b'fptn-platform-dependency-v1\0')
inputs=[(generated/'Dockerfile').read_text().split('# END DEPENDENCY IMAGE INPUTS')[0].encode(),(generated/'profile').read_bytes()]
for name in ['sources.lock.json','scripts/prepare-source.py','scripts/fix-platform-libgcc.py','scripts/verify-platform-elf.py']:inputs.append((root/name).read_bytes())
for data in inputs:h.update(str(len(data)).encode()+b'\0'+data)
print(h.hexdigest())
