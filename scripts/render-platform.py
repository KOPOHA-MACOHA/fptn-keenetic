#!/usr/bin/env python3
"""Render a separate SDK build; keep the verified MIPS Dockerfile/cache untouched."""
import json,pathlib,sys
root=pathlib.Path(sys.argv[1]);platform=sys.argv[2];out=pathlib.Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=True)
target=json.loads((root/'ports/targets.json').read_text())[platform]
text=(root/'Dockerfile').read_text()
text=text.replace('openwrt/sdk:ramips-mt7621-24.10.7@sha256:80fac8772ff0c536ba8a1b59497acd038daa744b7e3a628508b447a50145308c',target['sdk'])
text=text.replace('mipsel_24kc_gcc-13.3.0_musl',target['toolchain']).replace('mipsel-openwrt-linux-musl',target['triple'])
text=text.replace('COPY profiles/mipsel-softfloat /work/profile',f'COPY {out.as_posix()}/profile /work/profile')
text=text.replace('COPY scripts/fix-static-libgcc.py /work/fix-static-libgcc.py','COPY scripts/fix-platform-libgcc.py /work/fix-static-libgcc.py')
text=text.replace('RUN python /work/fix-static-libgcc.py',f'RUN python /work/fix-static-libgcc.py {target["triple"]}-gcc')
text=text.replace('COPY scripts/verify-elf.py /work/verify-elf.py','COPY scripts/verify-platform-elf.py /work/verify-elf.py')
text=text.replace('-march=mips32r2 -mabi=32 \\\n      -msoft-float',target['flags']).replace('qemu-mipsel',target['emulator'])
text=text.replace('COPY src/platform-probe.cpp /work/platform-probe.cpp',f'COPY {out.as_posix()}/probe.cpp /work/platform-probe.cpp')
text=text.replace('RUN sh /work/check-probe-mode.sh',f'RUN sed -i s/qemu-mipsel/{target["emulator"]}/g /work/check-probe-mode.sh && sh /work/check-probe-mode.sh')
profile=(root/'profiles/mipsel-softfloat').read_text().replace('\narch=mips\n','\narch='+target['arch']+'\n').replace('mipsel_24kc_gcc-13.3.0_musl',target['toolchain']).replace('mipsel-openwrt-linux-musl',target['triple']).replace('system_processor=mips','system_processor='+target['processor'])
profile=profile.replace('["-march=mips32r2", "-mabi=32", "-msoft-float"]',json.dumps(target['flags'].split()))
if '-mabi=32' in profile or '-march=mips' in profile or '-march=armv832' in profile:raise ValueError('MIPS flags leaked into ARM profile')
# GCC 13 ARM32 loses the config->n <= SHA256_CBLOCK BSSL_CHECK across calls
# in slhdsa/thash.cc.inc:117. Keep its diagnostic visible, demote only this
# dependency's stringop-overflow warning, without touching crypto source/checks.
if platform=='armv7-softfloat':
    profile+='\nopenssl/*:tools.build:cxxflags='+json.dumps(target['flags'].split()+['-Wno-error=stringop-overflow'])+'\n'
(out/'profile').write_text(profile)
probe=(root/'src/platform-probe.cpp').read_text();a=probe.index('#if !defined(__mips__)');b=probe.index('struct Task',a)
probe=probe[:a]+('static_assert(sizeof(void*) == '+('8' if platform=='aarch64' else '4')+', "Target ABI");\n\n')+probe[b:]
probe=probe.replace('MIPS32 little-endian soft-float',platform+' little-endian')
(out/'probe.cpp').write_text(probe)
text+='''
FROM toolchain AS token-decoder
RUN curl -fL --retry 3 https://github.com/google/brotli/archive/028fb5a23661f123017c060daa546b55cf4bde29.tar.gz -o /tmp/brotli.tar.gz && \\
    mkdir /work/brotli && tar -xzf /tmp/brotli.tar.gz --strip-components=1 -C /work/brotli && \\
    cmake -S /work/brotli -B /work/decoder-build -DCMAKE_SYSTEM_NAME=Linux \\
      -DCMAKE_C_COMPILER=TRIPLE-gcc -DCMAKE_CXX_COMPILER=TRIPLE-g++ \\
      -DCMAKE_C_FLAGS="@@FLAGS@@" -DCMAKE_CXX_FLAGS="@@FLAGS@@" \\
      -DCMAKE_FIND_ROOT_PATH=${STAGING_DIR} -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY \\
      -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY \\
      -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER -DCMAKE_EXE_LINKER_FLAGS=-static \\
      -DBUILD_SHARED_LIBS=OFF -DBROTLI_BUILD_TOOLS=ON -DBROTLI_DISABLE_TESTS=ON && \\
    cmake --build /work/decoder-build --target brotli -j2 && mkdir /decoder && \\
    cp /work/decoder-build/brotli /decoder/ && cp /work/brotli/LICENSE /decoder/LICENSE-BROTLI && \\
    TRIPLE-strip /decoder/brotli && python /work/verify-elf.py /decoder/brotli && \\
    printf 'portable decoder test' > /tmp/plain && EMULATOR /decoder/brotli -c /tmp/plain > /tmp/compressed && \\
    EMULATOR /decoder/brotli -d -c /tmp/compressed > /tmp/restored && cmp /tmp/plain /tmp/restored && \\
    echo https://github.com/google/brotli/tree/028fb5a23661f123017c060daa546b55cf4bde29 > /decoder/BROTLI-SOURCE.txt
FROM scratch AS platform-export
COPY --from=client /out/ /client/
COPY --from=token-decoder /decoder/ /decoder/
'''.replace('TRIPLE',target['triple']).replace('@@FLAGS@@',target['flags']).replace('EMULATOR',target['emulator'])
(out/'Dockerfile').write_text(text)
print(target['sdk'])
