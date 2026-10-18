#!/usr/bin/env python3
"""Frontend (lexer/parser) regression tests, black-box against bin/leashc.

Each test maps to a specific audit finding so future changes cannot silently
regress it:

  Lexer  - strict \\x/\\u/\\U escapes, non-greedy multi-line strings, strings/
           chars may not span raw newlines, CRLF line counting, bad numeric
           literals (0b2/0o8/0xZ), digit separators, hex floats, leading-zero
           rejection, float overflow, unterminated block comment, line
           tracking across multi-line tokens, unterminated literal messages.
  Parser - list separators (call args, params, literals, kwargs), relational
           chains a < b > c / >>, generic speculation + error propagation,
           bit-width validation, expression depth guard, interpolation error
           surfacing (parser errors) vs literal fallback (lexer errors),
           show(end=) diagnostic position, statement-start whitelist, multi-
           line string interpolation.

AST-internal assertions (node kinds, member positions, _type_expr_str) have
no CLI-observable equivalent; their behaviour is covered by the source-level
checks above.

Run: python3 -m unittest tests.test_frontend
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(REPO_ROOT, "bin", "leashc")


def _leashc(*args, cwd=None):
    return subprocess.run(
        [BIN_LEASHC, *args],
        capture_output=True, text=True, cwd=cwd, timeout=600,
    )


class FrontendTestCase(unittest.TestCase):
    """Helpers: write a source file, compile it with --check, or run it."""

    def setUp(self):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        self.dir = tempfile.mkdtemp(prefix="leash_frontend_test_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, src):
        p = os.path.join(self.dir, "main.lsh")
        # newline="" keeps CRLF fixtures byte-exact.
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        return p

    def _check(self, src):
        self._write(src)
        r = _leashc("compile", "main.lsh", "--check", cwd=self.dir)
        return r.stdout + r.stderr

    def _run(self, src):
        self._write(src)
        r = _leashc("run", "main.lsh", cwd=self.dir)
        out = r.stdout + r.stderr
        return "\n".join(
            ln for ln in out.splitlines() if not ln.startswith("--- Executed at")
        ).strip()

    def _check_body(self, body):
        return self._check("fnc main() : void { %s }\n" % body)

    def assertParseOk(self, out, src):
        self.assertNotIn("parse error", out,
                         "unexpected parse error for %r:\n%s" % (src, out))
        self.assertNotIn("Expected ',' or", out,
                         "unexpected separator error for %r:\n%s" % (src, out))
        self.assertNotIn("Unexpected token", out,
                         "unexpected fatal parse error for %r:\n%s" % (src, out))


class TestLexerEscapes(FrontendTestCase):
    def test_valid_fixed_width_hex_escapes(self):
        out = self._run('fnc main() : void { show("\\x41\\u0041\\U00000041"); }')
        self.assertEqual(out, "AAA")

    def test_invalid_hex_escape_is_rejected(self):
        cases = {
            "\\xZZ": "\\x escape",
            "\\x4": "\\x escape",
            "\\u12": "\\u escape",
            "\\uZZZZ": "\\u escape",
            "\\U0000": "\\U escape",
        }
        for raw, fragment in cases.items():
            with self.subTest(raw=raw):
                out = self._check('s := "%s";\n' % raw)
                self.assertIn(fragment, out)
                self.assertIn("hex digits", out)

    def test_escape_example_is_zero_padded(self):
        # The diagnostic example must show the full-width escape form.
        self.assertIn(r"(e.g. \x41)", self._check('s := "\\x0g";\n'))
        self.assertIn(r"(e.g. \u0041)", self._check('s := "\\u00";\n'))
        self.assertIn(r"(e.g. \U00000041)", self._check('s := "\\U00";\n'))

    def test_surrogate_escape_is_rejected(self):
        out = self._check('s := "\\ud800";\n')
        self.assertIn("surrogate", out)

    def test_code_point_above_max_is_rejected(self):
        out = self._check('s := "\\U0011FFFF";\n')
        self.assertIn("out of range", out)
        self.assertIn("U+10FFFF", out)

    def test_escape_error_reports_literal_position(self):
        # python reports the string literal's own position (line 1, col 5).
        out = self._check('s := "\\xZZ";\n')
        self.assertIn(":1:5", out)


class TestLexerStringLiterals(FrontendTestCase):
    def test_multiline_string_is_non_greedy(self):
        # Greedy matching used to swallow everything up to the LAST """.
        out = self._run('fnc main() : void { show("""one""" + """two"""); }')
        self.assertEqual(out, "onetwo")

    def test_string_cannot_span_raw_newline(self):
        out = self._check('s := "abc\ndef";\n')
        self.assertIn("Unterminated or malformed string", out)
        self.assertIn(":1:5", out)

    def test_char_cannot_span_raw_newline(self):
        out = self._check("c := 'a\nb';\n")
        self.assertIn("Unterminated or malformed character", out)

    def test_unterminated_string_message_and_position(self):
        out = self._check('s := "abc')
        self.assertIn("Unterminated or malformed string", out)
        self.assertIn(":1:5", out)

    def test_unterminated_multiline_string_message(self):
        out = self._check('s := """abc')
        self.assertIn("multi-line string", out)
        self.assertIn('"""', out)

    def test_unterminated_char_message(self):
        out = self._check("c := 'a")
        self.assertIn("Unterminated or malformed character", out)

    def test_multiline_string_interpolates(self):
        out = self._run('fnc main() : void { show("""pre {1 + 2} post"""); }')
        self.assertEqual(out, "pre 3 post")


class TestLexerLinesAndComments(FrontendTestCase):
    def test_crlf_counts_one_line(self):
        out = self._check("x := 1;\r\ny := 1;\r\n#\n")
        self.assertIn("Unexpected character", out)
        self.assertIn(":3:0", out)

    def test_line_tracking_after_multiline_token(self):
        out = self._check('a := """\nline2\nline3""";\n#\n')
        self.assertIn(":4:0", out)

    def test_unterminated_block_comment(self):
        out = self._check("/* abc")
        self.assertIn("Unterminated block comment", out)
        self.assertIn("*/", out)


class TestLexerNumbers(FrontendTestCase):
    def test_bad_binary_octal_hex_digits(self):
        for src, fragment in [("x := 0b2;", "binary"), ("x := 0o8;", "octal"),
                              ("x := 0xZ;", "hexadecimal")]:
            with self.subTest(src=src):
                out = self._check(src + "\n")
                self.assertIn(fragment, out)
                self.assertIn("Invalid", out)

    def test_digit_separators(self):
        out = self._run('fnc main() : void { show(1_000, " ", 0x1_FFFF, " ", '
                        '0b1111_0000, " ", 0o17); }')
        self.assertEqual(out, "1000 131071 240 15")

    def test_hex_float(self):
        out = self._run("fnc main() : void { show(0x1.8p1); }")
        self.assertEqual(out, "3.000000")

    def test_leading_zero_decimal_rejected(self):
        out = self._check("x := 0123;\n")
        self.assertIn("leading zeros", out)

    def test_float_overflow_rejected(self):
        out = self._check("x := 1e999;\n")
        self.assertIn("out of range", out)


class TestParserSeparators(FrontendTestCase):
    """L5: space-separated lists were silently accepted."""

    BAD = [
        ("foo(1 2);", "call argument list"),
        ("obj.meth(1 2);", "method call argument list"),
        ("c := create Foo(1 2);", "create expression argument list"),
        ("throw Err(1 2);", "throw argument list"),
        ("p := P { x: 1 y: 2 };", "struct initializer"),
        ('h := {"a": 1 "b": 2};', "hash initializer"),
        ("v := {1 2 3};", "array literal"),
        ('show("a", end = "x" "y");', "show() argument list"),
        ("fnc inner(a int b int) : int { return a; }", "method parameter list"),
        ("f := fnc(a int b int) : int { return a; };", "lambda parameter list"),
    ]

    GOOD = [
        "foo(1, 2);",
        "foo();",
        "foo(1, 2,);",
        "v := {1, 2, 3,};",
        'h := {"a": 1, "b": 2};',
        "p := P { x: 1, y: 2 };",
        "fnc inner(a int, b int) : int { return a + b; }",
        "fnc inner(a int = 1, b int = 2) : int { return a; }",
        "f := fnc(a int, b int) : int { return a + b; };",
        "m := obj.meth(1, 2);",
        "c := create Foo(1, 2);",
        "throw Err(1, 2);",
        "show('a', 'b');",
        'show(end = "x");',
        "showb('a', true);",
        "x: fnc(int, int) : int = nil;",
    ]

    def test_missing_separator_is_rejected(self):
        for src, fragment in self.BAD:
            with self.subTest(src=src):
                out = self._check_body(src)
                self.assertIn("Expected ',' or", out, "no error for %r:\n%s" % (src, out))
                self.assertIn(fragment, out, "wrong context for %r:\n%s" % (src, out))

    def test_valid_lists_still_parse(self):
        for src in self.GOOD:
            with self.subTest(src=src):
                self.assertParseOk(self._check_body(src), src)

    def test_toplevel_generic_function_params(self):
        out = self._check("fnc f<T>(a int b int) : T { return a; }\n")
        self.assertIn("Expected ',' or", out)
        self.assertIn("parameter list", out)

    def test_enum_semicolon_members_unaffected(self):
        out = self._check("def Color : enum { RED; GREEN; BLUE };\n"
                          "fnc main() : void { c: Color = Color::RED; show(c); }\n")
        self.assertParseOk(out, "enum with semicolon members")


class TestParserGenericRelational(FrontendTestCase):
    """L4/H5: `a < b > c` must parse as comparisons, not generic syntax."""

    PARSE_OK = [
        "x := a < b > c;",
        "x := a < b >> c;",
        "x := ident<int>(1);",
        "x := Vec<int>.sum(1);",
        "v: vec<vec<int>> = {};",
    ]

    def test_relational_chain(self):
        self.assertParseOk(self._check_body("x := a < b > c;"), "x := a < b > c;")

    def test_relational_chain_with_shift(self):
        self.assertParseOk(self._check_body("x := a < b >> c;"), "x := a < b >> c;")

    def test_generic_call(self):
        self.assertParseOk(self._check_body("x := ident<int>(1);"), "ident<int>")

    def test_generic_static_access(self):
        self.assertParseOk(self._check_body("x := Vec<int>.sum(1);"), "Vec<int>.sum")

    def test_nested_generic_type_with_shift_tokens(self):
        self.assertParseOk(self._check_body("v: vec<vec<int>> = {};"),
                           "vec<vec<int>>")

    def test_error_inside_committed_generic_is_not_swallowed(self):
        # M8: once `fnc f<T>(` is seen, argument errors must propagate
        # instead of silently abandoning the generic parse.
        out = self._check("fnc f<T>(a int b int) : T { return a; }\n")
        self.assertIn("Expected ',' or", out)

    def test_generic_function_definition(self):
        self.assertParseOk(self._check("fnc ident<T>(a T) : T { return a; }\n"),
                           "fnc ident<T>")


class TestParserTypes(FrontendTestCase):
    def test_invalid_fractional_bit_width(self):
        out = self._check_body("x: int<32.5> = 5;")
        self.assertIn("Invalid bit width", out)
        self.assertIn("positive integer", out)
        self.assertIn("'32.5'", out)

    def test_zero_bit_width(self):
        out = self._check_body("x: int<0> = 5;")
        self.assertIn("Invalid bit width", out)
        self.assertIn("'0'", out)

    def test_multi_return_type_lookahead(self):
        out = self._check('fnc pair() : (int, string) { return 1, "a"; }\n'
                          "fnc main() : void { show(pair()); }\n")
        self.assertParseOk(out, "(int, string)")

    def test_grouping_paren_is_not_multi_return(self):
        # A non-multi-return paren at type position is a fatal error.
        out = self._check_body("x: (int) = 5;")
        self.assertIn("Unexpected token LPAREN ('(') where a type was expected", out)

    def test_array_size_type_expr_has_no_object_repr(self):
        out = self._check("fnc a(n int, m int) : int { return n; }\n"
                          "fnc main() : void { x: int[a(1, 2)]; }\n")
        self.assertNotIn("object at", out)


class TestParserDepthGuard(FrontendTestCase):
    def test_deep_nesting_raises_leash_error(self):
        src = "x := " + "(" * 150 + "1" + ")" * 150 + ";"
        out = self._check_body(src)
        self.assertIn("too deep", out)
        self.assertIn("maximum 100 levels", out)

    def test_moderate_nesting_ok(self):
        src = "x := " + "(" * 20 + "1" + ")" * 20 + ";"
        self.assertParseOk(self._check_body(src), "20 levels")


class TestParserInterpolation(FrontendTestCase):
    def test_parser_error_inside_interpolation_surfaces(self):
        out = self._check_body('s := "pre {x +} post";')
        self.assertIn("Invalid interpolation expression", out)
        self.assertIn("x +", out)
        self.assertIn(":1:", out)

    def test_interpolation_error_position_is_string_token(self):
        src = 'fnc main() : void {\n    s := "pre {x +} post";\n}\n'
        out = self._check(src)
        self.assertIn("Invalid interpolation expression", out)
        self.assertIn(":2:", out)

    def test_unlexable_interpolation_stays_literal(self):
        # JSON-style content with \" escapes must not become a parse error.
        out = self._run('fnc main() : void { s := "json {\\"a\\": 1}"; show(s); }')
        self.assertEqual(out, 'json {"a": 1}')


class TestParserShowDiagnostics(FrontendTestCase):
    def test_show_end_error_points_at_end_keyword(self):
        src = 'fnc main() : void {\n    show("a", end = 1);\n}\n'
        out = self._check(src)
        self.assertIn("must be a string literal", out)
        self.assertIn(":2:14", out)

    def test_show_unexpected_kwarg_points_at_keyword(self):
        src = 'fnc main() : void {\n    show("a", endd = 1);\n}\n'
        out = self._check(src)
        self.assertIn("Unexpected keyword argument 'endd'", out)
        self.assertIn(":2:14", out)


class TestParserStatementWhitelist(FrontendTestCase):
    """L1: parse_statement's start whitelist rejected valid statements."""

    VALID = [
        "i := 0; ++i;",
        "i := 0; --i;",
        "c := 'a';",
        "x := create Foo(1);",
        "null;",
        "nil;",
        "{1, 2};",
        "x := 1; !x;",
        "x := 1; ~x;",
        "x := thisworker.id();",
        's := """a""";',
        "b := true;",
        's := "str";',
        "n := -1;",
        "x := (1 + 2);",
        "p := &x;",
        "x := 1; x++;",
    ]

    def test_statement_start_whitelist(self):
        for src in self.VALID:
            with self.subTest(src=src):
                self.assertParseOk(self._check_body(src), src)

    def test_char_statement_parses_as_expression(self):
        self.assertParseOk(self._check_body("'c';"), "'c';")

    def test_increment_statement_parses(self):
        self.assertParseOk(self._check_body("i := 0; ++i;"), "++i")


if __name__ == "__main__":
    unittest.main()
