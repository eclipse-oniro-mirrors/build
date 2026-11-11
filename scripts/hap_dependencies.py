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
"""HAP input discovery and project-local SDK cache invalidation."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile

IGNORED_DIRS = {'.git', '.hg', '.svn', '.hvigor', '.cache', '__pycache__',
                '.idea', '.vscode', '.gradle', '.cxx'}
IGNORED_FILES = {'local.properties', '.DS_Store', '.git', '.hg', '.svn'}


def ignored(name):
    return name in IGNORED_FILES or name.endswith(('.log', '.tsbuildinfo', '.tsbuildinfo.linter'))


def inventory(roots, excluded=()):
    """Keep directories for membership changes and files for ordinary edits."""
    excluded = {os.path.realpath(p) for p in excluded}
    paths, visited = set(), set()
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.exists(root):
            paths.add(root)
            parent = Path(root).parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            paths.add(str(parent))
            continue
        if not os.path.isdir(root):
            paths.add(root)
            continue
        def failed(error):
            raise error
        for directory, directories, files in os.walk(root, followlinks=True, onerror=failed):
            paths.add(directory)
            info = os.stat(directory)
            identity = (info.st_dev, info.st_ino)
            if identity in visited:
                directories[:] = []
                continue
            visited.add(identity)
            directories[:] = sorted(name for name in directories if name not in IGNORED_DIRS
                                    and (not excluded or os.path.realpath(os.path.join(directory, name)) not in excluded))
            paths.update(os.path.join(directory, name) for name in files if not ignored(name))
    return sorted(paths)


def signature(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode]


def fingerprint(path, old=None):
    before = signature(path.stat())
    if isinstance(old, dict) and old.get('stat') == before:
        return old
    value = hashlib.sha256()
    with path.open('rb') as stream:
        if signature(os.fstat(stream.fileno())) != before:
            raise RuntimeError('Input changed while opening: ' + str(path))
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
        if signature(os.fstat(stream.fileno())) != before:
            raise RuntimeError('Input changed while reading: ' + str(path))
    if signature(path.stat()) != before:
        raise RuntimeError('Input changed while hashing: ' + str(path))
    return {'stat': before, 'sha256': value.hexdigest()}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path, value, force=False):
    path = Path(path)
    content = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()
    if not force and path.is_file() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name+'.')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def sdk_inputs(sdk, cache):
    """Cache content hashes, never SDK file/directory membership discovery."""
    sdk = Path(sdk).absolute()
    if not sdk.is_dir():
        raise FileNotFoundError('Required HAP SDK is missing: ' + str(sdk))
    with lock(str(cache)+'.lock'):
        previous = read_json(cache)
        old = previous.get('files', {}) if isinstance(previous, dict) and previous.get('schema') == 1 else {}
        if not isinstance(old, dict):
            old = {}
        files, directories, paths = {}, [], inventory([sdk])
        for name in paths:
            path = Path(name)
            relative = str(path.relative_to(sdk))
            if path.is_dir():
                directories.append(relative)
            else:
                files[relative] = fingerprint(path, old.get(relative))
        digest = hashlib.sha256(json.dumps(
            [directories, [(name, data['sha256'], bool(data['stat'][-1] & 0o111))
                           for name, data in sorted(files.items())]],
            separators=(',', ':')).encode()).hexdigest()
        write_json(cache, {'schema': 1, 'files': files, 'directories': directories})
    return digest, paths


def module_roots(profile, cwd):
    return [Path(cwd)/module['srcPath'] for module in profile.get('modules', [])
            if module.get('srcPath')]


def project_inputs(profile, cwd):
    roots = [Path(cwd), *module_roots(profile, cwd)]
    return inventory(roots, [root/'build' for root in roots])



def is_within(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False

def depfile_inputs(path):
    """Decode a GN/Ninja depfile without treating escaped spaces as separators."""
    text = Path(path).read_text().replace('\\\n', '')
    parts = re.split(r':\s', text, maxsplit=1)
    if len(parts) != 2:
        raise ValueError('Invalid dependency file: ' + str(path))
    return [re.sub(r'\\([ \t#\\:])', r'\1', word).replace('$$', '$')
            for word in re.findall(r'(?:\\.|[^\s])+', parts[1])]
