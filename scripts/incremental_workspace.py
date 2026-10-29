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
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

MANIFEST = 'out/incremental_workspace.json'


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


def prepare_build(root, arguments):
    destination = root / MANIFEST
    if destination.exists():
        data = json.loads(destination.read_text())
    else:
        # Legacy output can still preserve paths on a rename. Source timestamp
        # fingerprints require the automatic seal from a successful build.
        request = build_request(arguments)
        config_file = root / 'out/ohos_config.json'
        if not request.product_name or not config_file.is_file():
            return
        config = json.loads(config_file.read_text())
        stored_root = config.get('root_path')
        if not isinstance(stored_root, str) or not Path(stored_root).is_absolute():
            return
        canonical = Path(stored_root)
        if canonical == root:
            return
        build = root / 'out' / request.product_name.split('@', 1)[0]
        if not (build / 'build.ninja').is_file():
            return
        data = {'version': 1, 'canonical_root': str(canonical),
                'build_dir': str(build.relative_to(root)), 'target': 'make_all',
                'files': {}, 'tools': {}, 'automatic': True, 'unsealed': True}
        args = (build / 'args.gn').read_text() if (build / 'args.gn').is_file() else ''
        for name, field in (('ohos_build_time', 'build_time'),
                            ('ohos_build_datetime', 'build_datetime')):
            match = re.search(r'^' + name + r'\s*=\s*(.*)$', args, re.M)
            data[field] = match[1].strip('"') if match else ''
        write_manifest(destination, data)
    if data.get('version') != 1:
        raise ValueError('Unsupported manifest version')
    if (data.get('automatic') and Path(data['canonical_root']) == root
            and data.get('origin_instance') == instance_identity(root)):
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


def finish_build(root, arguments):
    request = build_request(arguments)
    if (not request.product_name or request.build_only_gn or request.build_only_load
            or request.build_target != ['make_all']):
        return
    build = root / 'out' / request.product_name.split('@', 1)[0]
    if not (build / 'build.ninja').is_file():
        return
    destination = root / MANIFEST
    previous = json.loads(destination.read_text()) if destination.exists() else None
    identity = graph_identity(build)
    previous_graph = previous.get('graph_identity', {}) if previous else {}
    same_graph = previous_graph == identity
    # GN rewrites large generated graphs even on a no-op. A successful Ninja
    # invocation with no material work, unchanged arguments/deps and unchanged
    # source stamps can refresh this identity without rediscovering all inputs.
    if (not same_graph and previous_graph.get('args') == identity.get('args')
            and previous_graph.get('.ninja_deps') == identity.get('.ninja_deps')
            and only_optional_probe_executed(build)):
        same_graph = True
    if (previous and previous.get('canonical_root') == str(root)
            and previous.get('build_dir') == str(build.relative_to(root))
            and same_graph):
        unchanged = True
        for name, record in previous['files'].items():
            path = root / name
            if (not path.exists() or path.stat().st_mtime_ns != record['mtime_ns']
                    or bool(path.stat().st_mode & 0o111) != record.get('executable', bool(path.stat().st_mode & 0o111))):
                unchanged = False
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
            print('incremental workspace: successful baseline refreshed (unchanged inputs)', flush=True)
            return
    automatic = bool(previous.get('automatic')) if previous else True
    capture(root, build, 'make_all', True, automatic=automatic)


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
    for name, expected in data.get('tools', {}).items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'Invalid tool path: {name}')
        path = root / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError(f'Toolchain changed: {name}; create a matching build baseline before reusing this snapshot')


def ninja_command(root, build, *args):
    machine = 'aarch64' if os.uname().machine == 'aarch64' else 'x86'
    return [str(root / f'prebuilts/build-tools/linux-{machine}/bin/ninja'),
            '-C', str(build), '-w', 'dupbuild=warn', *args]


def audit(root, build, target):
    p = subprocess.run(ninja_command(root, build, '-n', '-d', 'explain', target),
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    steps = re.findall(r'^\[(\d+)/(\d+)\] (\S+)', p.stdout, re.M)
    return p.returncode, len(steps), p.stdout


def capture(root, build, target, allow_dirty, manifest=None, automatic=False):
    print('incremental workspace: recording successful build baseline', flush=True)
    code, count, output = audit(root, build, target)
    if code or (count and not allow_dirty):
        raise RuntimeError(f'Cannot seal a clean baseline: exit={code}, planned={count}.\n' + output[-4000:])
    paths = set()
    for tool in ('inputs', 'deps'):
        print(f'incremental workspace: collecting Ninja {tool}', flush=True)
        p = subprocess.Popen(ninja_command(root, build, '-t', tool, *([target] if tool == 'inputs' else [])),
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        for line in p.stdout:
            if tool == 'deps' and not line.startswith('    '):
                continue
            paths.add(line.strip())
        if p.wait():
            raise RuntimeError(f'ninja -t {tool} failed')
    depfile = build / 'build.ninja.d'
    if depfile.exists():
        deps = depfile.read_text().replace('\\\n', '')
        paths.update(shlex.split(deps.split(': ', 1)[-1]))
    files = {}
    next_progress = 50000
    for value in sorted(paths):
        path = Path(os.path.abspath(build / value)).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue  # External environment must be pinned separately, not retimestamped.
        if (relative.parts and relative.parts[0] == 'out') or path.is_symlink():
            continue
        if not path.is_file() and not path.is_dir():
            continue
        stat = path.stat()
        kind = 'directory' if path.is_dir() else 'file'
        files[str(relative)] = {'sha256': directory_digest(path) if kind == 'directory' else digest(path),
                                'kind': kind, 'mtime_ns': stat.st_mtime_ns,
                                'executable': bool(stat.st_mode & 0o111)}
        if len(files) >= next_progress:
            print(f'incremental workspace: fingerprinted {len(files)} source inputs', flush=True)
            next_progress += 50000
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
    data = {'version': 1, 'canonical_root': str(root), 'build_dir': str(build.relative_to(root)),
            'target': target, 'baseline_planned': count, 'files': files, 'mtime_ceiling_ns': ceiling,
            'tools': tool_identities(root),
            'build_time': value('ohos_build_time'), 'build_datetime': value('ohos_build_datetime'),
            'automatic': automatic, 'origin_instance': instance_identity(root),
            'graph_identity': graph_identity(build)}
    destination = root / (manifest or MANIFEST)
    write_manifest(destination, data)
    print(json.dumps({'manifest': str(destination), 'source_inputs': len(files), 'baseline_planned': count}))


def restore(root, data):
    canonical = Path(data['canonical_root']) if 'canonical_root' in data else root
    if root != canonical:
        enter(root, data, ['python3', 'build/scripts/incremental_workspace.py',
                           '--root', str(canonical), 'restore'])
    validate_tools(root, data)
    counts = {'restored': 0, 'changed': 0, 'missing': 0}
    for name, record in data['files'].items():
        path = root / name
        # Reject traversal even for manifests provided with external snapshots.
        if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())):
            raise ValueError(f'Invalid source input: {name}')
        is_directory = record.get('kind') == 'directory'
        if not path.exists():
            counts['missing'] += 1
        elif ((path.is_dir() != is_directory) or
              (directory_digest(path) if is_directory else digest(path)) != record['sha256'] or
              bool(path.stat().st_mode & 0o111) != record.get('executable', bool(path.stat().st_mode & 0o111))):
            if data.get('mtime_ceiling_ns', 0) >= time.time_ns():
                raise RuntimeError('Snapshot outputs are newer than the host clock; synchronize clocks before restoring changed inputs')
            counts['changed'] += 1
            # A changed file with an old preserved mtime still must rebuild.
            os.utime(path, None)
        else:
            st = path.stat()
            if st.st_mtime_ns != record['mtime_ns']:
                os.utime(path, ns=(st.st_atime_ns, record['mtime_ns']))
                counts['restored'] += 1
    print(json.dumps(counts))


def restore_transferred_snapshot(root, data):
    """Restore once per transferred baseline, never retouch later local edits."""
    stat = root.stat()
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    identity = {'manifest_sha256': digest(root / MANIFEST), 'boot_id': boot_id,
                'device': stat.st_dev, 'inode': stat.st_ino}
    state = root / 'out/incremental_workspace.restore-state.json'
    try:
        previous = json.loads(state.read_text())
    except (FileNotFoundError, ValueError):
        previous = None
    if previous != identity:
        restore(root, data)
        state.write_text(json.dumps(identity) + '\n')


def private_mountpoint(canonical):
    try:
        exists = canonical.is_dir()
    except PermissionError:
        exists = False
    if exists:
        if any(canonical.iterdir()):
            raise RuntimeError(f'Canonical mountpoint is occupied: {canonical}')
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


def enter(root, data, command):
    canonical = Path(data['canonical_root'])
    if not canonical.is_absolute() or canonical == Path('/'):
        raise ValueError('canonical_root must be an absolute workspace directory')
    env = os.environ.copy()
    env['OHOS_INCREMENTAL_ENTERED'] = str(canonical)
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
        validate_tools(root, data)
        restore_transferred_snapshot(root, data)
        os.chdir(root)
        os.execvpe(command[0], command, env)
    if not shutil.which('bwrap'):
        raise RuntimeError('Cross-path reuse requires bubblewrap or an equivalent CI container mount')
    mountpoint_args = private_mountpoint(canonical)
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
        if args.mode == 'prepare':
            prepare_build(root, arguments)
            # No mapping was necessary. Re-enter the original shell once with
            # a guard; do not return to a second copy of the build pipeline.
            env = os.environ.copy()
            env['OHOS_INCREMENTAL_ENTERED'] = str(root)
            os.chdir(root)
            os.execvpe('bash', ['bash', './build.sh', *arguments], env)
        else:
            finish_build(root, arguments)
    else:
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
