#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Record shader completion and source dependencies without directory outputs."""
import argparse
from pathlib import Path
import subprocess
from util import build_utils


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--depfile', required=True)
    parser.add_argument('--input-root', action='append', default=[])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('missing shader command')
    subprocess.run(command, check=True)
    dependencies = {command[0]}
    for value in args.input_root:
        root = Path(value)
        if not root.exists():
            raise FileNotFoundError(root)
        # Source directories detect added/deleted assets. Generated directories
        # are never inputs, as their mtimes change during downstream builds.
        dependencies.add(str(root))
        if root.is_dir():
            dependencies.update(str(p) for p in root.rglob('*'))
    # A completion marker alone cannot detect a deleted shader/copied asset.
    # Track files produced by this invocation as implicit dependencies too.
    # Do not track generated directories: downstream ROFS jobs mutate them.
    if len(command) > 1:
        generated = Path(command[1])
        dependencies.update(str(p) for p in generated.rglob('*') if p.is_file())
    build_utils.write_depfile(args.depfile, args.stamp, sorted(dependencies),
                             relative_inputs=True)
    stamp = Path(args.stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()


if __name__ == '__main__':
    main()
