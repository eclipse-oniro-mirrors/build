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
"""Stdlib input edits/additions/deletions must invalidate compiled bytecode."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
NINJA=ROOT/'prebuilts/build-tools/linux-x86/bin/ninja'
RUNNER=ROOT/'arkcompiler/ets_runtime/script/run_ark_executable.py'


class StdlibDependenciesTest(unittest.TestCase):
    def test_files_and_membership_are_tracked(self):
        with tempfile.TemporaryDirectory(prefix='stdlib-dependency-test-') as td:
            root=Path(td);source=root/'source';source.mkdir();one=source/'one.ets';one.write_text('one')
            generator=root/'generate.py';generator.write_text('from pathlib import Path\nPath("result.abc").write_text("".join(p.read_text() for p in sorted(Path("source").glob("*.ets"))))\n')
            command=shlex.join(['python3',str(RUNNER),'--script-file','/usr/bin/python3','--script-args',str(generator),'--env-path','/tmp','--expect-output','0','--depfile','result.d','--dep-target','result.abc','--dependency-root',str(source)])
            (root/'build.ninja').write_text(f'rule compile\n  command = {command}\n  depfile = result.d\n  restat = 1\nbuild result.abc: compile {generator}\nbuild all: phony result.abc\n')
            def build():
                p=subprocess.run([str(NINJA),'-C',str(root),'all'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertEqual(p.returncode,0,p.stdout);return p.stdout
            build();self.assertIn('no work to do',build())
            one.write_text('changed');build();self.assertEqual((root/'result.abc').read_text(),'changed')
            two=source/'two.ets';two.write_text('two');build();self.assertEqual((root/'result.abc').read_text(),'changedtwo')
            one.unlink();build();self.assertEqual((root/'result.abc').read_text(),'two')
            self.assertIn('no work to do',build())


if __name__=='__main__':unittest.main(verbosity=2)
