#!/bin/sh
# Read Entware metadata as data. Never source an installation-controlled file.
set -eu
ROOT=${FPTN_PREFIX:-/opt}
release=$ROOT/etc/entware_release
[ -r "$release" ] || { echo 'Не найден entware_release.' >&2; exit 1; }
value() { awk -F= -v key="$1" '$1==key {gsub(/["\047\r]/,"",$2); print $2}' "$release"; }
case "$(value arch):$(value cpu_type):$(value float)" in
  mipsel:mips32r2:soft) printf '%s\n' mipsel-mips32r2-softfloat ;;
  aarch64:*)
    [ "$(uname -m)" = aarch64 ] || { echo 'Архитектура ядра отличается от Entware aarch64.' >&2; exit 1; }
    printf '%s\n' aarch64 ;;
  arm:*)
    [ "$(uname -m)" = armv7l ] || { echo 'Для статической ARM-сборки нужно little-endian ядро ARMv7.' >&2; exit 1; }
    printf '%s\n' armv7-softfloat ;;
  *) echo 'Для этой архитектуры Entware готовая сборка не поддерживается.' >&2; exit 1 ;;
esac
