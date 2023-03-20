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
from util.log_util import LogUtil

class BuildFileGeneratorInterface(ServiceInterface):

    def __init__(self):
        super().__init__()
        self._flags_dict = {}

    @property
    def flags_dict(self):
        return self._flags_dict

    def regist_flag(self, flag_name: str, flag_value):
        self._flags_dict[flag_name] = flag_value

    def regist_arg(self, arg_name: str, arg_value: str):
        self._args_dict[arg_name] = arg_value

    @abstractmethod
    def run(self):
        pass
