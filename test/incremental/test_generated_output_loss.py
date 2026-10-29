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
"""Deleted device resources must rebuild even when a completion marker exists."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
NINJA=ROOT/'prebuilts/build-tools/linux-x86/bin/ninja'
WRAPPER=ROOT/'build/scripts/run_shader_compile.py'


class GeneratedOutputTest(unittest.TestCase):
    def test_asset_loss_and_new_source_membership_trigger_generation(self):
        with tempfile.TemporaryDirectory(prefix='shader-dependency-test-') as td:
            root=Path(td);assets=root/'assets';assets.mkdir();(assets/'one.txt').write_text('one')
            generated=root/'generated'
            script=root/'generate.sh';script.write_text('#!/bin/sh\nset -e\nmkdir -p "$1"\ncp "$2"/* "$1"/\n');script.chmod(0o755)
            stamp=root/'complete';dep=root/'complete.d'
            command=shlex.join(['python3',str(WRAPPER),'--stamp',str(stamp),'--depfile',str(dep),'--input-root',str(assets),'--',str(script),str(generated),str(assets)])
            (root/'build.ninja').write_text(f'rule generate\n  command = {command}\n  depfile = {dep}\n  restat = 1\nbuild {stamp}: generate\nbuild all: phony {stamp}\n')
            def build():
                p=subprocess.run([str(NINJA),'-C',str(root),'all'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertEqual(p.returncode,0,p.stdout);return p.stdout
            build();self.assertIn('no work to do',build())
            (generated/'one.txt').unlink()
            self.assertIn('[1/1]',build());self.assertTrue((generated/'one.txt').is_file())
            (assets/'two.txt').write_text('two')
            self.assertIn('[1/1]',build());self.assertTrue((generated/'two.txt').is_file())
            self.assertIn('no work to do',build())


if __name__=='__main__':unittest.main(verbosity=2)
