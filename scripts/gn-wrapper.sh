#!/usr/bin/env bash
# Copyright (c) 2026 Huawei Device Co., Ltd.
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

set -euo pipefail
# Use inside the source tree, for example:
#   build/scripts/gn-wrapper.sh desc out/x86_64_virt //path:target
# This explicit tool is independent of the build.sh entry and baseline capture.
exec python3 "$(dirname "$0")/gn_workspace_query.py" "$@"
