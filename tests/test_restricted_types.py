#!/usr/bin/env python3
"""Restricted literal types (`def Name : type lit, ...;`)."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")
EXAMPLE = os.path.join(ROOT, "examples", "weirdtypes.lsh")

ZERO_BAD = """\
def zero : type 0;
fnc main() {
    a: zero = 0;
    a = 1;
    show(a);
}
"""

ONETWO_BAD = """\
def onetwo : type 1, 2;
fnc main() {
    b: onetwo = 1;
    b = 3;
    show(b);
}
"""

STATUS_BAD = """\
def status : type "ok", "error", nil;
fnc main() {
    s: status = "bad";
    show(s);
}
"""

STATUS_WIDEN = """\
def status : type "ok", "error", nil;
fnc main() {
    s: status = "ok";
    c: string = s;
    show(c);
}
"""

STATUS_COPY = """\
def status : type "ok", "error", nil;
fnc main() {
    s: status = "ok";
    t: status = s;
    show(t);
}
"""

RESTRICTED_EXPRESSIONS = """\
def flag : type true, false;
def r : type "ok";
def S : struct { v: r; }
g: r = "ok";
fnc same(s: r) : void { show(s); }
fnc main() {
    f: flag = true;
    if f { show("yes"); }
    s: r = "ok";
    t: S = S { v: s };
    show(t.v, " ", g, " ", s + "!");
    same(s);
}
"""

GLOBAL_BAD = """\
def r : type "ok";
g: r = "ok";
fnc main() {
    g = "bad";
    show(g);
}
"""

NEGATIVE = """\
def neg : type -1, 0;
fnc main() {
    n: neg = -1;
    show(n);
    n = 0;
    show(n);
}
"""

LITERAL_ARG_BAD = """\
def r : type "ok";
fnc f(x: r) : void { show(x); }
fnc main() { f("bad"); }
"""

METHOD_ARG_BAD = """\
def r : type "ok";
def C : class {
    pub fnc set(x: string) : void { show(x); }
}
fnc main() {
    s: r = "ok";
    c: C = C {};
    c.set(s);
}
"""

FIELD_GLOBAL_DEFAULT = """\
def r : type 1, 2;
g: r = 1;
def S : struct { v: r = g; }
fnc main() {
    s: S = S {};
    show(s.v);
}
"""

NEGATIVE_FLOAT_LITERAL = """\
fnc main() {
    show(-.5, " ", - -1.5);
}
"""

NEGATIVE_FLOAT = """\
def negf : type -1.5, -2.5;
fnc main() {
    x: negf = -1.5;
    show(x);
    x = -2.5;
    show(x);
    x = -3.5;
}
"""

CALL_WIDEN = """\
def r : type "ok";
fnc takes(s string) : void { show(s); }
fnc main() {
    s: r = "ok";
    takes(s);
}
"""


class TestRestrictedTypes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_restricted_")

    def _check(self, source, name):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        r = subprocess.run([BIN_LEASHC, "check", src], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
        return r.stdout + r.stderr

    def _run(self, source, name):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        out = os.path.join(self.tmp, name + "_bin")
        b = subprocess.run([BIN_LEASHC, "compile", src, "to", out], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(b.returncode, 0, b.stdout + b.stderr)
        r = subprocess.run([out], cwd=self.tmp, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_example_runs(self):
        r = subprocess.run([BIN_LEASHC, "compile", EXAMPLE, "to",
                            os.path.join(self.tmp, "weirdtypes")],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        run = subprocess.run([os.path.join(self.tmp, "weirdtypes")],
                             cwd=self.tmp, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "0\n2\nok\n")

    def test_zero_rejects_other_int(self):
        self.assertIn("only lets you use the value of 0", self._check(ZERO_BAD, "zero_bad"))

    def test_onetwo_rejects_other_int(self):
        self.assertIn("only lets you use the values of 1 and 2", self._check(ONETWO_BAD, "onetwo_bad"))

    def test_status_rejects_other_string(self):
        self.assertIn("super restrictive type 'status'", self._check(STATUS_BAD, "status_bad"))

    def test_no_implicit_widening_to_base(self):
        self.assertIn("Cannot use the super restrictive type 'status' as 'string'",
                      self._check(STATUS_WIDEN, "status_widen"))

    def test_expressions_fields_globals_and_params(self):
        self.assertEqual(
            self._run(RESTRICTED_EXPRESSIONS, "restricted_exprs"),
            "yes\nok ok ok!\nok\n")

    def test_global_assignment_is_checked(self):
        self.assertIn("only lets you use the value of \"ok\"",
                      self._check(GLOBAL_BAD, "global_bad"))

    def test_negative_literal_values(self):
        self.assertEqual(self._run(NEGATIVE, "negative"), "-1\n0\n")

    def test_literal_argument_is_checked(self):
        self.assertIn("only lets you use the value of \"ok\"",
                      self._check(LITERAL_ARG_BAD, "literal_arg_bad"))

    def test_method_argument_widening_is_rejected(self):
        self.assertIn("super restrictive type 'r' as 'string'",
                      self._check(METHOD_ARG_BAD, "method_arg_bad"))

    def test_field_default_can_use_restricted_global(self):
        self.assertEqual(self._run(FIELD_GLOBAL_DEFAULT, "field_global_default"), "1\n")

    def test_negative_float_literal_text_is_valid_ir(self):
        self.assertEqual(self._run(NEGATIVE_FLOAT_LITERAL, "neg_float_literal"),
                         "-0.500000 1.500000\n")

    def test_negative_float_values(self):
        self.assertIn("only lets you use the values of -1.5 and -2.5",
                      self._check(NEGATIVE_FLOAT, "negative_float"))

    def test_call_cannot_widen_to_base(self):
        self.assertIn("super restrictive type 'r' as 'string'",
                      self._check(CALL_WIDEN, "call_widen"))

    def test_same_restricted_type_copy_allowed(self):
        out = self._check(STATUS_COPY, "status_copy")
        self.assertNotIn("error", out)
        self.assertEqual(self._run(STATUS_COPY, "status_copy_run"), "ok\n")


if __name__ == "__main__":
    unittest.main()
