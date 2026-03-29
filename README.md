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
- [Input Handling](#input-handling)
- [Arrays](#arrays)
- [Structs](#structs)
- [Unions](#unions)
- [Enums](#enums)
- [Type Aliases](#type-aliases)
- [Type Casting](#type-casting)
- [Type Conversions](#type-conversions)
- [Strings](#strings)
- [Classes](#classes)
- [Memory Management](#memory-management)
- [Error Handling & Safety](#error-handling--safety)
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
```

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

show("Size: ", v.size());
show("First: ", v.get(0));

v.set(1, "middle");
v.insert(1, "extra");

foreach i, s in<vector> v {
    show(i, ": ", s);
}

v.clear();
```

### Vector Methods

| Method | Description |
|--------|-------------|
| `.pushb(val)` | Push element to the back |
| `.popb()` | Remove and return the last element |
| `.pushf(val)` | Push element to the front |
| `.popf()` | Remove and return the first element |
| `.size()` | Return the current number of elements |
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
    pub fnc new(name string, age int) : Person {
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
- **Methods**: Functions defined inside a class. If a method is not a "factory" (like `new`), it automatically receives an implicit `this` pointer to the current instance.
- **Static vs Instance**: Methods called on the class name (e.g., `Person.new()`) are treated as static, while those called on a variable (e.g., `p.greet()`) are instance methods.
- **The `this` Keyword**: Automatically available inside instance methods to access fields and other methods of the current object.

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
