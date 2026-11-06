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
import json
import os
from pathlib import Path
import subprocess
import tempfile
from util import build_utils


def dependency_report(path):
    """An optional executed-script report uses paths relative to the action cwd."""
    if not path.is_file():
        return {}
    with path.open(encoding='utf-8') as stream:
        report = json.load(stream)
    if (not isinstance(report, dict) or type(report.get('version')) is not int
            or report['version'] != 1):
        raise ValueError('Unsupported shader dependency report')
    for name in ('input_files', 'input_roots', 'output_roots'):
        values = report.get(name, [])
        if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values):
            raise ValueError('Invalid shader dependency report field: '+name)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--depfile', required=True)
    parser.add_argument('--input', action='append', default=[])
    parser.add_argument('--input-root', action='append', default=[])
    parser.add_argument('--output-root', action='append', default=[])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('missing shader command')
    with tempfile.TemporaryDirectory(prefix='ohos-shader-deps-') as temporary:
        report_path = Path(temporary)/'dependencies.json'
        env = dict(os.environ, OHOS_SHADER_DEPENDENCY_FILE=str(report_path))
        subprocess.run(command, check=True, env=env)
        report = dependency_report(report_path)
    dependencies = {command[0]}
    for value in args.input+report.get('input_files', []):
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(path)
        dependencies.add(str(path))
    for value in args.input_root+report.get('input_roots', []):
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
    generated_roots = args.output_root+report.get('output_roots', [])
    if not generated_roots and len(command) > 1:
        # Compatibility with the original wrapper's positional output protocol.
        # Updated GN templates supply --output-root explicitly.
        generated_roots = [command[1]]
    for value in generated_roots:
        generated = Path(value)
        dependencies.update(str(p) for p in generated.rglob('*') if p.is_file())
    build_utils.write_depfile(args.depfile, args.stamp, sorted(dependencies),
                             relative_inputs=True)
    stamp = Path(args.stamp)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()


if __name__ == '__main__':
    main()
