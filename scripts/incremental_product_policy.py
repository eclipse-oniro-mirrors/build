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
"""Product-scoped opt-in for workspace baselines, relocation and bwrap only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

try:
    from .product_config import ProductPaths, products, read_json
except ImportError:
    from product_config import ProductPaths, products, read_json

FEATURE = 'build_framework_enable_incremental_build'
CONTEXT = 'OHOS_INCREMENTAL_PRODUCT_POLICY'
ENTERED_POLICY = 'OHOS_INCREMENTAL_ENTERED_POLICY'
ENTERED_PID = 'OHOS_INCREMENTAL_ENTERED_PID'
WORKSPACE_ENV = (CONTEXT, ENTERED_POLICY, ENTERED_PID, 'OHOS_INCREMENTAL_ENTERED',
                 'OHOS_INCREMENTAL_BUILD_TIME', 'OHOS_INCREMENTAL_BUILD_DATETIME',
                 'OHOS_INCREMENTAL_PHYSICAL_ROOT', 'OHOS_INCREMENTAL_VERIFIED_RUN')


def _component(data):
    parts = data.get('parts')
    if parts:
        return parts.get('build:build_framework')
    selected = None
    for subsystem in data.get('subsystems', []):
        if subsystem.get('subsystem') == 'build':
            for component in subsystem.get('components', []):
                if component.get('component') == 'build_framework':
                    selected = component
    return selected


def product_feature(root, path):
    """Match HB's inherited-part override order, including an empty override."""
    info = read_json(path)
    component = None
    sources = [Path(root)/name for name in info.get('inherit', [])] + [Path(path)]
    for source in sources:
        if source.is_file():
            item = _component(read_json(source))
            if item is not None:
                component = item
    features = (component or {}).get('features') or {}
    value = False
    if isinstance(features, dict):
        value = features.get(FEATURE, False)
        if type(value) is not bool:
            raise ValueError(FEATURE+' must be boolean in '+str(path))
        return value
    if not isinstance(features, list):
        raise ValueError('build_framework features must be a list or mapping in '+str(path))
    for feature in features:
        if not isinstance(feature, str):
            raise ValueError('Invalid build_framework feature in '+str(path))
        name, separator, raw = feature.partition('=')
        if name.strip() == FEATURE:
            if not separator or raw.strip() not in ('true', 'false'):
                raise ValueError(FEATURE+' requires true or false in '+str(path))
            value = raw.strip() == 'true'
    return value


def request_product(arguments):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--product-name')
    parsed, _ = parser.parse_known_args(arguments)
    return parsed.product_name


def select_product(root, name):
    product, separator, company = name.partition('@')
    for info in products(ProductPaths(root)):
        if info['name'] == product and (not separator or info['company'] == company):
            return info
    raise ValueError('product '+name+' not found')


def build_directory(root, info, require_within=True):
    config = read_json(info['config'])
    if info.get('build_out_path'):
        directory = Path(root)/info['build_out_path']
    elif info.get('compile_mode') == 'host':
        directory = Path(root)/'out/host'/info['name']
    elif info.get('os_level') == 'standard':
        directory = Path(root)/'out'/config.get('board', info['name'])
    else:
        directory = Path(root)/'out'/config.get('board', info['name'])/info['name']
    try:
        return str(Path(os.path.abspath(directory)).relative_to(Path(root).absolute()))
    except ValueError:
        if not require_within:
            return str(Path(os.path.abspath(directory)))
        raise ValueError('Incremental workspace output must be inside the source tree: '+str(directory))


def _gn_override(arguments):
    values = []
    collecting = False
    for argument in arguments:
        if argument == '--gn-args':
            collecting = True
        elif argument.startswith('--gn-args='):
            values.append(argument.partition('=')[2]); collecting = False
        elif argument.startswith('--'):
            collecting = False
        elif collecting:
            values.append(argument)
    matches = re.findall(r'(?:^|\s)'+FEATURE+r'\s*=\s*([^\s]+)', ' '.join(values))
    return matches


def resolve(root, arguments, check_fast=False):
    root = Path(root).absolute()
    name = request_product(arguments)
    if not name:
        return {'version': 1, 'enabled': False, 'product': None}
    info = select_product(root, name)
    enabled = product_feature(root, info['config'])
    for override in _gn_override(arguments):
        if override not in ('true', 'false') or (override == 'true') != enabled:
            raise ValueError(FEATURE+' must match the selected product configuration; configure its build_framework features')
    data = {'version': 1, 'enabled': enabled, 'feature': FEATURE,
            'product': info['name'], 'company': info['company'],
            'config': os.path.relpath(info['config'], root)}
    if enabled or (check_fast and '--fast-rebuild' in arguments):
        data['build_dir'] = build_directory(root, info, require_within=enabled)
    if enabled:
        st = root.stat()
        data['instance'] = [st.st_dev, st.st_ino]
        identity = json.dumps(data, sort_keys=True, separators=(',', ':'))
        data['key'] = hashlib.sha256(identity.encode()).hexdigest()
    if check_fast and '--fast-rebuild' in arguments:
        args = root/data['build_dir']/'args.gn'
        if args.is_file():
            text = args.read_text()
            match = re.search(r'^'+FEATURE+r'\s*=\s*(true|false)\s*$', text, re.M)
            previous = bool(match and match[1] == 'true')
            owner = re.search(r'^product_name\s*=\s*"([^"\n]+)"', text, re.M)
            if owner and owner[1] != info['name']:
                raise ValueError('Selected product changed; rerun without --fast-rebuild to regenerate GN')
            if previous != enabled:
                raise ValueError(FEATURE+' changed; rerun the same command without --fast-rebuild to regenerate GN')
    return data


def baseline_policy(data):
    return {name: data[name] for name in ('version', 'enabled', 'feature', 'product', 'company', 'config', 'build_dir')}


def inherited_snapshot(root, product, config):
    """Snapshot times belong to this direct HB child, never SDK/other products."""
    try:
        data = json.loads(os.environ.get(CONTEXT, '{}'))
        if not isinstance(data, dict) or not data.get('enabled') or data.get('product') != product:
            return False
        actual = resolve(root, ['--product-name', product+'@'+data['company']])
        return (actual == data and Path(root)/actual['config'] == Path(config)
                and os.environ.get(ENTERED_POLICY) == actual['key']
                and os.environ.get(ENTERED_PID) == str(os.getppid()))
    except (ValueError, KeyError, OSError, TypeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--shell', action='store_true')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    arguments = options.arguments[1:] if options.arguments[:1] == ['--'] else options.arguments
    data = resolve(options.root, arguments, check_fast=True)
    if options.shell:
        print('1' if data['enabled'] else '0')
        print(data.get('key', ''))
        print(json.dumps(data, separators=(',', ':')))
    else:
        print(json.dumps(data, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('incremental workspace policy: '+str(error), file=sys.stderr)
        sys.exit(2)
