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
"""Mirror a declared contribution and publish content-based completion.

--preserve declares paths owned by other targets. This explicit contract,
rather than cache history, controls stale-file cleanup. Runtime caches survive
only while the owned payload remains unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid


def record(path):
    before = path.lstat()
    result = {'mode': stat.S_IMODE(before.st_mode)}
    if stat.S_ISLNK(before.st_mode):
        result.update(kind='link', target=os.readlink(path))
    elif stat.S_ISDIR(before.st_mode):
        result.update(kind='directory')
    elif stat.S_ISREG(before.st_mode):
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for content in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(content)
        result.update(kind='file', sha256=digest.hexdigest(), size=before.st_size)
    else:
        raise ValueError('Unsupported copy input: ' + str(path))
    after = path.lstat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode')
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        raise RuntimeError('Copy input changed while hashing: ' + str(path))
    return result


def within(name, parent):
    return name == parent or parent in name.parents


def tree(path, excluded=()):
    result = {'.': record(path)}
    if result['.']['kind'] == 'directory':
        for parent, directories, files in os.walk(path, followlinks=False):
            for name in list(directories) + files:
                item = Path(parent) / name
                relative = item.relative_to(path)
                if any(within(relative, value) for value in excluded):
                    if name in directories:
                        directories.remove(name)
                    continue
                result[str(relative)] = record(item)
    return result


def remove(path):
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def write_changed(path, content):
    if path.is_file() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        temporary.write_text(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def make_escape(path):
    return str(path).replace('\\', '\\\\').replace('$', '$$').replace(' ', '\\ ').replace('#', '\\#').replace(':', '\\:')


def relative_exclusions(destination, values):
    result = []
    for value in values:
        path = Path(os.path.abspath(value))
        if path == destination:
            raise ValueError('A copy root cannot be preserved or discarded: ' + str(path))
        if destination in path.parents:
            result.append(path.relative_to(destination))
    return tuple(sorted(result))


def copy_outputs(pairs, state_path, stamp_path, depfile_path, preserve=(), caches=(), declare_roots=False):
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    plans, rows, dependencies = [], {}, set()
    # Validate all sources and ownership boundaries before changing outputs.
    for source, destination in pairs:
        source, destination = Path(os.path.abspath(source)), Path(os.path.abspath(destination))
        actual_source, actual_destination = source.resolve(), destination.resolve()
        if (actual_source == actual_destination or actual_destination == Path('/')
                or actual_source in actual_destination.parents
                or actual_destination in actual_source.parents):
            raise ValueError('Overlapping source and destination: ' + str(source))
        kept = relative_exclusions(destination, preserve)
        discarded = relative_exclusions(destination, caches)
        excluded = kept + discarded
        for relative in kept:
            candidate = source / relative
            if candidate.is_symlink() or candidate.exists():
                raise ValueError('Source overlaps a preserved contribution: ' + str(candidate))
        desired = tree(source, excluded)
        for name, info in desired.items():
            if info['kind'] != 'directory' and any(within(value, Path(name)) for value in excluded):
                raise ValueError('Copy input conflicts with another contribution: ' + str(source / name))
        rows[str(destination)] = {'source': str(source), 'entries': desired,
                                  'preserve': list(map(str, kept)), 'caches': list(map(str, discarded))}
        plans.append((source, destination, desired, kept, discarded))
    repair = previous.get('repair') if isinstance(previous, dict) else None
    state = {'version': 2, 'copies': rows, 'repair': repair}
    changed = previous != state
    repaired = False
    declared = {str(destination) for _, destination, _, _, _ in plans} if declare_roots else set()
    for source, destination, desired, kept, discarded in plans:
        excluded = kept + discarded
        if destination.is_symlink() and desired['.']['kind'] != 'link':
            destination.unlink()
            changed = True
            repaired = True
        try:
            existing = tree(destination, excluded)
        except (FileNotFoundError, NotADirectoryError):
            existing = {}
        # The contribution owns every non-excluded name, even without a cache.
        # Keep ancestors needed by other contributors; never follow symlinks.
        for name in sorted(existing.keys() - desired.keys(), key=len, reverse=True):
            relative = Path(name)
            if name == '.' or any(within(value, relative) for value in excluded):
                continue
            target = destination / relative
            if target.is_symlink() or not target.is_dir():
                target.unlink(missing_ok=True)
            else:
                target.rmdir()
            changed = True
            repaired = True
        needs_copy = any(existing.get(name) != value for name, value in desired.items())
        if needs_copy:
            for name, expected in sorted(desired.items(), key=lambda item: len(Path(item[0]).parts)):
                old = existing.get(name)
                if old is not None and old['kind'] != expected['kind']:
                    target = destination if name == '.' else destination / name
                    if old['kind'] == 'directory' and any(target.iterdir()):
                        raise RuntimeError('Conflicting output directory: ' + str(target))
                    remove(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if desired['.']['kind'] == 'directory':
                destination.mkdir(exist_ok=True)
                # GNU cp preserves modes, links, xattrs and hard-link groups.
                # Filter a private snapshot so no other contribution is copied.
                # Shared destination ancestors may be cleaned by another
                # contributor. Keep staging beside our private state instead.
                state_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.directory-copy-', dir=state_path.parent) as temporary:
                    snapshot = Path(temporary) / 'payload'
                    subprocess.run(['cp', '-af', '--', str(source), str(snapshot)], check=True)
                    for relative in excluded:
                        candidate = snapshot / relative
                        if candidate.is_symlink() or candidate.exists():
                            remove(candidate)
                    if tree(snapshot, excluded) != desired:
                        raise RuntimeError('Copy input changed while taking snapshot: ' + str(source))
                    subprocess.run(['cp', '-af', '--', str(snapshot) + '/.', str(destination)], check=True)
            else:
                remove(destination)
                try:
                    os.link(source, destination, follow_symlinks=False)
                except OSError:
                    subprocess.run(['cp', '-af', '--', str(source), str(destination)], check=True)
            changed = True
            repaired = True
        if tree(source, excluded) != desired:
            raise RuntimeError('Copy source changed during publication: ' + str(source))
        for name, expected in desired.items():
            origin = source if name == '.' else source / name
            target = destination if name == '.' else destination / name
            if record(target) != expected:
                raise RuntimeError('Copy output does not match input: ' + str(target))
            if expected['kind'] == 'link':
                dependencies.update(value for value in [str(origin.parent), str(target.parent)] if value not in declared)
            else:
                dependencies.update(value for value in [str(origin), str(target)] if value not in declared)
    if repaired:
        state['repair'] = uuid.uuid4().hex
    if changed:
        for _, destination, _, _, discarded in plans:
            for relative in discarded:
                path = destination / relative
                if path.is_symlink() or path.exists():
                    remove(path)
    write_changed(state_path, json.dumps(state, sort_keys=True, separators=(',', ':')) + '\n')
    depfile = make_escape(os.path.relpath(stamp_path)) + ': ' + ' '.join(
        make_escape(os.path.relpath(name)) for name in sorted(dependencies)) + '\n'
    write_changed(depfile_path, depfile)
    if changed or not stamp_path.exists():
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.touch()


def publish_state(source, destination):
    data = json.loads(source.read_text())
    if not isinstance(data, dict) or data.get('version') != 2 or not isinstance(data.get('copies'), dict):
        raise ValueError('Invalid directory copy state: ' + str(source))
    for root, row in data['copies'].items():
        excluded = tuple(Path(value) for value in row['preserve'] + row['caches'])
        actual = tree(Path(root), excluded)
        actual = {name: value for name, value in actual.items()
                  if name in row['entries'] or not any(within(path, Path(name)) for path in excluded)}
        if actual != row['entries']:
            raise RuntimeError('Directory contribution changed after generation: ' + root)
    semantic = hashlib.sha256(json.dumps(data['copies'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        previous = json.loads(destination.read_text())
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    # A missing cache does not lose the last published repair identity. Actual
    # payload repairs have their own new token and must invalidate consumers.
    repair = data.get('repair') or previous.get('repair')
    write_changed(destination, json.dumps({'semantic': semantic, 'repair': repair}, sort_keys=True) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish-state', nargs=2, type=Path)
    parser.add_argument('--pair', nargs=2, action='append')
    parser.add_argument('--preserve', action='append', default=[])
    parser.add_argument('--discard-cache', action='append', default=[])
    parser.add_argument('--declare-roots', action='store_true')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--stamp', type=Path)
    parser.add_argument('--depfile', type=Path)
    args = parser.parse_args()
    if args.publish_state:
        publish_state(*args.publish_state)
    else:
        if not all([args.pair, args.state, args.stamp, args.depfile]):
            parser.error('copying requires --pair, --state, --stamp and --depfile')
        copy_outputs(args.pair, args.state, args.stamp, args.depfile,
                     args.preserve, args.discard_cache, args.declare_roots)


if __name__ == '__main__':
    main()
