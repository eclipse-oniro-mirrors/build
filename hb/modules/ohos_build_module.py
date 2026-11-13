#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# Copyright (c) 2023 Huawei Device Co., Ltd.
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
#


import os
import subprocess
import sys

from modules.interface.build_module_interface import BuildModuleInterface
from resolver.interface.args_resolver_interface import ArgsResolverInterface
from services.interface.preload_interface import PreloadInterface
from services.interface.load_interface import LoadInterface
from services.interface.build_file_generator_interface import BuildFileGeneratorInterface
from services.interface.build_executor_interface import BuildExecutorInterface
from containers.arg import BuildPhase
from exceptions.ohos_exception import OHOSException
from util.system_util import SystemUtil
from util.log_util import LogUtil
from containers.status import throw_exception
from util.monitor import Monitor
from dfx.build_tracker import build_tracker


class OHOSBuildModule(BuildModuleInterface):

    _instance = None

    def __init__(self,
                 args_dict: dict,
                 args_resolver: ArgsResolverInterface,
                 preloader: PreloadInterface,
                 loader: LoadInterface,
                 target_generator: BuildFileGeneratorInterface,
                 target_compiler: BuildExecutorInterface):

        super().__init__(args_dict, args_resolver, preloader,
                         loader, target_generator, target_compiler)
        OHOSBuildModule._instance = self
        self._start_time = SystemUtil.get_current_time()

    @property
    def build_time(self):
        return SystemUtil.get_current_time() - self._start_time

    @staticmethod
    def get_instance():
        if OHOSBuildModule._instance is not None:
            return OHOSBuildModule._instance
        else:
            raise OHOSException(
                'OHOSBuildModule has not been instantiated', '0000')

    @throw_exception
    def run(self):
        monitor = Monitor()
        try:
            super().run()
        except OHOSException as exception:
            monitor.run()
            raise exception
        else:
            monitor.run()
            LogUtil.hb_info('{} build success'.format(
                self.args_dict.get('product_name').arg_value))

    def _prebuild(self):
        self._run_phase(BuildPhase.PRE_BUILD)

    @build_tracker(
        event_name="_preload",
        build_type="build",
        args_key="args_dict",
    )
    def _preload(self):
        self._run_phase(BuildPhase.PRE_LOAD)
        if self.args_dict.get('fast_rebuild', None) and not self.args_dict.get('fast_rebuild').arg_value:
            LogUtil.set_stage("[PRELOAD]")
            self.preloader.run()
            LogUtil.clear_stage()

    @build_tracker(
        event_name="_load",
        build_type="build",
        args_key="args_dict",
    )
    def _load(self):
        self._run_phase(BuildPhase.LOAD)
        if self.args_dict.get('fast_rebuild', None) and not self.args_dict.get('fast_rebuild').arg_value:
            LogUtil.set_stage("[LOAD]")
            self.loader.run()
            LogUtil.clear_stage()

    def _pre_target_generate(self):
        self._run_phase(BuildPhase.PRE_TARGET_GENERATE)

    @build_tracker(
        event_name="_target_generate",
        build_type="build",
        args_key="args_dict",
    )
    def _target_generate(self):
        self._run_phase(BuildPhase.TARGET_GENERATE)
        if not self.args_dict.get("build_only_load").arg_value and not self.args_dict.get("fast_rebuild").arg_value:
            LogUtil.set_stage("[GN]")
            self.target_generator.run()
            LogUtil.clear_stage()

    def _post_target_generate(self):
        self._run_phase(BuildPhase.POST_TARGET_GENERATE)

    def _pre_target_compilation(self):
        self._run_phase(BuildPhase.PRE_TARGET_COMPILATION)

    @build_tracker(
        event_name="_target_compilation",
        build_type="build",
        args_key="args_dict",
    )
    def _target_compilation(self):
        self._run_phase(BuildPhase.TARGET_COMPILATION)
        if not self.args_dict.get("build_only_load").arg_value and not self.args_dict.get("build_only_gn").arg_value:
            LogUtil.set_stage("[NINJA]")
            self.target_compiler.run()
            LogUtil.clear_stage()

    @build_tracker(
        event_name="_post_target_compilation",
        build_type="build",
        args_key="args_dict",
    )
    def _post_target_compilation(self):
        self._record_incremental_baseline()
        self._run_phase(BuildPhase.POST_TARGET_COMPILATION)

    def _record_incremental_baseline(self):
        # Seal the successful Ninja build before optional OSKM/LSP collection.
        # A partial build must never replace the transferable make_all baseline.
        if (self.args_dict['build_target'].arg_value != ['make_all']
                or self.args_dict['build_only_load'].arg_value
                or self.args_dict['build_only_gn'].arg_value):
            return
        from resources.global_var import CURRENT_OHOS_ROOT
        from scripts.incremental_product_policy import resolve

        arguments = sys.argv[2:]
        if not resolve(CURRENT_OHOS_ROOT, arguments)['enabled']:
            return
        # HB can select make_all without an explicit CLI target. Pass the
        # resolved full-product target to finish, whose parser has no HB defaults.
        if not any(argument == '--build-target' or argument.startswith('--build-target=')
                   for argument in arguments):
            arguments = [*arguments, '--build-target', 'make_all']
        command = [sys.executable,
                   os.path.join(CURRENT_OHOS_ROOT, 'build/scripts/incremental_workspace.py'),
                   '--root', CURRENT_OHOS_ROOT, 'finish', '--', *arguments]
        try:
            result = subprocess.run(command)
        except OSError as error:
            raise OHOSException(f'Cannot record the relocatable baseline: {error}', '4000') from error
        if result.returncode:
            raise OHOSException('Build completed but recording the relocatable baseline failed.', '4000')

    def _post_build(self):
        self._run_phase(BuildPhase.POST_BUILD)

    def _run_phase(self, phase: BuildPhase):
        '''Description: Traverse all registered parameters in build process and 
            execute the resolver function of the corresponding phase
        @parameter: [phase]:  Build phase corresponding to parameter
        @return :none
        '''
        for phase_arg in [arg for arg in self.args_dict.values()if arg.arg_phase == phase]:
            self.args_resolver.resolve_arg(phase_arg, self)
