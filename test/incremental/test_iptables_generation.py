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
"""Generated C remains byte-identical on no-op and updates after real edits."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
SCRIPT=ROOT/'third_party/iptables/extensions/genInit.py'


class IptablesGeneratorTest(unittest.TestCase):
    def test_noop_and_real_source_edit(self):
        with tempfile.TemporaryDirectory(prefix='iptables-generation-') as td:
            root=Path(td);ext=root/'third_party/iptables/extensions';ext.mkdir(parents=True)
            shutil.copy2(SCRIPT,ext/'genInit.py')
            source=ext/'libxt_example.c';source.write_text('int value = 1;\nvoid _init(void) {}\n')
            def generate():subprocess.run(['python3',str(ext/'genInit.py'),'libxt_','[]','initext.c','extensions'],check=True,stdout=subprocess.PIPE)
            generate();init=root/'out/gen/initext.c';output=root/'out/gen/libxt_example.c'
            old=(init.read_bytes(),init.stat().st_mtime_ns,output.read_bytes(),output.stat().st_mtime_ns)
            generate();self.assertEqual(old,(init.read_bytes(),init.stat().st_mtime_ns,output.read_bytes(),output.stat().st_mtime_ns))
            source.write_text('int value = 2;\nvoid _init(void) {}\n');generate()
            self.assertIn('value = 2',output.read_text())
            self.assertIn('libxt_example_init(void)',output.read_text())
            mtime=output.stat().st_mtime_ns;generate();self.assertEqual(mtime,output.stat().st_mtime_ns)


if __name__=='__main__':unittest.main(verbosity=2)
