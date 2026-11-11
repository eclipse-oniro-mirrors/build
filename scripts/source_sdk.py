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
"""Validate source-built HAP SDKs; leave downloaded SDKs under existing policy."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

_spec = importlib.util.spec_from_file_location('_hap_sdk_dependencies',
                                              Path(__file__).with_name('hap_dependencies.py'))
dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dependencies)


def state_path(root, api):
    return Path(root)/'prebuilts/ohos-sdk'/('.source-sdk-'+api+'.json')


def has_source_graph(root):
    build = Path(root)/'out/sdk'
    return ((build/'build.ninja').is_file() and (build/'args.gn').is_file()
            and re.search(r'^sdk_for_hap_build\s*=\s*true\s*$',
                          (build/'args.gn').read_text(), re.M) is not None)


def _key(root, path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _input_paths(root):
    """Query actual SDK graph only after a successful SDK build, never every no-op."""
    root = Path(root).absolute()
    build = root/'out/sdk'
    ninja = root/'prebuilts/build-tools'/('linux-'+('aarch64' if os.uname().machine == 'aarch64' else 'x86'))/'bin/ninja'
    paths, values = set(), set()
    for tool in ['inputs', 'deps']:
        command = [str(ninja), '-w', 'dupbuild=warn', '-C', str(build), '-t', tool]
        if tool == 'inputs':
            command.append('build_ohos_sdk')
        with tempfile.TemporaryFile(mode='w+t') as errors:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True)
            for line in process.stdout:
                if tool == 'deps' and not line.startswith('    '):
                    continue
                value = line.strip()
                if value:
                    values.add(value)
            process.stdout.close()
            if process.wait():
                errors.seek(0)
                raise RuntimeError('Cannot capture SDK '+tool+': '+errors.read()[-2000:])
    # The deps log repeats popular headers for thousands of objects. Deduplicate
    # before any filesystem calls rather than stat every repeated dependency.
    for value in values:
        path = Path(os.path.abspath(build/value))
        if (not dependencies.is_within(path, root/'out')
                and not dependencies.is_within(path, root/'prebuilts/ohos-sdk') and path.exists()):
            paths.add(path)
    # GN regeneration dependencies are not exposed by `ninja -t inputs
    # build_ohos_sdk`. Include them explicitly so .gn/.gni configuration edits refresh
    # the SDK before the product uses it, including with --fast-rebuild.
    gn_depfile = build/'build.ninja.d'
    if gn_depfile.is_file():
        for value in dependencies.depfile_inputs(gn_depfile):
            path = Path(os.path.abspath(build/value))
            if path.exists() and not dependencies.is_within(path, root/'out'):
                paths.add(path)
    # API membership must invalidate even when a newly added file is not yet in
    # an old generated graph. These are interface repositories, not SDK caches.
    for name in ['interface/sdk-js', 'interface/sdk_c', 'interface/sdk_cj']:
        if (root/name).is_dir():
            paths.update(Path(p) for p in dependencies.inventory([root/name]) if Path(p).is_file())
    paths.update([Path(__file__), Path(dependencies.__file__)])
    return sorted(paths)


def _source_records(root, paths, previous=None):
    files = {}
    previous = previous or {}
    for path in paths:
        key = _key(root, path)
        if path.is_dir():
            names = sorted(item.name for item in path.iterdir()
                           if item.name not in dependencies.IGNORED_DIRS and not dependencies.ignored(item.name))
            files[key] = {'sha256': hashlib.sha256(json.dumps(names).encode()).hexdigest(),
                          'directory': True}
        else:
            files[key] = dependencies.fingerprint(path, previous.get(key))
    return files


def _members(root):
    return {name: [os.path.relpath(p, root) for p in dependencies.inventory([root/name])]
            for name in ['interface/sdk-js', 'interface/sdk_c', 'interface/sdk_cj'] if (root/name).is_dir()}


def _sdk_digest(root, api):
    relative = 'prebuilts/ohos-sdk/linux/'+api
    name = hashlib.sha256(relative.encode()).hexdigest()+'.json'
    return dependencies.sdk_inputs(root/relative, root/'out/.sdk-inputs'/name)[0]


def needs_build(root, api, context):
    root = Path(root).absolute()
    path = state_path(root, api)
    if not path.exists():
        return has_source_graph(root)  # A package without source provenance stays fixed.
    data = dependencies.read_json(path)
    if (data.get('schema') != 1 or data.get('api') != api or data.get('context') != context
            or not isinstance(data.get('inputs'), dict) or not data['inputs']):
        return True
    if _members(root) != data.get('members'):
        return True
    try:
        files = _source_records(root, [root/name for name in data['inputs']], data['inputs'])
    except FileNotFoundError:
        return True
    if any(not isinstance(old, dict) or files[name]['sha256'] != old.get('sha256')
           for name, old in data['inputs'].items()):
        return True
    try:
        if _sdk_digest(root, api) != data.get('published'):
            return True
    except FileNotFoundError:
        return True
    # Refresh only optional stat metadata. It is outside linux/, so consumers
    # do not rebuild merely because an identical source file was touched.
    data['inputs'] = files
    dependencies.write_json(path, data)
    return False


def record_success(root, api, context):
    root = Path(root).absolute()
    if not has_source_graph(root):
        raise RuntimeError('Successful source SDK has no HAP SDK build graph')
    data = {'schema': 1, 'api': api, 'context': context,
            'inputs': _source_records(root, _input_paths(root)),
            'members': _members(root), 'published': _sdk_digest(root, api)}
    dependencies.write_json(state_path(root, api), data)


def publish(root, api):
    """Stage one owned API version, retain SDK build outputs and other versions."""
    root = Path(root)
    source = root/'out/sdk/ohos-sdk/linux'
    if not source.is_dir():
        raise FileNotFoundError('Required source SDK output is missing: '+str(source))
    home = root/'prebuilts/ohos-sdk'
    home.mkdir(parents=True, exist_ok=True)
    linux = home/'linux'
    target = linux/api
    if linux.is_symlink() or target.is_symlink():
        raise RuntimeError('Refusing to replace a linked preinstalled SDK: '+str(target))
    linux.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sdk-publish-', dir=home) as temporary:
        stage = Path(temporary)/api
        stage.mkdir()
        for path in source.iterdir():
            if path.is_dir():
                shutil.copytree(path, stage if path.name == api else stage/path.name, dirs_exist_ok=True, symlinks=True)
            else:
                # Preserve the original layout for non-versioned root metadata.
                shutil.copy2(path, linux/path.name)
        native = stage/'native';native.mkdir(exist_ok=True)
        for relative in ['sdk-native/os-irrelevant', 'sdk-native/os-specific/linux']:
            directory = root/'out/sdk'/relative
            if directory.is_dir():
                shutil.copytree(directory, native, dirs_exist_ok=True, symlinks=True)
        package = native/'oh-uni-package.json'
        if package.is_file():
            previewer = stage/'previewer';previewer.mkdir(exist_ok=True)
            (previewer/package.name).write_text(package.read_text().replace('Native','Previewer').replace('native','previewer'))
        _preserve_unchanged_times(stage, target)
        backup = Path(temporary)/'previous'
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup.exists():
                backup.rename(target)
            raise


def _preserve_unchanged_times(stage, previous):
    if not previous.is_dir():
        return
    for path in stage.iterdir():
        old = previous/path.name
        if path.is_symlink():
            if old.is_symlink() and os.readlink(path) == os.readlink(old):
                info = old.lstat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns), follow_symlinks=False)
        elif path.is_dir() and old.is_dir() and not old.is_symlink():
            _preserve_unchanged_times(path, old)
        elif path.is_file() and old.is_file() and path.stat().st_size == old.stat().st_size:
            if (path.stat().st_mode & 0o111) == (old.stat().st_mode & 0o111) and dependencies.fingerprint(path)['sha256'] == dependencies.fingerprint(old)['sha256']:
                os.utime(path, ns=(old.stat().st_atime_ns, old.stat().st_mtime_ns))
    names = lambda directory: {p.name for p in directory.iterdir()
                                if p.name not in dependencies.IGNORED_DIRS and not dependencies.ignored(p.name)}
    if names(stage) == names(previous):
        os.utime(stage, ns=(previous.stat().st_atime_ns, previous.stat().st_mtime_ns))
