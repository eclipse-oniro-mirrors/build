#!/usr/bin/env python3
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

"""Evaluation candidate: fresh installation trees with verified large-file reuse."""
import contextvars
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

PARTITIONS = ('system', 'vendor', 'eng_system', 'eng_chipset', 'sys_prod',
              'chip_prod', 'updater', 'updater_vendor', 'ramdisk',
              'cloud_rom', 'system_arm64e')
MINIMUM_BYTES = 1024 * 1024
_ACTIVE = contextvars.ContextVar('previous_installation', default=None)


def current_copy_function():
    previous = _ACTIVE.get()
    return previous.copy2 if previous is not None and previous.moved else shutil.copy2


def current_file_test():
    previous = _ACTIVE.get()
    return previous.is_source_file if previous is not None else os.path.isfile


def file_identity(path):
    current = os.lstat(path)
    return (current.st_dev, current.st_ino, current.st_size,
            current.st_mtime_ns, current.st_ctime_ns, current.st_mode,
            current.st_uid, current.st_gid, current.st_nlink)


def same_bytes(source, previous):
    with open(source, 'rb') as left, open(previous, 'rb') as right:
        while True:
            source_bytes = left.read(MINIMUM_BYTES)
            if source_bytes != right.read(MINIMUM_BYTES):
                return False
            if not source_bytes:
                return True


def has_no_extra_metadata(source, previous, destination):
    parent = os.path.dirname(destination)
    paths = (source, previous, parent)
    if any(os.path.realpath(path) != os.path.abspath(path) for path in paths):
        return False
    if any(os.listxattr(path, follow_symlinks=False) for path in paths):
        return False
    if not ordinary_storage(previous) or not ordinary_storage(parent):
        return False
    return not os.stat(parent).st_mode & stat.S_ISGID


def ordinary_storage(path):
    """Reject Linux inode flags and project attributes a fresh copy would lose."""
    if fcntl is None or sys.platform != 'linux':
        return False
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
        flags = bytearray(8)
        fcntl.ioctl(descriptor, 0x80086601, flags, True)  # FS_IOC_GETFLAGS
        # Extent/index implementation bits do not change copy semantics.
        if int.from_bytes(flags[:4], sys.byteorder) & ~0x00081000:
            return False
        attributes = bytearray(28)
        fcntl.ioctl(descriptor, 0x801c581f, attributes, True)  # FS_IOC_FSGETXATTR
        xflags, extent_size, _, project, cow_size = struct.unpack('=5I', attributes[:20])
        return not any((xflags, extent_size, project, cow_size))
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def eligible(source, previous, destination):
    if os.path.lexists(destination):
        return False
    source_stat, old_stat = os.lstat(source), os.lstat(previous)
    if not stat.S_ISREG(source_stat.st_mode) or not stat.S_ISREG(old_stat.st_mode):
        return False
    if source_stat.st_size < MINIMUM_BYTES or source_stat.st_size != old_stat.st_size:
        return False
    if source_stat.st_mtime_ns != old_stat.st_mtime_ns or old_stat.st_nlink != 1:
        return False
    if old_stat.st_uid != os.geteuid() or old_stat.st_gid != os.getegid():
        return False
    return has_no_extra_metadata(source, previous, destination)


class PreviousInstallation:
    """Archive only disposable old partitions; always build fresh directories."""

    def __init__(self, platform, cache=None, system=None, enabled=True):
        self.platform = Path(platform).absolute()
        self.cache = Path(cache).absolute() if cache else self.platform.parent / '.install-copy-cache'
        self.system = Path(system).absolute() if system else self.platform / 'system'
        self.enabled = enabled
        self.large_source_seen = False
        self.backup = None
        self.moved = []
        self.lock = None
        self.token = None
        self.reused_files = 0
        self.reused_bytes = 0
        self.finished = False

    def __enter__(self):
        if not self._cache_safe():
            self.token = _ACTIVE.set(None)
            return self
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.cache / 'lock', os.O_RDWR | os.O_CREAT |
                                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            self.lock = os.fdopen(descriptor, 'a', encoding='utf-8')
            fcntl.flock(self.lock, fcntl.LOCK_EX)
            self._purge_abandoned()
            if self.enabled is None:
                self.enabled = self._read_hint()
            if self.enabled and self._supported_platform():
                self._start_archive()
        except (ImportError, OSError):
            self._disable()
        self.token = _ACTIVE.set(self if self.lock is not None else None)
        return self

    def __exit__(self, exception_type, exception, traceback):
        _ACTIVE.reset(self.token)
        try:
            if exception_type is None:
                self.finish()
            elif self.backup is not None:
                shutil.rmtree(self.backup)
        finally:
            self._unlock()
        return False

    def is_source_file(self, path):
        try:
            current = os.stat(path)
        except (OSError, ValueError):
            return False
        regular = stat.S_ISREG(current.st_mode)
        self.large_source_seen |= regular and current.st_size >= MINIMUM_BYTES
        return regular

    def finish(self):
        """Complete cleanup before publishing an action's successful output."""
        if self.finished:
            return
        if self.backup is not None:
            shutil.rmtree(self.backup)
            self.backup = None
        if self.lock is not None:
            self._write_hint()
        self.finished = True

    def previous_file(self, destination):
        try:
            relative = Path(destination).absolute().relative_to(self.platform)
        except ValueError:
            return None
        return self.backup / relative

    def copy2(self, source, destination, *, follow_symlinks=True):
        source_stat = os.lstat(source)
        if source_stat.st_size < MINIMUM_BYTES or not stat.S_ISREG(source_stat.st_mode):
            return shutil.copy2(source, destination, follow_symlinks=follow_symlinks)
        self.large_source_seen = True
        if os.path.isdir(destination):
            destination = os.path.join(destination, os.path.basename(source))
        previous = self.previous_file(destination)
        if previous is not None and self._reuse(source, previous, destination):
            return destination
        return shutil.copy2(source, destination, follow_symlinks=follow_symlinks)

    def _supported_platform(self):
        current = os.lstat(self.platform)
        return (current.st_uid == os.geteuid() and current.st_gid == os.getegid() and
                not os.listxattr(self.platform) and ordinary_storage(self.platform))

    def _read_hint(self):
        try:
            data = json.loads((self.cache / 'hint.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return (isinstance(data, dict) and data.get('schema') == 1 and
                data.get('platform') == str(self.platform) and data.get('large') is True)

    def _write_hint(self):
        data = {'schema': 1, 'platform': str(self.platform), 'large': self.large_source_seen}
        content = json.dumps(data, sort_keys=True)
        target, temporary = self.cache / 'hint.json', None
        try:
            if target.read_text(encoding='utf-8') == content:
                return
        except (OSError, UnicodeError):
            pass
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             prefix='hint-', dir=self.cache,
                                             delete=False) as output:
                temporary = output.name
                output.write(content)
            os.replace(temporary, target)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    def _start_archive(self):
        self.backup = Path(tempfile.mkdtemp(prefix='previous-', dir=self.cache))
        marker = {'schema': 1, 'platform': str(self.platform)}
        (self.backup / 'owner.json').write_text(json.dumps(marker), encoding='utf-8')
        self._archive()

    def _cache_safe(self):
        if fcntl is None:
            return False
        try:
            cache = self.cache.resolve()
            # Default partitions have one component below platform and are
            # never archived through a symlink. Resolve only the two roots.
            roots = [self.platform, self.system]
            for root in roots:
                root = root.resolve()
                if cache.is_relative_to(root) or root.is_relative_to(cache):
                    return False
        except OSError:
            return False
        return True

    def _purge_abandoned(self):
        for previous in self.cache.glob('previous-*'):
            if self._owned(previous):
                shutil.rmtree(previous)

    def _owned(self, previous):
        if previous.is_symlink() or previous.stat().st_uid != os.geteuid():
            return False
        try:
            marker = json.loads((previous / 'owner.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return (isinstance(marker, dict) and set(marker) == {'schema', 'platform'} and
                isinstance(marker['schema'], int) and not isinstance(marker['schema'], bool) and
                marker['schema'] == 1 and marker['platform'] == str(self.platform))

    def _archive(self):
        roots = [self.system] + [self.platform / name for name in PARTITIONS[1:]]
        for source in roots:
            if source.is_dir() and not source.is_symlink():
                self._move_partition(source)

    def _move_partition(self, source):
        try:
            relative = source.absolute().relative_to(self.platform)
        except ValueError:
            return
        previous = self.backup / relative
        previous.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, previous)
        self.moved.append((source, previous))

    def _restore_archive(self):
        for source, previous in reversed(self.moved):
            os.rename(previous, source)
        self.moved.clear()

    def _disable(self):
        try:
            self._restore_archive()
            if self.backup is not None:
                shutil.rmtree(self.backup)
                self.backup = None
        finally:
            self._unlock()

    def _unlock(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def _reuse(self, source, previous, destination):
        try:
            if not eligible(source, previous, destination):
                return False
            before = file_identity(source), file_identity(previous)
            if not same_bytes(source, previous):
                return False
            if before != (file_identity(source), file_identity(previous)):
                return False
            os.link(previous, destination, follow_symlinks=False)
        except OSError:
            return False
        try:
            shutil.copystat(source, destination)
        except OSError:
            os.unlink(destination)
            raise
        self.reused_files += 1
        self.reused_bytes += os.stat(destination).st_size
        return True
