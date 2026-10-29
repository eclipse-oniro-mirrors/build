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
"""Regression checks for unchanged metadata and successful/failed actions."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'build'), str(ROOT/'build/hb'), str(ROOT/'build/scripts')]
from hb.util.io_util import IoUtil


def load(name, relative):
    spec=importlib.util.spec_from_file_location(name, ROOT/relative)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class GeneratorTest(unittest.TestCase):
    def test_identical_json_preserves_nanosecond_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'metadata.json'
            IoUtil.dump_json_file(p, {'a':[1,2]})
            ns=1700000000123456789;os.utime(p,ns=(ns,ns))
            IoUtil.dump_json_file(p, {'a':[1,2]})
            self.assertEqual(p.stat().st_mtime_ns,ns)
            IoUtil.dump_json_file(p, {'a':[1,3]})
            self.assertNotEqual(p.stat().st_mtime_ns,ns)
            self.assertIn('3',p.read_text())

    def test_formatted_gn_preserves_mtime(self):
        from scripts.util import file_utils
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'BUILD.gn'
            # Different whitespace in raw templates must not rewrite final GN.
            file_utils.write_file(str(output), 'group("x"){deps=[]}\n')
            ns=1700000000123456789;os.utime(output,ns=(ns,ns))
            file_utils.write_file(str(output), 'group("x") { deps = [] }\n')
            self.assertEqual(output.stat().st_mtime_ns,ns)
            file_utils.write_file(str(output), 'group("y") { deps = [] }\n')
            self.assertNotEqual(output.stat().st_mtime_ns,ns)

    def test_json_type_changes_are_not_cached_as_equal(self):
        from scripts.util import file_utils
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'config.json'
            file_utils.write_json_file(str(output), {'flag': True}, check_changes=True)
            file_utils.write_json_file(str(output), {'flag': 1}, check_changes=True)
            import json
            self.assertIs(type(json.loads(output.read_text())['flag']),int)
            file_utils.write_json_file(str(output), {'flag': 1.0}, check_changes=True)
            self.assertIs(type(json.loads(output.read_text())['flag']),float)

    def test_loader_mutable_cache_is_not_a_compiler_input(self):
        module=load('build_js_assets_cache','build/scripts/build_js_assets.py')
        with tempfile.TemporaryDirectory() as td:
            home=Path(td);cache=home/'node_modules/.cache';cache.mkdir(parents=True)
            (cache/'shared.map').write_text('generated map')
            (home/'node_modules/.tsbuildinfo').write_text('compiler cache')
            (home/'node_modules/.tsbuildinfo.linter').write_text('linter cache')
            tool=home/'webpack.js';tool.write_text('compiler source')
            self.assertEqual(module.loader_dependencies(str(home)),[str(tool)])

    def test_signing_failure_never_writes_success_stamp(self):
        module=load('app_sign', 'build/scripts/app_sign.py')
        with tempfile.TemporaryDirectory() as td:
            stamp=Path(td)/'sign.stamp'
            opts=SimpleNamespace(hap_out_dir=None,inFile=str(Path(td)/'unsigned.hap'),outFile=str(Path(td)/'signed.hap'),stamp=str(stamp),depfile=str(Path(td)/'sign.d'))
            with patch.object(module,'parse_args',return_value=opts),patch.object(module,'sign_app',side_effect=RuntimeError('sign failure')):
                with self.assertRaises(RuntimeError): module.main([])
            self.assertFalse(stamp.exists())
            with patch.object(module,'parse_args',return_value=opts),patch.object(module,'sign_app',side_effect=lambda *args: Path(opts.outFile).write_bytes(b'signed')):
                module.main([])
            self.assertTrue(stamp.is_file())

    def test_empty_assets_produce_depfile(self):
        module=load('build_js_assets','build/scripts/build_js_assets.py')
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'assets.zip';depfile=Path(td)/'assets.d'
            opts=SimpleNamespace(nodejs_path='',webpack_js='',webpack_ets='',webpack_config_js='',webpack_config_ets='',js_assets_dir=None,ets_assets_dir=None,output=str(output),depfile=str(depfile))
            with patch.object(module,'parse_args',return_value=opts): module.main([])
            self.assertTrue(output.is_file());self.assertIn(str(output),depfile.read_text())


if __name__=='__main__': unittest.main(verbosity=2)
