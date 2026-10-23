#!/usr/bin/env bash
#
# Copyright (c) 2024 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Build the raceguard test pass plugin and optionally install it into the
# prebuilt clang toolchain (llvm/lib, where -fpass-plugin=<name> resolves it
# through clang's RUNPATH).
#
# Usage:
#   ./build.sh                 # build the plugin
#   ./build.sh --install       # build and install the plugin into $LLVM_DIR/lib
#   ./build.sh --runtime       # build the reference __raceguard_test_probe runtime
#   ./build.sh --install-runtime
#   ./build.sh --install-config   # copy the test raceguard_config.json into the
#                                 # build framework so use_raceguard takes effect
#   ./build.sh --remove-config    # remove the copied test raceguard_config.json
#
# Environment overrides:
#   LLVM_DIR      clang toolchain root
#   NAME          plugin file name (must match the product's raceguard config)
#   RUNTIME_NAME  runtime file name

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
LLVM_DIR="${LLVM_DIR:-$ROOT/prebuilts/clang/ohos/linux-x86_64/llvm}"
NAME="${NAME:-libclang_rt.raceguard-aarch64.so}"
RUNTIME_NAME="${RUNTIME_NAME:-libraceguard_test_runtime.so}"
OUT_DIR="${OUT_DIR:-$ROOT/out/raceguard_test_plugin}"

mkdir -p "$OUT_DIR"

CXX="$LLVM_DIR/bin/clang++"
LLVM_INC="$LLVM_DIR/include"
LIBCXX_INC="$LLVM_DIR/include/c++/v1"
LIBCXX_HOST_INC="$LLVM_DIR/include/x86_64-unknown-linux-gnu/c++/v1"

if [ ! -x "$CXX" ]; then
  echo "error: clang++ not found at $CXX (set LLVM_DIR)" >&2
  exit 1
fi

build_plugin() {
  "$CXX" -std=c++14 -O2 -fPIC -shared -fno-rtti -fno-exceptions \
    -nostdinc++ \
    -isystem "$LIBCXX_HOST_INC" \
    -isystem "$LIBCXX_INC" \
    -I"$LLVM_INC" \
    -nostdlib++ \
    "$DIR/OurCheckerPass.cpp" -o "$OUT_DIR/$NAME"
  echo "built $OUT_DIR/$NAME"
}

build_runtime() {
  "$CXX" -std=c++14 -O2 -fPIC -shared -fno-rtti -fno-exceptions \
    -nostdlib++ \
    "$DIR/TestProbeRuntime.cpp" -o "$OUT_DIR/$RUNTIME_NAME"
  echo "built $OUT_DIR/$RUNTIME_NAME"
}

install_file() {
  local src="$1"
  local dst_dir="$2"
  mkdir -p "$dst_dir"
  cp "$src" "$dst_dir/"
  echo "installed $(basename "$src") to $dst_dir"
}

case "${1:-}" in
  --install)
    build_plugin
    install_file "$OUT_DIR/$NAME" "$LLVM_DIR/lib"
    ;;
  --runtime)
    build_runtime
    ;;
  --install-runtime)
    build_runtime
    install_file "$OUT_DIR/$RUNTIME_NAME" "$LLVM_DIR/lib"
    ;;
  --install-config)
    install_file "$DIR/raceguard_config.json" \
      "$ROOT/build/config/sanitizers"
    ;;
  --remove-config)
    rm -f "$ROOT/build/config/sanitizers/raceguard_config.json"
    echo "removed $ROOT/build/config/sanitizers/raceguard_config.json"
    ;;
  "")
    build_plugin
    ;;
  *)
    echo "usage: $0 [--install|--runtime|--install-runtime|--install-config|--remove-config]" >&2
    exit 1
    ;;
esac
