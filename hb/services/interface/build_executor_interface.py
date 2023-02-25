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

from abc import abstractmethod
from services.interface.service_interface import ServiceInterface
from util.system_util import SystemUtil
from util.log_util import LogUtil

class BuildExecutorInterface(ServiceInterface):

    def __init__(self):
        super().__init__()
        self._start_time = SystemUtil.get_current_time()

    def regist_arg(self, arg_name: str, arg_value: str):
        if arg_name in self._args_dict.keys() and self._args_dict[arg_name] != arg_value:
            LogUtil.hb_warning('duplicated regist arg {}, the original value "{}" will be replace to "{}"'.format(
                arg_name, self._args_dict[arg_name], arg_value))

        self._args_dict[arg_name] = arg_value

    @abstractmethod
    def run(self):
        self._start_time = SystemUtil.get_current_time()
        pass
