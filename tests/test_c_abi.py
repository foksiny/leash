#!/usr/bin/env python3
"""C ABI regression: Leash `float` is C double, int<64> is C int64_t."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEASHC = os.path.join(ROOT, "bin", "leashc")

C_SRC = r"""
#include <stdint.h>
#include <stdbool.h>
double lshabi_double(double x) { return x * 2.0; }
int64_t lshabi_i64(int64_t x) { return x + 100; }
int lshabi_i32(int x) { return x + 7; }
bool lshabi_bool(int x) { return x != 0; }
int lshabi_strlen(const char* s) { int n = 0; while (s[n]) n++; return n; }
typedef struct { uint8_t r, g, b, a; } LshAbiColor;
typedef struct { float x, y; } LshAbiVec2;
typedef struct { float x, y, z; } LshAbiVec3;
typedef struct { int64_t a, b; } LshAbiPairI64;
typedef struct { double x, y; } LshAbiPairF64;
typedef struct { _Bool a, b; } LshAbiBools;
typedef struct { void* p; } LshAbiPtr;
float lshabi_float_add(float a, float b) { return a + b; }
uint64_t lshabi_color_rgba(LshAbiColor c) {
    return ((uint64_t)c.r) | ((uint64_t)c.g << 8) |
           ((uint64_t)c.b << 16) | ((uint64_t)c.a << 24);
}
LshAbiColor lshabi_color_make(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    LshAbiColor c = { r, g, b, a }; return c;
}
double lshabi_vec2_sum(LshAbiVec2 v) { return (double)v.x + (double)v.y; }
LshAbiVec3 lshabi_vec3_make(double x, double y, double z) {
    LshAbiVec3 v = { (float)x, (float)y, (float)z }; return v;
}
uint64_t lshabi_pair_i64(LshAbiPairI64 p) { return (uint64_t)(p.a + p.b); }
double lshabi_pair_f64(LshAbiPairF64 p) { return p.x + p.y; }
uint64_t lshabi_bools(LshAbiBools p) { return (uint64_t)p.a | ((uint64_t)p.b << 1); }
const char* lshabi_ptr_id(LshAbiPtr p) { return (const char*)p.p; }
"""

LEASH_SRC = r"""
@from("libabi.a") {
    fnc lshabi_double(x float) : float;
    fnc lshabi_i64(x int<64>) : int<64>;
    fnc lshabi_i32(x int) : int;
    fnc lshabi_bool(x int) : bool;
    fnc lshabi_strlen(s string) : int;
    def Color : struct { r: uint<8>; g: uint<8>; b: uint<8>; a: uint<8>; }
    def Vec2 : struct { x: float<32>; y: float<32>; }
    def Vec3 : struct { x: float<32>; y: float<32>; z: float<32>; }
    def PairI64 : struct { a: int<64>; b: int<64>; }
    def PairF64 : struct { x: double; y: double; }
    def Bools : struct { a: uint<8>; b: uint<8>; }  // C _Bool is one byte
    def PtrBox : struct { p: pointer<void>; }
    fnc lshabi_float_add(a float<32>, b float<32>) : float<32>;
    fnc lshabi_color_rgba(c Color) : int<64>;
    fnc lshabi_color_make(r int, g int, b int, a int) : Color;
    fnc lshabi_vec2_sum(v Vec2) : float;
    fnc lshabi_vec3_make(x float, y float, z float) : Vec3;
    fnc lshabi_pair_i64(p PairI64) : int<64>;
    fnc lshabi_pair_f64(p PairF64) : float;
    fnc lshabi_bools(p Bools) : int<64>;
    fnc lshabi_ptr_id(p PtrBox) : string;
}
fnc main() {
    show(lshabi_double(1.5));
    show(lshabi_i64(7));
    show(lshabi_i32(5));
    show(lshabi_bool(1));
    show(lshabi_bool(0));
    show(lshabi_strlen("leash"));
    show(lshabi_float_add(1.5, 2.25));
    c: Color = Color { r: 245, g: 100, b: 50, a: 255 };
    show(lshabi_color_rgba(c));
    made: Color = lshabi_color_make(10, 20, 30, 40);
    show(made.r, ",", made.g, ",", made.b, ",", made.a);
    v: Vec2 = Vec2 { x: 1.5, y: 2.25 };
    show(lshabi_vec2_sum(v));
    v3: Vec3 = lshabi_vec3_make(1.5, 2.25, 3.75);
    show(v3.x, ",", v3.y, ",", v3.z);
    show(lshabi_pair_i64(PairI64 { a: 7, b: 35 }));
    show(lshabi_pair_f64(PairF64 { x: tofloat(double, 1.5), y: tofloat(double, 2.25) }));
    show(lshabi_bools(Bools { a: 1, b: 1 }));
    show(lshabi_ptr_id(PtrBox { p: "hello" as pointer<void> }));
}
"""


class CAbi(unittest.TestCase):
    def test_native_types_match_abi(self):
        cc = shutil.which("cc")
        if not cc or not os.path.exists(LEASHC):
            self.skipTest("compiler or cc missing")
        with tempfile.TemporaryDirectory(prefix="leash_abi_") as td:
            c_path = os.path.join(td, "abi.c")
            with open(c_path, "w") as fh:
                fh.write(C_SRC)
            obj = os.path.join(td, "abi.o")
            lib = os.path.join(td, "libabi.a")
            self.assertEqual(subprocess.run([cc, "-O2", "-c", c_path, "-o", obj],
                                            capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(["ar", "rcs", lib, obj],
                                            capture_output=True).returncode, 0)
            src = os.path.join(td, "main.lsh")
            with open(src, "w") as fh:
                fh.write(LEASH_SRC)
            r = subprocess.run([LEASHC, "run", src], cwd=td, capture_output=True,
                               text=True, errors="replace", timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            lines = [l for l in r.stdout.splitlines()
                     if not l.startswith("--- Executed at")]
            self.assertEqual(lines, [
                "3.000000", "107", "12", "true", "false", "5",
                "3.750000",       # float<32> scalar -> C float, not double
                "4281492725",     # 0xFF3264F5: all four bytes reached C
                "10,20,30,40",    # struct returned by value from C
                "3.750000",       # two floats packed into one register
                "1.500000,2.250000,3.750000",  # multi-register struct return
                "42",                          # two i64 fields -> two GP registers
                "3.750000",                    # two doubles -> one SSE pair
                "3",                           # two bool bytes -> i16 register
                "hello",                       # pointer field -> i64 register
            ])


if __name__ == "__main__":
    unittest.main()
