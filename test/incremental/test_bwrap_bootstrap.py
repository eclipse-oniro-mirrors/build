#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Check bootstrap decisions without installing packages on the test host."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[2] / 'scripts/incremental_workspace.py'
SPEC = importlib.util.spec_from_file_location('bwrap_workspace', TOOL)
WORKSPACE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKSPACE)


class ExecTransfer(Exception):
    """Stop where the real helper would replace its process."""


class BwrapBootstrapTest(unittest.TestCase):
    def test_installed_binary_skips_privilege_check_and_package_manager(self):
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value='/usr/bin/bwrap'), \
                mock.patch.object(WORKSPACE.os, 'geteuid') as uid, \
                mock.patch.object(WORKSPACE.subprocess, 'run') as run:
            WORKSPACE.ensure_bwrap()
            uid.assert_not_called()
            run.assert_not_called()

    def test_missing_nonroot_stops_with_install_and_retry_instructions(self):
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value=None), \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=1000), \
                mock.patch.object(WORKSPACE.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError) as error:
                WORKSPACE.ensure_bwrap()
            self.assertIn('not root', str(error.exception))
            self.assertIn('sudo apt update && sudo apt install -y bubblewrap', str(error.exception))
            self.assertIn('rerun the original build command', str(error.exception))
            run.assert_not_called()

    def test_missing_root_installs_in_order_and_checks_binary_again(self):
        with mock.patch.object(WORKSPACE.shutil, 'which', side_effect=[None, '/usr/bin/bwrap']) as which, \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=0), \
                mock.patch.object(WORKSPACE.subprocess, 'run') as run:
            WORKSPACE.ensure_bwrap()
            self.assertEqual(run.call_args_list, [
                mock.call(['apt', 'update'], check=True),
                mock.call(['apt', 'install', '-y', 'bubblewrap'], check=True)])
            self.assertEqual(which.call_count, 2)

    def test_update_failure_prevents_install_and_propagates(self):
        failure = subprocess.CalledProcessError(100, ['apt', 'update'])
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value=None), \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=0), \
                mock.patch.object(WORKSPACE.subprocess, 'run', side_effect=failure) as run:
            with self.assertRaisesRegex(RuntimeError, 'installation failed'):
                WORKSPACE.ensure_bwrap()
            run.assert_called_once_with(['apt', 'update'], check=True)

    def test_install_failure_stops(self):
        failure = subprocess.CalledProcessError(100, ['apt', 'install', '-y', 'bubblewrap'])
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value=None), \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=0), \
                mock.patch.object(WORKSPACE.subprocess, 'run', side_effect=[None, failure]):
            with self.assertRaisesRegex(RuntimeError, 'installation failed'):
                WORKSPACE.ensure_bwrap()

    def test_missing_apt_reports_manual_recovery(self):
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value=None), \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=0), \
                mock.patch.object(WORKSPACE.subprocess, 'run', side_effect=FileNotFoundError('apt')):
            with self.assertRaisesRegex(RuntimeError, 'Run as root: apt update'):
                WORKSPACE.ensure_bwrap()

    def test_success_without_binary_is_rejected(self):
        with mock.patch.object(WORKSPACE.shutil, 'which', return_value=None), \
                mock.patch.object(WORKSPACE.os, 'geteuid', return_value=0), \
                mock.patch.object(WORKSPACE.subprocess, 'run'):
            with self.assertRaisesRegex(RuntimeError, 'still unavailable'):
                WORKSPACE.ensure_bwrap()

    def test_same_path_does_not_inspect_or_install_bwrap(self):
        root = Path('/test/current')
        with mock.patch.dict(os.environ, {'CCACHE_LOG_SUFFIX': 'test'}), \
                mock.patch.object(WORKSPACE, 'validate_tools'), \
                mock.patch.object(WORKSPACE, 'restore_transferred_snapshot'), \
                mock.patch.object(WORKSPACE.os, 'chdir'), \
                mock.patch.object(WORKSPACE.os, 'execvpe', side_effect=ExecTransfer), \
                mock.patch.object(WORKSPACE, 'ensure_bwrap') as ensure:
            with self.assertRaises(ExecTransfer):
                WORKSPACE.enter(root, {'canonical_root': str(root)}, ['bash', './build.sh'])
            ensure.assert_not_called()

    def test_cross_path_bootstraps_before_private_mapping_and_exec(self):
        root, canonical = Path('/test/local'), Path('/test/ci')
        operations = mock.Mock()
        with mock.patch.dict(os.environ, {'CCACHE_LOG_SUFFIX': 'test'}), \
                mock.patch.object(WORKSPACE, 'ensure_bwrap') as ensure, \
                mock.patch.object(WORKSPACE, 'private_mountpoint', return_value=[]) as mount, \
                mock.patch.object(WORKSPACE.os, 'execvpe', side_effect=ExecTransfer) as execute:
            operations.attach_mock(ensure, 'ensure')
            operations.attach_mock(mount, 'mount')
            operations.attach_mock(execute, 'execute')
            with self.assertRaises(ExecTransfer):
                WORKSPACE.enter(root, {'canonical_root': str(canonical)}, ['bash', './build.sh'])
            self.assertEqual(operations.mock_calls[:2], [mock.call.ensure(), mock.call.mount(canonical)])
            self.assertEqual(execute.call_args.args[0], 'bwrap')

    def test_nonroot_cli_exits_before_build_command_with_recovery_message(self):
        with tempfile.TemporaryDirectory(prefix='oh-bwrap-entry-') as temporary:
            root = Path(temporary) / 'local'
            (root / 'out').mkdir(parents=True)
            (root / WORKSPACE.MANIFEST).write_text(json.dumps({
                'version': 1, 'canonical_root': str(Path(temporary) / 'ci')}))
            marker = root / 'build-started'
            code = '''import sys,runpy
from unittest import mock
sys.argv = sys.argv[1:]
with mock.patch('shutil.which', return_value=None), mock.patch('os.geteuid', return_value=1000), mock.patch('subprocess.run', side_effect=AssertionError('package manager must not run')):
    runpy.run_path(sys.argv[0], run_name='__main__')
'''
            result = subprocess.run(
                ['python3', '-c', code, str(TOOL), '--root', str(root), 'enter', '--',
                 'python3', '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('sudo apt update && sudo apt install -y bubblewrap', result.stderr)
            self.assertIn('rerun the original build command', result.stderr)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
