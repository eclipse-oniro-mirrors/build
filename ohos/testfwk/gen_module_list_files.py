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

import optparse
import os
import sys
import json


def _parse_args(args):
    parser = optparse.OptionParser()
    parser.add_option('--depfile', help='Path to depfile (refer to `gn help depfile`)')
    parser.add_option('--output_dir', help='output directory')
    parser.add_option('--source_dir', help='source directory')
    parser.add_option('--target', help='name of target')
    parser.add_option('--target_label')
    parser.add_option('--test_type')
    parser.add_option('--module_list_file', help='file name of module list')
    options, _ = parser.parse_args(args)
    for name in ('output_dir', 'target', 'source_dir', 'target_label', 'module_list_file'):
        if getattr(options, name) is None:
            parser.error('--%s is required' % name.replace('_', '-'))
    return options, parser


def main(args):
    options, _ = _parse_args(args)
    if not os.path.exists(os.path.dirname(options.module_list_file)):
        os.makedirs(os.path.dirname(options.module_list_file), exist_ok=True)
    contents = json.dumps([{
        'target': options.target,
        'label': options.target_label,
        'source_directory': options.source_dir,
        'output_directory': options.output_dir,
        'test_type': options.test_type
    }])
    try:
        with open(options.module_list_file) as f:
            if f.read() == contents:
                return
    except (OSError, UnicodeError):
        # Preserve the previous write behavior for missing/unreadable output.
        pass
    with open(options.module_list_file, 'w') as f:
        f.write(contents)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
