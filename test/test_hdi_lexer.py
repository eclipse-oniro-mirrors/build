#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
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

"""Tests for the optimized HDI Lexer (Char-free, str/None return)."""

import sys
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD / 'config/components/hdi'))
# pylint: disable=wrong-import-position,import-error
import build_hdi_files_info as hdi


class HdiLexerTest(unittest.TestCase):
    """Verify the HDI Lexer returns str/None instead of Char wrappers."""

    def setUp(self):
        """Create a temporary directory for test IDL files."""
        # pylint: disable=consider-using-with
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        """Clean up the temporary directory."""
        self._temp.cleanup()

    def test_lexer_returns_str_not_char(self):
        """peek_char should return a str, not a Char wrapper object."""
        idl = self._idl('IFoo.idl', 'package ohos.test.v1_0;\ninterface IFoo {\n}\n')
        lex = hdi.Lexer(idl)
        char = lex.peek_char()
        self.assertIsInstance(char, str)
        self.assertEqual(char, 'p')

    def test_lexer_eof_returns_none(self):
        """peek_char/get_char should return None at end of file."""
        idl = self._idl('IFoo.idl', 'package ohos.test.v1_0;\n')
        lex = hdi.Lexer(idl)
        while lex.get_char() is not None:
            pass
        self.assertIsNone(lex.peek_char())

    def test_parse_package_and_interface(self):
        """IdlParser should extract package and interface name."""
        idl = self._idl('IFoo.idl', 'package ohos.test.v1_0;\ninterface IFoo {\n}\n')
        parser = hdi.IdlParser()
        detail = parser.parse_one(idl)
        self.assertEqual(detail.package, "ohos.test.v1_0")
        self.assertEqual(detail.name, "IFoo")

    def test_parse_import(self):
        """IdlParser should record import declarations."""
        idl = self._idl('IFoo.idl',
                        'package ohos.test.v1_0;\n'
                        'import ohos.other.v1_0.IOther;\n'
                        'interface IFoo {\n}\n')
        parser = hdi.IdlParser()
        detail = parser.parse_one(idl)
        self.assertIn('ohos.other.v1_0.IOther', detail.imports)

    def test_block_comment_skipped(self):
        """IdlParser should skip block comments and still parse package."""
        idl = self._idl('IFoo.idl',
                        '/* block comment */\n'
                        'package ohos.test.v1_0;\n'
                        'interface IFoo {\n}\n')
        parser = hdi.IdlParser()
        detail = parser.parse_one(idl)
        self.assertEqual(detail.package, "ohos.test.v1_0")

    def test_line_comment_skipped(self):
        """IdlParser should skip line comments and still parse package/interface."""
        idl = self._idl('IFoo.idl',
                        '// line comment\n'
                        'package ohos.test.v1_0;\n'
                        'interface IFoo {\n}\n')
        parser = hdi.IdlParser()
        detail = parser.parse_one(idl)
        self.assertEqual(detail.package, "ohos.test.v1_0")
        self.assertEqual(detail.name, "IFoo")

    def _idl(self, name, content):
        """Write a temporary IDL file and return its path."""
        path = self.root / name
        path.write_text(content)
        return str(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
