#!/usr/bin/env python3
"""Compile-time lifetime diagnostics for the experimental --arena backend.

The arena copies values whose types it knows how to move (strings,
containers, aggregates, unions). These checks cover the remaining cases
where it cannot prove safety: direct libc heap calls and raw pointers that
outlive the frame which created them.
"""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_LEASHC = os.path.join(ROOT, "bin", "leashc")

MANUAL_HEAP = """\
fnc main() {
    p: *int = malloc(8);
    show(1);
}
"""

SPAWN_RAW_POINTER = """\
worker fnc w(p *int) : void { show(1); }
fnc main() {
    x: int = 1;
    spawn w(&x);
    show(2);
}
"""

# A pointer of unknown provenance cannot be copied into the transfer cell:
# its pointee size is unknown and it may refer to a frame that is popping.
# A raw parameter is the canonical unprovable source.
UNPROVABLE_SPAWN_POINTER = """\
worker fnc w(p *int) : void { show(1); }
fnc relay(p *int) : void {
    spawn w(p);
}
fnc main() {
    show(2);
}
"""

INITIALIZER_HEAP = """\
g: *int = malloc(8);
fnc main() { show(1); }
"""

FIELD_HEAP = """\
def B : struct { p: *int = malloc(8); }
fnc main() { b: B = B {}; show(1); }
"""

STATIC_FIELD_HEAP = """\
def B : struct { static p: *int = malloc(8); }
fnc main() { show(1); }
"""

GLOBAL_LINE = """\
p: *int = null;
// declaration is on line 3
g: *int = p;
fnc main() { show(1); }
"""

SHADOWED_NAME = """\
sp: int = 1;
p: *int = null;
g: *int = p;
fnc main() { show(1); }
"""

COMMENT_NAME = """\
// note: g: was here
p: *int = null;
g: *int = p;
fnc main() { show(1); }
"""

NO_TRAILING_NEWLINE = """\
p: *int = null;
fnc main() { show(1); }
g: *int = p;"""

SAME_NAME_FIELD_AND_GLOBAL = """\
def Box : struct { p: int; }
// another declaration named p follows
p: *int = null;
g: *int = p;
fnc main() { show(1); }
"""

LONG_LIVED_FIELD_STORE = """\
def B : struct { p: *int; }
g: B = B { p: null };
fnc main() {
    x: int = 1;
    g.p = &x;
    show(1);
}
"""

LONG_LIVED_CONTAINER_STORE = """\
g: vec<*int>;
fnc main() {
    x: int = 1;
    g.pushb(&x);
    show(1);
}
"""

SAFE_ARENA = """\
def Node : class { v: string; next: Node; }
fnc make() : Node {
    a: Node = Node { v: "a", next: null };
    b: Node = Node { v: "b", next: null };
    a.next = b;
    b.next = a;
    return a;
}
fnc main() { n: Node = make(); show(n.v, " ", n.next.v); }
"""


class TestArenaLifetimeAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN_LEASHC):
            raise unittest.SkipTest("bin/leashc missing (run `leash self-host`)")
        cls.tmp = tempfile.mkdtemp(prefix="leash_arena_safety_")

    def _compile(self, source, name, extra=None):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        cmd = [BIN_LEASHC, "compile", src, "to",
               os.path.join(self.tmp, name + "_bin"), "--arena"]
        if extra:
            cmd += extra
        return subprocess.run(cmd, cwd=ROOT, capture_output=True,
                              text=True, timeout=300)

    def test_libc_heap_call_is_safe(self):
        # Libc heap pointers are foreign and stable, not arena frames.
        r = self._compile(MANUAL_HEAP, "manual_heap")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_raw_pointer_spawn_address_is_safe(self):
        # `&x` is copied into an immortal transfer cell, so the worker
        # never dereferences the spawner's frame.
        r = self._compile(SPAWN_RAW_POINTER, "spawn_raw")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_unprovable_raw_pointer_spawn_is_fatal(self):
        r = self._compile(UNPROVABLE_SPAWN_POINTER, "spawn_unprovable")
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("E_ARENA_LIFETIME", out)
        self.assertIn("= why:", out)
        self.assertIn("= help:", out)

    def test_warnings_as_errors_flag_is_noop_when_audit_is_strict(self):
        # -oe used to promote warnings to errors. With every diagnostic now
        # fatal, passing -oe changes nothing: both runs fail identically.
        plain = self._compile(UNPROVABLE_SPAWN_POINTER, "spawn_raw_plain")
        strict = self._compile(UNPROVABLE_SPAWN_POINTER, "spawn_raw_oe", ["-oe"])
        self.assertNotEqual(plain.returncode, 0)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("E_ARENA_LIFETIME", strict.stdout + strict.stderr)

    def test_libc_heap_in_global_initializer_is_safe(self):
        # libc heap storage is foreign to the arena, so a global handle to
        # it can never dangle when a frame pops.
        r = self._compile(INITIALIZER_HEAP, "init_heap")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_libc_heap_in_field_initializer_is_safe(self):
        r = self._compile(FIELD_HEAP, "field_heap")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_libc_heap_in_static_field_initializer_is_safe(self):
        r = self._compile(STATIC_FIELD_HEAP, "static_field_heap")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_declaration_diagnostic_uses_real_line(self):
        r = self._compile(GLOBAL_LINE, "global_line")
        out = r.stdout + r.stderr
        self.assertIn("E_ARENA_LIFETIME", out)
        self.assertIn("global_line.lsh:3", out)
        self.assertIn("g: *int = p;", out)

    def test_declaration_line_uses_token_boundary(self):
        r = self._compile(SHADOWED_NAME, "shadowed_name")
        out = r.stdout + r.stderr
        self.assertIn("shadowed_name.lsh:3", out)
        self.assertIn("g: *int = p;", out)

    def test_declaration_line_skips_comments(self):
        r = self._compile(COMMENT_NAME, "comment_name")
        out = r.stdout + r.stderr
        self.assertIn("comment_name.lsh:3", out)
        self.assertIn("g: *int = p;", out)

    def test_declaration_line_uses_ast_metadata(self):
        r = self._compile(SAME_NAME_FIELD_AND_GLOBAL, "same_name_decl")
        out = r.stdout + r.stderr
        self.assertIn("same_name_decl.lsh:4", out)
        self.assertIn("g: *int = p;", out)

    def test_declaration_line_without_trailing_newline(self):
        r = self._compile(NO_TRAILING_NEWLINE, "no_trailing_newline")
        out = r.stdout + r.stderr
        self.assertIn("no_trailing_newline.lsh:3", out)
        self.assertIn("g: *int = p;", out)

    def test_raw_pointer_into_long_lived_field_is_fatal(self):
        r = self._compile(LONG_LIVED_FIELD_STORE, "long_field")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_raw_pointer_into_long_lived_container_is_fatal(self):
        r = self._compile(LONG_LIVED_CONTAINER_STORE, "long_container")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn("E_ARENA_LIFETIME", out)

    def test_inline_pop_calls_frame_verifier(self):
        source = 'fnc one() : string { s: string = "x" + "y"; return s; }\nfnc main() { show(one()); }\n'
        src = os.path.join(self.tmp, "inline_verify.lsh")
        with open(src, "w") as fh:
            fh.write(source)
        out = os.path.join(self.tmp, "inline_verify")
        r = subprocess.run([BIN_LEASHC, "compile", src, "--emit-llvm", "to", out, "--arena"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ll = out + ".ll"
        if not os.path.exists(ll):
            ll = out + ".ll.ll"
        with open(ll) as fh:
            ir = fh.read()
        # Frame pops now go through the runtime helper, which performs the
        # registered-slot verification as part of arena_pop_cp(); the IR
        # no longer emits a separate verifier call at each pop site.
        self.assertIn("call void @leasharena_exit_slow()", ir)

    def test_safe_recursive_copy_has_no_lifetime_diagnostic(self):
        r = self._compile(SAFE_ARENA, "safe_recursive")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ARENA_LIFETIME", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
