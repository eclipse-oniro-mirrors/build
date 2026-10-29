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
"""Optional binary packages must be noticed when added, changed or removed."""
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
SCRIPT = ROOT / 'build/ohos/common/binary_install_info.py'


class BinaryMetadataTest(unittest.TestCase):
    def test_absent_optional_input_settles_and_real_changes_are_not_hidden(self):
        with tempfile.TemporaryDirectory(prefix='binary-metadata-test-') as temporary:
            home = Path(temporary)
            inputs = home / 'inputs'
            inputs.mkdir()
            source = inputs / 'src.json'
            source.write_text('[]')
            optional = inputs / 'component_dist/arm64/packages/dist.json'
            output = home / 'installed.json'
            command = shlex.join(['python3', str(SCRIPT), '--dist-parts-info-file', str(optional),
                                  '--parts-src-installed-info-file', str(source),
                                  '--binary-installed-info-file', str(output), '--depfile', 'result.d'])
            (home / 'build.ninja').write_text(f'rule generate\n  command = {command}\n'
                                             f'  depfile = result.d\n  restat = 1\n'
                                             f'build {output}: generate {source}\nbuild all: phony {output}\n')

            def build():
                result = subprocess.run([str(NINJA), '-C', str(home), 'all'], text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                return result.stdout

            build()
            self.assertEqual(json.loads(output.read_text()), [])
            self.assertIn('no work to do', build())
            optional.parent.mkdir(parents=True)
            optional.write_text('[{"part_name":"first"}]')
            self.assertIn('[1/1]', build())
            self.assertEqual(json.loads(output.read_text()), [{'part_name': 'first'}])
            self.assertIn('no work to do', build())
            optional.write_text('[{"part_name":"second"}]')
            self.assertIn('[1/1]', build())
            self.assertEqual(json.loads(output.read_text()), [{'part_name': 'second'}])
            optional.unlink()
            self.assertIn('[1/1]', build())
            self.assertEqual(json.loads(output.read_text()), [])
            self.assertIn('no work to do', build())


if __name__ == '__main__':
    unittest.main(verbosity=2)
