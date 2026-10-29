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
"""A changed native ABI header must reach native compilation in the same build."""
from pathlib import Path
import json
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
NINJA=ROOT/'prebuilts/build-tools/linux-x86/bin/ninja'
CLANG=ROOT/'prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang'
DEPLOY=ROOT/'foundation/arkui/ace_engine/frameworks/bridge/arkts_frontend/arkoala_generator/gn/command/deploy.py'


class DeployTest(unittest.TestCase):
    def test_same_content_no_compile_and_changed_header_effective_immediately(self):
        with tempfile.TemporaryDirectory(prefix='arkoala-order-test-') as td:
            root=Path(td);sdk=root/'sdk';managed=sdk/'sig/arkoala-arkts/arkui/generated';managed.mkdir(parents=True)
            ets=managed/'Foo.ets';ets.write_text('managed one\n')
            header=sdk/'header.h';header.write_text('#define VALUE 1\n')
            config=root/'config.json';config.write_text(json.dumps({'native':[{'from':'header.h','to':'native.h'}]}))
            source=root/'main.c';source.write_text('#include "native.h"\nint main(void){return VALUE;}\n')
            all_stamp=root/'deployed';native_stamp=root/'native-signature';program=root/'program'
            command=shlex.join(['python3',str(DEPLOY),'--source',str(sdk),'--destination',str(root/'managed'),'--config',str(config),'--stamp',str(all_stamp),'--native-stamp',str(native_stamp)])
            cc=shlex.join([str(CLANG),str(source),'-I',str(root),'-o',str(program)])
            (root/'build.ninja').write_text(f'rule deploy\n  command = {command}\n  restat = 1\nrule cc\n  command = {cc}\nbuild {all_stamp} {native_stamp}: deploy {header} {ets}\nbuild {program}: cc {source} | {native_stamp}\nbuild all: phony {program}\n')
            def build():
                p=subprocess.run([str(NINJA),'-C',str(root),'all'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertEqual(p.returncode,0,p.stdout);return p.stdout
            build();self.assertEqual(subprocess.run([program]).returncode,1)
            mtime=program.stat().st_mtime_ns;header_time=(root/'native.h').stat().st_mtime_ns
            os.utime(header,None);build()
            self.assertEqual(mtime,program.stat().st_mtime_ns)
            self.assertEqual(header_time,(root/'native.h').stat().st_mtime_ns)
            ets.write_text('managed two\n');build()
            self.assertEqual(mtime,program.stat().st_mtime_ns)
            header.write_text('#define VALUE 2\n');build()
            self.assertEqual(subprocess.run([program]).returncode,2)
            self.assertIn('no work to do',build())


if __name__=='__main__':unittest.main(verbosity=2)
