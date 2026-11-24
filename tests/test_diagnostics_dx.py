#!/usr/bin/env python3
"""Developer-experience regression tests for compiler diagnostics.

The compiler keeps the original first line of every diagnostic byte-identical
(scripts and older tooling match on it) and appends structured guidance:
``  why:`` explains the failure, ``  help:`` tells the programmer what to do,
and ``  example:`` shows the shape of a fix where useful. Common name typos
also get a Levenshtein-based “Did you mean” suggestion.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")


class DiagnosticTests(unittest.TestCase):
    def _check(self, source):
        with tempfile.TemporaryDirectory(prefix="leash_dx_") as td:
            path = os.path.join(td, "main.lsh")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            r = subprocess.run([LEASHC, "check", path], cwd=ROOT,
                               capture_output=True, text=True, timeout=120)
            return r.stdout + r.stderr

    def test_undefined_function_suggests_closest_builtin(self):
        out = self._check('fnc main(): void { show(getin("x")); }\n')
        self.assertIn("Call to undefined function: 'getin'", out)
        self.assertIn("why:", out)
        self.assertIn("Did you mean 'getint'?", out)

    def test_undefined_variable_teaches_declaration(self):
        out = self._check('fnc main(): void { show(counter); }\n')
        self.assertIn("Undefined variable: 'counter'", out)
        self.assertIn("why:", out)
        self.assertIn("help:", out)
        self.assertIn("example:", out)
        self.assertIn("name := value;", out)

    def test_unknown_method_suggests_string_method(self):
        out = self._check('fnc main(): void { s := "hi"; show(s.starts(1)); }\n')
        self.assertIn("Type 'string' has no method named 'starts'", out)
        self.assertIn("Did you mean 'start'?", out)
        self.assertIn("why:", out)

    def test_unknown_type_explains_declaration(self):
        out = self._check('fnc main(): void { x: Posi = Posi { a: 1 }; show(x); }\n')
        self.assertIn("declared with unknown type 'Posi'", out)
        self.assertIn("why:", out)
        self.assertIn("help:", out)

    def test_existing_first_lines_are_preserved(self):
        out = self._check('fnc main(): void { nope(); }\n')
        self.assertIn("error: Call to undefined function: 'nope'", out)
        self.assertNotIn("error[E_", out)  # typecheck keeps the legacy prefix


if __name__ == "__main__":
    unittest.main()
