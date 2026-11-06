#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Device Co., Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Read-only product enumeration shared with HB; no Config initialization."""
import json
import os
from pathlib import Path


def read_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


class ProductPaths:
    def __init__(self, root):
        self.root_path = os.path.abspath(root)

    @property
    def vendor_path(self):
        path = os.path.join(self.root_path, 'vendor')
        return path if os.path.isdir(path) else ''

    @property
    def built_in_product_path(self):
        path = os.path.join(self.root_path, 'productdefine/common/products')
        if not os.path.isdir(path):
            raise ValueError('Invalid built-in product path: '+path)
        return path

    @property
    def built_in_product_path_for_llvm(self):
        return os.path.join(self.root_path, 'toolchain/llvm-project/llvm_products')


def products(config, read_json=read_json):
    _ext_scan_path = os.path.join(config.root_path,
                                  'out/products_ext/vendor')
    if os.path.exists(_ext_scan_path):
        for company in os.listdir(_ext_scan_path):
            company_path = os.path.join(_ext_scan_path, company)
            if not os.path.isdir(company_path):
                continue
            for product in os.listdir(company_path):
                p_config_path = os.path.join(company_path, product)
                config_path = os.path.join(p_config_path, 'config.json')
                if os.path.isfile(config_path):
                    info = read_json(config_path)
                    product_name = info.get('product_name')
                    if info.get('product_path'):
                        product_path = os.path.join(
                            config.root_path, info.get('product_path'))
                    else:
                        product_path = p_config_path
                    if product_name is not None:
                        subsystem_config_overlay_path = os.path.join(product_path,
                            'subsystem_config_overlay.json')
                        if os.path.isfile(subsystem_config_overlay_path):
                            yield {
                                'company': company,
                                "name": product_name,
                                'product_config_path': p_config_path,
                                'product_path': product_path,
                                'version': info.get('version', '3.0'),
                                'os_level': info.get('type', "mini"),
                                'build_out_path': info.get('build_out_path'),
                                'subsystem_config_json':
                                info.get('subsystem_config_json'),
                                'subsystem_config_overlay_json':
                                subsystem_config_overlay_path,
                                'config': config_path,
                                'component_type': info.get('component_type', ''),
                                'compile_mode': info.get('compile_mode', 'cross')
                            }
                        else:
                            yield {
                                'company': company,
                                "name": product_name,
                                'product_config_path': p_config_path,
                                'product_path': product_path,
                                'version': info.get('version', '3.0'),
                                'os_level': info.get('type', "mini"),
                                'build_out_path': info.get('build_out_path'),
                                'subsystem_config_json':
                                info.get('subsystem_config_json'),
                                'config': config_path,
                                'component_type': info.get('component_type', ''),
                                'compile_mode': info.get('compile_mode', 'cross')
                            }
    if config.vendor_path != '':
        for company in os.listdir(config.vendor_path):
            company_path = os.path.join(config.vendor_path, company)
            if not os.path.isdir(company_path):
                continue
            for product in os.listdir(company_path):
                product_path = os.path.join(company_path, product)
                config_path = os.path.join(product_path, 'config.json')
                if os.path.isfile(config_path):
                    info = read_json(config_path)
                    product_name = info.get('product_name')
                    if product_name is not None:
                        yield {
                            'company': company,
                            "name": product_name,
                            'product_config_path': product_path,
                            'product_path': product_path,
                            'version': info.get('version', '3.0'),
                            'os_level': info.get('type', "mini"),
                            'config': config_path,
                            'component_type': info.get('component_type', ''),
                            'compile_mode': info.get('compile_mode', 'cross')
                        }
    bip_path = config.built_in_product_path
    for item in os.listdir(bip_path):
        if item[0] in ".":
            continue
        else:
            product_name = item[0:-len('.json')
                                ] if item.endswith('.json') else item
            config_path = os.path.join(bip_path, item)
            info = read_json(config_path)
            yield {
                'company': 'built-in',
                "name": product_name,
                'product_config_path': bip_path,
                'product_path': bip_path,
                'version': info.get('version', '2.0'),
                'os_level': info.get('type', 'standard'),
                'config': config_path,
                'component_type': info.get('component_type', ''),
                'compile_mode': info.get('compile_mode', 'cross')
            }
    bipl_path = config.built_in_product_path_for_llvm
    if os.path.isdir(bipl_path):
        for item in os.listdir(bipl_path):
            if item[0] in ".":
                continue
            else:
                product_name = item[0:-len('.json')
                                    ] if item.endswith('.json') else item
                config_path = os.path.join(bipl_path, item)
                info = read_json(config_path)
                yield {
                    'company': 'built-in',
                    "name": product_name,
                    'product_config_path': bipl_path,
                    'product_path': bipl_path,
                    'version': info.get('version', '2.0'),
                    'os_level': info.get('type', 'standard'),
                    'config': config_path,
                    'component_type': info.get('component_type', ''),
                    'compile_mode': info.get('compile_mode', 'cross')
                }
