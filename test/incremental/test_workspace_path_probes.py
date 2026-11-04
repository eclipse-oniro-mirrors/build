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
"""Do not dereference unrelated or replaced CI links before entering bwrap."""
from contextlib import contextmanager, ExitStack
import errno
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    'workspace_path_probes', ROOT / 'build/scripts/incremental_workspace.py')
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)


class WorkspacePathProbesTest(unittest.TestCase):
    def test_replaced_canonical_link_never_probes_its_unavailable_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            canonical = parent / 'code'
            canonical.symlink_to(parent / 'offline-job', target_is_directory=True)
            sibling = parent / 'sibling'
            sibling.mkdir()
            original = Path.is_dir

            def is_dir(path):
                if path == canonical:
                    raise OSError(errno.EIO, 'Input/output error', str(path))
                return original(path)

            with mock.patch.object(Path, 'is_dir', is_dir):
                arguments = workspace.private_mountpoint(canonical)
            self.assertEqual(arguments[:2], ['--tmpfs', str(parent)])
            self.assertIn(['--bind', str(sibling), str(sibling)],
                          [arguments[index:index + 3] for index in range(len(arguments))])
            self.assertEqual(arguments[-2:], ['--dir', str(canonical)])
            self.assertEqual(os.readlink(canonical), str(parent / 'offline-job'))

    def test_unreadable_real_canonical_directory_still_reports_io_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            canonical = Path(temporary)
            error = OSError(errno.EIO, 'Input/output error', str(canonical))
            with mock.patch.object(Path, 'is_dir', side_effect=error):
                with self.assertRaises(OSError) as caught:
                    workspace.private_mountpoint(canonical)
            self.assertEqual(caught.exception.errno, errno.EIO)

    def fixture(self, root):
        build = root / 'out/probe'
        cache = build / 'clang_x64/third_party/flatbuffers/CMakeCache.txt'
        cache.parent.mkdir(parents=True)
        canonical = Path('/ci/source')
        cache.write_text(
            f'CMAKE_HOME_DIRECTORY:INTERNAL={canonical}/third_party/flatbuffers\n'
            f'CMAKE_CACHEFILE_DIR:INTERNAL={canonical}/{cache.parent.relative_to(root)}\n')
        (build / 'src').symlink_to('/unavailable/source/third_party/ffmpeg', target_is_directory=True)
        return build, canonical, cache

    def test_cmake_search_does_not_stat_nonmatching_src_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build, canonical, cache = self.fixture(root)
            original = os.scandir

            class Entry:
                def __init__(self, entry):
                    self.entry = entry

                def __getattr__(self, name):
                    return getattr(self.entry, name)

                def is_dir(self, *args, **kwargs):
                    if self.entry.name == 'src':
                        raise OSError(errno.EIO, 'Input/output error', str(build / 'src'))
                    return self.entry.is_dir(*args, **kwargs)

            @contextmanager
            def scandir(path):
                with original(path) as entries:
                    yield iter(Entry(entry) for entry in entries)

            with ExitStack() as patches:
                patches.enter_context(mock.patch.object(os, 'scandir', scandir))
                if hasattr(build, '_accessor'):  # pathlib before Python 3.11.
                    patches.enter_context(mock.patch.object(build._accessor, 'scandir', scandir))
                result, evidence = workspace.legacy_source_root(root, build)
            self.assertEqual(result, canonical)
            self.assertEqual(evidence, str(cache.relative_to(root)))

    def test_io_failure_reading_relevant_cache_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build, _, cache = self.fixture(root)
            original = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == cache:
                    raise OSError(errno.EIO, 'Input/output error', str(path))
                return original(path, *args, **kwargs)

            with mock.patch.object(Path, 'read_text', read_text):
                with self.assertRaises(OSError) as caught:
                    workspace.legacy_source_root(root, build)
            self.assertEqual(caught.exception.errno, errno.EIO)


if __name__ == '__main__':
    unittest.main(verbosity=2)
