#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# Copyright (c) 2025 Huawei Device Co., Ltd.
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

from services.interface.build_file_generator_interface import BuildFileGeneratorInterface
from util.system_util import SystemUtil
from exceptions.ohos_exception import OHOSException
import os
from util.log_util import LogUtil
import json
import shutil


class IndepBuild(BuildFileGeneratorInterface):

    def __init__(self):
        super().__init__()

    def run(self):
        flags_list = self._convert_flags()
        cmd = ["/bin/bash", "build/indep_configs/build_indep.sh"]
        cmd.extend(flags_list)
        variant = self.flags_dict["variant"]
        logpath = os.path.join('out', variant, 'build.log')
        SystemUtil.exec_command(cmd, log_path=logpath, pre_msg="run indep build",
                                            after_msg="indep build end")

    def _convert_flags(self) -> list:
        flags_list = []
        if self.flags_dict["local-repo"]:
            flags_list.append(self.flags_dict["local-repo"])
            self._generate_dependences_json(self.flags_dict["local-repo"])
            self.flags_dict.pop("local-repo")
        else:
            flags_list.append(os.path.join(os.path.expanduser("~"), ".hpm/.hpmcache"))
        flags_list.append(self.flags_dict["path"])
        build_type = self.flags_dict["buildType"]
        if build_type == "both":
            flags_list.append("1")
        elif build_type == "onlytest":
            flags_list.append("2")
        else:
            flags_list.append("0")
        variant = self.flags_dict["variant"]
        flags_list.append(variant)
        self.flags_dict.pop("buildType")
        self.flags_dict.pop("path")

        for key in self.flags_dict.keys():
            if isinstance(self.flags_dict[key], bool) and self.flags_dict[key]:
                flags_list.append(f"--{key}")
            if isinstance(self.flags_dict[key], str) and self.flags_dict[key]:
                flags_list.append(f"--{key}")
                flags_list.append(f"{self.flags_dict[key]}")
            if isinstance(self.flags_dict[key], list) and self.flags_dict[key]:
                flags_list.append(f"--{key}")
                flags_list.extend(self.flags_dict[key])
        return flags_list

    def _generate_dependences_json(self, local_repo: str):
        if not os.path.exists(local_repo):
            raise Exception(f"ERROR: local repo {local_repo} does not exist, please check")

        dependences_json = os.path.join(local_repo, "dependences.json")
        dirname = os.path.basename(local_repo)
        if os.path.exists(dependences_json) and dirname == ".hpmcache":
            LogUtil.hb_info(f"use dependences.json under .hpmcache, skip generating")
            return
        
        dependences_dict = dict()
        binarys_path = os.path.join(local_repo, "binarys")
        flag_path = os.path.join(binarys_path, "binarys_flag")
        if os.path.exists(binarys_path):
            if os.path.exists(flag_path):
                LogUtil.hb_info(f"remove {binarys_path}")
                shutil.rmtree(binarys_path)
            else:
                renamed_path = os.path.join(local_repo, "renamed_binarys")
                if os.path.exists(renamed_path):
                    LogUtil.hb_info(f"remove {renamed_path}")
                    shutil.rmtree(renamed_path)
                LogUtil.hb_info(f"rename {binarys_path} to {renamed_path}")
                shutil.move(binarys_path, renamed_path)
        LogUtil.hb_info(f"create {binarys_path}")
        os.makedirs(binarys_path)

        for item in os.listdir(local_repo):
            if item == "binarys":
                continue
            item_path = os.path.join(local_repo, item)
            if os.path.isdir(item_path):
                os.symlink(item_path, os.path.join(binarys_path, item))
            open(flag_path, "w").close()
        
        ignore_directories = ['innerapis', 'common', 'binarys']
        for root, dirs, files in os.walk(local_repo):
            dirs[:] = [d for d in dirs if d not in ignore_directories]
            for file_name in files:
                if file_name == "bundle.json":
                    self._update_dependences_dict(local_repo, root, file_name, dependences_dict)
        LogUtil.hb_info(f"generating {dependences_json}")
        with open(dependences_json, 'w') as f:
            json.dump(dependences_dict, f, indent=4)
    
    def _update_dependences_dict(self, local_repo: str, root: str, file_name: str, dependences_dict: dict):
        bundle_json_path = os.path.join(root, file_name)
        with open(bundle_json_path, 'r') as f:
            bundle_data = json.load(f)
            component_name = bundle_data["component"]["name"]
            relative_path = os.path.relpath(root, local_repo)
            dependences_dict[component_name] = {
                "installPath": "/" + relative_path
            }