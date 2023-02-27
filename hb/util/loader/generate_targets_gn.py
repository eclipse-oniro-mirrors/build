#!/usr/bin/env python
# -*- coding: utf-8 -*-
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


import os
import sys
import shutil

from resources.global_var import CURRENT_OHOS_ROOT
from util.log_util import LogUtil

# Import jinja2 from third_party/jinja2
sys.path.insert(1, os.path.join(CURRENT_OHOS_ROOT, 'third_party'))
from jinja2 import Template  # noqa: E402

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.util.file_utils import write_file  # noqa: E402

PARTS_LIST_GNI_TEMPLATE = """
parts_list = [
  {}
]
"""

INNER_KITS_GNI_TEMPLATE = """
inner_kits_list = [
  {}
]
"""

SYSTEM_KITS_GNI_TEMPLATE = """
system_kits_list = [
  {}
]
"""

PARTS_TEST_GNI_TEMPLATE = """
parts_test_list = [
  {}
]
"""

PHONY_TARGET_LIST_TEMPLATE = """
group("part_phony_targets") {{
  deps = [
    {}
  ]
}}"""

PHONY_GROUP_TEMPLATE = """
group("{}_phony") {{
  deps = [ "{}" ]
}}"""

gn_file_template = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'build_gn.template')


def gen_targets_gn(parts_targets, config_output_dir):
    parts_list = []
    inner_kits_list = []
    system_kits_list = []
    parts_test_list = []
    phony_target_list = []
    for part_labels in parts_targets.values():
        parts_list.append(part_labels.get('part'))
        if 'phony' in part_labels:
            phony_target_list.append(part_labels.get('phony'))
        if 'inner_kits' in part_labels:
            inner_kits_list.append(part_labels.get('inner_kits'))
        if 'system_kits' in part_labels:
            system_kits_list.append(part_labels.get('system_kits'))
        if 'test' in part_labels:
            parts_test_list.append(part_labels.get('test'))
    parts_list_gni_file = os.path.join(config_output_dir, 'parts_list.gni')
    parts_list_content = '"{}",'.format('",\n  "'.join(parts_list))
    write_file(parts_list_gni_file,
               PARTS_LIST_GNI_TEMPLATE.format(parts_list_content))
    LogUtil.hb_info(
        "generate part list gni file to '{}/parts_list.gni'".format(config_output_dir))

    inner_kits_gni_file = os.path.join(config_output_dir,
                                       'inner_kits_list.gni')
    if inner_kits_list:
        inner_kits_content = '"{}",'.format('",\n  "'.join(inner_kits_list))
    else:
        inner_kits_content = ''
    write_file(inner_kits_gni_file,
               INNER_KITS_GNI_TEMPLATE.format(inner_kits_content))
    LogUtil.hb_info(
        "generate inner kits gni file to '{}/inner_kits_list.gni'".format(config_output_dir))

    system_list_gni_file = os.path.join(config_output_dir,
                                        'system_kits_list.gni')
    if system_kits_list:
        system_kits_content = '"{}",'.format('",\n  "'.join(system_kits_list))
    else:
        system_kits_content = ''
    write_file(system_list_gni_file,
               SYSTEM_KITS_GNI_TEMPLATE.format(system_kits_content))
    LogUtil.hb_info(
        "generate system list gni file to '{}/system_kits_list.gni'".format(config_output_dir))

    parts_test_gni_file = os.path.join(config_output_dir,
                                       'parts_test_list.gni')
    if parts_test_list:
        test_list_content = '"{}",'.format('",\n  "'.join(parts_test_list))
    else:
        test_list_content = ''
    write_file(parts_test_gni_file,
               PARTS_TEST_GNI_TEMPLATE.format(test_list_content))
    LogUtil.hb_info(
        "generate parts test gni file to '{}/parts_test_list.gni'".format(config_output_dir))

    build_gn_file = os.path.join(config_output_dir, 'BUILD.gn')
    shutil.copyfile(gn_file_template, build_gn_file)
    LogUtil.hb_info(
        "generate build gn file to '{}/BUILD.gn'".format(config_output_dir))


def gen_phony_targets(variant_phony_targets, config_output_dir):
    phony_target_list = []
    phony_group_list = []
    for part_name, part_label in variant_phony_targets.items():
        phony_target_list.append('{}_phony'.format(part_name))
        phony_group_list.append(
            PHONY_GROUP_TEMPLATE.format(part_name, part_label))

    phony_list_content = ''
    if phony_target_list:
        phony_list_content = '":{}",'.format(
            '",\n  ":'.join(phony_target_list))
    phony_build_content = []
    phony_build_content.append(
        PHONY_TARGET_LIST_TEMPLATE.format(phony_list_content))
    phony_build_content.extend(phony_group_list)

    phony_build_file = os.path.join(config_output_dir, 'phony_targets',
                                    'BUILD.gn')
    write_file(phony_build_file, '\n'.join(phony_build_content))
    LogUtil.hb_info(
        "generate phony target build file to '{}/phony_targets/BUILD.gn'".format(config_output_dir))


def gen_stub_targets(parts_kits_info, platform_stubs, config_output_dir):
    template = Template("""
    # AUTO generated gn file, DONOT try to modify it.
    import("//build/config/ohos/rules.gni")
    import("//build/ohos/kits/kits_package.gni")
    {% if combined_jar_deps %}
      ohos_combine_jars("{{ platform }}_stub_kits_combine_java") {
        deps = [
          {{ combined_jar_deps }}
        ]
      }
    {% endif %}

    stub_jar("{{ platform }}_zframework_stub_java")  {
      deps = [
        "//third_party/openjdk_stubs:rt_java",
        "//build/ohos/kits/system_api:phone_systemsdk_base_java($default_toolchain)",
      ]
      {% if platform != "phone" %}
        deps += [
          "//build/ohos/kits/system_api:{{ platform }}_systemsdk_platform_java($default_toolchain)" # noqa: E501
        ]
      {% endif %}

      {% if sources_list_files %}
        sources_list_file = [ {{ sources_list_files }} ]
      {% endif %}

      {% if combined_jar_deps %}
        sources_jar_deps = [":{{ platform }}_stub_kits_combine_java"]
      {% endif %}
    }
    """,
                        trim_blocks=True,
                        lstrip_blocks=True)

    for platform, stubs in platform_stubs.items():
        gn_file = os.path.join(config_output_dir,
                               '{}-stub/BUILD.gn'.format(platform))
        gni_file = os.path.join(config_output_dir,
                                '{}-stub/zframework_stub_exists.gni'.format(platform))
        gni_contents = []
        stub_kit_targets = []
        dist_stub = []
        parts = stubs.get('src')
        for part in parts:
            stub_kit_targets.extend(parts_kits_info.get(part))
        if stubs.get('dist'):
            dist_stub = stubs.get('dist')
        if stub_kit_targets or dist_stub:
            gni_contents.append('zframework_stub_exists = true')
            gn_contents = template.render(
                platform=platform,
                combined_jar_deps=',\n'.join(stub_kit_targets),
                sources_list_files=',\n'.join(dist_stub))
            write_file(gn_file, gn_contents)
            LogUtil.hb_info(
                "generated platform stub to '{}/{}-stub/BUILD.gn'".format(config_output_dir, platform))

        else:
            gni_contents.append('zframework_stub_exists = false')

        write_file(gni_file, '\n'.join(gni_contents))
        LogUtil.hb_info(
            "generated platform zframework stub to '{}/subsystem_info/{}-stub/zframework_stub_exists.gni'".format(
                config_output_dir, platform))
