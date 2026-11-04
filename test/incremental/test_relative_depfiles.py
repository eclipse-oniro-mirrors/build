#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Relative depfile inputs preserve outputs and real incremental changes."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'build'))
from scripts.util import build_utils

NINJA = ROOT / 'prebuilts/build-tools/linux-x86/bin/ninja'
CLANG = ROOT / 'prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang'
WORKSPACE = ROOT / 'build/scripts/incremental_workspace.py'
ARGS = ['--product-name', 'probe', '--build-target', 'make_all', '--ccache']


@contextlib.contextmanager
def cwd(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def load(relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem + '_relative_probe', path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, 'path', [str(path.parent)] + sys.path):
        spec.loader.exec_module(module)
    return module


class RelativeDepfileTest(unittest.TestCase):
    def test_default_writer_and_output_spelling_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary, cwd(temporary):
            dependency = str(Path(temporary) / 'input.h')
            output = str(Path(temporary) / 'obj/result.stamp')
            build_utils.write_depfile('deps/result.d', output, [dependency], add_pydeps=False)
            self.assertEqual(Path('deps/result.d').read_text(), output + ': ' + dependency + '\n')

    def test_inputs_use_ninja_cwd_not_the_depfile_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / 'out/probe'
            build.mkdir(parents=True)
            header = root / 'header with space.h'
            header.write_text('header')
            alias = root / 'alias.h'
            alias.symlink_to(header.name)
            with cwd(build):
                output = 'obj/result.stamp'
                build_utils.write_depfile('nested/deps.d', output, [str(header), str(alias)],
                                         add_pydeps=False, relative_inputs=True)
                target, inputs = Path('nested/deps.d').read_text().split(': ', 1)
                self.assertEqual(target, output)
                paths = shlex.split(inputs)
                self.assertTrue(all(not os.path.isabs(path) for path in paths))
                self.assertEqual(set(paths), {'../../header with space.h', '../../alias.h'})
                self.assertEqual({(build / path).resolve() for path in paths}, {header.resolve()})

    def test_different_drive_fallback_does_not_break_existing_inputs(self):
        with tempfile.TemporaryDirectory() as temporary, cwd(temporary):
            with mock.patch.object(build_utils.os.path, 'relpath', side_effect=ValueError('different drive')):
                build_utils.write_depfile('deps/result.d', 'result', ['/external/header.h'],
                                         add_pydeps=False, relative_inputs=True)
            self.assertIn('/external/header.h', Path('deps/result.d').read_text())

    def test_companion_helpers_do_not_require_a_new_build_api(self):
        scripts = ['interface/sdk-js/sdk_copy_dependencies.py',
                   'arkcompiler/ets_runtime/script/run_ark_executable.py',
                   'base/security/selinux_adapter/scripts/selinux_check/selinux_check_main.py']
        with tempfile.TemporaryDirectory() as temporary, cwd(temporary):
            header = Path(temporary) / 'input.h'
            for relative in scripts:
                with self.subTest(script=relative):
                    module = load(relative)
                    self.assertEqual(module._relative_depfile_inputs([str(header)]), ['input.h'])
                    with mock.patch.object(module.os.path, 'relpath', side_effect=ValueError('different drive')):
                        self.assertEqual(module._relative_depfile_inputs([str(header)]), [str(header)])

    def run_command(self, command, expected=0):
        result = subprocess.run([str(arg) for arg in command], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, expected, result.stdout)
        return result.stdout

    def fixture(self, root):
        (root / 'build/scripts').mkdir(parents=True)
        shutil.copy2(WORKSPACE, root / 'build/scripts/incremental_workspace.py')
        (root / 'prebuilts/build-tools/linux-x86/bin').mkdir(parents=True)
        (root / 'prebuilts/build-tools/linux-x86/bin/ninja').symlink_to(NINJA)
        build = root / 'out/probe'
        build.mkdir(parents=True)
        (build / 'args.gn').write_text('ohos_build_time=1\nohos_build_datetime="baseline"\n')
        (root / 'header with space.h').write_text('#define VALUE 1\n')
        (root / 'main.c').write_text('#include "header with space.h"\nint main(void){return VALUE;}\n')
        (root / 'compile.py').write_text(
            'from pathlib import Path\nimport subprocess,sys\n'
            f'sys.path.insert(0, {str(ROOT / "build")!r})\n'
            'from scripts.util import build_utils\n'
            'root=Path(__file__).parent\n'
            f'subprocess.run([{str(CLANG)!r}, str(root/"main.c"), "-o", "program"], check=True)\n'
            'inputs=[str(root/"main.c"),str(root/"header with space.h"),str(root/"compile.py")]\n'
            'build_utils.write_depfile("program.d","program",inputs,add_pydeps=False,\n'
            '                          relative_inputs=sys.argv[1]=="relative")\n')
        production = (ROOT / 'build/build_scripts/build.sh').read_text()
        hook = production.split('# Detect relocation before', 1)[1].split('incremental_original_args=', 1)[0]
        (root / 'build.sh').write_text(
            '#!/bin/bash\nset -e\nset -o pipefail\n'
            'SOURCE_ROOT_DIR=$(cd "$(dirname "$0")";pwd)\n'
            '# Detect relocation before' + hook +
            '\necho "Excuting ninja command: fixture" > "$SOURCE_ROOT_DIR/out/probe/build.log"\n'
            f'{shlex.quote(str(NINJA))} -C "$SOURCE_ROOT_DIR/out/probe" make_all '
            '| tee -a "$SOURCE_ROOT_DIR/out/probe/build.log"\n'
            'python3 "$SOURCE_ROOT_DIR/build/scripts/incremental_workspace.py" '
            '--root "$SOURCE_ROOT_DIR" finish -- "$@"\n')
        (root / 'build.sh').chmod(0o755)
        self.graph(root, 'absolute')

    def graph(self, root, mode):
        command = shlex.join(['python3', str(root / 'compile.py'), mode])
        (root / 'out/probe/build.ninja').write_text(
            f'rule compile\n  command = {command}\n  depfile = program.d\n  restat = 1\n'
            'build program: compile ../../main.c\nbuild make_all: phony program\n')

    def build(self, root, expected=0):
        return self.run_command([root / 'build.sh', *ARGS], expected)

    def value(self, root, expected):
        self.run_command([root / 'out/probe/program'], expected)

    def test_payload_equal_and_same_path_real_changes_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'workspace with space'
            self.fixture(root)
            self.build(root)
            before = (root / 'out/probe/program').read_bytes()
            self.value(root, 1)
            self.assertIn('no work to do', self.build(root))
            self.graph(root, 'relative')
            self.assertIn('[1/1]', self.build(root))  # One-time command transition.
            self.assertEqual((root / 'out/probe/program').read_bytes(), before)
            inputs = (root / 'out/probe/program.d').read_text().split(': ', 1)[1]
            self.assertTrue(all(not os.path.isabs(path) for path in shlex.split(inputs)))
            self.assertIn('no work to do', self.build(root))
            (root / 'header with space.h').write_text('#define VALUE 2\n')
            self.assertIn('[1/1]', self.build(root))
            self.value(root, 2)
            self.assertIn('no work to do', self.build(root))
            (root / 'out/probe/program').unlink()
            self.assertIn('[1/1]', self.build(root))
            self.value(root, 2)
            self.assertIn('no work to do', self.build(root))

    def test_copied_renamed_and_updated_old_mtime_workspaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            producer = home / 'preheat'
            self.fixture(producer)
            self.graph(producer, 'relative')
            self.build(producer)
            manifest = json.loads((producer / 'out/incremental_workspace.json').read_text())
            self.assertIn('header with space.h', manifest['files'])
            self.assertIn('compile.py', manifest['files'])
            local = home / 'local copy'
            updated = home / 'updated copy'
            shutil.copytree(producer, local, symlinks=True)
            shutil.copytree(producer, updated, symlinks=True)
            original = (producer / 'out/probe/program').read_bytes()
            self.assertIn('no work to do', self.build(local))
            self.assertEqual((local / 'out/probe/program').read_bytes(), original)
            (local / 'header with space.h').write_text('#define VALUE 3\n')
            self.assertIn('[1/1]', self.build(local))
            self.value(local, 3)
            self.assertIn('no work to do', self.build(local))
            renamed = home / 'renamed copy'
            local.rename(renamed)
            self.assertIn('no work to do', self.build(renamed))
            for _ in range(2):
                result = self.build(renamed)
                self.assertIn('no work to do', result)
                self.assertIn('input restore skipped', result)
                self.assertNotIn('collecting Ninja inputs', result)
            header = updated / 'header with space.h'
            header.write_text('#define VALUE 4\n')
            os.utime(header, ns=(1, 1))
            self.assertIn('[1/1]', self.build(updated))
            self.value(updated, 4)
            self.assertIn('no work to do', self.build(updated))
            self.assertEqual((producer / 'out/probe/program').read_bytes(), original)
            self.value(producer, 1)

    def test_retained_depfile_parser_and_transitive_live_inputs(self):
        workspace = load('build/scripts/incremental_workspace.py')
        self.assertEqual(list(workspace.depfile_rules(
            "result: header\\ with\\ space.h odd'quote.h escaped\\#hash.h $$money.h \\\n"
            " next.h\nheader\\ with\\ space.h:\n"))[0],
            (['result'], ['header with space.h', "odd'quote.h", 'escaped#hash.h', '$money.h', 'next.h']))
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            (build / 'first.d').write_text('result: generated.h\n')
            (build / 'second.d').write_text('generated.h: ../../header\\ with\\ space.h\n')
            (build / 'unrelated.d').write_text('unused.o: ../../unrelated.h\ngenerated.h:\n')
            paths = {'result'}
            records = workspace.collect_retained_depfiles(build, paths, 'all')
            self.assertEqual(set(records), {'first.d', 'second.d'})
            self.assertIn('../../header with space.h', paths)
            self.assertNotIn('../../unrelated.h', paths)

    def test_selinux_cli_keeps_policy_payloads_and_failure_propagation(self):
        relative = 'scripts/selinux_check/selinux_check_main.py'
        repository = ROOT / 'base/security/selinux_adapter'
        old = subprocess.check_output(['/usr/bin/git', '-C', str(repository), 'show', 'HEAD:' + relative])
        new = (repository / relative).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            source = home / 'source'
            scripts = source / 'base/security/selinux_adapter/scripts/selinux_check'
            scripts.mkdir(parents=True)
            (source / 'base/security/selinux_adapter/sepolicy').mkdir()
            (source / 'build').symlink_to(ROOT / 'build', target_is_directory=True)
            shutil.copy2(repository / 'scripts/selinux_check/check_common.py', scripts / 'check_common.py')
            script = scripts / 'selinux_check_main.py'
            (source / 'checks.json').write_text('{"selinux_check": []}')
            (source / 'user.policy').write_text('user payload')
            developer = source / 'developer.policy'
            developer.write_text('developer payload')
            tools = home / 'tools'
            tools.mkdir()
            tool = tools / 'checkpolicy'
            tool.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nimport sys\n'
                            'value=Path(sys.argv[sys.argv.index("-b")+1]).read_bytes()\n'
                            'if value==b"BAD": sys.exit(17)\n'
                            'output=Path(sys.argv[sys.argv.index("-o")+1])\n'
                            'output.parent.mkdir(parents=True,exist_ok=True)\n'
                            'output.write_bytes(b"CIL:"+value)\n')
            tool.chmod(0o755)
            output = home / 'out'
            output.mkdir()
            command = ['python3', str(script), '--output-path', str(output),
                       '--source-root-dir', str(source), '--selinux-check-config', 'checks.json',
                       '--user-policy', str(source / 'user.policy'), '--developer-policy', str(developer),
                       '--tool-path', str(tools), '--policy-dir-list', 'default',
                       '--stamp', 'checked', '--depfile', 'checked.d']
            with cwd(output):
                payloads = []
                for body in (old, new):
                    script.write_bytes(body)
                    self.run_command(command)
                    payloads.append([(output / name).read_bytes() for name in ('all.cil', 'developer/all.cil')])
                self.assertEqual(payloads[0], payloads[1])
                self.assertEqual(payloads[1], [b'CIL:user payload', b'CIL:developer payload'])
                inputs = (output / 'checked.d').read_text().split(': ', 1)[1]
                self.assertTrue(all(not os.path.isabs(path) for path in shlex.split(inputs)))
                (output / 'checked').unlink()
                (output / 'checked.d').unlink()
                developer.write_text('BAD')
                self.run_command(command, expected=1)
                self.assertFalse((output / 'checked').exists())
                self.assertFalse((output / 'checked.d').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
