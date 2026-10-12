#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLVM_DIR="${LLVM_DIR:-/home/blueyouth/code/master/prebuilts/clang/ohos/linux-x86_64/llvm}"
NAME="libclang_rt.raceguard-aarch64.so"

"$LLVM_DIR/bin/clang++" -std=c++14 -O2 -fPIC -shared -fno-rtti -fno-exceptions \
  -nostdinc++ \
  -isystem "$LLVM_DIR/include/x86_64-unknown-linux-gnu/c++/v1" \
  -isystem "$LLVM_DIR/include/c++/v1" \
  -I"$LLVM_DIR/include" \
  -nostdlib++ \
  "$DIR/OurCheckerPass.cpp" -o "$DIR/$NAME"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$LLVM_DIR/plugins/PAC/lib"
  cp "$DIR/$NAME" "$LLVM_DIR/plugins/PAC/lib/$NAME"
  echo "installed to $LLVM_DIR/plugins/PAC/lib/$NAME"
fi
