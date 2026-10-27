#!/bin/sh
# Read Entware metadata as data. Never source an installation-controlled file.
set -eu
ROOT=${FPTN_PREFIX:-/opt}
release=$ROOT/etc/entware_release
[ -r "$release" ] || { echo 'Не найден entware_release.' >&2; exit 1; }
value() { awk -F= -v key="$1" '$1==key {gsub(/["\047\r]/,"",$2); print $2}' "$release"; }
if [ "$(value arch)" != mipsel ] || [ "$(value cpu_type)" != mips32r2 ] || [ "$(value float)" != soft ]; then
  echo 'Готовый клиент поддерживает Entware mipsel / mips32r2 / soft-float. Для этой платформы сборка пока не опубликована.' >&2
  exit 1
fi
printf '%s\n' mipsel-mips32r2-softfloat
