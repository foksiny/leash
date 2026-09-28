#!/usr/bin/env python3
"""Frontend (lexer/parser) regression tests for the Phase-1 audit fixes.

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
           AST positions on MethodCall/IndexAccess/ArrayInit, show(end=)
           diagnostic position, statement-start whitelist, multi-line string
           interpolation, type expression pretty-printing.

Run: python3 -m unittest tests.test_frontend
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from leash.lexer import Lexer, leash_unescape  # noqa: E402
from leash.parser_l import Parser  # noqa: E402
from leash.errors import LeashError  # noqa: E402
from leash import ast_nodes as A  # noqa: E402


def tokenize(src):
    return Lexer(src).tokenize()


def parse(src, file="<test>"):
    return Parser(tokenize(src), source_file=file).parse()


def parse_expr(src):
    return Parser(tokenize(src), source_file="<test>").parse_expression()


def body_of(src):
    """Parse `fnc main() : void { <src> }` and return its statement list."""
    prog = parse("fnc main() : void { %s }" % src)
    return prog.items[0].body


def parse_stmt(src):
    """Parse a single statement (no wrapper, so positions are exact)."""
    return Parser(tokenize(src), source_file="<test>").parse_statement()


def err_of(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) and return the LeashError, or None if none."""
    try:
        fn(*args, **kwargs)
    except LeashError as e:
        return e
    except Exception as e:  # noqa: BLE001 - test wants any unexpected raise
        raise AssertionError("expected LeashError, got %r" % (e,))
    return None


class TestLexerEscapes(unittest.TestCase):
    def test_valid_fixed_width_hex_escapes(self):
        self.assertEqual(leash_unescape(r"\x41"), "A")
        self.assertEqual(leash_unescape(r"\u0041"), "A")
        self.assertEqual(leash_unescape(r"\U00000041"), "A")

    def test_invalid_hex_escape_is_rejected(self):
        cases = {
            r"\xZZ": r"\x escape",
            r"\x4": r"\x escape",
            r"\u12": r"\u escape",
            r"\uZZZZ": r"\u escape",
            r"\U0000": r"\U escape",
        }
        for raw, fragment in cases.items():
            with self.subTest(raw=raw):
                e = err_of(leash_unescape, raw, line=1, col=1)
                self.assertIsNotNone(e)
                self.assertIn(fragment, e.msg)
                self.assertIn("hex digits", e.msg)

    def test_escape_example_is_zero_padded(self):
        # The diagnostic example must show the full-width escape form.
        e = err_of(leash_unescape, r"\x0g", line=1, col=1)
        self.assertIsNotNone(e)
        self.assertIn(r"(e.g. \x41)", e.msg)
        e = err_of(leash_unescape, r"\u00", line=1, col=1)
        self.assertIsNotNone(e)
        self.assertIn(r"(e.g. \u0041)", e.msg)
        e = err_of(leash_unescape, r"\U00", line=1, col=1)
        self.assertIsNotNone(e)
        self.assertIn(r"(e.g. \U00000041)", e.msg)

    def test_surrogate_escape_is_rejected(self):
        e = err_of(leash_unescape, r"\ud800", line=1, col=1)
        self.assertIsNotNone(e)
        self.assertIn("surrogate", e.msg)

    def test_code_point_above_max_is_rejected(self):
        e = err_of(leash_unescape, r"\U0011FFFF", line=1, col=1)
        self.assertIsNotNone(e)
        self.assertIn("out of range", e.msg)
        self.assertIn("U+10FFFF", e.msg)


class TestLexerStringLiterals(unittest.TestCase):
    def test_multiline_string_is_non_greedy(self):
        # Greedy [\s\S]* swallowed everything up to the LAST """ on the line.
        toks = tokenize('a = """one""" + """two""";')
        strings = [t for t in toks if t.type == "MLSTRING_D"]
        self.assertEqual(len(strings), 2)
        self.assertEqual(strings[0].value, "one")
        self.assertEqual(strings[1].value, "two")

    def test_string_cannot_span_raw_newline(self):
        e = err_of(tokenize, 's := "abc\ndef";')
        self.assertIsNotNone(e)
        self.assertIn("Unterminated or malformed string", e.msg)
        self.assertEqual(e.line, 1)

    def test_char_cannot_span_raw_newline(self):
        e = err_of(tokenize, "c := 'a\nb';")
        self.assertIsNotNone(e)
        self.assertIn("Unterminated or malformed character", e.msg)

    def test_unterminated_string_message_and_position(self):
        e = err_of(tokenize, 's := "abc')
        self.assertIsNotNone(e)
        self.assertIn("Unterminated or malformed string", e.msg)
        self.assertEqual(e.line, 1)
        self.assertEqual(e.col, 5)

    def test_unterminated_multiline_string_message(self):
        e = err_of(tokenize, 's := """abc')
        self.assertIsNotNone(e)
        self.assertIn("multi-line string", e.msg)
        self.assertIn('"""', e.msg)

    def test_unterminated_char_message(self):
        e = err_of(tokenize, "c := 'a")
        self.assertIsNotNone(e)
        self.assertIn("Unterminated or malformed character", e.msg)

    def test_multiline_string_interpolates(self):
        # MLSTRING tokens must carry the raw inner text so {expr} is parsed.
        st = body_of('v := """pre {1 + 2} post""";')[0]
        self.assertIsInstance(st, A.VariableDecl)
        node = st.value
        self.assertIsInstance(node, A.InterpolatedString)
        kinds = [type(p[1]).__name__ if p[1] is not None else None for p in node.parts]
        self.assertIn("BinaryOp", kinds)
        literals = [p[0] for p in node.parts if p[0] is not None]
        self.assertTrue(any("pre" in lit for lit in literals))
        self.assertTrue(any("post" in lit for lit in literals))


class TestLexerLinesAndComments(unittest.TestCase):
    def test_crlf_counts_one_line(self):
        e = err_of(tokenize, "x := 1;\r\ny := 1;\r\n#")
        self.assertIsNotNone(e)
        self.assertEqual(e.line, 3)
        self.assertIn("Unexpected character", e.msg)

    def test_line_tracking_after_multiline_token(self):
        src = 'a := """\nline2\nline3""";\n#'
        e = err_of(tokenize, src)
        self.assertIsNotNone(e)
        self.assertEqual(e.line, 4)

    def test_unterminated_block_comment(self):
        e = err_of(tokenize, "/* abc")
        self.assertIsNotNone(e)
        self.assertIn("Unterminated block comment", e.msg)
        self.assertIn("*/", e.msg)


class TestLexerNumbers(unittest.TestCase):
    def test_bad_binary_octal_hex_digits(self):
        for src, fragment in [("x := 0b2;", "binary"), ("x := 0o8;", "octal"),
                              ("x := 0xZ;", "hexadecimal")]:
            with self.subTest(src=src):
                e = err_of(tokenize, src)
                self.assertIsNotNone(e)
                self.assertIn(fragment, e.msg)
                self.assertIn("Invalid", e.msg)

    def test_digit_separators(self):
        self.assertEqual(tokenize("1_000")[0].value, 1000)
        self.assertEqual(tokenize("0x1_FFFF")[0].value, 0x1FFFF)
        self.assertEqual(tokenize("0b1111_0000")[0].value, 0b11110000)
        self.assertEqual(tokenize("0o17")[0].value, 15)

    def test_hex_float(self):
        self.assertEqual(tokenize("0x1.8p1")[0].value, 3.0)

    def test_leading_zero_decimal_rejected(self):
        e = err_of(tokenize, "x := 0123;")
        self.assertIsNotNone(e)
        self.assertIn("leading zeros", e.msg)

    def test_float_overflow_rejected(self):
        e = err_of(tokenize, "x := 1e999;")
        self.assertIsNotNone(e)
        self.assertIn("out of range", e.msg)

    def test_number_separators_reach_ast(self):
        node = body_of("x := 1_000;")[0]
        self.assertEqual(node.value.value, 1000)


class TestParserSeparators(unittest.TestCase):
    """L5: space-separated lists were silently accepted."""

    BAD = [
        ("foo(1 2);", "call argument list"),
        ("obj.meth(1 2);", "method call argument list"),
        ("c := create Foo(1 2);", "create expression argument list"),
        ("throw Err(1 2);", "throw argument list"),
        ("p := P { x: 1 y: 2 };", "struct initializer"),
        ('h := {"a": 1 "b": 2};', "hash initializer"),
        ("v := {1 2 3};", "array literal"),
        ("show(\"a\", end = \"x\" \"y\");", "show() argument list"),
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
        "show(end = \"x\");",
        "showb('a', true);",
        "x: fnc(int, int) : int = nil;",
    ]

    def test_missing_separator_is_rejected(self):
        for src, fragment in self.BAD:
            with self.subTest(src=src):
                e = err_of(lambda s: parse("fnc main() : void { %s }" % s), src)
                self.assertIsNotNone(e, "expected error for %r" % src)
                self.assertIn("Expected ',' or", e.msg)
                self.assertIn(fragment, e.msg)

    def test_valid_lists_still_parse(self):
        for src in self.GOOD:
            with self.subTest(src=src):
                e = err_of(lambda s: parse("fnc main() : void { %s }" % s), src)
                self.assertIsNone(e, "unexpected error for %r: %s"
                                  % (src, e.msg if e else ""))

    def test_toplevel_generic_function_params(self):
        e = err_of(parse, "fnc f<T>(a int b int) : T { return a; }")
        self.assertIsNotNone(e)
        self.assertIn("Expected ',' or", e.msg)

    def test_enum_semicolon_members_unaffected(self):
        e = err_of(parse, "def Color : enum { RED; GREEN; BLUE };")
        self.assertIsNone(e, e.msg if e else "")


class TestParserGenericRelational(unittest.TestCase):
    """L4/H5: `a < b > c` must parse as comparisons, not generic syntax."""

    def test_relational_chain(self):
        e = err_of(lambda: body_of("x := a < b > c;"))
        self.assertIsNone(e, e.msg if e else "")

    def test_relational_chain_with_shift(self):
        e = err_of(lambda: body_of("x := a < b >> c;"))
        self.assertIsNone(e, e.msg if e else "")

    def test_generic_call(self):
        e = err_of(lambda: body_of("x := ident<int>(1);"))
        self.assertIsNone(e, e.msg if e else "")

    def test_generic_static_access(self):
        e = err_of(lambda: body_of("x := Vec<int>.sum(1);"))
        self.assertIsNone(e, e.msg if e else "")

    def test_nested_generic_type_with_shift_tokens(self):
        e = err_of(lambda: body_of("v: vec<vec<int>> = {};"))
        self.assertIsNone(e, e.msg if e else "")

    def test_error_inside_committed_generic_is_not_swallowed(self):
        # M8: once `fnc f<T>(` is seen, argument errors must propagate
        # instead of silently abandoning the generic parse.
        e = err_of(parse, "fnc f<T>(a int b int) : T { return a; }")
        self.assertIsNotNone(e)
        self.assertIn("Expected ',' or", e.msg)

    def test_generic_function_definition(self):
        e = err_of(parse, "fnc ident<T>(a T) : T { return a; }")
        self.assertIsNone(e, e.msg if e else "")


class TestParserTypes(unittest.TestCase):
    def test_invalid_fractional_bit_width(self):
        e = err_of(lambda: body_of("x: int<32.5> = 5;"))
        self.assertIsNotNone(e)
        self.assertIn("Invalid bit width", e.msg)
        self.assertIn("positive integer", e.msg)

    def test_zero_bit_width(self):
        e = err_of(lambda: body_of("x: int<0> = 5;"))
        self.assertIsNotNone(e)
        self.assertIn("Invalid bit width", e.msg)

    def test_multi_return_type_lookahead(self):
        p = Parser(tokenize("(int, string)"), source_file="<test>")
        self.assertEqual(p.parse_type(), "(int, string)")

    def test_grouping_paren_is_not_multi_return(self):
        # Non-multi-return lookahead must restore instead of consuming.
        p = Parser(tokenize("(int)"), source_file="<test>")
        e = err_of(p.parse_type)
        self.assertIsNotNone(e)
        self.assertEqual(e.msg, "Unexpected token LPAREN ('(') where a type was expected")

    def test_array_size_type_expr_has_no_object_repr(self):
        st = body_of("x: int[a(1)];")[0]
        self.assertEqual(st.var_type, "int[a(1)]")
        self.assertNotIn("object at", st.var_type)

    def test_type_expr_str_handles_nodes(self):
        expr = parse_expr("a(1, 2)")
        self.assertEqual(Parser._type_expr_str(expr), "a(1, 2)")
        self.assertNotIn("object at", Parser._type_expr_str(expr))


class TestParserDepthGuard(unittest.TestCase):
    def test_deep_nesting_raises_leash_error(self):
        src = "x := " + "(" * 150 + "1" + ")" * 150 + ";"
        e = err_of(lambda: body_of(src))
        self.assertIsNotNone(e, "deep nesting must fail with LeashError")
        self.assertIn("too deep", e.msg)

    def test_moderate_nesting_ok(self):
        src = "x := " + "(" * 20 + "1" + ")" * 20 + ";"
        e = err_of(lambda: body_of(src))
        self.assertIsNone(e, e.msg if e else "")


class TestParserInterpolation(unittest.TestCase):
    def test_parser_error_inside_interpolation_surfaces(self):
        e = err_of(lambda: body_of('s := "pre {x +} post";'))
        self.assertIsNotNone(e)
        self.assertIn("Invalid interpolation expression", e.msg)
        self.assertIn("x +", e.msg)
        self.assertEqual(e.line, 1)

    def test_interpolation_error_position_is_string_token(self):
        src = 'fnc main() : void {\n    s := "pre {x +} post";\n}'
        e = err_of(parse, src)
        self.assertIsNotNone(e)
        self.assertEqual(e.line, 2)

    def test_unlexable_interpolation_stays_literal(self):
        # JSON-style content with \" escapes must not become a parse error.
        node = body_of("""s := "json {\\"a\\": 1}";""")[0]
        self.assertIsInstance(node, A.VariableDecl)
        self.assertIsInstance(node.value, A.InterpolatedString)
        text = "".join(p[0] for p in node.value.parts if p[0] is not None)
        self.assertEqual(text, 'json {"a": 1}')


class TestParserPositions(unittest.TestCase):
    """M6: call/index/array nodes carried no line/col (diagnostics had none)."""

    def test_method_call_position(self):
        st = parse_stmt("obj.method(1);")
        call = st.expr
        self.assertIsInstance(call, A.MethodCall)
        self.assertEqual(call.line, 1)
        self.assertEqual(call.col, 4)  # 0-based col of `method`

    def test_index_access_position(self):
        st = parse_stmt("arr[0];")
        idx = st.expr
        self.assertIsInstance(idx, A.IndexAccess)
        self.assertEqual(idx.line, 1)
        self.assertEqual(idx.col, 3)  # 0-based col of `[`

    def test_array_init_position(self):
        st = parse_stmt("v := {1};")
        self.assertIsInstance(st.value, A.ArrayInit)
        self.assertEqual(st.value.line, 1)
        self.assertEqual(st.value.col, 5)  # 0-based col of `{`


class TestParserShowDiagnostics(unittest.TestCase):
    def test_show_end_error_points_at_end_keyword(self):
        src = 'fnc main() : void {\n    show("a", end = 1);\n}'
        e = err_of(parse, src)
        self.assertIsNotNone(e)
        self.assertIn("must be a string literal", e.msg)
        self.assertEqual(e.line, 2)
        self.assertEqual(e.col, 14)  # 0-based col of `end`

    def test_show_unexpected_kwarg_points_at_keyword(self):
        src = 'fnc main() : void {\n    show("a", endd = 1);\n}'
        e = err_of(parse, src)
        self.assertIsNotNone(e)
        self.assertIn("Unexpected keyword argument 'endd'", e.msg)
        self.assertEqual(e.line, 2)
        self.assertEqual(e.col, 14)


class TestParserStatementWhitelist(unittest.TestCase):
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
        "s := \"str\";",
        "n := -1;",
        "x := (1 + 2);",
        "p := &x;",
        "x := 1; x++;",
    ]

    def test_statement_start_whitelist(self):
        for src in self.VALID:
            with self.subTest(src=src):
                e = err_of(lambda s: body_of(s), src)
                self.assertIsNone(e, "unexpected error for %r: %s"
                                  % (src, e.msg if e else ""))

    def test_char_statement_parses_as_expression(self):
        stmts = body_of("'c';")
        self.assertIsInstance(stmts[0], A.ExpressionStatement)

    def test_increment_statement_parses(self):
        stmts = body_of("i := 0; ++i;")
        self.assertIsInstance(stmts[1], A.ExpressionStatement)


if __name__ == "__main__":
    unittest.main()
