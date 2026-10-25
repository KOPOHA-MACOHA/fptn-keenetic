# syntax=docker/dockerfile:1
# SDK supplies a modern compiler only. No OpenWrt package is installed on Keenetic.
FROM --platform=linux/amd64 openwrt/sdk:ramips-mt7621-24.10.7 AS sdk
FROM --platform=linux/amd64 ubuntu:24.04 AS toolchain
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      export CURL_CA_BUNDLE=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca; \
    fi; \
    apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" update && \
    apt-get -o Acquire::https::CaInfo="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}" install -y --no-install-recommends \
      build-essential git ca-certificates curl wget file binutils perl \
      autoconf automake libtool pkg-config python3-pip python3-venv \
      ninja-build bison flex gettext golang-go unzip zip qemu-user && \
    rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python3 -m venv /tools && \
    /tools/bin/pip install --no-cache-dir 'conan>=2.20,<3' 'cmake==3.31.6' ninja
COPY --from=sdk /builder/staging_dir/toolchain-mipsel_24kc_gcc-13.3.0_musl /builder/staging_dir/toolchain-mipsel_24kc_gcc-13.3.0_musl
COPY --from=sdk /builder/staging_dir/host /builder/staging_dir/host
COPY --from=sdk /builder/scripts /builder/scripts
ENV STAGING_DIR=/builder/staging_dir/toolchain-mipsel_24kc_gcc-13.3.0_musl
ENV PATH=/tools/bin:/builder/staging_dir/toolchain-mipsel_24kc_gcc-13.3.0_musl/bin:/builder/staging_dir/host/bin:${PATH}
WORKDIR /work
COPY scripts/fix-static-libgcc.py /work/fix-static-libgcc.py
RUN python /work/fix-static-libgcc.py
COPY sources.lock.json /work/sources.lock.json
COPY profiles/mipsel-softfloat /work/profile
COPY scripts/verify-elf.py /work/verify-elf.py

FROM toolchain AS probe
COPY src/platform-probe.cpp /work/platform-probe.cpp
RUN mkdir /out && \
    mipsel-openwrt-linux-musl-g++ -std=c++20 -Os -march=mips32r2 -mabi=32 \
      -msoft-float -static -pthread /work/platform-probe.cpp -latomic -o /out/fptn-platform-probe && \
    mipsel-openwrt-linux-musl-strip /out/fptn-platform-probe && \
    python /work/verify-elf.py /out/fptn-platform-probe && \
    timeout 30 qemu-mipsel /out/fptn-platform-probe && \
    cp /work/sources.lock.json /out/ && \
    cd /out && sha256sum fptn-platform-probe sources.lock.json > SHA256SUMS

FROM scratch AS probe-export
COPY --from=probe /out/ /

FROM toolchain AS dependencies
COPY scripts/prepare-source.py /work/prepare-source.py
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export GIT_SSL_CAINFO=/run/secrets/proxy_ca; fi; \
    revision="$(python -c 'import json; print(json.load(open("/work/sources.lock.json"))["fptn"])')" && \
    git init /work/upstream && \
    git -C /work/upstream remote add origin https://github.com/fptn-project/fptn.git && \
    git -C /work/upstream fetch --depth=1 origin "$revision" && \
    git -C /work/upstream checkout --detach FETCH_HEAD && \
    test "$(git -C /work/upstream rev-parse HEAD)" = "$revision" && \
    python /work/prepare-source.py /work/upstream /work/sources.lock.json
RUN conan profile detect --force
WORKDIR /work/upstream
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      export REQUESTS_CA_BUNDLE=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca \
             CURL_CA_BUNDLE=/run/secrets/proxy_ca GIT_SSL_CAINFO=/run/secrets/proxy_ca \
             CMAKE_TLS_CAINFO=/run/secrets/proxy_ca; \
    fi; \
    conan install . --output-folder=/build --build=missing \
      --profile:build=default --profile:host=/work/profile -s:b compiler.cppstd=20 \
      -o with_gui_client=False -o openwrt=True

FROM dependencies AS client
COPY scripts/check-cross-includes.py /work/check-cross-includes.py
COPY scripts/prepare-connect-test.py /work/prepare-connect-test.py
COPY scripts/run-connect-test.sh /work/run-connect-test.sh
COPY docs/tunnel-test.md /work/TUNNEL-TEST.md
RUN python /work/prepare-connect-test.py /work/upstream
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/proxy_ca CMAKE_TLS_CAINFO=/run/secrets/proxy_ca; \
    fi; \
    revision="$(python -c 'import json; print(json.load(open("/work/sources.lock.json"))["fptn"])')" && \
    cmake -S /work/upstream -B /build \
      -DCMAKE_TOOLCHAIN_FILE=/build/conan_toolchain.cmake \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-static \
      -DCMAKE_COMPILE_WARNING_AS_ERROR=OFF -DCMAKE_CXX_CLANG_TIDY= \
      -DFPTN_VERSION=0.1.0 && \
    python /work/check-cross-includes.py /build/compile_commands.json && \
    cmake --build /build --target fptn-client-cli -j2
RUN mkdir /out && \
    cp /build/src/fptn-client/fptn-client-cli /out/ && \
    mipsel-openwrt-linux-musl-strip /out/fptn-client-cli && \
    python /work/verify-elf.py /out/fptn-client-cli && \
    timeout 60 qemu-mipsel /out/fptn-client-cli --help > /out/HELP.txt && \
    grep -q -- '--keenetic-connect' /out/HELP.txt && \
    if timeout 60 qemu-mipsel /out/fptn-client-cli --access-token test > /out/GUARD.txt 2>&1; then exit 1; fi && \
    grep -q 'Connection requires --keenetic-connect' /out/GUARD.txt && \
    cp /work/sources.lock.json /out/ && \
    cp /work/upstream/LICENSE /out/LICENSE-FPTN && \
    cp /work/run-connect-test.sh /work/TUNNEL-TEST.md /out/ && \
    cd /out && sha256sum fptn-client-cli HELP.txt sources.lock.json LICENSE-FPTN run-connect-test.sh TUNNEL-TEST.md > SHA256SUMS

FROM scratch AS client-export
COPY --from=client /out/ /
