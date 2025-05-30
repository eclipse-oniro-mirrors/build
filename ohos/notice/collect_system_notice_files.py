#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2021 Huawei Device Co., Ltd.
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

import sys
import argparse
import os
import shutil
import json
import glob
sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
from scripts.util import build_utils  # noqa: E402


def generate_notice_files(dest_dir, module_info, depfiles):
    dest = os.path.join(dest_dir, "{}.txt".format(module_info['dest'][0]))
    target_dir = os.path.join(dest_dir, module_info['dest'][0])
    module_source = module_info['source']
    if os.path.isdir(module_source):
        dest_files = []
        is_hvigor_hap = False
        for filename in os.listdir(module_source):
            if filename.endswith(".hap") or filename.endswith(".hsp"):
                is_hvigor_hap = True
                dest_files.append(f"{os.path.join(target_dir, filename)}.txt")
        if not is_hvigor_hap and os.listdir(module_source):
            dest_files.extend([f"{os.path.join(target_dir, f)}.txt" for f in os.listdir(module_source)])
    else:
        dest_files = [dest]
    for dest_file in dest_files:
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        shutil.copyfile(module_info['notice'], dest_file)
        depfiles.append(module_info['notice'])
        if os.path.isfile("{}.json".format(module_info['notice'])):
            os.makedirs(os.path.dirname("{}.json".format(dest_file)), exist_ok=True)
            shutil.copyfile("{}.json".format(module_info['notice']), "{}.json".format(dest_file))


def collect_notice_files(options, dest_dir: str, depfiles: str):
    subsystem_info_files = []
    with open(options.install_info_file, 'r') as file:
        install_info = json.load(file)
        for item in install_info:
            subsystem_info_files.append(item['part_info_file'])
    depfiles.extend(subsystem_info_files)

    module_info_files = []
    for subsystem_info in subsystem_info_files:
        with open(subsystem_info, 'r') as file:
            subsystem_info = json.load(file)
            for item in subsystem_info:
                module_info_files.append(item['module_info_file'])
    depfiles.extend(module_info_files)

    for module_info in module_info_files:
        with open(module_info, 'r') as file:
            module_info = json.load(file)
            if 'notice' in module_info and module_info[
                'type'] != "java_library":
                notice_file = module_info['notice']
                if os.path.exists(notice_file) is False or os.stat(
                        notice_file).st_size == 0:
                    continue
                generate_notice_files(dest_dir, module_info, depfiles)

    notice_files = build_utils.get_all_files(options.notice_root_dir)
    depfiles.extend(notice_files)
    for file in notice_files:
        dest = os.path.join(dest_dir,
                            os.path.relpath(file, options.notice_root_dir))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(file, dest)
        if os.path.isfile("{}.json".format(file)):
            os.makedirs(os.path.dirname("{}.json".format(dest)), exist_ok=True)
            shutil.copyfile("{}.json".format(file), "{}.json".format(dest))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install-info-file', required=True)
    parser.add_argument('--notice-root-dir', required=True)
    parser.add_argument('--output-file', required=True)
    parser.add_argument('--depfile', required=False)
    args = parser.parse_args()

    depfiles = [args.install_info_file]
    with build_utils.temp_dir() as tmp:
        collect_notice_files(args, tmp, depfiles)
        build_utils.zip_dir(args.output_file, tmp)

    if args.depfile:
        build_utils.write_depfile(args.depfile,
                                  args.output_file,
                                  sorted(depfiles),
                                  add_pydeps=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
