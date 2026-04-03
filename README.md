# Leash Programming Language

Leash is a strongly-typed, modern compiled programming language built on top of LLVM. It features an intuitive syntax and is designed to handle common tasks with native performance.

## Table of Contents
- [Running Leash](#running-leash)
- [Defining Variables](#defining-variables)
- [Immutable Variables](#immutable-variables)
- [Data Types](#data-types)
- [Operators](#operators)
- [Functions](#functions)
- [Global Variables](#global-variables)
- [Control Flow](#control-flow)
- [Input Handling](#input-handling)
- [Random Numbers](#random-numbers)
- [Time and Delays](#time-and-delays)
- [Arrays](#arrays)
- [Structs](#structs)
- [Pointers](#pointers)
- [Unions](#unions)
- [Enums](#enums)
- [Type Aliases](#type-aliases)
- [Generic Types](#generic-types)
- [Multi-Type Functions](#multi-type-functions)
- [Type Casting](#type-casting)
- [Type Conversions](#type-conversions)
- [Strings](#strings)
- [Classes](#classes)
  - [Class Inheritance (Subclasses)](#class-inheritance-subclasses)
  - [Polymorphism and Dynamic Dispatch](#polymorphism-and-dynamic-dispatch)
- [File I/O](#file-io)
- [Memory Management](#memory-management)
- [Error Handling & Safety](#error-handling--safety)
  - [Works-Otherwise Error Handling](#works-otherwise-error-handling)
- [Native Library Imports (FFI)](#native-library-imports-ffi)
- [Library Imports](#library-imports)
- [Program Termination](#program-termination)
- [Library Installation](#library-installation)
- [Syntax Highlighting](#syntax-highlighting)

## Running Leash

You can run leash files (`.lsh`) directly using the `run` command, or compile them to an executable using the `compile` command.

### Running Directly
To execute a Leash program without creating an output binary executable (and optionally pass command line arguments):
```bash
python3 -m leash.cli run program.lsh [arguments...]
```

### Compiling
Compiling will generate a native executable for your system.

```bash
# By default, compiles to a binary with the same name as the file (e.g., 'program')
python3 -m leash.cli compile program.lsh
./program

# You can also use the 'to' keyword to specify a custom output binary name
python3 -m leash.cli compile program.lsh to my_custom_binary
./my_custom_binary

# Compile to a dynamic library (.so)
python3 -m leash.cli compile program.lsh to-dynamic mylib

# Compile to a static library (.a)
python3 -m leash.cli compile program.lsh to-static mylib
```

### Native Library Compilation

Leash can compile programs into shared (`.so`) or static (`.a`) libraries for use by other programs or languages:

| Output Type | Flag | Output File |
|-------------|------|-------------|
| Executable | `to <name>` | `<name>` |
| Dynamic Library | `to-dynamic <name>` | `lib<name>.so` |
| Static Library | `to-static <name>` | `lib<name>.a` |

```bash
# Compile as dynamic library
leash compile math.lsh to-dynamic math
# Creates: libmath.so

# Compile as static library
leash compile utils.lsh to-static utils
# Creates: libutils.a
```

## Native Library Imports (FFI)

Leash supports importing functions from native libraries (`.a` or `.so` files) using the `@from` directive. This enables Foreign Function Interface (FFI) with C, C++, Rust, and other languages that compile to static or shared libraries.

```leash
@from("mylib.so") {
    fnc add(a int, b int) : int;
    fnc multiply(a int, b int) : int;
};

fnc main() : void {
    show(add(10, 20));        // 30
    show(multiply(5, 4));     // 20
}
```

### How It Works

The `@from` directive tells the compiler:
1. To declare functions, variables, structs, unions, enums, and type aliases as external
2. To link against the specified library during compilation

You can declare functions, variables, structs, unions, enums, and type aliases from native libraries:

```leash
@from("mylib.so") {
    fnc add(a int, b int) : int;
    my_global: int;
    def Point : struct {
        x: int;
        y: int;
    };
    def ErrorCode : union {
        code: int;
        message: string;
    };
    def Color : enum { RED, GREEN, BLUE };
    def MyInt : type int;
};
```

You can use `.a` (static) or `.so` (dynamic) library files:

```leash
// Link against a static library
@from("libmath.a") {
    fnc sin(x float) : float;
    fnc cos(x float) : float;
    PI: float;
};

// Link against a shared library
@from("/usr/lib/libcurl.so") {
    fnc curl_init() : int;
    fnc curl_setopt(handle int, option int, value string) : int;
};
```

### Creating Native Libraries

To use C functions in Leash, compile them to a library first:

```c
// math_utils.c
int add(int a, int b) {
    return a + b;
}
```

```bash
# Compile to static library
gcc -c math_utils.c -o math_utils.o
ar rcs libmath.a math_utils.o

# Or compile to shared library
gcc -shared -fPIC math_utils.c -o libmath.so
```

### Limitations

- Type signatures must match exactly between Leash and the native library
- The library must be available at link time
- Initializers are not supported for variable declarations (e.g., `x: int = 10;` is not allowed)
- Only basic struct/union/enum fields are supported (no nested anonymous structs)

## Library Imports

Leash supports importing definitions from other modules using the `use` statement. The syntax is:

```leash
use module_name::ItemName;
```

You can import multiple items from the same module:

```leash
use hash::Hash;
use colors::Color;
```

Or import all public items from a module using the wildcard `*`:

```leash
use mylib::*;
```

### Nested Paths

If your libraries are organized in subdirectories, you can use nested paths:

```leash
use math::operations::add;
use utils::string_helpers::capitalize;
```

This looks for files at `math/operations.lsh` or `utils/string_helpers.lsh` in your search paths.

When you import a module, all its public definitions (structs, classes, enums, functions, templates, etc.) become available in your file. The compiler searches for modules in two places:

1. The **local directory** of the file containing the `use` statement (including subdirectories)
2. The **global libraries directory** (`~/.leash/libs/`) - this is where you can install libraries to be available from anywhere

### Example

Given a file `hash.lsh`:

```leash
priv def T1 : template;
priv def T2 : template;

def Hash : class<T1, T2> {
    priv names:  vec<T1>;
    priv values: vec<T2>;
    pub  size:   int;

    static pub fnc new() : Hash {
        return Hash {};
    }

    pub fnc add(name T1, value T2) : int { ... }
    pub fnc get(name T1) : T2 { ... }
}
```

You can use it in your program:

```leash
use hash::Hash;

fnc main() : void {
    h: Hash<string, string> = Hash.new();
    h.add("key", "value");
    show(h.get("key"));
}
```

## Program Termination

The built-in `exit(exit_code)` function terminates the program immediately with the given exit code. The argument must be an integer.

```leash
fnc main() : void {
    if error_condition {
        exit(1);
    }
    exit(0);
}
```

## Library Installation

Leash provides a global library installation system to make your libraries available from any project. Use the `install` command to copy library files or directories into the global libs directory (`~/.leash/libs/`).

### Installing a Single File

```bash
leash install path/to/mylib.lsh
```

This copies `mylib.lsh` to `~/.leash/libs/mylib.lsh`. After installation, any program can import it using `use mylib::*;` or `use mylib::SpecificItem;`.

### Installing a Directory

You can also install an entire directory of library files:

```bash
leash install path/to/mylib_folder/
```

The **contents** of the directory (files and subdirectories) will be copied directly into `~/.leash/libs/`. The top-level folder name is not preserved.

For example, if you have:

```
mylib_folder/
├── hash.lsh
└── utils/
    └── string_helpers.lsh
```

After installation, `~/.leash/libs/` will contain:

```
~/.leash/libs/
├── hash.lsh
└── utils/
    └── string_helpers.lsh
```

You can then import using nested paths:

```leash
use hash::Hash;
use utils::string_helpers::SomeFunction;
```

### Notes

- If a file or directory with the same name already exists in the global libs directory, the installation will fail. Remove the existing library first if you want to replace it.
- The `install` command only copies files; it does not compile them.
- Once installed, libraries are automatically available to all your Leash programs via the `use` statement.

## Defining Variables

In Leash, variables must declare their type upon initialization.

```leash
a: int = 10;
b: string = "Hello World";
```

### Default Initialization

If you declare a variable without an assignment, Leash automatically initializes it to a default value (zero for numbers, empty strings, and empty vectors/arrays):

```leash
n: int;       // 0
s: string;    // ""
v: vec<int>;  // empty vector
```

## Immutable Variables

Leash supports immutable (constant) variables using the `imut` qualifier. Once assigned, an `imut` variable cannot be reassigned.

```leash
a: imut int = 10;
// a = 20; // ERROR: Cannot assign to immutable variable 'a'
show(a);
```

### Immutable Function Parameters

Function parameters can be marked `imut` to prevent modification inside the function body:

```leash
fnc add(a imut int, b imut int) : int {
    // a = 5; // ERROR: Cannot assign to immutable parameter 'a'
    return a + b;
}
```

### Infectious Immutability

Functions can return `imut` types. When an `imut` return value is assigned to a variable, that variable **automatically becomes immutable** — even if it wasn't originally declared as `imut`:

```leash
fnc imut_add(a imut int, b imut int) : imut int {
    return a + b;
}

fnc main() : void {
    c: int = imut_add(10, 20);
    // c = 50; // ERROR: 'c' is now immutable because it received an imut return value
}
```

This makes `imut` a powerful tool for enforcing data safety across function boundaries.

## Data Types

Leash supports rich generic and specific length types natively! 

### Primitive Types
- `int` and `uint`
- `float`
- `bool` (e.g., `true` / `false`)
- `string` and `char` (e.g., `'a'`)
- `void` (for null or empty values)

### Explicit Integer & Float Sizes
Leash integers and floats can specify an explicit bit width between `<` and `>` brackets to optimize memory and calculations. Maximum size is up to 512 bits:

```leash
maxInt: int<64> = 10;     // 64-bit integer
smallFl: float<16> = 1.0; // 16-bit float
pixel: uint<8> = 255;
```

## Operators

Leash supports a full suite of arithmetic, comparison, bitwise, and logical operators.

### Arithmetic Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `+` | Addition | `a + b` |
| `-` | Subtraction | `a - b` |
| `*` | Multiplication | `a * b` |
| `/` | Division | `a / b` |
| `%` | Modulo | `a % b` |

### Comparison Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `==` | Equal to | `a == b` |
| `!=` | Not equal to | `a != b` |
| `<` | Less than | `a < b` |
| `>` | Greater than | `a > b` |
| `<=` | Less than or equal | `a <= b` |
| `>=` | Greater than or equal | `a >= b` |

### Bitwise Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `&` | Bitwise AND | `a & b` |
| `\|` | Bitwise OR | `a \| b` |
| `^` | Bitwise XOR | `a ^ b` |
| `~` | Bitwise NOT | `~a` |
| `<<` | Left shift | `a << b` |
| `>>` | Right shift | `a >> b` |

### Logical Operators
| Operator | Description | Example |
|----------|-------------|---------|
| `&&` | Logical AND | `a && b` |
| `\|\|` | Logical OR | `a \|\| b` |
| `!` | Logical NOT | `!a` |

*Note: Logical `&&` and `||` use short-circuit evaluation — the right operand is only evaluated if necessary.*

### Ternary Operator

Leash supports the ternary conditional operator `? :` for concise conditional expressions:

```leash
fnc main() : void {
    a: int = 10 == 10 ? 10 : 20;
    show(a); // 10

    b: string = "Hello" != "World!" ? "Hello" : "World!";
    show(b); // "Hello"
}
```

The ternary operator evaluates the condition and returns the value of the matching branch. Both branches must have compatible types.

```leash
fnc main() : void {
    x: int = 5;
    result: string = x > 3 ? "big" : "small";
    show(result); // "big"
}
```

```leash
fnc main() : void {
    a: int = 5;
    b: int = 3;

    show(a + b);    // 8
    show(a % b);    // 2
    show(a & b);    // 1
    show(a << 1);   // 10
    show(a > b);    // 1 (true)
    show(!0);       // 1 (true)
}
```

## Functions

Functions are defined with the `fnc` keyword, followed by the argument list and its evaluated return type. The entrypoint to any compiled leash application is `main() : void`.

```leash
fnc add(a int, b int) : int {
    return a + b;
}

fnc main() : void {
    result: int = add(10, 20);
    show("Result: ", result);
}
```

### Command Line Arguments

To accept command line arguments, you can declare `main` with an `args string[]` parameter:

```leash
fnc main(args string[]) : void {
    show("Received ", args.size, " arguments!");
    foreach i, arg in<array> args {
        show(i, ": ", arg);
    }
}
```

*Note: The `show()` function is built-in and makes printing multiple values to console very easy!*

## Global Variables

Leash supports module-level variable declarations. These variables are accessible from any function within the same module. By default, top-level variables are public. You can also explicitly mark them as `pub` or `priv`.

```leash
// Public global variable (default)
a: int = 10;

// Explicitly public
pub b: string = "hello";

// Private global variable (only accessible within this module)
priv secret: int = 42;

fnc main() : void {
    show(a);      // 10
    show(b);      // hello
    show(secret); // 42 (if in same module)
}
```

Global variables can be of any type, including structs, classes, vectors, arrays, unions, and type aliases. They can also have initializers, which are evaluated at program startup (before `main` runs). If a global variable has no initializer, it is zero-initialized by default.

### Visibility

- `pub`: The variable is publicly accessible from other modules (future feature). Currently, all modules are compiled together, so this has no effect but is allowed for forward compatibility.
- `priv`: The variable is only accessible within the defining module. This is useful for hiding implementation details when modules are introduced.

Note: The `pub`/`priv` modifiers are optional; omitting them defaults to `pub`.

## Control Flow

### Branching
Use `if`, `also` (acts like `else if`), and `else`:

```leash
if a < b {
    show("a is less than b");
} also a > b {
    show("a is greater than b");
} else {
    show("a is equal to b");
}
```

### Loops

Leash comes packed with many loops built-in (`for`, `while`, and `do-while`):

```leash
// while
i: int = 0;
while i < 10 {
    i = i + 1;
}

// for
for j: int = 0; j < 10; j = j + 1 {
    show(j);
}

// do-while
k: int = 0;
do {
    k = k + 1;
} while k < 10;
```

#### `stop` and `continue`

Inside loops, you can use `stop` to exit the loop early (similar to `break` in other languages) and `continue` to skip to the next iteration immediately.

```leash
fnc main() : void {
    i: int = 0;
    while true {
        show(i);
        if i >= 10 {
            stop; // exit the loop
        }
        continue; // skip the rest of this iteration and jump to condition/update
        show("This will not be printed.");
    }
}
```

- `stop;` terminates the innermost loop and transfers control to the code after the loop.
- `continue;` skips the remaining statements in the current iteration and jumps to the loop's continuation point (the condition check for `while`/`do-while`, or the update step for `for`).

Both `stop` and `continue` can be used in `while`, `for`, `do-while`, and `foreach` loops (including `foreach` over arrays, strings, and vectors). They are not supported in `foreach` over structs because that loop is unrolled at compile time.

## Input Handling

Leash provides a built-in `get()` function to read interactive user input from the console.

```leash
fnc main() : void {
    // You can provide an optional string prompt
    name: string = get("What is your name? ");
    show("Hello, ", name, "!");

    // Or call it without arguments
    show("Enter something else: ");
    val: string = get();
    show("You entered: ", val);
}
```

The `get()` function automatically allocates memory for the input string and ensures it is managed by the SAMM system, so you don't have to worry about buffer overflows or manual `free()` calls.

## Random Numbers

Leash provides built-in functions for generating random numbers and making random choices:

- `rand(min, max)` - Returns a random integer between `min` and `max` (inclusive)
- `randf(min, max)` - Returns a random floating-point number between `min` and `max`
- `seed(value)` - Sets the random number generator seed. If not called explicitly, the RNG is automatically seeded with the current time.
- `choose(str1, str2, ...)` - Randomly selects and returns one of the provided string arguments

Example:

```leash
fnc main() : void {
    seed(42);  // Optional: set a specific seed for deterministic results

    // Random integer between -10 and 10
    num: int = rand(-10, 10);

    // Random float between 0 and 2
    f: float = randf(0, 2);

    // Randomly choose a name
    name: string = choose("Alice", "Bob", "Charlie");

    show(name, " got ", num, " and ", f);
}
```

## Time and Delays

Leash includes functions for measuring elapsed time and introducing delays:

- `wait(seconds)` - Pauses program execution for the specified number of seconds (can be a float for sub-second precision)
- `timepass()` - Returns the elapsed time in seconds (as a float) since the program started

Example:

```leash
fnc main() : void {
    show("Starting...");
    wait(1.5);  // Wait 1.5 seconds

    elapsed: float = timepass();
    show("Elapsed time: ", elapsed, " seconds");
}
```

## Arrays

Data is sequentially packed into memory and can be evaluated or constructed easily:
```leash
// Construct inline lists
a: int<64>[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};

// Query the size of an array dynamically
len: int = a.size;

// Iterate across items automatically using `in<array>`
foreach index, value in<array> a {
    show(index, ": ", value);
}
```

## Vectors

Vectors are dynamic arrays that can grow or shrink in size. They are declared using the `vec<T>` syntax.

```leash
v: vec<string>;
v.pushb("first");  // push to back
v.pushb("second");
v.pushf("start");  // push to front

show("Size: ", v.size);   // .size is a property (no parentheses)
show("First: ", v.get(0));

v.set(1, "middle");
v.insert(1, "extra");

foreach i, s in<vector> v {
    show(i, ": ", s);
}

v.clear();
```

### Vector Properties

| Property | Description |
|----------|-------------|
| `.size` | Return the current number of elements (as an `int`) |

### Vector Methods

| Method | Description |
|--------|-------------|
| `.pushb(val)` | Push element to the back |
| `.popb()` | Remove and return the last element |
| `.pushf(val)` | Push element to the front |
| `.popf()` | Remove and return the first element |
| `.size()` | Return the current number of elements (same as `.size` property) |
| `.get(idx)` | Get the element at the specified index |
| `.set(idx, val)` | Set the element at the specified index |
| `.insert(idx, val)` | Insert an element at the specified index |
| `.clear()` | Remove all elements from the vector |

## Structs

Structs group different variables together. You can declare structs at the top-level using the `def` keyword:

```leash
def Point : struct {
    x: int;
    y: int;
};

fnc main() : void {
    p: Point = Point {x: 10, y: 20};
    
    // Member access:
    p.x = 15;
    
    // Leash supports iterating directly across members of a struct natively!
    foreach name, value in<struct> p {
        show(name, ": ", value); // prints x: 15, y: 20
    }
}
```

## Pointers

Leash supports pointers for low-level memory operations and efficient parameter passing. Pointers use the `*` prefix for raw pointers and `&` for safe references.

### Basic Pointer Operations

```leash
a: int = 10;
p: *int = &a;       // p holds the address of a

show("p: ", p);     // prints the pointer address
show("*p: ", *p);   // dereferences p, prints 10

*p = 20;            // modifies a through the pointer
show("a: ", a);     // prints 20
```

### Pointer Arithmetic

Pointer arithmetic is supported on `*char` pointers for working with raw character data:

```leash
c_str: *char = cstr("Hello");
show(*(c_str + 1)); // prints 'e' (second character)
show(*(c_str + 4)); // prints 'o' (fifth character)
```

### Safe References (`&`)

Leash recommends using `&` (references) instead of `*` (raw pointers) for function parameters when possible. References provide the same efficiency with better safety guarantees:

```leash
// Using a reference - recommended for simple modifications
fnc increment(v &int) : void {
    v = v + 1;
}

// Using a raw pointer - for low-level operations
fnc decrement(v *int) : void {
    *v = *v - 1;
}

fnc main() : void {
    a: int = 10;
    
    increment(a);    // passes address of 'a' automatically
    show("a: ", a);  // prints 11
    
    p: *int = &a;
    decrement(p);    // passes the pointer directly
    show("a: ", a);  // prints 10
}
```

### Pointer Member Access (`->`)

Use the `->` operator to access struct members through a pointer:

```leash
def Point : struct {
    x: int;
    y: int;
};

fnc print_point(p *Point) : void {
    show("x: ", p->x);  // arrow operator for pointer access
    show("y: ", p->y);
}

fnc main() : void {
    pt: Point = Point {x: 10, y: 20};
    print_point(&pt);
}
```

### Pointer Types

| Syntax | Description | Example |
|--------|-------------|---------|
| `*T` | Raw pointer to type T | `*int`, `*Point`, `*char` |
| `&T` | Safe reference to type T | `&int`, `&Point` |

*Note: Leash uses the Boehm Garbage Collector for memory management, so pointers to GC-allocated objects remain valid throughout the program's lifetime.*

## Unions

Unions allow a variable to store different types of data at different times. Leash implements **Tagged Unions**, meaning the language tracks which type is currently active at runtime for safety.

```leash
def Value : union {
    i: int;
    f: float;
    s: string;
};

fnc main() : void {
    v: Value = 42;         // Auto-detects 'i' variant
    show("v: ", v.cur);    // Smart-prints active variant
    
    v = "hello";           // Changes active variant to 's'
    show("v: ", v.cur);
    
    // Explicit variant access:
    v.f = 3.14;
    show("v.f: ", v.f);
}
```

*Note: Accessing an inactive variant (e.g., calling `v.i` when `v.f` is active) will trigger a **Runtime Safety Error** to prevent crashes or memory corruption.*

## Enums

Enums allow you to define a set of named constants. In Leash, enums are represented as integers under the hood but provide a `.name` property to access their string representation.

```leash
def Color : enum {
    RED,
    GREEN,
    BLUE
};

fnc main() : void {
    c: Color = Color::RED;
    show("c = ", c);         // Prints the integer value (0)
    show("c.name = ", c.name); // Prints the member name ("RED")

    // Enums are compatible with int
    val: int = c;
    
    colors: Color[3] = {Color::RED, Color::GREEN, Color::BLUE};
    foreach i, v in<array> colors {
        show(i, ": ", v.name);
    }
}
```

## Type Aliases

You can define custom names for existing types to improve readability or create abstraction layers:

```leash
def MyInt : type int;
def Pixel : type uint<8>;

a: MyInt = 10;
p: Pixel = 255;
```

## Generic Types

Leash supports generic programming through templates. Templates allow you to define functions and classes that work with multiple types, providing type safety and code reuse.

### Defining Template Parameters

Use the `template` keyword to declare a type parameter:

```leash
def T : template;
```

Template parameters are conventionally named with a single uppercase letter (e.g., `T`, `U`, `V`) or with descriptive names like `KeyType`, `ValueType`.

### Generic Functions

You can use template parameters as types in function signatures:

```leash
def T : template;

fnc identity(x T) : T {
    return x;
}

fnc main() : void {
    show(identity<int>(42));        // 42
    show(identity<string>("hi"));  // "hi"
}
```

When calling a generic function, you must specify the concrete type inside angle brackets after the function name: `identity<int>(42)`.

### Generic Classes

Classes can also have multiple template parameters:

```leash
def K : template;
def V : template;

def Pair : class<K, V> {
    first: K;
    second: V;

    pub fnc get_first() : K {
        return this.first;
    }

    pub fnc get_second() : V {
        return this.second;
    }
}

fnc main() : void {
    p: Pair<int, string> = Pair<int, string> {first: 1, second: "one"};
    show(p.get_first());   // 1
    show(p.get_second()); // "one"
}
```

### Using `nil` with Generic Types

The `nil` value can be used with any type, including generic type parameters. This is useful for optional return values:

```leash
def T : template;

fnc find(key string, map Hash<string, T>) : T {
    // ... search logic
    return nil; // nil is valid for any T
}
```

Generic types can be used anywhere a regular type is expected, including as function parameters, return types, struct fields, and class members.

## Multi-Type Functions

Leash supports functions that can accept and return multiple types using the **multi-type syntax**. This allows you to write generic-like functions without explicitly declaring template parameters.

### Syntax

Use square brackets `[type1, type2, ...]` to specify multiple allowed types for parameters and return values:

```leash
fnc add(a [int, float], b [int, float]) : [int, float] {
    return a + b;
}

fnc main() : void {
    i: int = add(10, 20);
    f: float = add(10.5, 20.5);
    show(i, " ", f);  // prints: 30 31.0
}
```

### How It Works

When you call a multi-type function, Leash automatically instantiates a specialized version based on the concrete argument types:

- `add(10, 20)` creates an `int` version of the function
- `add(10.5, 20.5)` creates a `float` version of the function

Each instantiation is type-checked and compiled separately, ensuring type safety while maintaining the flexibility of generic functions.

### Use Cases

Multi-type functions are useful when:

- You want simple overloading without explicit template syntax
- A function should work with multiple related types (e.g., `int` and `float`)
- You need the compiler to automatically generate the right function based on usage

This is similar to how C++ templates work, but with Leash's type checker automatically handling the specialization.

## Type Casting

Leash supports explicit type casting using the `(type)expression` syntax to convert between incompatible types.

```leash
f: float = 3.99;
i: int = (int)f;    // Evaluates to 3 (truncation)

c: char = 'A';
u: uint = (uint)c;  // Evaluates to 65 (ASCII)

// Pointer casts (e.g., bitcasting internal pointers)
arr: int<32>[] = {1, 2, 3};
arr64: int<64>[] = (int<64>[])arr;
```

## Type Conversions

For dynamic conversions (such as parsing a string into a number or formatting a number as a string), Leash provides built-in conversion functions.

### String to Number
Use `toint(target_type, value)` and `tofloat(target_type, value)` to parse strings.

```leash
a: int = toint(int, "123");
b: float<32> = tofloat(float<32>, "3.14159");

// These functions also handle numeric-to-numeric conversions:
c: int = toint(int, 3.99); // Evaluates to 3
```

### Number to String
Use `tostring(value)` to format a numeric value into a string.

```leash
s1: string = tostring(123);    // "123"
s2: string = tostring(3.14);   // "3.140000"
```

Just like `get()`, `tostring()` returns a managed string that will be automatically cleaned up by the SAMM system.

## Strings

String evaluation is supported natively in the language!
```leash
a: string = "hello";
b: string = "world";

// Concatenation
c: string = a + b; 

// Removal of exact substring (removes first occurrence)
removed: string = c - b; // evaluates to 'hello'

// Equality testing
if a != b {
    show("Not equal!");
}

// Character Indexing & Sizes
charVar: char = c[1];
strLen: int = c.size; // evaluates length!
```

### Leash Strings vs Char Arrays

Leash natively supports both automatically managed `string` types and lower-level `char[]` slices. You can easily convert and concatenate between them using the built-in `lstr()` and `cstr()` functions.

```leash
// cstr() converts a Leash string to a char[]
str: char[] = cstr("Hello World");
show(str);

// lstr() converts a char[] to an implicitly managed Leash string
s: string = lstr(str);

// Iteration over strings:
foreach i, c in<string> s {
    show(i, ": ", c);
}

// Standard string concatenation automatically handles mixed conversions:
s = s + '!';            // concatenate with character
s = s + cstr(" :D");    // concatenate with char[]
show(s);
```

## Classes

Leash supports Object-Oriented Programming through classes. Classes combine data (fields) and behavior (methods) into a single unit.

```leash
def Person : class {
    // Fields with visibility modifiers
    pub name: string;
    priv age: int;

    // Static conversion/factory method
    static pub fnc new(name string, age int) : Person {
        return Person { name: name, age: age };
    }

    // Instance method using the 'this' keyword
    pub fnc greet() : string {
        return "Hello, my name is " + this.name + " and I am " + this.age + " years old!";
    }
}

fnc main() : void {
    // Call static method
    p: Person = Person.new("John", 30);
    
    // Call instance method
    show(p.greet());
    
    // Access public fields
    p.name = "Jane";
    show(p.name);
}
```

### Key Features:
- **Visibility**: Use `pub` (default) for members accessible from anywhere, and `priv` for members only accessible within the class methods.
- **Methods**: Functions defined inside a class. Non-static methods automatically receive an implicit `this` pointer to the current instance.
- **Static vs Instance**: Methods marked with the `static` keyword are called on the class name (e.g., `Person.new()`), while non-static methods are called on a variable instance (e.g., `p.greet()`).
- **The `this` Keyword**: Automatically available inside instance methods to access fields and other methods of the current object.

### Class Inheritance (Subclasses)

Leash supports class inheritance, allowing you to create subclasses that inherit fields and methods from a parent class. Subclasses can override parent methods to provide specialized behavior.

```leash
def Animal : class {
    pub name: string;

    static pub fnc new(name string) : Animal {
        return Animal { name: name };
    }

    pub fnc talk() : void {
        show("No sounds");
    }
}

// Define a subclass using class(Parent)
def Dog : class(Animal) {
    // Dog inherits 'name' field from Animal

    static pub fnc new(name string) : Dog(Animal) {
        return Dog { name: name };
    }

    // Override the parent's talk() method
    pub fnc talk() : void {
        show("Bark Bark!");
    }
}

def Cat : class(Animal) {
    static pub fnc new(name string) : Cat(Animal) {
        return Cat { name: name };
    }

    pub fnc talk() : void {
        show("Meow!");
    }
}
```

### Polymorphism and Dynamic Dispatch

Leash supports polymorphism - a child class can be used wherever its parent class is expected. Method calls are dispatched dynamically at runtime, so the correct overridden method is called based on the actual object type.

```leash
fnc main() : void {
    a: Animal = Animal.new("bob");
    d: Dog    = Dog.new("Jake");
    c: Cat    = Cat.new("Justaname");

    a.talk();  // "No sounds"
    d.talk();  // "Bark Bark!"
    c.talk();  // "Meow!"

    // Upcasting: Child can be assigned to parent variable
    a2: Animal = Dog.new("Jakeee");

    // Dynamic dispatch: calls Dog.talk() even though a2 is typed as Animal
    a2.talk(); // "Bark Bark!"

    // Downcasting: Explicit cast from parent to child
    d = (Dog)a;
}
```

### Inheritance Features:
- **Subclass Syntax**: Use `def Child : class(Parent)` to create a subclass
- **Field Inheritance**: Child classes automatically inherit all fields from the parent
- **Method Inheritance**: Child classes inherit all methods from the parent
- **Method Overriding**: Child classes can override parent methods by redefining them
- **Imut Methods**: Use `imut` before `fnc` to make a method non-overridable by subclasses
- **Upcasting**: Implicit conversion from child to parent type (e.g., `Animal a = Dog.new()`)
- **Downcasting**: Explicit cast from parent to child using `(ChildType)expr`
- **Dynamic Dispatch**: Method calls use vtables for runtime polymorphism

### Preventing Method Overriding with `imut`

If you want to make a method non-modifiable by subclasses, use `imut` before `fnc`:

```leash
def Animal : class {
    pub name: string;

    static pub fnc new(name string) : Animal {
        return Animal { name: name };
    }

    // imut makes this method non-overridable
    pub imut fnc talk() : void {
        show("No sounds");
    }
}

def Dog : class(Animal) {
    // ERROR: Cannot override imut method 'talk' from parent class 'Animal'
    pub fnc talk() : void {
        show("Bark Bark!"); // Compilation error!
    }
}
```

This is useful when you want to ensure certain methods always behave the same way across all subclasses, maintaining consistent behavior for critical operations.

## File I/O

Leash provides a built-in `File` class for reading and writing files. The `File` class is a native class (not a primitive type) that wraps the C standard library's `FILE*` operations.

### Opening and Closing Files

Use `File.open()` to open a file and `.close()` to close it:

```leash
fnc main() : void {
    // Open a file for writing (creates or truncates)
    file: File = File.open("output.txt", "w");
    file.write("Hello, World!");
    file.close();

    // Open a file for reading
    reader: File = File.open("output.txt", "r");
    content: string = reader.read();
    show(content);  // "Hello, World!"
    reader.close();
}
```

### File Modes

| Mode | Description |
|------|-------------|
| `"r"` | Open for reading (file must exist) |
| `"w"` | Open for writing (creates or truncates) |
| `"a"` | Open for appending (creates if doesn't exist) |
| `"r+"` | Open for reading and writing (file must exist) |

### Static Methods

These methods are called on the `File` class directly:

| Method | Description | Returns |
|--------|-------------|---------|
| `File.open(filename, mode)` | Open a file with the specified mode | `File` (or `nil` on error) |
| `File.rename(oldname, newname)` | Rename a file | `int` (0 on success) |
| `File.delete(filename)` | Delete a file | `int` (0 on success) |

### Instance Methods

These methods are called on a `File` object:

| Method | Description | Returns |
|--------|-------------|---------|
| `.read()` | Read entire file content | `string` |
| `.write(text)` | Write text to file | `int` (0 on success) |
| `.close()` | Close the file | `int` (0 on success) |
| `.readln()` | Read one line (strips newline) | `string` |
| `.readb()` | Read entire file as bytes | `char[]` |
| `.writeb(bytes)` | Write bytes to file | `int` (0 on success) |
| `.readlnb()` | Read one line as bytes | `char[]` |
| `.replace(old, new)` | Replace first occurrence | `int` (1 if found, 0 otherwise) |
| `.replaceall(old, new)` | Replace all occurrences | `int` (count of replacements) |
| `.rewind()` | Reset file position to start | `void` |

### Reading Line by Line

```leash
fnc main() : void {
    file: File = File.open("data.txt", "w");
    file.write("Line 1\nLine 2\nLine 3");
    file.close();

    reader: File = File.open("data.txt", "r");
    
    line1: string = reader.readln();  // "Line 1"
    line2: string = reader.readln();  // "Line 2"
    line3: string = reader.readln();  // "Line 3"
    
    show(line1);
    show(line2);
    show(line3);
    
    reader.close();
}
```

### Byte Operations

For binary data, use `readb()`, `writeb()`, and `readlnb()`:

```leash
fnc main() : void {
    // Write binary data
    file: File = File.open("data.bin", "wb");
    data: char[] = cstr("Binary content");
    file.writeb(data);
    file.close();

    // Read binary data
    reader: File = File.open("data.bin", "rb");
    bytes: char[] = reader.readb();
    show("Read ", bytes.size, " bytes");
    reader.close();
}
```

### String Replacement in Files

The `replace()` and `replaceall()` methods allow in-place string replacement:

```leash
fnc main() : void {
    file: File = File.open("template.txt", "w");
    file.write("Hello {name}! Welcome to {place}.");
    file.close();

    // Open for reading and writing
    editor: File = File.open("template.txt", "r+");
    
    // Replace first occurrence
    editor.replace("{name}", "World");
    
    // Replace all remaining occurrences
    editor.replaceall("{place}", "Leash");
    
    editor.rewind();
    show(editor.read());  // "Hello World! Welcome to Leash."
    
    editor.close();
}
```

### Appending to Files

```leash
fnc main() : void {
    // Create initial file
    file: File = File.open("log.txt", "w");
    file.write("Log started\n");
    file.close();

    // Append more content
    logger: File = File.open("log.txt", "a");
    logger.write("Entry 1\n");
    logger.write("Entry 2\n");
    logger.close();

    // Read all content
    reader: File = File.open("log.txt", "r");
    show(reader.read());
    reader.close();
}
```

## Memory Management

Leash uses a high-performance **Boehm Garbage Collector** to manage memory automatically. This means you can allocate objects (strings, vectors, arrays, etc.) without worrying about manual `free()` calls or scope limitations.

When you create a `vec<string>`, both the vector's internal buffer and the strings themselves are managed by the GC. When they are no longer reachable by your program, the memory is automatically reclaimed.

This ensures that operations like string concatenation (`a + b`) or vector manipulation do not leak memory, even when performed inside complex loops or returned across many function calls.

*Note: Leash links against `libgc` at compile-time.*

## Error Handling & Safety

Leash prioritizes developer experience with helpful error reporting and safety features:

- **Static Type Checker**: The compiler validates types before generating code, catching undefined variables, incompatible assignments, and member access errors.
- **Smart Error Tips**: When a syntax error occurs, Leash provides actionable tips (e.g., suggesting a missing semicolon or parenthetical).
- **Runtime Union Checks**: Accessing union members is checked at runtime to ensure the correct "tag" is active, avoiding memory-unsafe operations common in C.

### Works-Otherwise Error Handling

Leash provides a `works...otherwise` construct for catching and handling errors. Unlike traditional try-catch, it can catch **any** error (undefined variables, type errors, etc.):

```leash
fnc main() : void {
    works {
        a: int = unknown_var;  // This would normally cause an error
    } otherwise err {
        show("Error caught: ", err);  // Prints: "Error caught: Undefined variable: 'unknown_var'"
    }
    show("Program continues...");
}
```

**How it works:**
- The `works` block attempts to execute its statements
- If any error occurs (undefined variable, type mismatch, etc.), instead of crashing, control jumps to the `otherwise` block
- The error variable (`err` in the example) contains a string describing what went wrong
- After the `otherwise` block completes, the program continues normally

```leash
fnc main() : void {
    works {
        x: int = 10;
        show("Works block executed");
    } otherwise err {
        show("Caught error: ", err);
    }
    show("After works");  // This runs because no error occurred
}
```

This feature is useful for graceful error handling, fallback logic, and recovering from unexpected conditions.

## Syntax Highlighting

Leash provides native syntax highlighting for popular editors. You can find the files in the `syntax_highlighters/` directory.

### Vim / Neovim

1. Copy the `leash.vim` file to your `~/.vim/syntax/` (Vim) or `~/.config/nvim/syntax/` (Neovim) directory:
   ```bash
   mkdir -p ~/.vim/syntax
   cp syntax_highlighters/leash.vim ~/.vim/syntax/
   ```
2. Tell Vim to use it for `.lsh` files by adding this to your `.vimrc` or `init.vim`:
   ```vim
   au BufRead,BufNewFile *.lsh set filetype=leash
   ```

### Emacs

1. Move `leash-mode.el` to your load path (e.g., `~/.emacs.d/lisp/`):
   ```bash
   mkdir -p ~/.emacs.d/lisp
   cp syntax_highlighters/leash-mode.el ~/.emacs.d/lisp/
   ```
2. Add the following to your `init.el` or `.emacs` file:
   ```elisp
   (add-to-list 'load-path "~/.emacs.d/lisp/")
   (require 'leash-mode)
   ```
