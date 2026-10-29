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
"""The declared ramdisk output must be a real byte-identical image."""
import gzip
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
SCRIPT = ROOT / 'build/ohos/images/mkimage/mkcpioimage.py'


class RamdiskOutputTest(unittest.TestCase):
    def test_real_cpio_payload_is_preserved_and_noop_settles(self):
        with tempfile.TemporaryDirectory(prefix='ramdisk-output-test-') as temporary:
            home = Path(temporary)
            source = home / 'packages/phone/ramdisk'
            source.mkdir(parents=True)
            (source.parent / 'images').mkdir()
            init = source / 'init'
            init.write_bytes(b'first init payload\n')
            command = shlex.join(['python3', str(SCRIPT), 'packages/phone/ramdisk', 'ramdisk.img', '1048576'])
            (home / 'build.ninja').write_text(f'rule image\n  command = {command}\n'
                                             f'build ramdisk.img: image packages/phone/ramdisk/init\n')

            def build():
                result = subprocess.run([str(NINJA), '-C', str(home), 'ramdisk.img'], text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                image = (home / 'ramdisk.img').read_bytes()
                self.assertEqual(image, (source.parent / 'images/ramdisk.img').read_bytes())
                extracted = subprocess.run(['cpio', '-i', '--to-stdout', 'init'],
                                           input=gzip.decompress(image), stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, check=True)
                self.assertEqual(extracted.stdout, init.read_bytes())
                return result.stdout

            build()
            self.assertIn('no work to do', build())
            init.write_bytes(b'changed init payload\n')
            self.assertIn('[1/1]', build())
            self.assertIn('no work to do', build())


if __name__ == '__main__':
    unittest.main(verbosity=2)
