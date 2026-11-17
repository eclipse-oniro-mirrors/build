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
import time
import uuid


def record(path, *, contents=True):
    before = path.lstat()
    result = {'mode': stat.S_IMODE(before.st_mode)}
    if stat.S_ISLNK(before.st_mode):
        result.update(kind='link', target=os.readlink(path))
    elif stat.S_ISDIR(before.st_mode):
        result.update(kind='directory')
    elif stat.S_ISREG(before.st_mode):
        result.update(kind='file', size=before.st_size)
        if contents:
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for content in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(content)
            result['sha256'] = digest.hexdigest()
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
    if result['.']['kind'] != 'directory':
        return result
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


def apply_payload_copy(source, destination, desired, existing, excluded, state_path):
    for name, expected in sorted(desired.items(), key=lambda item: len(Path(item[0]).parts)):
        old = existing.get(name)
        if old is not None and old['kind'] != expected['kind']:
            target = destination if name == '.' else destination / name
            if old['kind'] == 'directory' and any(target.iterdir()):
                raise RuntimeError('Conflicting output directory: ' + str(target))
            remove(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if desired['.']['kind'] != 'directory':
        remove(destination)
        try:
            os.link(source, destination, follow_symlinks=False)
        except OSError:
            subprocess.run(['cp', '-af', '--', str(source), str(destination)], check=True)
        return
    destination.mkdir(exist_ok=True)
    # GNU cp preserves modes, links, xattrs and hard-link groups. Filter a
    # private snapshot so no other contribution is copied. Shared destination
    # ancestors may be cleaned by another contributor; stage beside our state.
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


def make_copy_plans(pairs, preserve, caches):
    plans, rows = [], {}
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
    return plans, rows


def prune_stale_names(destination, existing, desired, excluded):
    # The contribution owns every non-excluded name, even without a cache.
    # Keep ancestors needed by other contributors; never follow symlinks.
    removed = False
    for name in sorted(existing.keys() - desired.keys(), key=len, reverse=True):
        relative = Path(name)
        if name == '.' or any(within(value, relative) for value in excluded):
            continue
        target = destination / relative
        if target.is_symlink() or not target.is_dir():
            target.unlink(missing_ok=True)
        else:
            target.rmdir()
        removed = True
    return removed


def discard_runtime_caches(plans):
    for _, destination, _, _, discarded in plans:
        for relative in discarded:
            path = destination / relative
            if path.is_symlink() or path.exists():
                remove(path)


def copy_outputs(pairs, state_path, stamp_path, depfile_path, preserve=(), caches=(), declare_roots=False,
                 publication=None, published_state=None):
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    plans, rows = make_copy_plans(pairs, preserve, caches)
    dependencies = set()
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
        if prune_stale_names(destination, existing, desired, excluded):
            changed = True
            repaired = True
        needs_copy = any(existing.get(name) != value for name, value in desired.items())
        if needs_copy:
            apply_payload_copy(source, destination, desired, existing, excluded, state_path)
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
        discard_runtime_caches(plans)
    write_changed(state_path, json.dumps(state, sort_keys=True, separators=(',', ':')) + '\n')
    if publication is not None:
        write_changed(publication, publication_content(state, published_state or publication))
    depfile = make_escape(os.path.relpath(stamp_path)) + ': ' + ' '.join(
        make_escape(os.path.relpath(name)) for name in sorted(dependencies)) + '\n'
    write_changed(depfile_path, depfile)
    if changed or not stamp_path.exists():
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.touch()


def publication_content(data, destination):
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
    return json.dumps({'semantic': semantic, 'repair': repair}, sort_keys=True) + '\n'


def root_entries_match(path, row):
    """Check the roots omitted from the copier's self-dependency-free depfile."""
    expected = row['entries']
    if record(path) != expected['.']:
        return False
    if expected['.']['kind'] != 'directory':
        return True
    excluded = tuple(Path(value) for value in row['preserve'] + row['caches'])
    actual = {}
    for child in path.iterdir():
        name = Path(child.name)
        if any(within(name, value) for value in excluded):
            continue
        if child.name not in expected and any(within(value, name) for value in excluded):
            continue
        # Ordinary child files already appear in the payload depfile. Root
        # membership and symlink targets need this check; rehashing those
        # declared child inputs would duplicate Ninja/entry verification.
        actual[child.name] = record(child, contents=False)
    wanted = {name: {key: item for key, item in value.items() if key != 'sha256'}
              for name, value in expected.items() if name != '.' and len(Path(name).parts) == 1}
    return actual == wanted


def plan_path(build, value):
    path = build / value
    if Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('Invalid directory copy plan path: ' + value)
    if build.resolve() not in path.parent.resolve().parents and path.parent.resolve() != build.resolve():
        raise ValueError('Directory copy plan escapes the build directory: ' + value)
    return path


def check_root_plan(build, plan):
    if plan.get('version') != 1:
        raise ValueError('Unsupported directory copy plan')
    publication = plan_path(build, plan['publication'])
    if not publication.exists():
        return False  # Ninja already has a missing declared output to rebuild.
    state, stamp = plan_path(build, plan['state']), plan_path(build, plan['stamp'])
    try:
        ceiling = stamp.stat().st_mtime_ns
        changed_state = state.stat().st_mtime_ns > ceiling
        roots = [plan_path(build, name) for name in plan['roots']]
        data = json.loads(state.read_text())
        matches = (data.get('version') == 2 and
                   set(data['copies']) == {str(path.absolute()) for path in roots} and
                   all(root_entries_match(path, data['copies'][str(path.absolute())]) for path in roots))
        if not changed_state and matches:
            return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        pass
    # Invalidate only our disposable metadata. Ninja then runs the real copier,
    # which repairs payloads and publishes a fresh repair token for consumers.
    publication.unlink(missing_ok=True)
    return True


def check_root_plans(build):
    started = time.perf_counter()
    checked, invalidated = 0, 0
    for path in sorted((build / '.directory_copy_plans').glob('**/*.json')):
        plan = json.loads(path.read_text())
        invalidated += check_root_plan(build, plan)
        checked += 1
    if checked:
        print(f'directory copy: root plans checked={checked} invalidated={invalidated} '
              f'elapsed_s={time.perf_counter() - started:.3f}', flush=True)


def prepare_registered_roots(root, arguments):
    from scripts.incremental_product_policy import resolve

    policy = resolve(root, arguments)
    if policy['enabled']:
        check_root_plans(Path(root) / policy['build_dir'])


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
    write_changed(destination, publication_content(data, destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish-state', nargs=2, type=Path)
    parser.add_argument('--check-root-plans', type=Path)
    parser.add_argument('--pair', nargs=2, action='append')
    parser.add_argument('--preserve', action='append', default=[])
    parser.add_argument('--discard-cache', action='append', default=[])
    parser.add_argument('--declare-roots', action='store_true')
    parser.add_argument('--publication', type=Path)
    parser.add_argument('--published-state', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--stamp', type=Path)
    parser.add_argument('--depfile', type=Path)
    args = parser.parse_args()
    if args.check_root_plans:
        check_root_plans(args.check_root_plans)
    elif args.publish_state:
        publish_state(*args.publish_state)
    else:
        if not all([args.pair, args.state, args.stamp, args.depfile]):
            parser.error('copying requires --pair, --state, --stamp and --depfile')
        copy_outputs(args.pair, args.state, args.stamp, args.depfile,
                     args.preserve, args.discard_cache, args.declare_roots,
                     args.publication, args.published_state)


if __name__ == '__main__':
    main()
