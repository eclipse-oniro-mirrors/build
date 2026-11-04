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
"""Successful seals acknowledge only their exact baseline and physical instance."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('lifecycle_workspace', ROOT / 'build/scripts/incremental_workspace.py')
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)
ARGUMENTS = ['--product-name', 'probe', '--build-target', 'make_all']
PHYSICAL_ROOT_ENV = 'OHOS_INCREMENTAL_PHYSICAL_ROOT'


class BaselineLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {PHYSICAL_ROOT_ENV: str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.build = self.root / 'out/probe'
        self.build.mkdir(parents=True)
        (self.build / 'args.gn').write_text('ohos_build_time=1\nohos_build_datetime="baseline"\n')
        (self.build / 'build.ninja').write_text('# metadata fixture; no Ninja invocation\n')
        (self.build / 'build.log').write_text('Excuting ninja command: fixture\nninja: no work to do.\n')
        self.header = self.root / 'input.h'
        self.header.write_text('#define VALUE 1\n')
        data = {'version': 1, 'canonical_root': str(self.root), 'build_dir': 'out/probe',
                'target': 'make_all', 'automatic': True, 'tools': {},
                'origin_instance': {'boot_id': 'producer', 'device': 0, 'inode': 0},
                'files': {'input.h': self.record()}, 'graph_identity': workspace.graph_identity(self.build)}
        self.manifest = self.root / workspace.MANIFEST
        self.state = self.root / 'out/incremental_workspace.restore-state.json'
        workspace.write_manifest(self.manifest, data)

    def record(self):
        return {'kind': 'file', 'mtime_ns': self.header.stat().st_mtime_ns,
                'sha256': hashlib.sha256(self.header.read_bytes()).hexdigest(), 'executable': False}

    def data(self):
        return json.loads(self.manifest.read_text())

    def test_successful_metadata_refresh_never_repeats_full_restore(self):
        with mock.patch.object(workspace, 'restore', wraps=workspace.restore) as restore, \
                mock.patch.object(workspace, 'capture', side_effect=AssertionError('unexpected full capture')):
            workspace.restore_transferred_snapshot(self.root, self.data())
            self.assertEqual(restore.call_count, 1)
            for _ in range(3):
                graph = self.build / 'build.ninja'
                stat = graph.stat()
                os.utime(graph, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
                workspace.finish_build(self.root, ARGUMENTS)
                workspace.restore_transferred_snapshot(self.root, self.data())
            self.assertEqual(restore.call_count, 1)
            self.assertEqual(json.loads(self.state.read_text())['manifest_sha256'], workspace.digest(self.manifest))

    def test_full_capture_success_acknowledges_the_new_manifest(self):
        workspace.restore_transferred_snapshot(self.root, self.data())
        self.header.write_text('#define VALUE 22\n')
        os.utime(self.header, ns=(1, 1))

        def capture(*args, **kwargs):
            data = self.data()
            data['files']['input.h'] = self.record()
            data['graph_identity'] = workspace.graph_identity(self.build)
            workspace.write_manifest(self.manifest, data)

        with mock.patch.object(workspace, 'capture', side_effect=capture) as seal:
            workspace.finish_build(self.root, ARGUMENTS)
            seal.assert_called_once()
        with mock.patch.object(workspace, 'restore') as restore:
            workspace.restore_transferred_snapshot(self.root, self.data())
            restore.assert_not_called()

    def test_imported_manifest_in_same_instance_still_checks_changed_old_mtime(self):
        workspace.finish_build(self.root, ARGUMENTS)
        data = self.data()
        data['build_time'] = 'another-imported-baseline'
        workspace.write_manifest(self.manifest, data)
        self.header.write_text('#define VALUE 2\n')
        os.utime(self.header, ns=(1, 1))
        with mock.patch.object(workspace, 'restore', wraps=workspace.restore) as restore:
            workspace.restore_transferred_snapshot(self.root, self.data())
            restore.assert_called_once()
        self.assertGreater(self.header.stat().st_mtime_ns, 1)

    def test_rename_with_same_inode_rechecks_before_later_edits_keep_their_times(self):
        workspace.finish_build(self.root, ARGUMENTS)
        self.header.write_text('#define VALUE 2\n')
        os.utime(self.header, ns=(1, 1))
        with mock.patch.dict(os.environ, {PHYSICAL_ROOT_ENV: str(self.root) + '-renamed'}), \
                mock.patch.object(workspace, 'restore', wraps=workspace.restore) as restore:
            workspace.restore_transferred_snapshot(self.root, self.data())
            restore.assert_called_once()
            self.assertGreater(self.header.stat().st_mtime_ns, 1)
            self.header.write_text('#define VALUE 3\n')
            os.utime(self.header, ns=(2, 2))
            workspace.restore_transferred_snapshot(self.root, self.data())
            self.assertEqual(restore.call_count, 1)
            self.assertEqual(self.header.stat().st_mtime_ns, 2)

    def test_changed_host_or_inode_rechecks(self):
        original = workspace.instance_identity(self.root)
        for field, value in [('boot_id', 'another-host'), ('inode', original['inode'] + 1)]:
            workspace.finish_build(self.root, ARGUMENTS)
            with self.subTest(field=field), \
                    mock.patch.object(workspace, 'instance_identity', return_value={**original, field: value}), \
                    mock.patch.object(workspace, 'restore', wraps=workspace.restore) as restore:
                workspace.restore_transferred_snapshot(self.root, self.data())
                restore.assert_called_once()

    def test_failed_capture_does_not_acknowledge_any_new_baseline(self):
        workspace.restore_transferred_snapshot(self.root, self.data())
        previous = self.state.read_bytes()
        self.header.write_text('#define VALUE 2\n')
        os.utime(self.header, ns=(1, 1))
        with mock.patch.object(workspace, 'capture', side_effect=RuntimeError('seal failed')):
            with self.assertRaisesRegex(RuntimeError, 'seal failed'):
                workspace.finish_build(self.root, ARGUMENTS)
        self.assertEqual(self.state.read_bytes(), previous)

    def test_failed_restore_keeps_previous_state(self):
        workspace.restore_transferred_snapshot(self.root, self.data())
        previous = self.state.read_bytes()
        data = self.data()
        data['build_time'] = 'new import'
        workspace.write_manifest(self.manifest, data)
        with mock.patch.object(workspace, 'restore', side_effect=RuntimeError('input unreadable')):
            with self.assertRaisesRegex(RuntimeError, 'input unreadable'):
                workspace.restore_transferred_snapshot(self.root, self.data())
        self.assertEqual(self.state.read_bytes(), previous)

    def test_cache_write_failure_warns_and_next_entry_conservatively_rechecks(self):
        workspace.restore_transferred_snapshot(self.root, self.data())
        write = workspace.write_manifest

        def fail_state(path, data):
            if path == self.state:
                raise PermissionError('read-only state')
            return write(path, data)

        output = io.StringIO()
        with mock.patch.object(workspace, 'write_manifest', side_effect=fail_state), contextlib.redirect_stdout(output):
            workspace.finish_build(self.root, ARGUMENTS)
        self.assertIn('cannot save input restore state', output.getvalue())
        self.assertEqual(self.data()['origin_instance'], workspace.instance_identity(self.root))
        with mock.patch.object(workspace, 'restore', wraps=workspace.restore) as restore:
            workspace.restore_transferred_snapshot(self.root, self.data())
            restore.assert_called_once()

    def test_partial_builds_do_not_acknowledge_full_baselines(self):
        original = self.manifest.read_bytes()
        with mock.patch.object(workspace, 'capture') as capture:
            for arguments in [ARGUMENTS[:-1] + ['single'], ARGUMENTS + ['--build-only-gn'],
                              ARGUMENTS + ['--build-only-load']]:
                workspace.finish_build(self.root, arguments)
            capture.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_failed_atomic_state_replace_keeps_the_old_complete_file(self):
        workspace.restore_transferred_snapshot(self.root, self.data())
        previous = self.state.read_bytes()
        with mock.patch.object(workspace.os, 'replace', side_effect=PermissionError('replace denied')):
            workspace.write_restore_state(self.root, {'incomplete': 'must not be published'})
        self.assertEqual(self.state.read_bytes(), previous)
        self.assertFalse(list(self.state.parent.glob(self.state.name + '.*.tmp')))

    def tools(self):
        tool = self.root / 'compiler'
        tool.write_text('compiler-v1')
        alias = self.root / 'compiler++'
        alias.symlink_to(tool.name)
        return tool, {'tools': {'compiler': workspace.digest(tool), 'compiler++': workspace.digest(tool)}}

    def test_tool_aliases_share_a_digest_but_each_expected_value_is_checked(self):
        tool, data = self.tools()
        with mock.patch.object(workspace, 'digest', wraps=workspace.digest) as digest:
            workspace.validate_tools(self.root, data)
            digest.assert_called_once_with(tool)
        data['tools']['compiler++'] = 'incorrect'
        with self.assertRaisesRegex(RuntimeError, 'Toolchain changed: compiler\+\+'):
            workspace.validate_tools(self.root, data)

    def test_tool_changes_and_changes_during_validation_are_rejected(self):
        tool, data = self.tools()
        tool.write_text('another compiler')
        with self.assertRaisesRegex(RuntimeError, 'Toolchain changed'):
            workspace.validate_tools(self.root, data)
        data['tools'] = {'compiler': workspace.digest(tool)}
        real_digest = workspace.digest

        def changing(path):
            result = real_digest(path)
            path.write_text('replaced during hashing')
            return result

        with mock.patch.object(workspace, 'digest', side_effect=changing):
            with self.assertRaisesRegex(RuntimeError, 'changed while validating'):
                workspace.validate_tools(self.root, data)

    def test_direct_restore_does_not_trust_cached_tool_validation(self):
        tool, data = self.tools()
        data.update(self.data())
        data['tools'] = {'compiler': workspace.digest(tool)}
        tool.write_text('changed tool')
        with self.assertRaisesRegex(RuntimeError, 'Toolchain changed'):
            workspace.restore(self.root, data)

    def test_build_entry_validates_tools_only_once(self):
        class ExecTransfer(Exception):
            pass

        with mock.patch.object(workspace, 'validate_tools', wraps=workspace.validate_tools) as validate, \
                mock.patch.object(workspace.os, 'chdir'), \
                mock.patch.object(workspace.os, 'execvpe', side_effect=ExecTransfer):
            with self.assertRaises(ExecTransfer):
                workspace.enter(self.root, self.data(), ['bash', './build.sh'])
            validate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
