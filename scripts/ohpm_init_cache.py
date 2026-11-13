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
"""Validate installed tools locally or restore a verified workspace snapshot.

Host metadata accelerates hashing only within the same boot/workspace/HOME.
Portable decisions use relative names, contents, permissions and link targets.
No user configuration or credentials are archived.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import tarfile
import tempfile

VERSION = 2
LAYOUT = {
    'pnpm7': '.hvigor/wrapper/tools/node_modules',
    'pnpm10': '.hvigor/wrapper/tools/10.28.2/node_modules',
    'hypium': '.ohpm/oh_modules',
}
PROJECTS = {
    '.hvigor/wrapper/tools/package.json': '{"dependencies": {"pnpm": "7.30.0"}}\n',
    '.hvigor/wrapper/tools/10.28.2/package.json': '{"dependencies": {"pnpm": "10.28.2"}}\n',
    '.ohpm/oh-package.json5': '{"devDependencies":{"@ohos/hypium":"1.0.6"}}\n',
}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.ohpm-init-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(value) + b'\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def local_path(home):
    return home / '.ohpm/.openharmony-init-cache.json'


def portable_path(root):
    path = root / 'out/.incremental/ohpm-init/manifest.json'
    if not inside(path.parent.resolve(), root.resolve()) or path.is_symlink():
        raise ValueError('Portable initialization cache must stay inside the workspace')
    return path


def identity(root, home):
    def directory(path):
        info = path.stat()
        return [str(path.resolve()), info.st_dev, info.st_ino]
    return [Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            directory(root), directory(home)]


def signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def inside(path, root):
    return path == root or root in path.parents


class Inventory:
    def __init__(self, cached=None):
        self.cached = cached or {}
        self.stats = {}
        self.hashed_bytes = 0

    def tree(self, root, tag):
        root = root.absolute()
        if root.is_symlink():
            raise ValueError('Snapshot roots must be real directories/files')
        resolved_root = root.resolve(strict=True)
        rows = {}
        pending = [(root, '.')]
        directories = []
        while pending:
            path, name = pending.pop()
            before = path.lstat()
            key = tag + '/' + name
            row = {'mode': stat.S_IMODE(before.st_mode)}
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(path)
                resolved = path.resolve(strict=True)
                if os.path.isabs(target) or not inside(resolved, resolved_root):
                    raise ValueError('External or absolute package/tool link: ' + str(path))
                row.update(kind='link', target=target)
            elif stat.S_ISDIR(before.st_mode):
                row['kind'] = 'directory'
                directories.append((path, signature(before)))
                pending.extend((child, str(child.relative_to(root)))
                               for child in sorted(path.iterdir(), reverse=True))
            elif stat.S_ISREG(before.st_mode):
                previous = self.cached.get(key, {})
                digest = previous.get('sha256') if previous.get('stat') == signature(before) else None
                if not digest:
                    digest = sha_file(path)
                    self.hashed_bytes += before.st_size
                row.update(kind='file', size=before.st_size, sha256=digest)
            else:
                raise ValueError('Unsupported snapshot entry: ' + str(path))
            if signature(before) != signature(path.lstat()):
                raise ValueError('Initialization files changed during validation')
            rows[name] = row
            self.stats[key] = {'stat': signature(before), 'sha256': row.get('sha256')}
        for path, before in directories:
            if signature(path.lstat()) != before:
                raise ValueError('Initialization directory changed during validation')
        return rows


def npm_root(entry):
    entry = entry.resolve(strict=True)
    for parent in list(entry.parents)[:5]:
        manifest = parent / 'package.json'
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            if (isinstance(data, dict) and data.get('name') == 'npm'
                    and (parent / 'bin/npm-cli.js').resolve() == entry):
                return parent
    raise ValueError('Cannot identify a bounded npm package directory')


def context(root, home, inventory):
    if os.environ.get('NODE_PATH') or any(
            value == '-r' or value.startswith(('--require', '--import', '--loader', '--experimental-loader'))
            for value in shlex.split(os.environ.get('NODE_OPTIONS', ''))):
        raise ValueError('Custom Node module injection requires the original initializer')
    node, npm = shutil.which('node'), shutil.which('npm')
    if not node or not npm:
        raise ValueError('Node/npm is missing')
    roots = {'node': Path(node).resolve(strict=True), 'npm': npm_root(Path(npm)),
             'ohpm': root / 'prebuilts/tool/command-line-tools/ohpm',
             'entry': root / 'build/build_scripts/build.sh', 'helper': Path(__file__)}
    tools = {name: inventory.tree(path, 'tools/' + name) for name, path in roots.items()}
    def normalize(value):
        for path, marker in sorted([(str(root), '<workspace>'), (str(home), '<home>')],
                                   key=lambda item: len(item[0]), reverse=True):
            value = value.replace(path + '/', marker + '/').replace('='+path, '='+marker)
            if value == path:
                value = marker
        return value
    environment = {key: normalize(value) for key, value in os.environ.items()
                   if key.lower().startswith(('npm_config_', 'ohpm_'))
                   or (key.startswith('NODE_') and key != 'NODE_HOME')}
    configs = {'user-npm': home / '.npmrc', 'user-ohpm': home / '.ohpm/.ohpmrc',
               'project-npm': root / '.npmrc', 'system-npm': Path('/etc/npmrc'),
               'global-npm': Path(node).resolve().parent.parent / 'etc/npmrc'}
    file_variables = {'npm_config_userconfig', 'npm_config_globalconfig', 'npm_config_cafile',
                      'node_extra_ca_certs'}
    for key, value in os.environ.items():
        if key.lower() in file_variables and value:
            configs['env-' + key.lower()] = Path(value)
        if key.lower() == 'npm_config_prefix' and value:
            configs['prefix-npm'] = Path(value) / 'etc/npmrc'
    # npmrc may redirect user/global configuration or CA files. Read those
    # bounded files too; never promote an executable ancestor to a scan root.
    pending = list(configs.items())
    seen = set()
    while pending:
        name, path = pending.pop()
        if path in seen or not path.is_file() or name == 'user-ohpm':
            continue
        seen.add(path)
        if len(seen) > 32:
            raise ValueError('Too many npm configuration references')
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition('=')
            if not sep or key.strip().lower() not in ('prefix', 'globalconfig', 'userconfig', 'cafile'):
                continue
            value = os.path.expandvars(value.strip().strip('\"\''))
            candidate = Path(value).expanduser()
            if not candidate.is_absolute() or '${' in value:
                raise ValueError('Unresolved npm configuration path')
            if key.strip().lower() == 'prefix':
                candidate = candidate / 'etc/npmrc'
            tag = 'referenced-' + normalize(str(candidate))
            if tag not in configs:
                configs[tag] = candidate
                # Certificates are data, not recursively parsed npmrc files.
                if key.strip().lower() != 'cafile':
                    pending.append((tag, candidate))
    hashes = {name: sha_file(path) if path.is_file() else None for name, path in configs.items()}
    return {'host': [platform.system(), platform.machine(), list(platform.libc_ver())],
            'tools': tools, 'environment': sha_bytes(encoded(environment)), 'configuration': hashes}


def require_packages(home, projects=True):
    for tag, version in [('pnpm7', '7.30.0'), ('pnpm10', '10.28.2')]:
        root = home / LAYOUT[tag]
        data = json.loads((root / 'pnpm/package.json').read_text())
        if not isinstance(data, dict) or data.get('name') != 'pnpm' or data.get('version') != version:
            raise ValueError('Installed pnpm version changed')
        if not (root / 'pnpm/dist/pnpm.cjs').is_file() or not os.access(root / '.bin/pnpm', os.X_OK):
            raise ValueError('Installed pnpm entry is missing')
    root = home / LAYOUT['hypium'] / '@ohos/hypium'
    data = json.loads((root / 'oh-package.json5').read_text())
    if not isinstance(data, dict) or data.get('name') != '@ohos/hypium' or data.get('version') != '1.0.6':
        raise ValueError('Installed hypium version changed')
    if not (root / 'index.js').is_file():
        raise ValueError('Installed hypium entry is missing')
    if projects:
        for name, expected in PROJECTS.items():
            if json.loads((home / name).read_text()) != json.loads(expected):
                raise ValueError('Initialization project manifest changed')


def payload(home, inventory):
    require_packages(home)
    return {tag: inventory.tree(home / relative, 'payload/' + tag) for tag, relative in LAYOUT.items()}


def scan(root, home, previous=None):
    instance = identity(root, home)
    cached = previous.get('stats') if isinstance(previous, dict) and previous.get('instance') == instance else None
    inventory = Inventory(cached)
    result = {'version': VERSION, 'instance': instance, 'context': context(root, home, inventory),
              'payload': payload(home, inventory)}
    result['stats'] = inventory.stats
    return result, inventory.hashed_bytes


def check(root, home):
    previous = json.loads(local_path(home).read_text())
    if not isinstance(previous, dict) or previous.get('version') != VERSION:
        return False
    current, hashed = scan(root, home, previous)
    if current['context'] != previous.get('context') or current['payload'] != previous.get('payload'):
        return False
    if current != previous:
        write_json(local_path(home), current)
    print('incremental initialization: verified installed packages; hashed_bytes=' + str(hashed))
    return True


def archive_payload(path, home, rows):
    # Identical payloads share one archive across repeated records. Neither
    # temporary filenames nor capture times enter the compressed bytes.
    with open(path, 'wb') as output, gzip.GzipFile(filename='', fileobj=output, mode='wb',
                                                compresslevel=1, mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w') as archive:
        for tag in sorted(rows):
            for name, row in sorted(rows[tag].items()):
                path = home / LAYOUT[tag] / name
                info = tarfile.TarInfo(tag + ('/' + name if name != '.' else ''))
                info.mode = row['mode']
                info.mtime = 0
                if row['kind'] == 'directory':
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                elif row['kind'] == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = row['target']
                    archive.addfile(info)
                else:
                    content = path.read_bytes()
                    if len(content) != row['size'] or sha_bytes(content) != row['sha256']:
                        raise ValueError('Package changed while archiving')
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))


def record(root, home):
    current, hashed = scan(root, home)
    manifest = portable_path(root)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.payload-', dir=manifest.parent)
    os.close(fd)
    try:
        archive_payload(temporary, home, current['payload'])
        digest = sha_file(Path(temporary))
        name = 'payload-' + digest + '.tar.gz'
        os.replace(temporary, manifest.parent / name)
        verified, _ = scan(root, home, current)
        if verified != current:
            raise ValueError('Initialization changed before publication')
        write_json(manifest, {'version': VERSION, 'context': current['context'],
                              'payload': current['payload'], 'archive': name, 'archive_sha256': digest})
        write_json(local_path(home), current)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('incremental initialization: recorded portable packages; hashed_bytes=' + str(hashed))


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name:
        raise ValueError('Unsafe snapshot member')
    return path


def extract_payload(path, destination, expected):
    wanted = {tag + ('/' + name if name != '.' else ''): row
              for tag, rows in expected.items() for name, row in rows.items()}
    seen = set()
    links = []
    directories = []
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = member.name.rstrip('/')
            target = destination / str(safe_name(name))
            if name in seen or name not in wanted:
                raise ValueError('Unexpected or duplicate snapshot member')
            seen.add(name)
            row = wanted[name]
            if stat.S_IMODE(member.mode) != row['mode']:
                raise ValueError('Snapshot permissions changed')
            if row['kind'] == 'directory' and member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                directories.append((target, row['mode']))
            elif row['kind'] == 'link' and member.issym() and member.linkname == row['target']:
                if os.path.isabs(member.linkname):
                    raise ValueError('Absolute snapshot link')
                links.append((target, member.linkname))
            elif row['kind'] == 'file' and member.isfile() and member.size == row['size']:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream, target.open('xb') as output:
                    shutil.copyfileobj(stream, output)
                if sha_file(target) != row['sha256']:
                    raise ValueError('Snapshot file content changed')
                target.chmod(row['mode'])
            else:
                raise ValueError('Snapshot entry type changed')
    if seen != wanted.keys():
        raise ValueError('Snapshot is incomplete')
    for path, value in links:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(value)
    for path, mode in reversed(directories):
        path.chmod(mode)
    inventory = Inventory()
    for tag in LAYOUT:
        if inventory.tree(destination / tag, 'verify/' + tag) != expected[tag]:
            raise ValueError('Restored package tree differs from snapshot')


def restore(root, home):
    manifest = portable_path(root)
    data = json.loads(manifest.read_text())
    if not isinstance(data, dict) or data.get('version') != VERSION or set(data.get('payload', {})) != set(LAYOUT):
        return False
    inventory = Inventory()
    if context(root, home, inventory) != data.get('context'):
        return False
    name = data.get('archive', '')
    if not re.fullmatch(r'payload-[0-9a-f]{64}\.tar\.gz', name):
        raise ValueError('Invalid archive name')
    archive = manifest.parent / name
    if archive.is_symlink() or sha_file(archive) != data.get('archive_sha256'):
        raise ValueError('Snapshot archive changed')
    # Never replace unknown existing package installations or user manifests.
    missing = []
    for tag, relative in LAYOUT.items():
        destination = home / relative
        if not inside(destination.parent.resolve(), home.resolve()):
            return False
        if os.path.lexists(destination):
            if inventory.tree(destination, 'existing/' + tag) != data['payload'][tag]:
                return False
        else:
            missing.append(tag)
    for name, expected in PROJECTS.items():
        path = home / name
        if path.is_symlink() or not inside(path.parent.resolve(), home.resolve()):
            return False
        if path.exists() and json.loads(path.read_text()) != json.loads(expected):
            return False
    home_cache = home / '.ohpm'
    home_cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.init-restore-', dir=home_cache) as temporary:
        staging = Path(temporary)
        extract_payload(archive, staging, data['payload'])
        # Validate package versions before publishing anything into HOME.
        staged_home = staging / 'home'
        for tag, relative in LAYOUT.items():
            target = staged_home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging / tag, target)
        require_packages(staged_home, projects=False)
        installed = []
        try:
            for tag in missing:
                destination = home / LAYOUT[tag]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink() or destination.exists():
                    raise ValueError('Initialization destination changed')
                # Stage on the destination filesystem for an atomic rename.
                with tempfile.TemporaryDirectory(prefix='.init-publish-', dir=destination.parent) as local:
                    candidate = Path(local) / 'payload'
                    shutil.copytree(staged_home / LAYOUT[tag], candidate, symlinks=True)
                    os.rename(candidate, destination)
                installed.append(destination)
            for name, expected in PROJECTS.items():
                path = home / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(expected)
            current, _ = scan(root, home)
            if current['context'] != data['context'] or current['payload'] != data['payload']:
                raise ValueError('Initialization changed during restoration')
            write_json(local_path(home), current)
        except (OSError, ValueError, RuntimeError):
            for path in reversed(installed):
                shutil.rmtree(path)
            raise
    print('incremental initialization: verified workspace snapshot; restored_package_roots=' + str(len(missing)))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'record', 'restore'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root, home = args.root.absolute(), Path.home().absolute()
    try:
        if args.mode == 'check':
            return 0 if check(root, home) else 1
        if args.mode == 'restore':
            return 0 if restore(root, home) else 1
        record(root, home)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, tarfile.TarError) as error:
        print('incremental initialization: ' + args.mode + ' unavailable (' + type(error).__name__ + ')')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
