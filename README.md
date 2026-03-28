# Leash Programming Language

Leash is a strongly-typed, modern compiled programming language built on top of LLVM. It features an intuitive syntax and is designed to handle common tasks with native performance.

## Table of Contents
- [Running Leash](#running-leash)
- [Defining Variables](#defining-variables)
- [Immutable Variables](#immutable-variables)
- [Data Types](#data-types)
- [Operators](#operators)
- [Functions](#functions)
- [Control Flow](#control-flow)
- [Arrays](#arrays)
- [Structs](#structs)
- [Unions](#unions)
- [Enums](#enums)
- [Type Aliases](#type-aliases)
- [Type Casting](#type-casting)
- [Strings](#strings)
- [Memory Management](#memory-management)
- [Error Handling & Safety](#error-handling--safety)
- [Syntax Highlighting](#syntax-highlighting)

## Running Leash

You can run leash files (`.lsh`) directly using the `run` command, or compile them to an executable using the `compile` command.

### Running Directly
To execute a Leash program without creating an output binary executable:
```bash
python3 -m leash.cli run program.lsh
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
```

## Defining Variables

In Leash, variables must declare their type upon initialization.

```leash
a: int = 10;
b: string = "Hello World";
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

*Note: The `show()` function is built-in and makes printing multiple values to console very easy!*

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

## Arrays

Data is sequentially packed into memory and can be evaluated or constructed easily:
```leash
// Construct inline lists
a: int<64>[10] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};

// Iterate across items automatically using `in<array>`
foreach index, value in<array> a {
    show(index, ": ", value);
}
```

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

## Memory Management

Leash features **Scope-based Automatic Memory Management (SAMM)**. This means the compiler automatically tracks heap allocations (like those generated by string operations) and frees them when they are no longer needed.

When a function finishes its execution, the compiler automatically:
1.  Identifies all memory allocated during that function's scope.
2.  `free`s every memory address that is not being returned to the caller.

This ensures that operations like string concatenation (`a + b`) do not leak memory, even when performed inside loops, without requiring any manual `free()` calls from the developer.

## Error Handling & Safety

Leash prioritizes developer experience with helpful error reporting and safety features:

- **Static Type Checker**: The compiler validates types before generating code, catching undefined variables, incompatible assignments, and member access errors.
- **Smart Error Tips**: When a syntax error occurs, Leash provides actionable tips (e.g., suggesting a missing semicolon or parenthetical).
- **Runtime Union Checks**: Accessing union members is checked at runtime to ensure the correct "tag" is active, avoiding memory-unsafe operations common in C.

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
