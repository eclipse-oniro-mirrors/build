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
"""A cached validation must still reject a real privilege change."""
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
SCRIPT = ROOT / 'build/ohos/packages/process_field_validate.py'


class ProcessValidationTest(unittest.TestCase):
    def test_changed_privileges_fail_and_never_refresh_success_marker(self):
        with tempfile.TemporaryDirectory(prefix='process-validation-test-') as temporary:
            home = Path(temporary)
            system = home / 'system-init'
            vendor = home / 'vendor-init'
            system.mkdir()
            vendor.mkdir()
            cfg = system / 'service.cfg'
            cfg.write_text(json.dumps({'services': [{'name': 'test-service', 'uid': 'shell'}]}))
            privilege = home / 'privilege.json'
            critical = home / 'critical.json'
            privilege.write_text('{"high_privilege_process_list": []}')
            critical.write_text('{"critical_reboot_process_list": []}')
            marker = home / 'validated'
            command = shlex.join(['python3', str(SCRIPT), '--sys-cfg-folder', str(system),
                                  '--vendor-cfg-folder', str(vendor),
                                  '--high-privilege-process-list-path', str(privilege),
                                  '--critical-reboot-process-list-path', str(critical),
                                  '--result-path', str(marker), '--depfile', 'validate.d'])
            (home / 'build.ninja').write_text(f'rule validate\n  command = {command}\n'
                                             f'  depfile = validate.d\n  restat = 1\n'
                                             f'build {marker}: validate\nbuild all: phony {marker}\n')

            def build(expected=0):
                result = subprocess.run([str(NINJA), '-C', str(home), 'all'], text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, expected, result.stdout)
                return result.stdout

            build()
            self.assertIn('no work to do', build())
            previous = marker.stat().st_mtime_ns
            cfg.write_text(json.dumps({'services': [{'name': 'test-service', 'uid': 'root'}]}))
            self.assertIn('not authenticated', build(1))
            self.assertEqual(marker.stat().st_mtime_ns, previous)
            privilege.write_text('{"high_privilege_process_list": [{"name":"test-service","uid":"root"}]}')
            build()
            self.assertGreater(marker.stat().st_mtime_ns, previous)
            self.assertIn('no work to do', build())


if __name__ == '__main__':
    unittest.main(verbosity=2)
