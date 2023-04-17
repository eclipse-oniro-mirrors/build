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

from containers.arg import Arg
from resolver.interface.args_resolver_interface import ArgsResolverInterface
from modules.interface.env_module_interface import EnvModuleInterface
from resources.global_var import ENV_SETUP_FILE, ROOT_CONFIG_FILE, BUILD_CONFIG_FILE
from exceptions.ohos_exception import OHOSException
from util.log_util import LogUtil
from scripts.tools_checker import check_os_version, check_build_requried_packages
from containers.status import throw_exception
from util.io_util import IoUtil


class EnvArgsResolver(ArgsResolverInterface):

    def __init__(self, args_dict: dict):
        super().__init__(args_dict)

    @staticmethod
    def resolve_check(target_arg: Arg, set_module: EnvModuleInterface):
        if target_arg.arg_value:
            host_info = check_os_version()
            packages_info = check_build_requried_packages(
                host_info[1], check=False)
            for package in packages_info:
                if isinstance(package, list):
                    package.sort()
            LogUtil.hb_info('Necessary package: {}'.format(
                ', '.join(packages_info[0])))
            LogUtil.hb_info('Installed package: {}'.format(
                ', '.join(packages_info[1])))
            LogUtil.hb_info('Uninstalled package: {}'.format(
                ', '.join(packages_info[2])))
            if len(packages_info[2]) > 0:
                LogUtil.hb_info(
                    'Run "sudo apt-get install {}" to install dependencies'.format(' '.join(packages_info[2])))

            if os.path.exists(ROOT_CONFIG_FILE):
                config_json = ROOT_CONFIG_FILE
            else:
                config_json = BUILD_CONFIG_FILE
            json_data = IoUtil.read_json_file(config_json)
            root_path = json_data.get('root_path', 'not set')
            board = json_data.get('board', 'not set')
            kernel = json_data.get('kernel', 'not set')
            product = json_data.get('product', 'not set')
            product_path = json_data.get('product_path', 'not set')
            device_path = json_data.get('device_path', 'not set')
            device_company = json_data.get('device_company', 'not set')

            LogUtil.hb_info('root path: {}'.format(root_path))
            LogUtil.hb_info('board: {}'.format(board))
            LogUtil.hb_info('kernel: {}'.format(kernel))
            LogUtil.hb_info('product: {}'.format(product))
            LogUtil.hb_info('product path: {}'.format(product_path))
            LogUtil.hb_info('device path: {}'.format(device_path))
            LogUtil.hb_info('device company: {}'.format(device_company))

    @staticmethod
    @throw_exception
    def resolve_install(target_arg: Arg, set_module: EnvModuleInterface):
        if target_arg.arg_value:
            if os.path.exists(ENV_SETUP_FILE):
                LogUtil.hb_info('Starting install all dependencies...')
                LogUtil.hb_info(
                    'Please enter your sudo password for running apt-install command')
                subprocess.run('bash {}'.format(ENV_SETUP_FILE))
            else:
                raise OHOSException("There is no {} file", "0000")
