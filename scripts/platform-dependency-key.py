#!/usr/bin/env python3
import hashlib,pathlib,sys
root=pathlib.Path('.');generated=pathlib.Path(sys.argv[1]);h=hashlib.sha256(b'fptn-platform-dependency-v1\0')
inputs=[(generated/'Dockerfile').read_text().split('# END DEPENDENCY IMAGE INPUTS')[0].encode(),(generated/'profile').read_bytes()]
for name in ['sources.lock.json','scripts/prepare-source.py','scripts/fix-platform-libgcc.py','scripts/verify-platform-elf.py','scripts/dependency-lock.py', 'scripts/install-locked-dependencies.sh', 'scripts/seed-conan-cache.py',
             *('build-locks/'+n for n in ('conan.lock','graph.json','metadata.json','python.txt','apt.txt','build-profile','ubuntu.sources','shared-build-cache.json'))]:inputs.append((root/name).read_bytes())
for data in inputs:h.update(str(len(data)).encode()+b'\0'+data)
print(h.hexdigest())
