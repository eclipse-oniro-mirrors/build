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
"""Legacy CI artifacts must retain CMake paths without a new fingerprint file."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / 'build/scripts/incremental_workspace.py'
NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
CMAKE = ROOT / 'prebuilts/cmake/linux-x86/bin/cmake'
spec = importlib.util.spec_from_file_location('legacy_workspace', TOOL)
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class LegacyCMakeTests(unittest.TestCase):
    def run_command(self, command, cwd):
        result = subprocess.run([str(x) for x in command], cwd=cwd,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def fixture(self, ci):
        source = ci / 'third_party/flatbuffers'
        source.mkdir(parents=True)
        (ci / 'build/scripts').mkdir(parents=True)
        shutil.copy2(TOOL, ci / 'build/scripts/incremental_workspace.py')
        (ci / 'prebuilts/build-tools/linux-x86/bin').mkdir(parents=True)
        (ci / 'prebuilts/build-tools/linux-x86/bin/ninja').symlink_to(NINJA)
        (source / 'CMakeLists.txt').write_text(
            'cmake_minimum_required(VERSION 3.16)\n'
            'project(flatc_fixture C)\nadd_executable(flatc main.c)\n')
        (source / 'main.c').write_text('int main(void) { return 0; }\n')
        generator = '''from pathlib import Path
import json,sys
root=Path(sys.argv[1]);out=root/'out/wukong100';out.mkdir(parents=True,exist_ok=True)
cmake_root=out/'clang_x64/third_party/flatbuffers'
graph=f''' + '"""' + '''rule cmake
  command = "CMAKE_PATH" -S "{root}/third_party/flatbuffers" -B "{cmake_root}" && "CMAKE_PATH" --build "{cmake_root}"
  description = BUILD FLATC
build clang_x64/third_party/flatbuffers/flatc: cmake ../../third_party/flatbuffers/main.c ../../third_party/flatbuffers/CMakeLists.txt
build make_all: phony clang_x64/third_party/flatbuffers/flatc
''' + '"""' + '''
(out/'build.ninja').write_text(graph)
(out/'args.gn').write_text('ohos_build_time = 123\\nohos_build_datetime = "fixture"\\n')
(out/'captured-arguments.json').write_text(json.dumps(sys.argv[2:]))
'''
        (ci / 'graph.py').write_text(generator.replace('CMAKE_PATH', str(CMAKE)))
        # This preheat intentionally predates the relocation entry and manifest.
        self.run_command(['python3', ci / 'graph.py', ci], ci)
        self.run_command([NINJA, '-C', ci / 'out/wukong100', 'make_all'], ci)
        self.assertFalse((ci / workspace.MANIFEST).exists())
        production = (ROOT / 'build/build_scripts/build.sh').read_text()
        hook = production.split('# Detect relocation before', 1)[1].split('incremental_original_args=', 1)[0]
        entry = '''#!/bin/bash
set -e
set -o pipefail
export SOURCE_ROOT_DIR=$(cd "$(dirname "$0")";pwd)
# Detect relocation before''' + hook + '''
python3 "$SOURCE_ROOT_DIR/graph.py" "$SOURCE_ROOT_DIR" "$@"
echo 'Excuting ninja command: fixture' > "$SOURCE_ROOT_DIR/out/wukong100/build.log"
"NINJA_PATH" -C "$SOURCE_ROOT_DIR/out/wukong100" make_all | tee -a "$SOURCE_ROOT_DIR/out/wukong100/build.log"
python3 "$SOURCE_ROOT_DIR/build/scripts/incremental_workspace.py" --root "$SOURCE_ROOT_DIR" finish -- "$@"
'''
        (ci / 'build.sh').write_text(entry.replace('NINJA_PATH', str(NINJA)))
        (ci / 'build.sh').chmod(0o755)

    def test_legacy_cache_survives_missing_or_rewritten_ohos_config(self):
        for config in ('missing', 'rewritten'):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as temporary:
                ci, local = Path(temporary) / 'preheat', Path(temporary) / 'consumer'
                self.fixture(ci)
                ci.rename(local)
                if config == 'rewritten':
                    (local / 'out/ohos_config.json').write_text(json.dumps({'root_path': str(local)}))
                # Imported checkouts may retain content but have fresh mtimes.
                os.utime(local / 'third_party/flatbuffers/main.c', None)
                arguments = ['--product-name', 'wukong100', '--build-target', 'make_all',
                             '--ccache', '--gn-args', 'make_pac_format_image=true']
                output = self.run_command([local / 'build.sh', *arguments], local)
                self.assertIn('legacy root', output)
                self.assertIn('CMakeCache.txt', output)
                self.assertNotIn('CMake Error', output)
                manifest = json.loads((local / workspace.MANIFEST).read_text())
                self.assertEqual(manifest['canonical_root'], str(ci))
                self.assertFalse(manifest.get('unsealed', False))
                self.assertEqual(json.loads((local / 'out/wukong100/captured-arguments.json').read_text()), arguments)
                self.assertFalse(ci.exists(), 'No host placeholder may remain')
                self.run_command([local / 'out/wukong100/clang_x64/third_party/flatbuffers/flatc'], local)

    def test_absent_out_keeps_normal_build_path_without_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(workspace, 'enter') as enter:
                workspace.prepare_build(root, ['--product-name', 'wukong100',
                                                '--build-target', 'make_all'])
            enter.assert_not_called()
            self.assertFalse((root / 'out').exists())

    def test_consumer_rebuild_preserves_populated_preheat_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            ci, local = Path(temporary) / 'preheat', Path(temporary) / 'consumer'
            self.fixture(ci)
            arguments = ['--product-name', 'wukong100', '--build-target', 'make_all', '--ccache']
            self.run_command([ci / 'build.sh', *arguments], ci)
            shutil.copytree(ci, local, symlinks=True)
            before = {str(p.relative_to(ci)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in ci.rglob('*') if p.is_file() and not p.is_symlink()}
            (local / 'third_party/flatbuffers/main.c').write_text('int main(void) { return 9; }\n')
            output = self.run_command([local / 'build.sh', *arguments], local)
            self.assertIn('mapping', output)
            self.assertNotIn('CMake Error', output)
            binary = 'out/wukong100/clang_x64/third_party/flatbuffers/flatc'
            self.assertEqual(subprocess.run([str(local / binary)]).returncode, 9)
            self.assertEqual(subprocess.run([str(ci / binary)]).returncode, 0)
            again = self.run_command([local / 'build.sh', *arguments], local)
            self.assertIn('no work to do', again)
            after = {str(p.relative_to(ci)): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in ci.rglob('*') if p.is_file() and not p.is_symlink()}
            self.assertEqual(before, after)

    def test_existing_local_manifest_cannot_override_imported_cmake_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            ci, local = Path(temporary) / 'preheat', Path(temporary) / 'consumer'
            self.fixture(ci)
            ci.rename(local)
            data = {'version': 1, 'canonical_root': str(local),
                    'build_dir': 'out/wukong100', 'automatic': True,
                    'origin_instance': workspace.instance_identity(local),
                    'files': {'unknown-baseline-input.c': {}}, 'tools': {}}
            (local / workspace.MANIFEST).write_text(json.dumps(data))
            source = local / 'third_party/flatbuffers/main.c'
            source.write_text('int main(void) { return 7; }\n')
            stamp = source.stat().st_mtime_ns
            arguments = ['--product-name', 'wukong100', '--build-target', 'make_all', '--ccache']
            output = self.run_command([local / 'build.sh', *arguments], local)
            self.assertIn('manifest root', output)
            self.assertIn('CMakeCache.txt', output)
            binary = local / 'out/wukong100/clang_x64/third_party/flatbuffers/flatc'
            self.assertEqual(subprocess.run([str(binary)]).returncode, 7)
            self.assertEqual(source.stat().st_mtime_ns, stamp)
            rebuilt = json.loads((local / workspace.MANIFEST).read_text())
            self.assertEqual(rebuilt['canonical_root'], str(ci))
            self.assertNotIn('unknown-baseline-input.c', rebuilt['files'])
            self.assertFalse(rebuilt.get('unsealed', False))
            again = self.run_command([local / 'build.sh', *arguments], local)
            self.assertIn('no work to do', again)
            self.assertNotIn('manifest root', again)
            self.assertFalse(ci.exists())

    def test_imported_cmake_cache_is_used_when_gn_graph_was_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            ci, local = Path(temporary) / 'preheat', Path(temporary) / 'consumer'
            self.fixture(ci)
            ci.rename(local)
            (local / 'out/wukong100/build.ninja').unlink()
            (local / 'out/ohos_config.json').write_text(json.dumps({'root_path': str(local)}))
            os.utime(local / 'third_party/flatbuffers/main.c', None)
            output = self.run_command([local / 'build.sh', '--product-name', 'wukong100',
                                       '--build-target', 'make_all', '--ccache'], local)
            self.assertIn('legacy root', output)
            self.assertIn('CMakeCache.txt', output)
            self.assertNotIn('CMake Error', output)
            data = json.loads((local / workspace.MANIFEST).read_text())
            self.assertEqual(data['canonical_root'], str(ci))
            self.assertFalse(ci.exists())

    def test_config_alone_without_graph_does_not_relocate_new_product(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'out').mkdir()
            (root / 'out/ohos_config.json').write_text(json.dumps({'root_path': '/old/ci/root'}))
            with mock.patch.object(workspace, 'enter') as enter:
                workspace.prepare_build(root, ['--product-name', 'wukong100',
                                                '--build-target', 'make_all'])
            enter.assert_not_called()
            self.assertFalse((root / workspace.MANIFEST).exists())

    def test_existing_manifest_is_not_replaced_from_config_without_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'out/wukong100').mkdir(parents=True)
            (root / 'out/wukong100/build.ninja').write_text('')
            (root / 'out/ohos_config.json').write_text(json.dumps({'root_path': '/old/ci/root'}))
            data = {'version': 1, 'canonical_root': str(root), 'automatic': True,
                    'origin_instance': workspace.instance_identity(root)}
            text = json.dumps(data)
            (root / workspace.MANIFEST).write_text(text)
            with mock.patch.object(workspace, 'enter') as enter:
                workspace.prepare_build(root, ['--product-name', 'wukong100',
                                                '--build-target', 'make_all'])
            enter.assert_not_called()
            self.assertEqual((root / workspace.MANIFEST).read_text(), text)

    def test_inconsistent_cache_is_not_used_as_root_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / 'out/wukong100'
            cache = build / 'clang_x64/third_party/flatbuffers/CMakeCache.txt'
            cache.parent.mkdir(parents=True)
            cache.write_text('CMAKE_HOME_DIRECTORY:INTERNAL=/old/third_party/flatbuffers\n'
                             'CMAKE_CACHEFILE_DIR:INTERNAL=/different/out/wukong100\n')
            (root / 'out/ohos_config.json').write_text(json.dumps({'root_path': str(root)}))
            canonical, evidence = workspace.legacy_source_root(root, build)
            self.assertEqual(canonical, root)
            self.assertEqual(evidence, 'out/ohos_config.json')


if __name__ == '__main__':
    unittest.main()
