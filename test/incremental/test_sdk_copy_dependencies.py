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
"""SDK filtering must settle without hiding lost files or changed API inputs."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
SCRIPT = ROOT / 'interface/sdk-js/remove_internal.py'


class SdkCopyTest(unittest.TestCase):
    def test_copy_settles_and_recovers_missing_api(self):
        with tempfile.TemporaryDirectory(prefix='sdk-copy-test-') as temporary:
            home = Path(temporary)
            source = home / 'api'
            source.mkdir()
            (source / 'public.d.ts').write_text('export const value = 1;\n')
            (source / '@internal').mkdir()
            (source / '@internal/private.d.ts').write_text('private\n')
            output = home / 'copied'
            depfile = home / 'copy.d'
            command = shlex.join(['python3', str(SCRIPT), '--input', str(source),
                                  '--output', str(output), '--depfile', str(depfile)])
            (home / 'build.ninja').write_text(
                f'rule copy\n  command = {command}\n  depfile = {depfile}\n'
                f'  restat = 1\nbuild {output}: copy\nbuild all: phony {output}\n')

            def build():
                result = subprocess.run([str(NINJA), '-C', str(home), 'all'],
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                return result.stdout

            build()
            self.assertFalse((output / '@internal').exists())
            self.assertIn('no work to do', build())
            (output / 'public.d.ts').unlink()
            self.assertIn('[1/1]', build())
            self.assertTrue((output / 'public.d.ts').is_file())
            self.assertIn('no work to do', build())
            (source / 'public.d.ts').write_text('export const value = 2;\n')
            self.assertIn('[1/1]', build())
            self.assertIn('value = 2', (output / 'public.d.ts').read_text())
            (source / 'new.d.ts').write_text('export const added = true;\n')
            self.assertIn('[1/1]', build())
            self.assertTrue((output / 'new.d.ts').is_file())
            self.assertIn('no work to do', build())


if __name__ == '__main__':
    unittest.main(verbosity=2)
