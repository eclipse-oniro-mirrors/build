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
"""Check transactional/idempotent patch application with the actual series."""
from pathlib import Path
import hashlib
import importlib.util
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'third_party/sane-airscan'
spec=importlib.util.spec_from_file_location('patch_install',SOURCE/'patch_install.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def state(root):
    return {p.name:(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
            for p in root.iterdir() if p.suffix in ('.c','.h')}


class PatchStateTest(unittest.TestCase):
    def test_patched_tree_is_noop_and_failed_series_is_transactional(self):
        with tempfile.TemporaryDirectory(prefix='airscan-regression-') as td:
            root=Path(td)
            shutil.copytree(SOURCE/'patches',root/'patches')
            for p in SOURCE.iterdir():
                if p.is_file() and p.suffix in ('.c','.h'):shutil.copy2(p,root/p.name)
            before=state(root)
            module.apply_patch(str(root))
            self.assertEqual(before,state(root))
            (root/'patches/oh-fix-eloop-uv-run-mutex-serialization.patch').write_text('not a patch\n')
            with self.assertRaises(RuntimeError):module.apply_patch(str(root))
            self.assertEqual(before,state(root))


if __name__=='__main__':unittest.main(verbosity=2)
