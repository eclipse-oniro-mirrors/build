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
"""Integration tests: no-op relocation must not hide actual source/header changes."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT/'build/scripts/incremental_workspace.py'
NINJA = ROOT/'prebuilts/build-tools/linux-x86/bin/ninja'
CLANG = ROOT/'prebuilts/clang/ohos/linux-x86_64/llvm/bin/clang'
spec = importlib.util.spec_from_file_location('workspace', TOOL)
workspace = importlib.util.module_from_spec(spec); spec.loader.exec_module(workspace)


class IncrementalWorkspaceTest(unittest.TestCase):
    def command(self, args, expect=0):
        p = subprocess.run([str(x) for x in args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(p.returncode, expect, p.stdout)
        return p.stdout

    def test_cross_root_zero_work_and_real_header_change(self):
        with tempfile.TemporaryDirectory(prefix='oh-snapshot-test-') as td:
            a, b = Path(td)/'ci', Path(td)/'local'
            (a/'out/probe').mkdir(parents=True)
            (a/'prebuilts/build-tools/linux-x86/bin').mkdir(parents=True)
            (a/'toolbin').mkdir();shutil.copy2(NINJA,a/'toolbin/ninja')
            (a/'prebuilts/build-tools/linux-x86/bin/ninja').symlink_to(a/'toolbin/ninja')
            (a/'build/scripts').mkdir(parents=True)
            shutil.copy2(TOOL,a/'build/scripts/incremental_workspace.py')
            (a/'source.c').write_text('#include "header.h"\nint value = VALUE;\n')
            (a/'header.h').write_text('#define VALUE 1\n')
            (a/'resources').mkdir()
            (a/'resources/one.txt').write_text('one')
            (a/'out/probe/build.ninja').write_text(f'''rule cc
  command = {CLANG} -MMD -MF $out.d -c {a}/source.c -o $out
  deps = gcc
  depfile = $out.d
build value.o: cc {a}/source.c | {a}/resources
build all: phony value.o
''')
            self.command([NINJA, '-C', a/'out/probe', 'all'])
            self.command(['python3', TOOL, '--root', a, 'capture', '--build-dir', 'out/probe', '--target', 'all'])
            data=json.loads((a/workspace.MANIFEST).read_text())
            self.assertIn('header.h', data['files'])
            before_log=(a/'out/probe/.ninja_log').read_bytes()
            a.rename(b); a.mkdir()  # Explicit empty canonical mountpoint.
            os.utime(b/'header.h', None)
            os.utime(b/'resources', None)
            output=self.command(['python3', TOOL, '--root', b, 'enter', '--', NINJA, '-C', a/'out/probe', 'all'])
            self.assertIn('no work to do', output)
            self.assertEqual(before_log,(b/'out/probe/.ninja_log').read_bytes())
            self.assertEqual(data['files']['resources']['mtime_ns'], (b/'resources').stat().st_mtime_ns)
            (b/'resources/two.txt').write_text('two')
            self.command(['python3', TOOL, '--root', b, 'restore'])
            output=self.command(['python3', TOOL, '--root', b, 'enter', '--', NINJA, '-C', a/'out/probe', 'all'])
            self.assertIn('[1/1]', output)
            (b/'header.h').write_text('#define VALUE 2\n')
            os.utime(b/'header.h', ns=(1,1))  # Content differs even with stale mtime.
            self.command(['python3', TOOL, '--root', b, 'restore'])
            output=self.command(['python3', TOOL, '--root', b, 'enter', '--', NINJA, '-C', a/'out/probe', 'all'])
            self.assertIn('[1/1]', output)
            output=self.command(['python3', TOOL, '--root', b, 'enter', '--', NINJA, '-C', a/'out/probe', 'all'])
            self.assertIn('no work to do', output)
            (b/'header.h').unlink()
            output=self.command(['python3', TOOL, '--root', b, 'enter', '--', NINJA, '-C', a/'out/probe', 'all'], expect=1)
            self.assertIn('header.h', output)

    def test_default_command_survives_plain_rename_without_capture_or_mountpoint(self):
        with tempfile.TemporaryDirectory(prefix='oh-plain-rename-') as temporary:
            home = Path(temporary)
            ci, local = home / '1029_increase_oh', home / 'bak_1029_increase_oh'
            (ci / 'build/scripts').mkdir(parents=True)
            (ci / 'prebuilts/build-tools/linux-x86/bin').mkdir(parents=True)
            (ci / 'prebuilts/build-tools/linux-x86/bin/ninja').symlink_to(NINJA)
            shutil.copy2(TOOL, ci / 'build/scripts/incremental_workspace.py')
            build = ci / 'out/wukong100'
            build.mkdir(parents=True)
            (ci / 'source.c').write_text('#include "header.h"\nint main(void){return VALUE;}\n')
            (ci / 'header.h').write_text('#define VALUE 1\n')
            (ci / 'unrelated.txt').write_text('old unrelated')
            (build / 'args.gn').write_text('ohos_build_time = 123\nohos_build_datetime = "baseline"\n')
            (build / 'build.ninja').write_text(f'''rule cc
  command = {CLANG} -MMD -MF $out.d {ci}/source.c -o $out
  deps = gcc
  depfile = $out.d
build probe: cc {ci}/source.c
rule copy
  command = cp {ci}/unrelated.txt $out
build unrelated: copy {ci}/unrelated.txt
build make_all: phony probe unrelated
''')
            # Exercise the production dispatch hook, with real Ninja/Clang in
            # place of product-specific HB and package initialization.
            production = (ROOT / 'build/build_scripts/build.sh').read_text()
            hook = production.split('# Detect relocation before', 1)[1].split('incremental_original_args=', 1)[0]
            (ci / 'build.sh').write_text('''#!/bin/bash
set -e
set -o pipefail
SOURCE_ROOT_DIR=$(cd "$(dirname "$0")"; pwd)
# Detect relocation before''' + hook + f'''
selected_target=make_all
next_target=false
for argument in "$@"; do
  if [[ "$next_target" == true ]]; then selected_target="$argument"; next_target=false; fi
  if [[ "$argument" == --build-target ]]; then next_target=true; fi
done
echo 'Excuting ninja command: fixture' > "$SOURCE_ROOT_DIR/out/wukong100/build.log"
"{NINJA}" -C "$SOURCE_ROOT_DIR/out/wukong100" "$selected_target" | tee -a "$SOURCE_ROOT_DIR/out/wukong100/build.log"
python3 "$SOURCE_ROOT_DIR/build/scripts/incremental_workspace.py" --root "$SOURCE_ROOT_DIR" finish -- "$@"
''')
            (ci / 'build.sh').chmod(0o755)
            arguments = ['--product-name', 'wukong100', '--build-target', 'make_all', '--ccache']
            self.command([ci / 'build.sh', *arguments])
            self.assertTrue((ci / workspace.MANIFEST).is_file())
            os.utime(build / 'build.ninja', None)  # GN rewrites an equivalent graph.
            output = self.command([ci / 'build.sh', *arguments])
            self.assertIn('unchanged inputs', output)
            self.assertNotIn('collecting Ninja inputs', output)
            ci.rename(local)
            self.assertFalse(ci.exists())
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('no work to do', output)
            self.assertFalse(ci.exists(), 'Mapping must not leave a host mountpoint')
            self.command([local / 'out/wukong100/probe'], expect=1)
            (local / 'header.h').write_text('#define VALUE 2\n')
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('[1/1]', output)
            self.command([local / 'out/wukong100/probe'], expect=2)
            local.rename(ci)
            output = self.command([ci / 'build.sh', *arguments])
            self.assertIn('no work to do', output)
            # Import the full workspace, update code BEFORE its first build,
            # and even retain an old mtime. Content comparison must preserve
            # that update and compile it rather than reuse the CI executable.
            ci.rename(local)
            (local / 'header.h').write_text('#define VALUE 3\n')
            os.utime(local / 'header.h', ns=(1, 1))
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('[1/1]', output)
            self.command([local / 'out/wukong100/probe'], expect=3)
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('no work to do', output)
            (local / 'header.h').write_text('#define VALUE 4\n')
            (local / 'unrelated.txt').write_text('new unrelated')
            before_single = (local / workspace.MANIFEST).read_bytes()
            single = ['--product-name', 'wukong100', '--build-target', 'probe', '--ccache']
            output = self.command([local / 'build.sh', *single])
            self.assertIn('[1/1]', output)
            self.command([local / 'out/wukong100/probe'], expect=4)
            self.assertEqual((local / 'out/wukong100/unrelated').read_text(), 'old unrelated')
            self.assertEqual((local / workspace.MANIFEST).read_bytes(), before_single)
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('[1/1]', output)
            self.assertEqual((local / 'out/wukong100/unrelated').read_text(), 'new unrelated')
            local.rename(ci)
            # Older CI artifacts have a recorded root but no new manifest.
            (ci / 'out/ohos_config.json').write_text(json.dumps({'root_path': str(ci)}))
            (ci / workspace.MANIFEST).unlink()
            ci.rename(local)
            output = self.command([local / 'build.sh', *arguments])
            self.assertIn('no work to do', output)
            self.assertFalse(ci.exists())
            self.assertFalse(json.loads((local / workspace.MANIFEST).read_text()).get('unsealed', False))
            local.rename(ci)

    def test_reject_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                workspace.restore(Path(td), {'files': {'../outside': {'sha256':'', 'mtime_ns':0}}})

    def test_missing_mountpoint_allows_atomic_parent_config_updates(self):
        with tempfile.TemporaryDirectory(prefix='oh-private-parent-') as temporary:
            home = Path(temporary)
            root, canonical = home / 'local', home / 'missing-ci'
            (root / 'build/scripts').mkdir(parents=True)
            (root / 'out').mkdir()
            shutil.copy2(TOOL, root / 'build/scripts/incremental_workspace.py')
            (root / workspace.MANIFEST).write_text(json.dumps({
                'version': 1, 'canonical_root': str(canonical), 'files': {}, 'tools': {}}))
            config = home / 'settings'
            config.write_text('host')
            config.chmod(0o600)
            code = ('from pathlib import Path;import os,sys;'
                    'p=Path(sys.argv[1]);assert p.stat().st_mode & 0o777 == 0o600;'
                    'q=p.with_suffix(".tmp");q.write_text("private");os.replace(q,p);'
                    'assert p.read_text()=="private"')
            self.command(['python3', TOOL, '--root', root, 'enter', '--',
                          'python3', '-c', code, config])
            self.assertEqual(config.read_text(), 'host')
            self.assertFalse(canonical.exists())
            locked = home / 'locked-parent'
            locked.mkdir()
            locked.chmod(0)
            canonical = locked / 'nested/ci'
            try:
                (root / workspace.MANIFEST).write_text(json.dumps({
                    'version': 1, 'canonical_root': str(canonical), 'files': {}, 'tools': {}}))
                self.command(['python3', TOOL, '--root', root, 'enter', '--', 'python3', '-c',
                              'from pathlib import Path;import sys;assert Path(sys.argv[1]).is_dir()',
                              canonical])
            finally:
                locked.chmod(0o700)
            self.assertEqual(list(locked.iterdir()), [])

    def test_nonempty_mountpoint_is_private_and_host_files_are_preserved(self):
        self.mountpoint_preservation(symlink=False)

    def test_symlink_mountpoint_does_not_resolve_to_another_job(self):
        self.mountpoint_preservation(symlink=True)

    def mountpoint_preservation(self, symlink):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'local'
            canonical = Path(temporary) / 'ci'
            host = Path(temporary) / 'previous-job' if symlink else canonical
            (host / 'out').mkdir(parents=True)
            (host / 'source.txt').write_text('HOST')
            (host / 'out/result.txt').write_text('HOST OUTPUT')
            (host / 'host-only.txt').write_text('preserve me')
            if symlink:
                canonical.symlink_to(host, target_is_directory=True)
            before = {str(p.relative_to(host)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in host.rglob('*') if p.is_file()}
            (root / 'build/scripts').mkdir(parents=True)
            (root / 'out').mkdir()
            shutil.copy2(TOOL, root / 'build/scripts/incremental_workspace.py')
            (root / 'source.txt').write_text('CONSUMER')
            (root / workspace.MANIFEST).write_text(json.dumps({
                'version': 1, 'canonical_root': str(canonical), 'files': {}, 'tools': {}}))
            probe = ('from pathlib import Path;import sys;root=Path(sys.argv[1]);'
                     'assert not root.is_symlink();assert root.resolve()==root;'
                     'assert (root/"source.txt").read_text()=="CONSUMER";'
                     'assert not (root/"host-only.txt").exists();'
                     '(root/"out/result.txt").write_text("CONSUMER OUTPUT")')
            self.command(['python3', TOOL, '--root', root, 'enter', '--',
                          'python3', '-c', probe, canonical])
            self.assertEqual((root / 'out/result.txt').read_text(), 'CONSUMER OUTPUT')
            after = {str(p.relative_to(host)): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in host.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            if symlink:
                self.assertEqual(os.readlink(canonical), str(host))

    def test_two_concurrent_workspaces_share_canonical_name_without_cross_writes(self):
        for occupied in (False, True, 'symlink'):
            with self.subTest(occupied=occupied):
                self.concurrent_workspaces(occupied)

    def concurrent_workspaces(self, occupied):
        with tempfile.TemporaryDirectory(prefix='oh-concurrent-snapshots-') as temporary:
            home = Path(temporary)
            canonical = home / 'ci'
            host = home / 'previous-job' if occupied == 'symlink' else canonical
            host.mkdir()
            if occupied == 'symlink':
                canonical.symlink_to(host, target_is_directory=True)
            if occupied:
                (host / 'source.txt').write_text('HOST')
                (host / 'out').mkdir()
                (host / 'out/same-output.txt').write_text('HOST OUTPUT')
            before = {str(p.relative_to(host)): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in host.rglob('*') if p.is_file()}
            # Parent replacement snapshots its existing children. Put the
            # barrier in a pre-existing bound directory so later writes remain
            # visible without relying on new siblings appearing in that parent.
            control = home / 'control'
            control.mkdir()
            release = control / 'release'
            roots = [home / 'local-a', home / 'local-b']
            processes = []
            probe = '''from pathlib import Path
import os,sys,time
root,expected,release=Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3])
assert not root.is_symlink()
assert root.resolve()==root
assert (root/'source.txt').read_text()==expected
(root/'ready').write_text(expected)
(root/'out/cache-suffix').write_text(os.environ['CCACHE_LOG_SUFFIX'])
deadline=time.monotonic()+10
while not release.exists():
    if time.monotonic()>deadline:raise RuntimeError('concurrent barrier timeout')
    time.sleep(0.01)
for i in range(20):
    assert (root/'source.txt').read_text()==expected
    (root/'out/same-output.txt').write_text(expected)
    time.sleep(0.005)
assert (root/'out/same-output.txt').read_text()==expected
'''
            try:
                for root, expected in zip(roots, ('A', 'B')):
                    (root / 'build/scripts').mkdir(parents=True)
                    (root / 'out').mkdir()
                    shutil.copy2(TOOL, root / 'build/scripts/incremental_workspace.py')
                    (root / 'source.txt').write_text(expected)
                    (root / 'probe.py').write_text(probe)
                    (root / workspace.MANIFEST).write_text(json.dumps({
                        'version': 1, 'canonical_root': str(canonical), 'files': {}, 'tools': {}}))
                    environment = os.environ.copy()
                    environment.pop('CCACHE_LOG_SUFFIX', None)
                    processes.append(subprocess.Popen(
                        ['python3', str(TOOL), '--root', str(root), 'enter', '--',
                         'python3', str(canonical / 'probe.py'), str(canonical), expected, str(release)],
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment))
                deadline = time.monotonic() + 10
                while not all((root / 'ready').exists() for root in roots):
                    if any(process.poll() is not None for process in processes):
                        self.fail('Namespace exited before concurrent barrier: ' +
                                  str([process.communicate(timeout=2)[0]
                                       for process in processes if process.poll() is not None]))
                    self.assertLess(time.monotonic(), deadline, 'Concurrent startup timed out')
                    time.sleep(0.01)
                release.touch()
                for process in processes:
                    output, _ = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, output)
                self.assertEqual((roots[0] / 'out/same-output.txt').read_text(), 'A')
                self.assertEqual((roots[1] / 'out/same-output.txt').read_text(), 'B')
                self.assertNotEqual((roots[0] / 'out/cache-suffix').read_text(),
                                    (roots[1] / 'out/cache-suffix').read_text())
                after = {str(p.relative_to(host)): (p.read_bytes(), p.stat().st_mtime_ns)
                         for p in host.rglob('*') if p.is_file()}
                self.assertEqual(before, after)
                if occupied == 'symlink':
                    self.assertEqual(os.readlink(canonical), str(host))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
