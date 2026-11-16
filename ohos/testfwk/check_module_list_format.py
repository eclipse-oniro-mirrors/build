#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2026 Huawei Device Co., Ltd.
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

"""Allow native generation only for the exact validated Python serializer."""
import hashlib
from pathlib import Path
import sys


def main(arguments):
    try:
        supported = hashlib.sha256(Path(arguments[0]).read_bytes()).hexdigest() == arguments[1]
    except OSError:
        supported = False
    print('true' if supported else 'false')


if __name__ == '__main__':
    main(sys.argv[1:])
