#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for find.py batch (multi-input) support and backward compatibility."""
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1]
FIND = BUILD / 'scripts/find.py'


class FindBatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.f1 = self.root / 'a.h'
        self.f2 = self.root / 'b.h'
        self.f1.write_text('a')
        self.f2.write_text('b')
        self.subdir = self.root / 'sub'
        self.subdir.mkdir()
        (self.subdir / 'c.h').write_text('c')

    def run_find(self, args):
        result = subprocess.run([sys.executable, str(FIND), *args],
                                text=True, capture_output=True)
        lines = result.stdout.strip().split('\n') if result.stdout.strip() else []
        return result.returncode, lines

    def test_single_file_returns_one_line(self):
        rc, lines = self.run_find([str(self.f1)])
        self.assertEqual(rc, 0)
        self.assertEqual(lines, [str(self.f1)])

    def test_multiple_files_batch_returns_all(self):
        rc, lines = self.run_find([str(self.f1), str(self.f2)])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(lines), sorted([str(self.f1), str(self.f2)]))

    def test_file_and_directory_batch(self):
        rc, lines = self.run_find([str(self.f1), str(self.subdir)])
        self.assertEqual(rc, 0)
        self.assertIn(str(self.f1), lines)
        self.assertIn(str(self.subdir / 'c.h'), lines)

    def test_missing_file_errors(self):
        rc, _ = self.run_find([str(self.f1), str(self.root / 'absent.h')])
        self.assertEqual(rc, 1)

    def test_return_relpath_single_file(self):
        rc, lines = self.run_find(
            [str(self.f1), '--return-relpath', '--base-dir=' + str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(lines, ['a.h'])

    def test_directory_walk_unchanged(self):
        rc, lines = self.run_find([str(self.subdir)])
        self.assertEqual(rc, 0)
        self.assertEqual(lines, [str(self.subdir / 'c.h')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
