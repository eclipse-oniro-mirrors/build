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
"""Product-scoped reuse of successful Loader/GN configuration, before Ninja.

GN's depfile remains authoritative for read_file/exec_script dependencies.
Discovery additionally watches configuration/scripts and directory membership,
including configurations that were not present in the previous GN graph.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zlib

from scripts import hap_dependencies as dependencies
from scripts.incremental_product_policy import inherited_snapshot


CONFIG_SUFFIXES = {'.gn', '.gni', '.json', '.json5', '.py', '.sh', '.js',
                   '.yaml', '.yml', '.xml', '.ini', '.cfg', '.toml', '.prop',
                   '.properties', '.build', '.template', '.list'}
OUTPUT_SUFFIXES = CONFIG_SUFFIXES | {'.ninja', '.txt', '.rsp', '.h', '.hpp', '.inc', '.c', '.cc', '.cpp'}
IGNORED_DIRS = {'.git', '.repo', '__pycache__', '.cache', '.hvigor', 'node_modules'}
ROOT_EXCLUDES = {'out', 'report', 'oh_venv'}
# Capture selected wrapper paths as well as GN/Ninja identities. PATH may vary
# without changing the tool selected by HB, but CCACHE_EXEC changes GN commands.
ENV_PREFIXES = ('ohos_', 'gn_', 'python', 'npm_config_', 'ohpm_', 'depot_tools_',
                'ccache_', 'xcache_', 'lcache_')
ENV_NAMES = {'USE_CCACHE', 'USE_XCACHE', 'CACHE_BASE', 'FORCE_MAC_SDK_MIN'}
# These identify an invocation or tune bookkeeping, not its configuration.
# Build timestamps and product policy remain semantic inputs.
RUNTIME_ENV = {'OHOS_INCREMENTAL_VERIFIED_RUN', 'OHOS_INCREMENTAL_ENTERED_PID',
               'OHOS_INCREMENTAL_JOBS', 'OHOS_INCREMENTAL_RESTORE_JOBS'}
CACHE_VERSION = 5
CONFIG_ROOTS = {'build', 'device', 'vendor', 'productdefine', 'component_dist'}
SCRIPT_SUFFIXES = {'.gn', '.gni', '.py', '.sh', '.build', '.template'}
CACHE_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError,
                IndexError, subprocess.SubprocessError, zlib.error)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def environment_digest():
    return digest({key: value for key, value in os.environ.items()
                   if key.upper() not in RUNTIME_ENV and
                   (key.lower().startswith(ENV_PREFIXES) or key.upper() in ENV_NAMES)})


def entries_for(directory, root):
    with os.scandir(directory) as stream:
        return sorted((entry for entry in stream
                       if entry.name not in IGNORED_DIRS and
                       not entry.name.endswith(('.pyc', '.log')) and
                       not (directory == root and (entry.name in ROOT_EXCLUDES or
                            (entry.name.startswith('.') and entry.name != '.gn')))),
                      key=lambda entry: entry.name)


def membership(entries):
    return digest([(entry.name, entry.is_dir(),
                    os.readlink(entry.path) if entry.is_symlink() else '') for entry in entries])


def normalized_roots(roots, root):
    return sorted({os.path.abspath(name) for name in (roots or [root])})


def discover(roots, root, old):
    """Re-enumerate membership over the given roots, never trust an old list.

    Roots are confined to configuration trees and the subsystem paths the
    selected product actually builds, instead of walking the whole source tree.
    A missing subsystem configuration (first run, or tests) falls back to root.
    """
    files, directories, visited = set(), {}, set()
    starts = normalized_roots(roots, root)
    if old.get('roots') == starts and old.get('directories'):
        unchanged = True
        for name, previous in old['directories'].items():
            try:
                info = dependencies.signature(os.stat(name))
            except OSError:
                unchanged = False
                break
            if info == previous['stat']:
                directories[name] = previous
            elif os.path.isdir(name):
                current = membership(entries_for(Path(name), root))
                directories[name] = {'stat': info, 'members': current}
                if current != previous['members']:
                    unchanged = False
                    break
            else:
                unchanged = False
                break
        if unchanged:
            return set(old['discovered']), directories
        directories.clear()

    def walk(directory, configuration_tree=False):
        info = directory.stat()
        identity = (info.st_dev, info.st_ino)
        if identity in visited:
            return
        visited.add(identity)
        entries = entries_for(directory, root)
        directories[str(directory)] = {'stat': dependencies.signature(info),
                                       'members': membership(entries)}
        for entry in entries:
            if entry.is_dir():
                walk(Path(entry.path), configuration_tree or
                     (directory == root and entry.name in CONFIG_ROOTS))
                continue
            suffix = os.path.splitext(entry.name)[1]
            if (entry.name in ('.gn', 'bundle.json') or suffix in SCRIPT_SUFFIXES or
                    (configuration_tree and suffix in CONFIG_SUFFIXES)):
                # Broken links are a cache miss, never a silently ignored input.
                files.add(entry.path)

    for name in starts:
        start = Path(name)
        if start.is_dir():
            walk(start, start.parent == Path(root) and start.name in CONFIG_ROOTS)
        elif not start.exists():
            # Some product subsystem maps also mention repositories absent from
            # this checkout. Watch the nearest existing parent's membership so
            # adding such a root invalidates the old discovery inventory.
            parent = start.parent
            while not parent.is_dir():
                parent = parent.parent
            directories[str(parent)] = {
                'stat': dependencies.signature(parent.stat()),
                'members': membership(entries_for(parent, root))}
        else:
            raise ValueError('Configuration root is not a directory: ' + name)
    return files, directories


def records(paths, previous):
    result = {}
    for name in sorted(paths):
        old = previous.get(name)
        if isinstance(old, dict) and old.get('stat') == dependencies.signature(os.stat(name)):
            result[name] = old
        else:
            result[name] = dependencies.fingerprint(Path(name), old)
    return result


def same_files(left, right):
    return (left.keys() == right.keys() and
            all(left[name]['sha256'] == right[name]['sha256'] and
                left[name]['stat'][-1] == right[name]['stat'][-1] for name in left))


def output_records(paths):
    # Generated outputs need not be hashed: any metadata change can safely
    # regenerate Loader/GN. This avoids reading gigabytes of Ninja/SDK files.
    return {name: dependencies.signature(os.stat(name)) for name in sorted(paths)}


def instance_identity(root, output):
    physical = Path(os.environ.get('OHOS_INCREMENTAL_PHYSICAL_ROOT', str(root)))
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return [boot, str(physical.absolute()),
            *[[path.stat().st_dev, path.stat().st_ino]
              for path in (root, output, physical)]]


def cold_output(output):
    # A lost build.ninja in a previously built out must not become a costly
    # "first build". Preloader may already have created args/metadata, while
    # the following graph/log/artifact paths establish a pre-existing build.
    return not any((output / name).exists() for name in
                   ('build.ninja', 'build.ninja.d', 'build.ninja.stamp',
                    '.ninja_log', '.ninja_deps', 'obj', 'gen', 'packages',
                    'build_configs'))


def transform_paths(data, root, encode):
    """Compact only path fields; preserve configuration values verbatim."""
    prefix = str(root) + os.sep

    def convert(name):
        if encode:
            return '@' + name[len(prefix):] if name.startswith(prefix) else name
        return prefix + name[1:] if name.startswith('@') else name

    value = dict(data)
    inputs = dict(value['inputs'])
    for field in ('files', 'directories'):
        inputs[field] = {convert(name): record for name, record in inputs[field].items()}
    for field in ('roots', 'discovered', 'priority', 'missing'):
        inputs[field] = [convert(name) for name in inputs.get(field, [])]
    value['inputs'] = inputs
    value['outputs'] = {convert(name): record for name, record in value['outputs'].items()}
    return value


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _check_batch(items, kind, root):
    for name, record in items:
        info = dependencies.signature(os.stat(name))
        if info == record['stat']:
            continue
        if kind == 'directory':
            if (info[:2] != record['stat'][:2] or
                    info[-1] != record['stat'][-1] or
                    membership(entries_for(Path(name), root)) != record['members']):
                return False
        else:
            generated = str(root / 'out') + os.sep
            if info[:2] != record['stat'][:2] and not name.startswith(generated):
                return False
            if info[-1] != record['stat'][-1] or dependencies.fingerprint(
                    Path(name), record)['sha256'] != record['sha256']:
                return False
    return True


def validate_records(records, kind, root):
    # Serial warm metadata checks avoid per-path allocations and thread/GIL
    # contention, and stop immediately on the first invalid record.
    return _check_batch(records.items(), kind, root)


class ConfigurationCache:
    def __init__(self, root, output, product, context, log=print, ninja=None,
                 can_record=True):
        self.root = Path(root).absolute()
        self.output = Path(output).absolute()
        self.product = product
        self.context = digest(context)
        self.path = self.output / '.incremental/configuration.snapshot.z'
        self.success_path = self.output / '.incremental/configuration.success.json'
        self.priority_path = self.output / '.incremental/configuration.priority.z'
        self.log = log
        self.previous = {}
        self.inputs = None
        self.reuse = False
        self.generated = False
        self.ninja = ninja
        self.can_record = can_record
        self.header = {}

    @classmethod
    def for_build(cls, module):
        config = module.loader.config
        args = {key: arg.arg_value for key, arg in module.args_dict.items()}
        if (args.get('fast_rebuild') or args.get('build_only_load') or
                args.get('build_only_gn') or args.get('build_target') != ['make_all'] or
                not inherited_snapshot(config.root_path, config.product, config.product_json)):
            return None
        from util.log_util import LogUtil
        context = {'args': args, 'environment': environment_digest(),
                   'python': sys.version, 'executable': sys.executable,
                   'root': config.root_path, 'output': config.out_path,
                   'gn_tool': dependencies.signature(Path(module.target_generator.exec).stat()),
                   'ninja_tool': dependencies.signature(Path(module.target_compiler.exec).stat())}
        cache = cls(config.root_path, config.out_path, config.product, context,
                    LogUtil.hb_info, module.target_compiler.exec,
                    can_record=cold_output(Path(config.out_path)))
        # An existing out with no valid local proof uses the original build.
        # In particular, never parse/recapture an old 163 MB record on upgrade,
        # migration, failed builds, argument changes or malformed state.
        if not cache.can_record and not cache.local_header():
            return None
        return cache

    def local_header(self):
        try:
            header = dependencies.read_json(self.success_path)
            if (header.get('version') == CACHE_VERSION and
                    header.get('context') == self.context and
                    header.get('instance') == instance_identity(self.root, self.output) and
                    header.get('snapshot_stat') == dependencies.signature(self.path.stat()) and
                    header.get('priority_stat') == dependencies.signature(self.priority_path.stat())):
                self.header = header
                return True
        except CACHE_ERRORS:
            pass
        return False

    def _walk_roots(self):
        """Confine discovery to configuration trees and the product's subsystems."""
        roots = [self.root / name for name in CONFIG_ROOTS
                 if (self.root / name).is_dir()]
        subsystem_config = self.root / 'out/preloader' / self.product / 'subsystem_config.json'
        for value in dependencies.read_json(subsystem_config).values():
            path = value.get('path') if isinstance(value, dict) else value
            if isinstance(path, list):
                roots.extend(self.root / item for item in path if item)
            elif path:
                roots.append(self.root / str(path))
        unique, seen = [], set()
        for item in roots:
            key = str(item)
            if key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    def source_snapshot(self, old):
        roots = normalized_roots(self._walk_roots(), self.root)
        paths, directories = discover(roots, self.root, old)
        discovered = sorted(paths)
        # Preloader still runs normally. Compare its contents, not rewrite times.
        for base in (self.root / 'out/preloader' / self.product, self.root / 'out/products_ext'):
            if base.exists():
                paths.update(str(p) for p in base.rglob('*') if p.is_file())
        depfile = self.output / 'build.ninja.d'
        priority = set(paths) - set(discovered)
        if depfile.is_file():
            priority.update(os.path.abspath(self.output / p)
                            for p in dependencies.depfile_inputs(depfile))
            paths.update(priority)
        priority.update(name for name in paths if os.path.basename(name) in
                        ('bundle.json', 'ohos.build', 'config.json', 'subsystem_config.json'))
        paths.add(sys.executable)
        return {'roots': roots,
                'members': digest({name: value['members'] for name, value in directories.items()}),
                'directories': directories, 'discovered': discovered,
                'priority': sorted(priority),
                'missing': [name for name in roots if not Path(name).is_dir()],
                'files': records(paths, old.get('files', {}))}

    def read_record(self, priority=False):
        path = self.priority_path if priority else self.path
        key = 'priority_sha256' if priority else 'snapshot_sha256'
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != self.header[key]:
            raise ValueError('Configuration snapshot digest changed')
        value = json.loads(zlib.decompress(content))
        return transform_paths(value, self.root, False)

    def priority_record(self, value):
        old = value['inputs']
        files = {name: old['files'][name] for name in old.get('priority', [])}
        names = set()
        for name in files:
            parent = Path(name).parent
            while parent != parent.parent:
                if str(parent) in old['directories']:
                    names.add(str(parent))
                if parent == self.root:
                    break
                parent = parent.parent
        inputs = dict(old, files=files,
                      directories={name: old['directories'][name] for name in sorted(names)},
                      discovered=[])
        return dict(value, inputs=inputs, outputs={})

    def write_record(self, value):
        content = json.dumps(transform_paths(value, self.root, True),
                             separators=(',', ':')).encode()
        content = zlib.compress(content, 1)
        atomic_bytes(self.path, content)
        priority = self.priority_record(value)
        compact = json.dumps(transform_paths(priority, self.root, True),
                             separators=(',', ':')).encode()
        compact = zlib.compress(compact, 1)
        atomic_bytes(self.priority_path, compact)
        self.header = {'version': CACHE_VERSION, 'context': self.context,
                       'instance': instance_identity(self.root, self.output),
                       'snapshot_stat': dependencies.signature(self.path.stat()),
                       'snapshot_sha256': hashlib.sha256(content).hexdigest(),
                       'priority_stat': dependencies.signature(self.priority_path.stat()),
                       'priority_sha256': hashlib.sha256(compact).hexdigest()}
        dependencies.write_json(self.success_path, self.header)

    def validate_inputs(self, old, checked=None):
        if (old['roots'] != normalized_roots(self._walk_roots(), self.root) or
                any(Path(name).exists() for name in old.get('missing', []))):
            return False
        checked = checked or {'files': {}, 'directories': {}}
        rest = {name: value for name, value in old['files'].items() if name not in checked['files']}
        directories = {name: value for name, value in old['directories'].items()
                       if name not in checked['directories']}
        return (validate_records(rest, 'file', self.root) and
                validate_records(directories, 'directory', self.root))

    def output_paths(self):
        paths = set()
        if self.ninja:
            # Query the real target graph once on capture, including generated
            # inputs with no extension. No compile/install actions are run.
            result = subprocess.run([self.ninja, '-w', 'dupbuild=warn', '-C', str(self.output),
                                     '-t', 'inputs', '-0', '-E', 'make_all'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            output_root = str(self.root / 'out') + os.sep
            for raw in result.stdout.split(b'\0'):
                if raw:
                    name = os.path.abspath(self.output / os.fsdecode(raw))
                    if name.startswith(output_root) and os.path.isfile(name):
                        paths.add(name)
        loader_root = str(self.output / 'build_configs')
        for directory, directories, files in os.walk(self.output):
            directories[:] = [name for name in directories if name != '.incremental']
            loader_directory = directory == loader_root or directory.startswith(loader_root + os.sep)
            for name in files:
                selected = (not self.ninja and os.path.splitext(name)[1] in OUTPUT_SUFFIXES)
                selected = selected or name.endswith('.ninja') or loader_directory
                if (selected and
                        not name.startswith('incremental_workspace')):
                    path = os.path.join(directory, name)
                    # Optional installed SDK links can already be dangling in
                    # a successful make_all. Track actual files; a previously
                    # captured output becoming dangling still fails probe().
                    if not (os.path.islink(path) and not os.path.exists(path)):
                        paths.add(path)
        paths.update(str(self.output / name) for name in
                     ('build.ninja', 'build.ninja.d', 'build.ninja.stamp', 'args.gn'))
        return paths

    def probe(self):
        started = time.monotonic()
        try:
            if not self.local_header():
                self.invalidate()
                self.log('configuration cache: original flow (no valid local proof)')
                return False
            if not self.invalidate():
                return False
            priority = self.read_record(priority=True)
            if not self.validate_inputs(priority['inputs']):
                self.log('configuration cache: original flow (core configuration changed)')
                return False
            self.previous = self.read_record()
            if (self.previous.get('version') != CACHE_VERSION or
                    self.previous.get('context') != self.context or
                    not all(str(self.output / name) in self.previous.get('outputs', {})
                            for name in ('build.ninja', 'build.ninja.d', 'build.ninja.stamp', 'args.gn'))):
                return False
            if not self.validate_inputs(self.previous['inputs'], priority['inputs']):
                self.log('configuration cache: miss (configuration inputs or membership changed)')
            elif output_records(self.previous['outputs']) != self.previous['outputs']:
                self.log('configuration cache: miss (generated configuration changed)')
            else:
                self.reuse = True
                self.inputs = self.previous['inputs']
                self.log('configuration cache: reuse Loader/GN ({:.2f}s validation)'.format(
                    time.monotonic() - started))
            # A failed/interrupted build must not leave a reusable success marker.
            self.invalidate()
        except CACHE_ERRORS as error:
            self.log('configuration cache: miss ({})'.format(error))
            self.reuse = False
            if not isinstance(error, OSError):
                self.previous = {}
            self.invalidate()
        return self.reuse

    def invalidate(self):
        try:
            self.success_path.unlink(missing_ok=True)
            return True
        except OSError:
            # An unwritable success marker must never be used or refreshed.
            self.reuse = False
            self.inputs = None
            self.log('configuration cache: disabled (cannot invalidate success marker)')
            return False

    def check_gn(self, generator):
        try:
            value = {'args': generator.args_dict, 'flags': generator.flags_dict}
            self.gn = digest(value)
        except CACHE_ERRORS as error:
            self.reuse = False
            self.gn = None
            self.log('configuration cache: miss (GN arguments unreadable: {})'.format(error))
            return self.reuse
        if self.reuse and self.previous.get('gn') != self.gn:
            self.reuse = False
            self.log('configuration cache: miss (resolved GN arguments changed)')
        return self.reuse

    def after_gn(self):
        # Capture the new depfile after a real GN generation. Never publish a
        # cache on a failed Loader/GN/Ninja/post-build path.
        if not self.can_record:
            return
        self.generated = True
        started = time.monotonic()
        try:
            self.inputs = self.source_snapshot(self.previous.get('inputs', {}))
            self.log('configuration cache: captured inputs ({:.2f}s)'.format(time.monotonic() - started))
        except CACHE_ERRORS as error:
            self.inputs = None
            self.log('configuration cache: recording disabled ({})'.format(error))

    def commit(self):
        if self.inputs is None or not (self.reuse or self.generated):
            return
        started = time.monotonic()
        try:
            if self.reuse:
                depfile = str(self.output / 'build.ninja.d')
                if output_records([depfile])[depfile] != self.previous['outputs'][depfile]:
                    self.log('configuration cache: Ninja regenerated GN; refresh on next build')
                    return
                # Keep the verified snapshot immutable. Later output changes
                # are conservatively rejected at the next probe; a warm finish
                # only republishes the tiny successful-build marker.
                dependencies.write_json(self.success_path, self.header)
            else:
                outputs = output_records(self.output_paths())
                self.write_record({'version': CACHE_VERSION, 'context': self.context,
                                   'gn': self.gn, 'inputs': self.inputs,
                                   'outputs': outputs})
            self.log('configuration cache: recorded successful configuration ({:.2f}s)'.format(
                time.monotonic() - started))
        except CACHE_ERRORS as error:
            self.log('configuration cache: recording disabled ({})'.format(error))
