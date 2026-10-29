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
"""Stabilize equivalent scans without changing the installed baseline winner."""
from pathlib import Path
import os
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'build'), str(ROOT / 'build/hb')]
# Other legacy build scripts also expose a top-level package named util.
# Let discovery resolve HB's modules after those scripts have been imported.
import util
if str(ROOT / 'build/hb/util') not in util.__path__:
    util.__path__.append(str(ROOT / 'build/hb/util'))
from util.loader.generate_targets_gn import gen_targets_gn


class TargetOrderTest(unittest.TestCase):
    def test_reversed_scan_keeps_existing_overlay_winner_and_mtime(self):
        with tempfile.TemporaryDirectory(prefix='target-order-test-') as temporary:
            home = Path(temporary)
            targets = {'base': {'part': '//base:component'}, 'vendor': {'part': '//vendor:component'}}
            gen_targets_gn(targets, str(home))
            output = home / 'parts_list.gni'
            original = output.read_bytes()
            old_time = 1700000000123456789
            os.utime(output, ns=(old_time, old_time))
            gen_targets_gn(dict(reversed(list(targets.items()))), str(home))
            self.assertEqual(original, output.read_bytes())
            self.assertEqual(output.stat().st_mtime_ns, old_time)
            # The last installed payload for an overlapping path stays vendor.
            labels = re.findall(r'"([^"\n]+)"', output.read_text())
            payloads = {'//base:component': b'default-config', '//vendor:component': b'device-config'}
            self.assertEqual([payloads[label] for label in labels][-1], b'device-config')

    def test_changed_membership_updates_the_graph(self):
        with tempfile.TemporaryDirectory(prefix='target-membership-test-') as temporary:
            home = Path(temporary)
            gen_targets_gn({'base': {'part': '//base:component'}}, str(home))
            gen_targets_gn({'new': {'part': '//new:component'}, 'base': {'part': '//base:component'}}, str(home))
            labels = re.findall(r'"([^"\n]+)"', (home / 'parts_list.gni').read_text())
            self.assertEqual(labels, ['//new:component', '//base:component'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
