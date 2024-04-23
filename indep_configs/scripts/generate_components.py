#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2024 Huawei Device Co., Ltd.
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

import argparse
import json
import os
import time
import shutil
import stat
import utils


def _get_args():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "-sp",
        "--source_code_path",
        default=r".",
        type=str,
        help="Path of source code",
    )
    parser.add_argument(
        "-hp",
        "--hpmcache_path",
        default=r".",
        type=str,
        help="Path of .hpmcache",
    )
    parser.add_argument(
        "-v",
        "--variants",
        default=r".",
        type=str,
        help="variants of build target",
    )
    parser.add_argument(
        "-rp",
        "--root_path",
        default=r".",
        type=str,
        help="Path of root",
    )
    args = parser.parse_args()
    return args


def _get_dependence_json(_path) -> dict:
    dependences_path = os.path.join(_path, 'dependences.json')
    _json = utils.get_json(dependences_path)
    return _json


def _get_bundle_path(hpm_cache_path, dependences_json, part_name):
    bundle_path = (hpm_cache_path +
                   dependences_json[part_name]['installPath'] + os.sep + 'bundle.json')
    return bundle_path


def _get_src_bundle_path(source_code_path):
    bundle_paths = list()
    for root, dirs, files in os.walk(source_code_path):
        for file in files:
            if file.endswith("bundle.json"):
                bundle_paths.append(os.path.join(root, file))
    return bundle_paths


def _symlink_src2dest(src_dir, dest_dir):
    if os.path.exists(dest_dir) and os.path.islink(dest_dir):
        os.unlink(dest_dir)
    if os.path.exists(dest_dir) and dest_dir != src_dir:
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        else:
            os.remove(dest_dir)
    os.symlink(src_dir, dest_dir)


def _symlink_binarys(hpm_cache_path, bundle_json, dependences_json, part_name):
    path = bundle_json["segment"]["destPath"]
    link_path = os.path.join("binarys", path)
    if not os.path.isdir(link_path):
        try:
            os.remove(link_path)
        except FileNotFoundError:
            pass
        os.makedirs(link_path, exist_ok=True)
    real_path = hpm_cache_path + dependences_json[part_name]['installPath']
    _symlink_src2dest(real_path, link_path)


def _link_kernel_binarys(variants, hpm_cache_path, dependences_json):
    musl_real_path = hpm_cache_path + dependences_json["musl"]['installPath']
    musl_include_link_path = os.path.join("out", variants, "obj/binarys/third_party/musl/usr/include/arm-linux-ohos")
    musl_lib_link_path = os.path.join("out", variants, "obj/binarys/third_party/musl/usr/lib/arm-linux-ohos")
    os.makedirs(musl_include_link_path, exist_ok=True)
    os.makedirs(musl_lib_link_path, exist_ok=True)
    _symlink_src2dest(musl_real_path + os.sep + "innerapis/includes", musl_include_link_path)
    _symlink_src2dest(musl_real_path + os.sep + "innerapis/libs", musl_lib_link_path)

    kernel_real_path = hpm_cache_path + dependences_json["linux"]['installPath']
    kernel_link_path = os.path.join("kernel/linux/patches")
    if not os.path.isdir(kernel_link_path):
        try:
            os.remove(kernel_link_path)
        except FileNotFoundError:
            pass
        os.makedirs(kernel_link_path, exist_ok=True)
    os.makedirs(kernel_link_path, exist_ok=True)
    _symlink_src2dest(kernel_real_path + os.sep + "innerapis/patches", kernel_link_path)


def _gen_components_info(components_json, bundle_json, part_name, src_build_name_list):
    subsystem = bundle_json["component"]["subsystem"]
    path = bundle_json["segment"]["destPath"]
    try:
        component = bundle_json["component"]["build"]["inner_kits"]
    except KeyError:
        component = bundle_json["component"]["build"]["inner_api"]
    innerapi_value_list = list()
    for i in component:
        innerapi_name = i["name"].split(':')[-1]
        if part_name == 'musl':
            innerapi_label = "{}:{}".format(os.path.join("//binarys", path), innerapi_name)
        elif part_name in src_build_name_list:
            innerapi_label = i['name']
        else:
            innerapi_label = "{}:{}".format(os.path.join("//binarys", path, "innerapis", innerapi_name), innerapi_name)
        innerapi_value_list.append({"name": innerapi_name, "label": innerapi_label})
    if part_name == 'cjson':
        part_name = 'cJSON'
    if part_name == 'f2fs_tools':
        part_name = 'f2fs-tools'
    if part_name == 'fsverity_utils':
        part_name = 'fsverity-utils'
    one_component_dict = {part_name: {
        "innerapis": innerapi_value_list,
        "path": path,
        "subsystem": subsystem
    }}
    components_json.update(one_component_dict)

    return components_json


def _get_src_part_name(src_bundle_paths):
    _name = ''
    _path = ''
    for src_bundle_path in src_bundle_paths:
        src_bundle_json = utils.get_json(src_bundle_path)
        part_name = src_bundle_json['component']['name']
        if part_name.endswith('lite'):
            pass
        else:
            _name = part_name
            _path = src_bundle_path
    return _name, _path


def _components_info_handler(part_name_list, source_code_path, hpm_cache_path, root_path, dependences_json):
    components_json = dict()
    src_bundle_paths = _get_src_bundle_path(source_code_path)
    src_part_name, src_bundle_path = _get_src_part_name(src_bundle_paths)
    src_build_name_list = [src_part_name, 'build_framework']
    components_json = _gen_components_info(components_json, utils.get_json(src_bundle_path), src_part_name,
                                           src_build_name_list)
    components_json = _gen_components_info(components_json,
                                           utils.get_json(os.path.join(root_path, "build", "bundle.json")),
                                           "build_framework", src_build_name_list)
    for part_name in part_name_list:
        bundle_path = _get_bundle_path(hpm_cache_path, dependences_json, part_name)
        bundle_json = utils.get_json(bundle_path)
        components_json = _gen_components_info(components_json, bundle_json, part_name, src_build_name_list)
        _symlink_binarys(hpm_cache_path, bundle_json, dependences_json, part_name)

    return components_json


def _out_components_json(components_json, output_path):
    file_name = os.path.join(output_path, "components.json")
    flags = os.O_WRONLY | os.O_CREAT
    modes = stat.S_IWUSR | stat.S_IRUSR
    with os.fdopen(os.open(file_name, flags, modes), 'w') as f:
        json.dump(components_json, f, indent=4)


def _generate_platforms_list(output_path):
    platforms_list_gni_file = os.path.join(output_path, "platforms_list.gni")
    platforms_list = ['phone']
    platforms_list_strings = ' "," '.join(platforms_list)
    gni_file_content = [f'target_platform_list = [ "{platforms_list_strings}" ]',
                        f'kits_platform_list  = [ "{platforms_list_strings}" ]']
    flags = os.O_WRONLY | os.O_CREAT
    modes = stat.S_IWUSR | stat.S_IRUSR
    with os.fdopen(os.open(platforms_list_gni_file, flags, modes), 'w') as f:
        f.write('\n'.join(gni_file_content))


def main():
    args = _get_args()
    source_code_path = args.source_code_path
    hpm_cache_path = args.hpmcache_path
    variants = args.variants
    root_path = args.root_path
    project_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    output_part_path = os.path.join(project_path, 'out', variants, 'build_configs', 'parts_info')
    output_config_path = os.path.join(project_path, 'out', variants, 'build_configs')
    dependences_json = _get_dependence_json(hpm_cache_path)
    part_name_list = dependences_json.keys()

    components_json = _components_info_handler(part_name_list, source_code_path,
                                               hpm_cache_path, root_path, dependences_json)
    _out_components_json(components_json, output_part_path)
    _generate_platforms_list(output_config_path)
    _link_kernel_binarys(variants, hpm_cache_path, dependences_json)


if __name__ == '__main__':
    main()
