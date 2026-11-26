#!/usr/bin/env python3
"""Regression tests for compiler CLI/runtime security hardening."""
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin", "leashc")


class SecurityHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIN):
            raise unittest.SkipTest("bin/leashc missing")
        cls.tmp = tempfile.mkdtemp(prefix="leash_security_")

    def _compile(self, source, name, extra=None):
        src = os.path.join(self.tmp, name + ".lsh")
        with open(src, "w") as fh:
            fh.write(source)
        cmd = [BIN, "compile", src, "to", os.path.join(self.tmp, name + "_bin")]
        if extra:
            cmd += extra
        return subprocess.run(cmd, cwd=self.tmp, capture_output=True,
                              text=True, errors="replace", timeout=120)

    def test_config_out_name_cannot_inject_shell(self):
        marker = os.path.join(self.tmp, "PWNED_CONFIG")
        if os.path.exists(marker):
            os.remove(marker)
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj, exist_ok=True)
        with open(os.path.join(proj, "config.lshc"), "w") as fh:
            fh.write('main: "main.lsh"\nout_name: "x;touch %s;true"\n' % marker)
        with open(os.path.join(proj, "main.lsh"), "w") as fh:
            fh.write("fnc main() : void { show(1); }\n")
        r = subprocess.run([BIN, "build"], cwd=proj, capture_output=True,
                           text=True, errors="replace", timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("out_name", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_from_library_name_cannot_inject_shell(self):
        marker = os.path.join(self.tmp, "PWNED_FROM")
        if os.path.exists(marker):
            os.remove(marker)
        src = '@from("x;touch %s") { fnc dummy() : void; }\nfnc main() : void { }\n' % marker
        r = self._compile(src, "from_inject")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("E_SECURITY", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_l_flag_cannot_inject_response_file_lines(self):
        r = self._compile("fnc main() : void { }\n", "lib_inject",
                          ["-lfoo\n-fnosuchflag"])
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unsafe library name", r.stdout + r.stderr)

    def test_predictable_temp_symlink_is_not_followed(self):
        victim = os.path.join(self.tmp, "VICTIM")
        with open(victim, "w") as fh:
            fh.write("ORIGINAL")
        link = "/tmp/leashc_symlink_target.rsp"
        try:
            os.symlink(victim, link)
        except FileExistsError:
            os.remove(link)
            os.symlink(victim, link)
        self._compile("fnc main() : void { }\n", "symlink_target")
        with open(victim) as fh:
            self.assertEqual(fh.read(), "ORIGINAL")
        os.remove(link)

    def test_json_dos_is_rejected(self):
        src = '''use json::Json;
fnc main() : void {
    a := Json.parse("1e1000000000000000000");
    show(a.ok, " ", a.err);
    deep: string = "";
    i: int = 0;
    while i < 200 { deep = deep + "["; i = i + 1; }
    deep = deep + "0";
    i = 0;
    while i < 200 { deep = deep + "]"; i = i + 1; }
    b := Json.parse(deep);
    show(b.ok, " ", b.err);
}
'''
        with tempfile.NamedTemporaryFile("w", suffix=".lsh", delete=False,
                                         dir=self.tmp) as fh:
            fh.write(src)
            path = fh.name
        r = subprocess.run([BIN, "run", path, "--other-imports",
                            os.path.join(ROOT, "installthis")],
                           cwd=ROOT, capture_output=True, text=True,
                           errors="replace", timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("false json: exponent out of range", r.stdout)
        self.assertIn("false json: maximum nesting depth exceeded", r.stdout)



    def test_warm_ir_cache_is_keyed_by_source_path(self):
        # Two identical programs in different paths embed different
        # _FILEPATH values; a cache keyed only on source text leaked one
        # path into the other program.
        src = 'fnc main() : void { show(_FILEPATH); }\n'
        outs = []
        for name in ("cache_a", "cache_b"):
            path = os.path.join(self.tmp, name + ".lsh")
            with open(path, "w") as fh:
                fh.write(src)
            exe = os.path.join(self.tmp, name + "_bin")
            r = subprocess.run([BIN, "compile", path, "to", exe], cwd=self.tmp,
                               capture_output=True, text=True,
                               errors="replace", timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            run = subprocess.run([exe], cwd=self.tmp, capture_output=True,
                                 text=True, errors="replace", timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            outs.append(run.stdout.strip())
        self.assertNotEqual(outs[0], outs[1])

    def test_project_clibs_name_cannot_inject_shell(self):
        marker = os.path.join(self.tmp, "PWNED_CLIBS")
        if os.path.exists(marker):
            os.remove(marker)
        proj = os.path.join(self.tmp, "clibs_proj")
        os.makedirs(proj, exist_ok=True)
        with open(os.path.join(proj, "config.lshc"), "w") as fh:
            fh.write('main: "main.lsh"\nclibs: { "x;touch %s": "1.0" }\n' % marker)
        with open(os.path.join(proj, "main.lsh"), "w") as fh:
            fh.write("fnc main() : void { show(1); }\n")
        r = subprocess.run([BIN, "build"], cwd=proj, capture_output=True,
                           text=True, errors="replace", timeout=120)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("unsafe library name", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

if __name__ == "__main__":
    unittest.main()
