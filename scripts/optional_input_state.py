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
"""Observe named optional inputs without depending on unrelated parent entries."""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


def input_kind(path):
    try:
        return stat.S_IFMT(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return 0


def input_state(path):
    return [input_kind(path), os.readlink(path) if path.is_symlink() else None]


def update_state(destination, paths):
    destination = Path(destination).absolute()
    data = [[os.path.relpath(path, destination.parent), input_state(Path(path))] for path in paths]
    content = json.dumps({'version': 1, 'inputs': data}, sort_keys=True) + '\n'
    try:
        if destination.read_text(encoding='utf-8') == content:
            return
    except (FileNotFoundError, UnicodeError):
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.optional-', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_plans(build):
    build = Path(build).absolute()
    for path in sorted((build / '.optional_input_plans').glob('**/*.json')):
        plan = json.loads(path.read_text(encoding='utf-8'))
        state = Path(plan['state'])
        if plan.get('version') != 1 or state.is_absolute() or '..' in state.parts:
            raise ValueError('Invalid optional input plan: ' + str(path))
        update_state(build / state, [build / name for name in plan['paths']])


def prepare_optional_inputs(arguments):
    from scripts.incremental_product_policy import resolve

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('-p', '--product-name')
    request, _ = parser.parse_known_args(arguments)
    if request.product_name:
        arguments = ['--product-name', request.product_name, *arguments]
    root = Path(__file__).resolve().parents[2]
    policy = resolve(root, arguments)
    if policy['enabled']:
        check_plans(root / policy['build_dir'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    check_plans(parser.parse_args().build)
