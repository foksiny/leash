# Leash Standard Libraries

Documentation for the libraries shipped in `installthis/`.

---

## `hotreload.lsh` — Reloader

A file watcher that re-executes a Leash script when it changes on disk.

```lsh
use hotreload::Reloader;
```

### Class: `Reloader`

#### Static Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `new` | `Reloader.new(name string) : Reloader` | Creates a reloader watching the given file path. |
| `create` | `create Reloader(name string)` | Creates a reloader watching the given file path. |

#### Instance Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `start` | `start() : void` | Enters a polling loop (0.1 s interval). On file change, runs `leash run <filename>` and prints reload banners. Exits with error if the watched file is the hot-reloader itself. |

---

## `tuple.lsh` — Tuple

An immutable, fixed-size generic container.

```lsh
use tuple::Tuple;
```

### Class: `Tuple<T>`

| Member | Type | Description |
|--------|------|-------------|
| `size` | `pub int` | Number of elements |

#### Static Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `new` | `Tuple.new(vals T[]) : imut Tuple` | Creates an immutable tuple from an array. |

#### Instance Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get` | `get(idx int) : T` | Returns the element at the given index. |
| `isin` | `isin(val T) : bool` | Returns `true` if the value exists in the tuple. |

---

## `types.lsh` — Numeric Type Aliases

Shorthand aliases for sized integer and float types. Import everything with:

```lsh
use types::*;
```

### Integer Aliases

| Alias | Underlying Type |
|-------|----------------|
| `int8` | `int<8>` |
| `int16` | `int<16>` |
| `int64` | `int<64>` |
| `int128` | `int<128>` |
| `int256` | `int<256>` |
| `int512` | `int<512>` |
| `uint8` | `uint<8>` |
| `uint16` | `uint<16>` |
| `uint64` | `uint<64>` |
| `uint128` | `uint<128>` |
| `uint256` | `uint<256>` |
| `uint512` | `uint<512>` |

### Float Aliases

| Alias | Underlying Type |
|-------|----------------|
| `float16` | `float<16>` |
| `float64` | `float<64>` |
| `double` | `float<64>` |
| `float128` | `float<128>` |
| `float256` | `float<256>` |
| `float512` | `float<512>` |

---

## `utils/str.lsh` — Str

String utility functions (all static).

```lsh
use utils::str::Str;
```

### Class: `Str`

#### Static Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `split` | `Str.split(str string, del char) : vec<string>` | Splits a string by a single-character delimiter. |
| `splits` | `Str.splits(str string, dels string) : vec<string>` | Splits a string by any character in the `dels` string. |
| `starts` | `Str.starts(a string, b string) : bool` | Returns `true` if `a` starts with `b`. |
| `ends` | `Str.ends(a string, b string) : bool` | Returns `true` if `a` ends with `b`. |
| `upper` | `Str.upper(str string) : string` | Converts ASCII lowercase letters to uppercase. |
| `lower` | `Str.lower(str string) : string` | Converts ASCII uppercase letters to lowercase. |
| `reversed` | `Str.reversed(str string) : string` | Returns the string in reverse order. |
| `digit` | `Str.digit(txt string) : bool` | Returns `true` if all characters are `0`–`9`. Empty string returns `false`. |
| `alpha` | `Str.alpha(txt string) : bool` | Returns `true` if all characters are ASCII letters. Empty string returns `false`. |
| `alnum` | `Str.alnum(txt string) : bool` | Returns `true` if the first character is a letter and the rest are letters or digits. Empty string returns `false`. |
| `ident` | `Str.ident(txt string) : bool` | Returns `true` if the string is a valid identifier (letters, digits after position 0, and underscores). Empty string returns `false`. |

---

## `utils/math.lsh` — Math

Mathematical constants and functions (all static). Depends on `types::*`.

```lsh
use utils::math::Math;
```

### Macros

| Macro | Signature | Description |
|-------|-----------|-------------|
| `max` | `max(a, b)` | Returns the larger of two values. Implemented as `a < b ? b : a`. |
| `min` | `min(a, b)` | Returns the smaller of two values. Implemented as `a > b ? b : a`. |

### Class: `Math`

#### Constants

| Constant | Type | Value |
|----------|------|-------|
| `PI` | `double` | 3.14159265358979323846 |
| `E` | `double` | 2.71828182845904523536 |
| `HALF_PI` | `double` | 1.57079632679489661923 |
| `LN10` | `double` | 2.30258509299404568402 |
| `LN2` | `double` | 0.69314718055994530942 |

#### Static Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `floor` | `Math.floor(x double) : double` | Rounds toward negative infinity. |
| `ceil` | `Math.ceil(x double) : double` | Rounds toward positive infinity. |
| `fmod` | `Math.fmod(x double, y double) : double` | Floating-point modulo. Returns `0.0` if `y` is zero. |
| `sqrt` | `Math.sqrt(x double) : double` | Square root via Newton's method (8 iterations). Returns `nil` for negative input. |
| `exp` | `Math.exp(x double) : double` | e^x via Taylor series. |
| `log` | `Math.log(x double) : double` | Natural logarithm via Newton's method. Returns `nil` for non-positive input. |
| `sin` | `Math.sin(x double) : double` | Sine via Taylor series (normalized to [-π, π]). |
| `cos` | `Math.cos(x double) : double` | Cosine, implemented as `sin(x + π/2)`. |
| `tan` | `Math.tan(x double) : double` | Tangent, `sin(x) / cos(x)`. |
| `pow` | `Math.pow(base double, exponent double) : double` | Exponentiation. Uses fast squaring for positive integer exponents, `exp(e·log(b))` for positive bases. Returns `nil` for negative bases with non-integer exponents. |
| `asin` | `Math.asin(x double) : double` | Arc sine via Newton's method. Returns `nil` if `|x| > 1`. |
| `acos` | `Math.acos(x double) : double` | Arc cosine, `π/2 − asin(x)`. Returns `nil` if `|x| > 1`. |
| `atan` | `Math.atan(x double) : double` | Arc tangent via `asin(x / √(1+x²))`. |
| `cosh` | `Math.cosh(x double) : double` | Hyperbolic cosine, `(e^x + e^(−x)) / 2`. |
| `sinh` | `Math.sinh(x double) : double` | Hyperbolic sine. Uses Taylor series near zero, exponential formula otherwise. |
| `squared` | `Math.squared(x double) : double` | Returns `x * x`. |
| `log10` | `Math.log10(x double) : double` | Base-10 logarithm, `log(x) / LN10`. Returns `nil` for non-positive input. |
| `log2` | `Math.log2(x double) : double` | Base-2 logarithm, `log(x) / LN2`. Returns `nil` for non-positive input. |
| `fabs` | `Math.fabs(x double) : double` | **unsafe** — Absolute value via bit manipulation (clears sign bit of IEEE 754 double). |

---

## `utils/vecmath.lsh` — VecMath

Generic vector math operations (element-wise).

```lsh
use utils::vecmath::VecMath;
```

### Class: `VecMath<T>`

#### Static Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `sum` | `VecMath<T>.sum(a vec<T>, b vec<T>) : vec<T>` | Element-wise addition. Returns `nil` if sizes don't match. |
| `sub` | `VecMath<T>.sub(a vec<T>, b vec<T>) : vec<T>` | Element-wise subtraction. Returns `nil` if sizes don't match. |
| `mul` | `VecMath<T>.mul(a vec<T>, b vec<T>) : vec<T>` | Element-wise multiplication. Returns `nil` if sizes don't match. |
| `div` | `VecMath<T>.div(a vec<T>, b vec<T>) : vec<T>` | Element-wise division. Returns `nil` if sizes don't match. |

### Usage Example

```lsh
use utils::vecmath::VecMath;

fnc main() : void {
    a: vec<int>;
    b: vec<int>;
    a.extend({10, 20, 30});
    b.extend({1, 2, 3});

    c: vec<int> = VecMath<int>.sum(a, b);
    d: vec<int> = VecMath<int>.sub(a, b);
    e: vec<int> = VecMath<int>.mul(a, b);
    f: vec<int> = VecMath<int>.div(a, b);
}
``` |
