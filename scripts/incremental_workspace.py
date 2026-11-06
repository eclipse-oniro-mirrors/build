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
"""Seal source timestamps and reuse a snapshot at its original logical root.

This tool never rewrites Ninja hashes/deps or pretends missing outputs exist.
Transfer out/ with nanosecond mtimes, symlinks and both Ninja state files intact.
"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import stat as stat_module
import uuid

LEGACY_MANIFEST = 'out/incremental_workspace.json'
MANIFEST = LEGACY_MANIFEST
PHYSICAL_ROOT_ENV = 'OHOS_INCREMENTAL_PHYSICAL_ROOT'
VERIFIED_RUN_ENV = 'OHOS_INCREMENTAL_VERIFIED_RUN'
DEPFILE_INPUTS_VERSION = 1
ACTIVE_POLICY = None
ACTIVE_ROOT = None


def request_build_dir(root, request):
    if ACTIVE_POLICY is not None:
        return root / ACTIVE_POLICY['build_dir']
    return root / 'out' / request.product_name.split('@', 1)[0]


def restore_state_path(root):
    return (root / MANIFEST).with_name('incremental_workspace.restore-state.json')


def activate_product_policy(root, arguments=None):
    global MANIFEST, ACTIVE_POLICY, ACTIVE_ROOT
    try:
        from . import incremental_product_policy as policy
    except ImportError:
        import incremental_product_policy as policy
    if arguments is None:
        inherited = json.loads(os.environ.get(policy.CONTEXT, '{}'))
        name = inherited.get('product')
        if not name:
            raise ValueError('Mapped workspace entry requires a selected enabled product')
        arguments = ['--product-name', name+'@'+inherited['company']]
    data = policy.resolve(root, arguments)
    if not data['enabled']:
        ACTIVE_POLICY = None
        ACTIVE_ROOT = None
        MANIFEST = LEGACY_MANIFEST
        return False
    ACTIVE_POLICY = data
    ACTIVE_ROOT = root
    MANIFEST = str(Path(data['build_dir'])/'incremental_workspace.json')
    os.environ[policy.CONTEXT] = json.dumps(data, separators=(',', ':'))
    os.environ[policy.ENTERED_POLICY] = data['key']
    return True


def read_build_baseline(root):
    destination = root / MANIFEST
    if destination.exists():
        data = json.loads(destination.read_text())
        if data.get('version') != 1:
            raise ValueError('Unsupported manifest version')
        if ACTIVE_POLICY is not None:
            try:
                from .incremental_product_policy import baseline_policy
            except ImportError:
                from incremental_product_policy import baseline_policy
            if (data.get('workspace_policy') != baseline_policy(ACTIVE_POLICY)
                    or data.get('build_dir') != ACTIVE_POLICY['build_dir']):
                phase('ignoring baseline for another product or workspace policy')
                return None
        return data
    # Only enabled, matching products may import an old global v1 baseline.
    # Keep the old file intact for rollback and never inspect it when disabled.
    if ACTIVE_POLICY is not None:
        legacy = root / 'out/incremental_workspace.json'
        if legacy.is_file():
            data = json.loads(legacy.read_text())
            build = root / ACTIVE_POLICY['build_dir']
            args = build / 'args.gn'
            match = re.search(r'^product_name\s*=\s*"([^"\n]+)"', args.read_text(), re.M) if args.is_file() else None
            if (data.get('version') == 1 and data.get('build_dir') == ACTIVE_POLICY['build_dir']
                    and match and match[1] == ACTIVE_POLICY['product']
                    and data.get('graph_identity', {}).get('args') == graph_identity(build).get('args')):
                phase('importing matching legacy baseline for '+ACTIVE_POLICY['product'])
                write_manifest(destination, data)
                return json.loads(destination.read_text())
    return None


def phase(message, started=None):
    if started is not None:
        message += f' elapsed_s={time.perf_counter() - started:.3f}'
    print(f'incremental workspace: {message}', flush=True)


def instance_identity(root):
    stat = root.stat()
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'device': stat.st_dev, 'inode': stat.st_ino}


def graph_identity(build):
    result = {}
    for name in ('build.ninja', 'build.ninja.d', 'toolchain.ninja', '.ninja_deps'):
        path = build / name
        if path.exists():
            stat = path.stat()
            result[name] = [stat.st_size, stat.st_mtime_ns]
    args = (build / 'args.gn').read_text() if (build / 'args.gn').exists() else ''
    args = re.sub(r'^ohos_build_(?:time|datetime)\s*=.*\n?', '', args, flags=re.M)
    result['args'] = hashlib.sha256(args.encode()).hexdigest()
    return result


def write_manifest(path, data):
    if ACTIVE_POLICY is not None and path == ACTIVE_ROOT / MANIFEST:
        try:
            from .incremental_product_policy import baseline_policy
        except ImportError:
            from incremental_product_policy import baseline_policy
        data = dict(data, workspace_policy=baseline_policy(ACTIVE_POLICY))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_request(arguments):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--product-name')
    parser.add_argument('--build-target', action='append', default=[])
    parser.add_argument('--build-only-gn', action='store_true')
    parser.add_argument('--build-only-load', action='store_true')
    request, _ = parser.parse_known_args(arguments)
    return request


def legacy_source_root(root, build):
    """Recover a legacy root even if HB has already refreshed ohos_config.json.

    Require the source and binary CMake roots to agree with this imported
    flatbuffers cache. Do not infer a workspace from an arbitrary CMake path.
    """
    candidates = {}
    suffix = '/third_party/flatbuffers'
    try:
        entries = list(build.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        entries = []
    for directory in entries:
        # pathlib's directory-only glob can stat every entry before checking
        # its name, including unrelated src links into an unavailable CI root.
        if not directory.name.startswith('clang_'):
            continue
        cache = directory / 'third_party/flatbuffers/CMakeCache.txt'
        if not cache.is_file():
            continue
        fields = dict(re.findall(
            r'^(CMAKE_HOME_DIRECTORY|CMAKE_CACHEFILE_DIR):INTERNAL=(.*)$',
            cache.read_text(errors='replace'), re.M))
        source = fields.get('CMAKE_HOME_DIRECTORY', '').rstrip('/')
        binary = fields.get('CMAKE_CACHEFILE_DIR', '').rstrip('/')
        if not source.endswith(suffix):
            continue
        canonical = Path(source[:-len(suffix)])
        expected = canonical / cache.parent.relative_to(root)
        if canonical.is_absolute() and canonical != Path('/') and binary == str(expected):
            candidates[canonical] = str(cache.relative_to(root))
    if len(candidates) == 1:
        canonical = next(iter(candidates))
        return canonical, candidates[canonical]
    config = root / 'out/ohos_config.json'
    if config.is_file():
        stored = json.loads(config.read_text()).get('root_path')
        if isinstance(stored, str) and Path(stored).is_absolute():
            return Path(stored), 'out/ohos_config.json'
    return None, None


def prepare_build(root, arguments):
    destination = root / MANIFEST
    data = read_build_baseline(root)
    request = build_request(arguments)
    if request.product_name:
        build = request_build_dir(root, request)
        canonical, evidence = legacy_source_root(root, build)
        cache_evidence = evidence is not None and evidence.endswith('/CMakeCache.txt')
        mismatch = (data is not None and cache_evidence
                    and canonical != Path(data['canonical_root']))
        legacy = (data is None and canonical is not None and canonical != root
                  and (cache_evidence or (build / 'build.ninja').is_file()))
        if mismatch or legacy:
            if mismatch:
                print(f'incremental workspace: manifest root {data["canonical_root"]} '
                      f'disagrees with CMake root {canonical} from {evidence}', flush=True)
            # A conflicting manifest cannot supply fingerprints for this cache.
            # Recover paths without claiming that unknown input times are safe.
            print(f'incremental workspace: legacy root {canonical} from {evidence}; '
                  'source timestamp fingerprints are unavailable', flush=True)
            data = {'version': 1, 'canonical_root': str(canonical),
                    'build_dir': str(build.relative_to(root)), 'target': 'make_all',
                    'files': {}, 'tools': {}, 'automatic': True, 'unsealed': True}
            args = (build / 'args.gn').read_text() if (build / 'args.gn').is_file() else ''
            for name, field in (('ohos_build_time', 'build_time'),
                                ('ohos_build_datetime', 'build_datetime')):
                match = re.search(r'^' + name + r'\s*=\s*(.*)$', args, re.M)
                data[field] = match[1].strip('"') if match else ''
            write_manifest(destination, data)
    if data is None:
        return
    if (data.get('automatic') and Path(data['canonical_root']) == root
            and data.get('origin_instance') == instance_identity(root)):
        print(f'incremental workspace: in-place build at {root}', flush=True)
        return  # Ordinary in-place build keeps its existing build-time behavior.
    enter(root, data, ['bash', './build.sh', *arguments])


def only_optional_probe_executed(build):
    log = build / 'build.log'
    if not log.is_file():
        return False
    text = log.read_text(errors='replace')
    if 'Excuting ninja command:' not in text:
        return False
    text = re.sub(r'\x1b\[[0-9;]*m', '', text.rsplit('Excuting ninja command:', 1)[1])
    steps = re.findall(r'^(?:\[[^\]\n]+\]\s*)*\[\d+/\d+\]\s+(.+)$', text, re.M)
    return all(description.startswith('ACTION //build/ohos/images:phone_eng_chipset_image(')
               for description in steps)


def refresh_unchanged_index(root, build, previous, cache):
    """Validate an unchanged complete inventory without reparsing its rules.

    Full stat identities are reusable only on the same filesystem instance.
    New/deleted depfiles, changed subgraphs, missing inputs and changed tools
    fall back to the indexed collector; source-only and legacy mtimes never
    establish an unchanged snapshot by themselves.
    """
    instance = instance_identity(root)
    if (not all(cache.get('instance', {}).get(key) == value for key, value in instance.items())
            or not cache.get('graph') or not cache.get('closure')
            or previous.get('depfile_inputs_version') != DEPFILE_INPUTS_VERSION):
        return False
    identity = graph_identity(build)
    if previous.get('graph_identity', {}).get('args') != identity.get('args'):
        return False
    try:
        for name, record in previous['files'].items():
            if file_signature(os.stat(os.path.join(root, name))) != record.get('stat'):
                return False
        for name, record in cache['graph'].items():
            if file_signature(os.stat(os.path.join(build, name))) != record.get('stat'):
                return False
        deps = build / '.ninja_deps'
        if (digest(deps) if deps.is_file() else None) != cache.get('deps_sha256'):
            return False
        seen = set()
        for path in depfile_inventory(build, cache.get('directories'), local=True):
            name = str(path.relative_to(build))
            record = cache['depfiles'].get(name)
            if record is None or file_signature(path.stat()) != record.get('stat'):
                return False
            seen.add(name)
        if seen != cache['depfiles'].keys():
            return False
        # Resolve the complete input candidate set as well: a previously absent
        # optional input or a changed symlink must not evade the source check.
        paths = set(cache['ninja_inputs']) | set(cache['ninja_deps'])
        paths.update(cache['closure']['extra_inputs'])
        depfile = build / 'build.ninja.d'
        if depfile.is_file():
            for _, values in depfile_rules(depfile.read_text()):
                paths.update(values)
        files = fingerprint_inputs(root, build, paths, previous['files'], local=True)
        if files != previous['files'] or tool_identities(root) != previous.get('tools'):
            return False
    except (FileNotFoundError, NotADirectoryError):
        return False
    previous = dict(previous)
    previous['graph_identity'] = identity
    previous['origin_instance'] = instance
    args = (build / 'args.gn').read_text()
    for name, field in (('ohos_build_time', 'build_time'),
                        ('ohos_build_datetime', 'build_datetime')):
        match = re.search(r'^' + name + r'\s*=\s*(.*)$', args, re.M)
        previous[field] = match[1].strip('"') if match else ''
    # Missing generated outputs can be rebuilt with unchanged source inputs.
    # Preserve their new ceiling for later timestamp restoration after transfer.
    ceiling = previous.get('mtime_ceiling_ns', 0)
    log = build / '.ninja_log'
    if log.is_file():
        for line in log.read_text().splitlines():
            fields = line.split('\t')
            if len(fields) >= 3 and fields[2].isdigit():
                ceiling = max(ceiling, int(fields[2]))
    previous['mtime_ceiling_ns'] = ceiling
    write_manifest(root / MANIFEST, previous)
    return True


def finish_build(root, arguments):
    try:
        _finish_build(root, arguments)
    finally:
        request = build_request(arguments)
        if request.product_name:
            build = request_build_dir(root, request)
            journal = verified_inputs_path(build)
            if journal is not None:
                try:
                    journal.unlink(missing_ok=True)
                except OSError as error:
                    phase(f'warning: cannot remove entry verification: {error}')


def _finish_build(root, arguments):
    started = time.perf_counter()
    request = build_request(arguments)
    if (not request.product_name or request.build_only_gn or request.build_only_load
            or request.build_target != ['make_all']):
        return
    build = request_build_dir(root, request)
    if not (build / 'build.ninja').is_file():
        return
    destination = root / MANIFEST
    previous = read_build_baseline(root)
    cache = load_collection_cache(root, build, previous)
    if cache is not None:
        if refresh_unchanged_index(root, build, previous, cache):
            acknowledge_successful_baseline(root)
            phase('successful baseline refreshed (unchanged indexed inputs)', started)
            return
        phase('refreshing indexed successful build baseline')
        capture(root, build, 'make_all', True, automatic=bool(previous.get('automatic')),
                previous=previous, cache=cache)
        acknowledge_successful_baseline(root)
        phase('successful indexed baseline refreshed', started)
        return
    if previous and previous.get('collection_generation'):
        phase('full baseline capture reason=collection-cache-missing-or-invalid')
        capture(root, build, 'make_all', True, automatic=bool(previous.get('automatic')), previous=previous)
        acknowledge_successful_baseline(root)
        return
    identity = graph_identity(build)
    previous_graph = previous.get('graph_identity', {}) if previous else {}
    same_graph = previous_graph == identity
    complete_inputs = previous is not None and previous.get('depfile_inputs_version') == DEPFILE_INPUTS_VERSION
    reason = 'no-baseline' if previous is None else 'graph-or-dependencies-changed'
    # GN rewrites large generated graphs even on a no-op. A successful Ninja
    # invocation with no material work, unchanged arguments/deps and unchanged
    # source stamps can refresh this identity without rediscovering all inputs.
    if (not same_graph and previous_graph.get('args') == identity.get('args')
            and previous_graph.get('.ninja_deps') == identity.get('.ninja_deps')
            and only_optional_probe_executed(build)):
        same_graph = True
    if (previous and previous.get('canonical_root') == str(root)
            and previous.get('build_dir') == str(build.relative_to(root))
            and same_graph and complete_inputs):
        unchanged = True
        for name, record in previous['files'].items():
            path = root / name
            if (not path.exists() or path.stat().st_mtime_ns != record['mtime_ns']
                    or bool(path.stat().st_mode & 0o111) != record.get('executable', bool(path.stat().st_mode & 0o111))):
                unchanged = False
                reason = f'input-state-changed:{name}'
                break
        retained = previous.get('retained_depfiles', {})
        if unchanged:
            for name, record in retained.items():
                path = build / name
                if Path(name).is_absolute() or '..' in Path(name).parts or not path.is_file():
                    unchanged = False
                else:
                    stat = path.stat()
                    if [stat.st_size, stat.st_mtime_ns] != record[:2]:
                        if digest(path) != record[2]:
                            unchanged = False
                        else:
                            retained[name] = [stat.st_size, stat.st_mtime_ns, record[2]]
                if not unchanged:
                    reason = f'retained-depfile-changed:{name}'
                    break
        if unchanged:
            previous['graph_identity'] = identity
            previous['origin_instance'] = instance_identity(root)
            args = (build / 'args.gn').read_text()
            for name, field in (('ohos_build_time', 'build_time'),
                                ('ohos_build_datetime', 'build_datetime')):
                match = re.search(r'^' + name + r'\s*=\s*(.*)$', args, re.M)
                previous[field] = match[1].strip('"') if match else ''
            write_manifest(destination, previous)
            acknowledge_successful_baseline(root)
            phase('successful baseline refreshed (unchanged inputs)', started)
            return
    if previous is not None:
        if not complete_inputs:
            reason = 'upgrade-retained-depfile-inputs'
        elif previous.get('canonical_root') != str(root):
            reason = 'baseline-root-changed'
        elif previous.get('build_dir') != str(build.relative_to(root)):
            reason = 'build-directory-changed'
        elif previous_graph.get('args') != identity.get('args'):
            reason = 'gn-arguments-changed'
        elif previous_graph.get('.ninja_deps') != identity.get('.ninja_deps'):
            reason = 'ninja-dependencies-changed'
    phase(f'full baseline capture reason={reason}')
    automatic = bool(previous.get('automatic')) if previous else True
    capture(root, build, 'make_all', True, automatic=automatic)
    acknowledge_successful_baseline(root)
    phase('successful baseline recorded', started)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def directory_digest(path):
    """Fingerprint membership; file contents remain separate Ninja inputs."""
    entries = []
    for child in sorted(path.iterdir()):
        if child.is_symlink():
            kind = 'link:' + os.readlink(child)
        else:
            kind = 'directory' if child.is_dir() else 'file'
        entries.append((child.name, kind))
    return hashlib.sha256(json.dumps(entries, ensure_ascii=True).encode()).hexdigest()


def tool_identities(root):
    machine = 'aarch64' if os.uname().machine == 'aarch64' else 'x86'
    llvm_machine = 'aarch64' if os.uname().machine == 'aarch64' else 'x86_64'
    candidates = [f'prebuilts/build-tools/linux-{machine}/bin/{name}' for name in ('ninja', 'gn')]
    candidates += [f'prebuilts/clang/ohos/linux-{llvm_machine}/llvm/bin/{name}'
                   for name in ('clang', 'clang++', 'ld.lld')]
    candidates += ['prebuilts/build-tools/common/nodejs/current/bin/node']
    identities, hashed = {}, {}
    for name in candidates:
        path = root / name
        if path.is_file():
            resolved = path.resolve()
            if resolved not in hashed:
                hashed[resolved] = digest(path)
            identities[name] = hashed[resolved]
    return identities


def validate_tools(root, data):
    started = time.perf_counter()
    hashes = {}
    phase(f'validating tools entries={len(data.get("tools", {}))}')
    for name, expected in data.get('tools', {}).items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'Invalid tool path: {name}')
        path = root / name
        if not path.is_file():
            raise RuntimeError(f'Toolchain changed: {name}; create a matching build baseline before reusing this snapshot')
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if key not in hashes:
            phase(f'hashing tool {name} bytes={stat.st_size}')
            hashes[key] = digest(path)
            after = path.stat()
            if key != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError(f'Toolchain changed while validating: {name}')
        if hashes[key] != expected:
            raise RuntimeError(f'Toolchain changed: {name}; create a matching build baseline before reusing this snapshot')
    phase(f'tools validated unique_files={len(hashes)} bytes_hashed={sum(key[2] for key in hashes)}', started)


def ninja_command(root, build, *args):
    machine = 'aarch64' if os.uname().machine == 'aarch64' else 'x86'
    return [str(root / f'prebuilts/build-tools/linux-{machine}/bin/ninja'),
            '-C', str(build), '-w', 'dupbuild=warn', *args]


def audit(root, build, target):
    p = subprocess.run(ninja_command(root, build, '-n', '-d', 'explain', target),
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    steps = re.findall(r'^\[(\d+)/(\d+)\] (\S+)', p.stdout, re.M)
    return p.returncode, len(steps), p.stdout


def depfile_words(text):
    """Decode Make depfile words; quotes are filenames, not shell syntax."""
    if not any(char in text for char in ('\\', '$', '#')):
        return text.split()
    words, word = [], []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text) and text[index + 1] in ' \t#\\:':
            word.append(text[index + 1])
            index += 2
            continue
        if char == '$' and text[index:index + 2] == '$$':
            word.append('$')
            index += 2
            continue
        if char == '#':
            break
        if char.isspace():
            if word:
                words.append(''.join(word))
                word = []
        else:
            word.append(char)
        index += 1
    if word:
        words.append(''.join(word))
    return words


def depfile_rules(text):
    for line in text.replace('\\\r\n', ' ').replace('\\\n', ' ').splitlines():
        separator = line.find(':')
        drive = (separator > 0 and line[separator - 1].isalpha()
                 and (separator == 1 or line[separator - 2].isspace())
                 and line[separator + 1:separator + 2] in ('/', '\\'))
        if (separator >= 0 and not drive
                and not any(char in line for char in ('\\', '$', '#'))):
            yield line[:separator].split(), line[separator + 1:].split()
            continue
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == '\\':
                escaped = True
            elif char == '#':
                break
            elif char == ':':
                # A Windows drive letter is not the rule separator.
                if (index > 0 and line[index - 1].isalpha()
                        and (index == 1 or line[index - 2].isspace())
                        and line[index + 1:index + 2] in ('/', '\\')):
                    continue
                yield depfile_words(line[:index]), depfile_words(line[index + 1:])
                break



# The collection cache is optional acceleration, committed by generation only
# after a successful complete product build. It never participates in restore.
COLLECTION_VERSION = 2
PARSED_DEPFILE_CACHE_BYTES = 64 * 1024 * 1024


def local_collection_identity(root):
    return {**instance_identity(root),
            'physical_root': os.environ.get(PHYSICAL_ROOT_ENV, str(root))}


def file_signature(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns, stat.st_mode]


def stable_bytes(path):
    before = path.stat()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read()
        finished = os.fstat(stream.fileno())
    after = path.stat()
    signature = file_signature(before)
    if not signature == file_signature(opened) == file_signature(finished) == file_signature(after):
        raise RuntimeError(f'Input changed while recording baseline: {path}')
    return content, signature


def collection_cache_path(root, build):
    return build / '.incremental/collection.json'


def load_collection_cache(root, build, previous):
    if not previous or not previous.get('collection_generation'):
        return None
    try:
        content, _ = stable_bytes(collection_cache_path(root, build))
        if hashlib.sha256(content).hexdigest() != previous.get('collection_sha256'):
            return None
        data = json.loads(content)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(key), kind) for key, kind in (
            ('ninja_inputs', list), ('ninja_deps', list), ('depfiles', dict))):
        return None
    if (data.get('version') != COLLECTION_VERSION
            or data.get('generation') != previous.get('collection_generation')
            or data.get('canonical_root') != str(root)
            or data.get('build_dir') != str(build.relative_to(root))
            or data.get('target') != 'make_all' or previous.get('target') != 'make_all'):
        return None
    if data.get('ninja_sha256') != digest(Path(ninja_command(root, build)[0])):
        return None
    return data


def resolve_cached_string(path, cache):
    """Resolve each ancestor once, keeping pathlib's handling of real symlinks."""
    value = os.path.abspath(path)
    parent, pending = value, []
    while parent not in cache:
        head, tail = os.path.split(parent)
        if head == parent:
            cache[parent] = os.path.realpath(parent)
            break
        pending.append((parent, tail))
        parent = head
    resolved = cache[parent]
    while pending:
        lexical, name = pending.pop()
        resolved = os.path.join(resolved, name)
        try:
            if stat_module.S_ISLNK(os.lstat(resolved).st_mode):
                resolved = str(Path(resolved).resolve())
        except OSError:
            pass  # realpath(strict=False) also leaves unavailable prefixes unresolved.
        cache[lexical] = resolved
    return cache[value]


def resolve_cached(path, cache):
    return Path(resolve_cached_string(path, cache))


def graph_content_records(root, build, previous, local):
    """Walk static Ninja include/subninja closure; unsupported syntax disables reuse.

    GN emits literal includes. Arbitrary Ninja variable expansion is deliberately
    not approximated: Ninja itself will rediscover inputs on that fallback path.
    """
    records, pending = {}, [build / 'build.ninja']
    resolutions = {}
    resolved_root = resolve_cached(root, resolutions)
    progress = time.perf_counter() + 2
    old = previous or {}
    include = re.compile(rb'(?m)^(?:include|subninja)[ \t]+([^\r\n]+)')
    while pending:
        path = pending.pop()
        relative = os.path.relpath(path, build)
        if relative in records:
            continue
        if not resolve_cached(path, resolutions).is_relative_to(resolved_root):
            return None
        signature = file_signature(path.stat())
        cached = old.get(relative)
        if local and cached and cached.get('stat') == signature:
            record = cached
        else:
            content, signature = stable_bytes(path)
            children = []
            for match in include.finditer(content):
                value = match[1].decode().strip()
                # Full expansion/continuation belongs to Ninja. Unsupported
                # constructs cause conservative tool queries, never a guessed graph.
                literal_check = re.sub(r'\$([$ :])', '', value)
                if '$' in literal_check or '#' in value:
                    return None
                value = re.sub(r'\$([$ :])', lambda match: match[1], value)
                children.append(value)
            record = {'sha256': hashlib.sha256(content).hexdigest(),
                      'stat': signature, 'includes': children}
        records[relative] = record
        pending.extend(Path(os.path.abspath(build / value)) for value in record['includes'])
        if time.perf_counter() >= progress:
            phase(f'complete Ninja graph progress files={len(records)}')
            progress = time.perf_counter() + 2
    return records


def same_graph_content(old, new):
    return (old is not None and new is not None and old.keys() == new.keys()
            and all(old[name]['sha256'] == record['sha256'] for name, record in new.items()))


def collect_ninja_paths(root, build, tool, target='make_all'):
    started = time.perf_counter()
    phase(f'collecting Ninja {tool}')
    paths = set()
    command = ninja_command(root, build, '-n', '-t', tool,
                            *(['-E', target] if tool == 'inputs' else []))
    with subprocess.Popen(command, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        for line in process.stdout:
            if tool != 'deps' or line.startswith('    '):
                paths.add(line.strip())
        if process.wait():
            raise RuntimeError(f'ninja -t {tool} failed')
    phase(f'Ninja {tool} collected unique_paths={len(paths)}', started)
    return paths


def fingerprint_inputs(root, build, paths, previous=None, local=False):
    """Normalize parents once, deduplicate aliases, and hash only changed objects."""
    started = time.perf_counter()
    old = previous or {}
    candidates, resolutions = {}, {}
    build_base, root_base = os.path.abspath(build), os.path.abspath(root)
    prefix = root_base.rstrip(os.sep) + os.sep
    for value in sorted(paths):
        lexical = os.path.abspath(os.path.join(build_base, value))
        parent, name = os.path.split(lexical)
        path = os.path.join(resolve_cached_string(parent, resolutions), name)
        try:
            current = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            if not (path == root_base or path.startswith(prefix)):
                continue
            raise
        if stat_module.S_ISLNK(current.st_mode):
            path = str(Path(path).resolve())
            current = None
        if path == root_base:
            relative = '.'
        elif path.startswith(prefix):
            relative = path[len(prefix):]
        else:
            continue
        if relative == 'out' or relative.startswith('out' + os.sep):
            continue
        if current is None:
            try:
                current = os.stat(path)
            except (FileNotFoundError, NotADirectoryError):
                continue
        candidates[relative] = (path, current)
    workers = int(os.environ.get('OHOS_INCREMENTAL_JOBS', '4'))
    if not 1 <= workers <= 8:
        raise ValueError('OHOS_INCREMENTAL_JOBS must be between 1 and 8')
    reused = 0
    files = {}

    def record_input(item):
        name, path = item
        try:
            before = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return name, None, False
        if not (stat_module.S_ISREG(before.st_mode) or stat_module.S_ISDIR(before.st_mode)):
            return name, None, False
        signature = file_signature(before)
        cached = old.get(name)
        if local and cached and cached.get('stat') == signature:
            return name, cached, True
        is_directory = stat_module.S_ISDIR(before.st_mode)
        fingerprint = directory_digest(path) if is_directory else digest(path)
        if file_signature(path.stat()) != signature:
            raise RuntimeError(f'Input changed while fingerprinting: {name}')
        return name, {'sha256': fingerprint, 'kind': 'directory' if is_directory else 'file',
                      'mtime_ns': before.st_mtime_ns, 'executable': bool(before.st_mode & 0o111),
                      'stat': signature}, False

    # Batches bound pending futures and memory; deterministic merge order also
    # keeps aliases from depending on thread completion order.
    items = []
    for name, (path, current) in sorted(candidates.items()):
        cached = old.get(name)
        if local and cached and cached.get('stat') == file_signature(current):
            files[name] = cached
            reused += 1
        else:
            items.append((name, Path(path)))
    next_progress = time.perf_counter() + 2
    if items:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for offset in range(0, len(items), 256):
                for name, record, cached in pool.map(record_input, items[offset:offset + 256]):
                    if record is not None:
                        files[name] = record
                        reused += cached
                if time.perf_counter() >= next_progress:
                    phase(f'input fingerprint progress inputs={len(files)} reused={reused}', started)
                    next_progress = time.perf_counter() + 2
    files = dict(sorted(files.items()))
    phase(f'input fingerprints complete inputs={len(files)} reused={reused} workers={workers}', started)
    return files


def depfile_inventory(build, previous=None, local=False, directory_index=None):
    """Rescan changed directories; stat checks preserve discovery of added files."""
    excluded = {'.cache', 'lsp-portable', '.clangd-lsp-work', '.git', '.incremental'}
    old = previous or {}
    pending, scanned, reused = [('.', build)], 0, 0
    next_progress = time.perf_counter() + 2
    while pending:
        name, path = pending.pop()
        before = path.stat() if name == '.' else path.lstat()
        if name != '.' and stat_module.S_ISLNK(before.st_mode):
            continue
        signature = file_signature(before) + [before.st_nlink]
        cached = old.get(name)
        if local and cached and cached.get('timestamp_stable') and cached['stat'] == signature:
            record = cached
            reused += 1
        else:
            children, files = [], []
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name in excluded:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        children.append(entry.name)
                    elif entry.name.endswith('.d') and entry.is_file():
                        files.append(entry.name)
            after = path.stat() if name == '.' else path.lstat()
            if file_signature(after) + [after.st_nlink] != signature:
                raise RuntimeError(f'Output directory changed while collecting depfiles: {path}')
            stable = max(before.st_mtime_ns, before.st_ctime_ns) < time.time_ns() - 2_000_000_000
            record = {'stat': signature, 'children': sorted(children), 'depfiles': sorted(files),
                      'timestamp_stable': stable}
        scanned += 1
        if directory_index is not None:
            directory_index[name] = record
        for filename in record['depfiles']:
            yield path / filename
        pending.extend((str(Path(name) / child), path / child) for child in record['children'])
        if time.perf_counter() >= next_progress:
            phase(f'depfile directory progress checked={scanned} reused={reused}')
            next_progress = time.perf_counter() + 2
    phase(f'depfile directories checked={scanned} reused={reused}')


def collect_retained_depfiles(build, paths, target, *, previous=None, local=False, index=None,
                              directories=None, directory_index=None, closure=None, closure_result=None):
    """Discover changed inventories and reuse a bounded set of parsed rules."""
    started = time.perf_counter()
    phase('collecting retained action depfiles')
    build_base = os.path.abspath(build)
    absolute_paths = {}
    def absolute(value):
        if value not in absolute_paths:
            absolute_paths[value] = os.path.normpath(os.path.join(build_base, value))
        return absolute_paths[value]
    initial_paths = paths.copy()
    base_digest = hashlib.sha256('\0'.join(sorted(paths | {target})).encode()).hexdigest()
    known = {absolute(path) for path in paths | {target}}
    records, producers, parsed = {}, {}, {}
    old = previous or {}
    reused, retained_bytes, deferred = 0, 0, 0
    budget = PARSED_DEPFILE_CACHE_BYTES
    def rule_bytes(rules):
        return (sys.getsizeof(rules) + sum(sys.getsizeof(rule) +
                sum(sys.getsizeof(words) + sum(sys.getsizeof(word) for word in words)
                    for words in rule) for rule in rules))
    for path in depfile_inventory(build, directories, local, directory_index):
        relative = str(path.relative_to(build))
        signature = file_signature(path.stat())
        cached = old.get(relative)
        if local and cached and cached.get('stat') == signature:
            record = cached.copy()
            reused += 1
        else:
            content, signature = stable_bytes(path)
            fingerprint = hashlib.sha256(content).hexdigest()
            if cached and cached.get('sha256') == fingerprint:
                record = {**cached, 'stat': signature}
            else:
                rules = list(depfile_rules(content.decode('utf-8', errors='replace')))
                outputs = [output for rule_outputs, inputs in rules if inputs for output in rule_outputs]
                record = {'stat': signature, 'sha256': fingerprint,
                          'rules': rules, 'outputs': outputs}
        rules = record['rules']
        if rules is not None:
            size = rule_bytes(rules)
            if retained_bytes + size > budget:
                record['rules'] = None
            else:
                retained_bytes += size
        parsed[relative] = record
        for output in record['outputs']:
            producers.setdefault(absolute(output), set()).add(relative)
    phase(f'retained depfile inventory files={len(parsed)} reused={reused} parsed_cache_bytes={retained_bytes}', started)
    same_inventory = (closure is not None and closure.get('base') == base_digest
                      and parsed.keys() == old.keys()
                      and all(old[name]['sha256'] == record['sha256'] for name, record in parsed.items()))
    if same_inventory:
        paths.update(closure['extra_inputs'])
        for name in closure['retained']:
            record = parsed[name]
            signature = record['stat']
            records[name] = [signature[2], signature[3], record['sha256']]
        phase(f'retained closure reused files={len(records)}')
    else:
        pending, loaded = deque(known), set()
        while pending:
            for relative in producers.get(pending.popleft(), ()):
                if relative in loaded:
                    continue
                loaded.add(relative)
                record = parsed[relative]
                rules = record['rules']
                if rules is None:
                    content, signature = stable_bytes(build / relative)
                    if signature != record['stat'] or hashlib.sha256(content).hexdigest() != record['sha256']:
                        raise RuntimeError(f'Depfile changed while collecting baseline: {relative}')
                    rules = list(depfile_rules(content.decode('utf-8', errors='replace')))
                    deferred += 1
                for _, inputs in rules:
                    new_inputs = set(inputs).difference(paths)
                    paths.update(inputs)
                    for value in new_inputs:
                        resolved = absolute(value)
                        if resolved not in known:
                            known.add(resolved)
                            pending.append(resolved)
                signature = record['stat']
                records[relative] = [signature[2], signature[3], record['sha256']]
    for name, record in parsed.items():
        if file_signature((build / name).stat()) != record['stat']:
            raise RuntimeError(f'Depfile changed while collecting baseline: {name}')
    if closure_result is not None:
        closure_result.update({'base': base_digest, 'extra_inputs': sorted(paths - initial_paths),
                               'retained': sorted(records)})
    if index is not None:
        index.update(parsed)
    phase(f'retained depfiles collected files={len(records)} deferred_parses={deferred}', started)
    return records


def capture(root, build, target, allow_dirty, manifest=None, automatic=False, *, previous=None, cache=None):
    started = time.perf_counter()
    print('incremental workspace: recording successful build baseline', flush=True)
    instance = instance_identity(root)
    # Renaming the same root does not invalidate inode/stat identities. A real
    # copy, host change or independent checkout still has a different identity.
    local = bool(cache and all(cache.get('instance', {}).get(key) == value
                               for key, value in instance.items()))
    graph_started = time.perf_counter()
    graph = graph_content_records(root, build, cache.get('graph') if cache else None, local)
    same_graph = bool(cache and same_graph_content(cache.get('graph'), graph))
    deps_path = build / '.ninja_deps'
    deps_digest = digest(deps_path) if deps_path.is_file() else None
    same_deps = bool(cache and cache.get('deps_sha256') == deps_digest)
    phase(f'complete Ninja graph checked files={len(graph or {})} same_content={same_graph}', graph_started)
    if cache and allow_dirty:
        count = previous.get('baseline_planned') if previous else None
        phase('Ninja audit skipped: successful make_all; previous diagnostic count retained')
    else:
        phase('auditing Ninja baseline')
        audit_started = time.perf_counter()
        code, count, output = audit(root, build, target)
        phase(f'Ninja audit complete planned={count}', audit_started)
        if code or (count and not allow_dirty):
            raise RuntimeError(f'Cannot seal a clean baseline: exit={code}, planned={count}.\n' + output[-4000:])
    inputs = set(cache['ninja_inputs']) if same_graph else collect_ninja_paths(root, build, 'inputs', target)
    deps = set(cache['ninja_deps']) if same_graph and same_deps else collect_ninja_paths(root, build, 'deps', target)
    phase(f'Ninja inventory reused inputs={same_graph} deps={same_graph and same_deps}')
    paths = inputs | deps
    depfile = build / 'build.ninja.d'
    if depfile.exists():
        for _, values in depfile_rules(depfile.read_text()):
            paths.update(values)
    dep_index, directory_index, closure_index = {}, {}, {}
    retained = collect_retained_depfiles(build, paths, target,
                                        previous=cache.get('depfiles') if cache else None,
                                        local=local, index=dep_index,
                                        directories=cache.get('directories') if cache else None,
                                        directory_index=directory_index,
                                        closure=cache.get('closure') if cache else None,
                                        closure_result=closure_index)
    fingerprints = previous.get('files', {}) if previous else {}
    verified = load_verified_inputs(root, build)
    if verified:
        # These records were hashed by this build's relocation entry, on this
        # physical instance. Every candidate still checks its current full stat.
        fingerprints = {**fingerprints, **verified} if local else verified
        phase(f'reusing entry-verified fingerprints candidates={len(verified)}')
    files = fingerprint_inputs(root, build, paths, fingerprints, local or bool(verified))
    if previous and files == previous.get('files'):
        phase('successful baseline refreshed (unchanged inputs)')
    args = (build / 'args.gn').read_text() if (build / 'args.gn').exists() else ''
    def value(name):
        match = re.search(r'^' + name + r'\s*=\s*(.*)$', args, re.M)
        return match[1].strip('"') if match else ''
    ceiling = 0
    log = build / '.ninja_log'
    if log.exists():
        with log.open() as stream:
            for line in stream:
                fields = line.split('\t')
                if len(fields) >= 3 and fields[2].isdigit():
                    ceiling = max(ceiling, int(fields[2]))
    tool_started = time.perf_counter()
    phase('fingerprinting baseline tools')
    tools = tool_identities(root)
    phase('baseline tools fingerprinted', tool_started)
    data = {'version': 1, 'canonical_root': str(root), 'build_dir': str(build.relative_to(root)),
            'target': target, 'baseline_planned': count, 'files': files, 'mtime_ceiling_ns': ceiling,
            'tools': tools,
            'build_time': value('ohos_build_time'), 'build_datetime': value('ohos_build_datetime'),
            'automatic': automatic, 'origin_instance': instance_identity(root),
            'graph_identity': graph_identity(build),
            'depfile_inputs_version': DEPFILE_INPUTS_VERSION, 'retained_depfiles': retained}
    generation = uuid.uuid4().hex
    collection = {'version': COLLECTION_VERSION, 'generation': generation,
                  'canonical_root': str(root), 'build_dir': str(build.relative_to(root)),
                  'target': target, 'instance': local_collection_identity(root), 'graph': graph,
                  'ninja_sha256': digest(Path(ninja_command(root, build)[0])),
                  'deps_sha256': deps_digest, 'ninja_inputs': sorted(inputs),
                  'ninja_deps': sorted(deps), 'depfiles': dep_index,
                  'directories': directory_index, 'closure': closure_index}
    cache_started = time.perf_counter()
    unchanged_index = (local and same_graph and same_deps
                       and cache.get('directories', {}).keys() == directory_index.keys()
                       and all(not entry.get('timestamp_stable')
                               or cache['directories'][name].get('timestamp_stable')
                               for name, entry in directory_index.items())
                       and cache.get('closure') == closure_index
                       and cache['depfiles'].keys() == dep_index.keys()
                       and all(cache['depfiles'][name]['sha256'] == entry['sha256']
                               for name, entry in dep_index.items()))
    if unchanged_index:
        generation = cache['generation']
        phase('collection index reused (unchanged dependency content)')
    else:
        write_manifest(collection_cache_path(root, build), collection)
    data['collection_generation'] = generation
    data['collection_sha256'] = (previous['collection_sha256'] if unchanged_index
                                 else digest(collection_cache_path(root, build)))
    data['baseline_planned_reused'] = bool(cache and allow_dirty)
    phase('collection index ready', cache_started)
    destination = root / (manifest or MANIFEST)
    save_started = time.perf_counter()
    phase('writing baseline manifest')
    write_manifest(destination, data)
    phase('baseline manifest written', save_started)
    print(json.dumps({'manifest': str(destination), 'source_inputs': len(files), 'baseline_planned': count}))
    phase('baseline capture complete', started)


def restore(root, data, *, tools_validated=False, verified=None):
    canonical = Path(data['canonical_root']) if 'canonical_root' in data else root
    if root != canonical:
        enter(root, data, ['python3', 'build/scripts/incremental_workspace.py',
                           '--root', str(canonical), 'restore'])
    if not tools_validated:
        validate_tools(root, data)
    started = time.perf_counter()
    total = len(data['files'])
    phase(f'restoring transferred inputs total={total}')
    next_report = started + 2
    counts = {'restored': 0, 'changed': 0, 'missing': 0}
    for processed, (name, record) in enumerate(data['files'].items(), 1):
        path = root / name
        # Reject traversal even for manifests provided with external snapshots.
        if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())):
            raise ValueError(f'Invalid source input: {name}')
        is_directory = record.get('kind') == 'directory'
        if not path.exists():
            counts['missing'] += 1
        else:
            before = path.stat()
            actual_directory = stat_module.S_ISDIR(before.st_mode)
            fingerprint = directory_digest(path) if actual_directory else digest(path)
            after_hash = path.stat()
            stable = file_signature(before) == file_signature(after_hash)
            executable = bool(after_hash.st_mode & 0o111)
            if (actual_directory != is_directory or fingerprint != record['sha256']
                    or executable != record.get('executable', executable)):
                if data.get('mtime_ceiling_ns', 0) >= time.time_ns():
                    raise RuntimeError('Snapshot outputs are newer than the host clock; synchronize clocks before restoring changed inputs')
                counts['changed'] += 1
                # A changed file with an old preserved mtime still must rebuild.
                os.utime(path, None)
            elif after_hash.st_mtime_ns != record['mtime_ns']:
                os.utime(path, ns=(after_hash.st_atime_ns, record['mtime_ns']))
                counts['restored'] += 1
            current = path.stat()
            if verified is not None and stable and (before.st_dev, before.st_ino, before.st_size, before.st_mode) == (
                    current.st_dev, current.st_ino, current.st_size, current.st_mode):
                verified[name] = {'sha256': fingerprint, 'kind': 'directory' if actual_directory else 'file',
                                  'mtime_ns': current.st_mtime_ns, 'executable': bool(current.st_mode & 0o111),
                                  'stat': file_signature(current)}
        now = time.perf_counter()
        if now >= next_report:
            phase(f'input restore progress={processed}/{total}', started)
            next_report = now + 2
    print(json.dumps(counts))
    phase(f'input restore complete inputs={total}', started)


def restore_identity(root):
    return {'manifest_sha256': digest(root / MANIFEST), **instance_identity(root),
            'physical_root': os.environ.get(PHYSICAL_ROOT_ENV, str(root))}


def verified_inputs_path(build):
    token = os.environ.get(VERIFIED_RUN_ENV, '')
    if not re.fullmatch('[0-9a-f]{32}', token):
        return None
    return build / '.incremental' / ('verified-inputs.' + token + '.state')


def load_verified_inputs(root, build):
    path = verified_inputs_path(build)
    if path is None:
        return None
    try:
        expected, separator, content = path.read_bytes().partition(b'\n')
        if not separator or hashlib.sha256(content).hexdigest().encode() != expected:
            return None
        data = json.loads(content)
        if (isinstance(data, dict) and data.get('version') == 1 and data.get('identity') == restore_identity(root)
                and data.get('run') == os.environ[VERIFIED_RUN_ENV]
                and isinstance(data.get('files'), dict)):
            return data['files']
    except (OSError, ValueError):
        pass
    return None


def acknowledge_successful_baseline(root):
    # Only finish's successful full-product branches acknowledge a newly sealed
    # manifest. A changed/imported manifest must still invalidate this cache.
    write_restore_state(root, restore_identity(root))


def write_restore_state(root, identity):
    try:
        write_manifest(restore_state_path(root), identity)
    except OSError as error:
        # The manifest remains valid. A stale/missing cache causes a conservative
        # recheck next time; it must not turn a successful product into failure.
        phase(f'warning: cannot save input restore state; next entry will recheck: {error}')


def restore_transferred_snapshot(root, data, *, tools_validated=False):
    """Restore once per baseline and physical path, preserving later edits."""
    started = time.perf_counter()
    identity = restore_identity(root)
    state = restore_state_path(root)
    try:
        previous = json.loads(state.read_text())
    except (FileNotFoundError, ValueError):
        previous = None
    if previous != identity:
        reason = 'state-missing-or-invalid' if previous is None else 'baseline-or-instance-changed'
        phase(f'input restore required reason={reason}')
        build_relative = data.get('build_dir')
        journal = verified_inputs_path(root / build_relative) if build_relative else None
        verified = {} if journal is not None else None
        if verified is None:
            restore(root, data, tools_validated=tools_validated)
        else:
            restore(root, data, tools_validated=tools_validated, verified=verified)
            started_write = time.perf_counter()
            temporary = journal.with_name(journal.name + f'.{os.getpid()}.tmp')
            try:
                journal.parent.mkdir(parents=True, exist_ok=True)
                content = json.dumps({'version': 1, 'run': os.environ[VERIFIED_RUN_ENV],
                                      'identity': identity, 'files': verified}, separators=(',', ':')).encode()
                temporary.write_bytes(hashlib.sha256(content).hexdigest().encode() + b'\n' + content)
                os.replace(temporary, journal)
                phase(f'entry verification saved inputs={len(verified)}', started_write)
            except OSError as error:
                phase(f'warning: cannot save entry verification; finish will hash normally: {error}')
            finally:
                temporary.unlink(missing_ok=True)
        write_restore_state(root, identity)
    else:
        phase('input restore skipped reason=baseline-and-instance-match', started)


def private_mountpoint(canonical):
    replace_symlink = False
    try:
        replace_symlink = canonical.is_symlink()
        # A symlink will be replaced privately; its target is irrelevant and
        # may refer to a previous job on an unavailable filesystem.
        exists = not replace_symlink and canonical.is_dir()
    except PermissionError:
        exists = False
    if exists and not replace_symlink:
        # The bind below shadows this directory only inside bubblewrap's
        # private mount namespace. A retained preheat workspace may be nonempty;
        # neither it nor another consumer's namespace is modified by this bind.
        return []
    # Present a missing CI path only inside this namespace. Rebind existing
    # siblings to preserve HOME/tools, without creating host mount directories.
    ancestor = canonical.parent
    while True:
        try:
            if ancestor.exists():
                break
        except PermissionError:
            pass
        ancestor = ancestor.parent
    args = ['--tmpfs', str(ancestor)]
    try:
        children = sorted(ancestor.iterdir())
    except PermissionError:
        # A CI root under /root is commonly inaccessible on a developer host.
        # Its private namespace replacement needs no inaccessible siblings.
        children = []
    for child in children:
        if replace_symlink and child == canonical:
            # Replace a stale job alias with a directory only in this private
            # parent. Keep the host link and its previous-job target untouched.
            continue
        if child.is_symlink():
            args += ['--symlink', os.readlink(child), str(child)]
        elif child.is_file() and child.stat().st_size <= 16 * 1024 * 1024 and os.access(child, os.W_OK):
            # npm and other tools atomically replace HOME configuration files;
            # a file bind mount would make that fail with EBUSY. Private copies
            # retain original permissions and support normal rename semantics.
            fd = os.open(child, os.O_RDONLY)
            os.set_inheritable(fd, True)
            args += ['--perms', format(child.stat().st_mode & 0o777, '04o'),
                     '--file', str(fd), str(child)]
        else:
            args += ['--bind', str(child), str(child)]
    return [*args, '--dir', str(canonical)]


def ensure_bwrap():
    if shutil.which('bwrap'):
        return
    root_command = 'apt update && apt install -y bubblewrap'
    manual_command = 'sudo apt update && sudo apt install -y bubblewrap'
    retry_message = 'After installation, rerun the original build command.'
    if os.geteuid() != 0:
        raise RuntimeError('Cross-path reuse requires bubblewrap (bwrap), which is not installed. '
                           'The current user is not root, so automatic installation is unavailable.\n'
                           f'Run: {manual_command}\n{retry_message}')
    print('incremental workspace: bwrap is missing; installing bubblewrap as root: '
          'apt update && apt install -y bubblewrap', flush=True)
    try:
        subprocess.run(['apt', 'update'], check=True)
        subprocess.run(['apt', 'install', '-y', 'bubblewrap'], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f'Automatic bubblewrap installation failed: {error}.\n'
                           f'Run as root: {root_command}\n{retry_message}') from error
    if not shutil.which('bwrap'):
        raise RuntimeError('The installation command completed, but bwrap is still unavailable. '
                           f'Check PATH or run as root: {root_command}\n{retry_message}')


def enter(root, data, command):
    canonical = Path(data['canonical_root'])
    if not canonical.is_absolute() or canonical == Path('/'):
        raise ValueError('canonical_root must be an absolute workspace directory')
    env = os.environ.copy()
    env['OHOS_INCREMENTAL_ENTERED'] = str(canonical)
    env['OHOS_INCREMENTAL_ENTERED_PID'] = str(os.getpid())
    env['OHOS_INCREMENTAL_BUILD_TIME'] = data.get('build_time', '')
    env['OHOS_INCREMENTAL_BUILD_DATETIME'] = data.get('build_datetime', '')
    # HB rotates ccache.log before building. Distinct physical checkouts must
    # not rotate each other's log when they share HOME and the CI logical root.
    # Preserve the physical identity across the child entered at canonical.
    if not env.get('CCACHE_LOG_SUFFIX'):
        stat = root.stat()
        identity = f'{root}:{stat.st_dev}:{stat.st_ino}'
        env['CCACHE_LOG_SUFFIX'] = 'incremental-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
    if root == canonical:
        phase('mapped workspace ready')
        validate_tools(root, data)
        restore_transferred_snapshot(root, data, tools_validated=True)
        os.chdir(root)
        os.execvpe(command[0], command, env)
    ensure_bwrap()
    env[PHYSICAL_ROOT_ENV] = str(root)
    started = time.perf_counter()
    print(f'incremental workspace: mapping {root} to {canonical}', flush=True)
    mountpoint_args = private_mountpoint(canonical)
    phase('private mountpoint prepared', started)
    # Validate tools inside the mapped root: absolute CI symlinks can be
    # intentionally unresolved in the physical checkout before this bind.
    checked_command = ['python3', 'build/scripts/incremental_workspace.py',
                       '--root', str(canonical), 'enter', '--', *command]
    args = ['bwrap', '--die-with-parent', '--bind', '/', '/', *mountpoint_args, '--proc', '/proc',
            '--dev-bind', '/dev', '/dev', '--bind', str(root), str(canonical),
            '--chdir', str(canonical), '--', *checked_command]
    os.execvpe(args[0], args, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='mode', required=True)
    for mode in ('capture', 'audit'):
        p = sub.add_parser(mode)
        p.add_argument('--build-dir', default='out/wukong100')
        p.add_argument('--target', default='make_all')
        if mode == 'capture':
            p.add_argument('--allow-dirty', action='store_true')
            p.add_argument('--manifest', type=Path, help='optional manifest destination; default opts in this workspace')
    sub.add_parser('restore')
    for mode in ('prepare', 'finish'):
        p = sub.add_parser(mode)
        p.add_argument('arguments', nargs=argparse.REMAINDER)
    p = sub.add_parser('enter'); p.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = args.root.absolute()
    if args.mode in ('capture', 'audit'):
        build = root / args.build_dir
        if args.mode == 'capture':
            capture(root, build, args.target, args.allow_dirty, args.manifest)
        else:
            code, count, output = audit(root, build, args.target)
            print(output, end=''); print(f'planned_steps={count}', file=sys.stderr)
            return code
    elif args.mode in ('prepare', 'finish'):
        arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
        enabled = activate_product_policy(root, arguments)
        if not enabled:
            if args.mode == 'prepare':
                env = {key: value for key, value in os.environ.items()
                       if not key.startswith('OHOS_INCREMENTAL_')}
                os.chdir(root)
                os.execvpe('bash', ['bash', './build.sh', *arguments], env)
            return 0
        if args.mode == 'prepare':
            os.environ[VERIFIED_RUN_ENV] = uuid.uuid4().hex
            request = build_request(arguments)
            if request.product_name:
                cache = request_build_dir(root, request) / '.incremental'
                for journal in cache.glob('verified-inputs.*.state'):
                    try:
                        journal.unlink(missing_ok=True)
                    except OSError as error:
                        phase(f'warning: cannot remove previous entry verification: {error}')
            prepare_build(root, arguments)
            # No mapping was necessary. Re-enter the original shell once with
            # a guard; do not return to a second copy of the build pipeline.
            env = os.environ.copy()
            env['OHOS_INCREMENTAL_ENTERED'] = str(root)
            env['OHOS_INCREMENTAL_ENTERED_PID'] = str(os.getpid())
            os.chdir(root)
            os.execvpe('bash', ['bash', './build.sh', *arguments], env)
        else:
            finish_build(root, arguments)
    else:
        if args.mode == 'enter' and os.environ.get('OHOS_INCREMENTAL_PRODUCT_POLICY'):
            if not activate_product_policy(root):
                raise ValueError('Mapped workspace product feature is disabled')
        data = json.loads((root / MANIFEST).read_text())
        if data.get('version') != 1: raise ValueError('Unsupported manifest version')
        if args.mode == 'restore':
            restore(root, data)
        else:
            command = args.command
            if command[:1] == ['--']: command = command[1:]
            if not command: raise ValueError('enter requires a command after --')
            enter(root, data, command)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'incremental workspace: {error}', file=sys.stderr)
        sys.exit(1)
