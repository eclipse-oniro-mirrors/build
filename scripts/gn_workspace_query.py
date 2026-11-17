#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

"""Run an explicit GN command against the current relocated workspace."""
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from incremental_workspace import private_mountpoint


BUILD_COMMANDS = {'analyze', 'args', 'check', 'clean', 'desc', 'gen', 'ls',
                  'meta', 'outputs', 'path', 'refs'}


def source_root(directory):
    for parent in (directory, *directory.parents):
        if (parent / '.gn').is_file():
            return parent
    raise RuntimeError('Run gn-wrapper.sh inside an OpenHarmony source tree')


def build_directory_argument(arguments):
    positional = [(i, arg) for i, arg in enumerate(arguments) if not arg.startswith('-')]
    if len(positional) < 2 or positional[0][1] not in BUILD_COMMANDS:
        return None
    return positional[1][0]


def build_directory(value, cwd, root):
    if value.startswith('//'):
        return Path(os.path.abspath(root / value[2:]))
    return Path(os.path.abspath(cwd / value))


def map_argument(value, root, canonical):
    prefix = str(root)
    if value == prefix or value.startswith(prefix + os.sep):
        return str(canonical) + value[len(prefix):]
    for option in ('--root=', '--dotfile='):
        if value.startswith(option):
            return option + map_argument(value[len(option):], root, canonical)
    return value


def canonical_root(build, root):
    manifest = build / 'incremental_workspace.json'
    if not manifest.is_file():
        return root
    data = json.loads(manifest.read_text())
    value = data.get('canonical_root')
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError('Invalid canonical_root in incremental workspace manifest')
    canonical = Path(os.path.normpath(value))
    if canonical == Path('/'):
        raise ValueError('canonical_root cannot be the filesystem root')
    if canonical == root or (canonical.exists() and os.path.samefile(canonical, root)):
        return root
    return canonical


def main(arguments):
    cwd = Path.cwd()
    root = source_root(cwd)
    operating_system = 'mac' if sys.platform == 'darwin' else 'linux'
    machine = platform.machine().lower()
    cpu = 'x86'
    if machine in ('aarch64', 'arm64'):
        cpu = 'arm64' if operating_system == 'mac' else 'aarch64'
    relative_tool = Path('prebuilts/build-tools') / (operating_system + '-' + cpu) / 'bin/gn'
    tool = root / relative_tool
    index = build_directory_argument(arguments)
    if index is None:
        os.execv(str(tool), [str(tool), *arguments])
    build = build_directory(arguments[index], cwd, root)
    if not build.is_relative_to(root):
        os.execv(str(tool), [str(tool), *arguments])
    canonical = canonical_root(build, root)
    if canonical == root:
        os.execv(str(tool), [str(tool), *arguments])
    if shutil.which('bwrap') is None:
        raise RuntimeError('Relocated GN commands require bubblewrap (bwrap)')
    logical_cwd = canonical / cwd.relative_to(root)
    mapped = [map_argument(arg, root, canonical) for arg in arguments]
    mapped[index] = str(canonical / build.relative_to(root))
    command = ['bwrap', '--die-with-parent', '--bind', '/', '/',
               *private_mountpoint(canonical), '--proc', '/proc',
               '--dev-bind', '/dev', '/dev', '--bind', str(root), str(canonical),
               '--chdir', str(logical_cwd), '--', str(canonical / relative_tool), *mapped]
    os.execvpe(command[0], command, os.environ.copy())


if __name__ == '__main__':
    main(sys.argv[1:])
