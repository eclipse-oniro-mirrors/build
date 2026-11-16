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

"""Tests for the optimized HCS Lexer (Char-free, str/None return)."""

import sys
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUILD / 'config/components/hc_gen'))
# pylint: disable=wrong-import-position,import-error
import hcs_build_info as hcs


class HcsLexerTest(unittest.TestCase):
    """Verify the HCS Lexer returns str/None instead of Char wrappers."""

    def setUp(self):
        """Create a temporary directory for test HCS files."""
        # pylint: disable=consider-using-with
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        """Clean up the temporary directory."""
        self._temp.cleanup()

    def _hcs(self, name, content):
        """Write a temporary HCS file and return its path."""
        path = self.root / name
        path.write_text(content)
        return str(path)

    def test_lexer_returns_str_not_char(self):
        """peek_char should return a str, not a Char wrapper object."""
        hcs_file = self._hcs('test.hcs', '#include "other.hcs"\nroot {\n}\n')
        lex = hcs.Lexer(hcs_file)
        char = lex.peek_char()
        self.assertIsInstance(char, str)
        self.assertEqual(char, '#')

    def test_lexer_eof_returns_none(self):
        """peek_char/get_char should return None at end of file."""
        hcs_file = self._hcs('test.hcs', 'root {\n}\n')
        lex = hcs.Lexer(hcs_file)
        while lex.get_char() is not None:
            pass
        self.assertIsNone(lex.peek_char())

    def test_parse_include(self):
        """The Lexer should emit an INCLUDE token for #include directives."""
        hcs_file = self._hcs('test.hcs', '#include "other.hcs"\nroot {\n}\n')
        lex = hcs.Lexer(hcs_file)
        token = lex.peek_token()
        self.assertEqual(token.token_type, hcs.TokenType.INCLUDE)
        self.assertEqual(token.value, '#include')

    def test_block_comment_returns_comment_token(self):
        """The Lexer should emit a COMMENT token for block comments."""
        hcs_file = self._hcs('test.hcs', '/* comment */\nroot {\n}\n')
        lex = hcs.Lexer(hcs_file)
        token = lex.peek_token()
        self.assertEqual(token.token_type, hcs.TokenType.COMMENT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
