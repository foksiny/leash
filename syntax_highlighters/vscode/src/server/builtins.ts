import { LshSymbol, ParamInfo } from './types';

export interface BuiltinDoc {
    sig: string;
    desc: string;
    detail?: string;
}

export const BUILTIN_DOCS: Record<string, BuiltinDoc> = {
    'show': { sig: 'show(...args)', desc: 'Prints arguments to console with spaces and a newline.' },
    'showb': { sig: 'showb(...args)', desc: 'Prints arguments to console buffer without spaces/newlines.' },
    'get': { sig: 'get(prompt?: string) : string', desc: 'Reads a line of user input from the console.' },
    'keyget': { sig: 'keyget() : char', desc: 'Reads a single key press immediately.' },
    'size': { sig: 'size(collection) : int', desc: 'Returns the number of elements in an array, vector, or string.' },
    'pushb': { sig: 'pushb(val: T)', desc: 'Appends an element to the back of a vector.' },
    'popb': { sig: 'popb() : T', desc: 'Removes and returns the last element of a vector.' },
    'pushf': { sig: 'pushf(val: T)', desc: 'Appends an element to the front of a vector.' },
    'popf': { sig: 'popf() : T', desc: 'Removes and returns the first element of a vector.' },
    'insert': { sig: 'insert(idx: int, val: T)', desc: 'Inserts an element at a specific index, shifting subsequent elements.' },
    'insertv': { sig: 'insertv(pos: int, other: vec<T>)', desc: 'Inserts all elements from another vector at position `pos`, shifting existing elements right.' },
    'inserta': { sig: 'inserta(pos: int, arr: T[])', desc: 'Inserts all elements from an array or slice at position `pos`, shifting existing elements right.' },
    'clear': { sig: 'clear()', desc: 'Removes all elements from a vector or hash table.' },
    'remove': { sig: 'remove(idx: int)', desc: 'Removes an element at a specific index, shifting subsequent elements.' },
    'isin': { sig: 'isin(val: T) : bool', desc: 'Checks if a value exists in a vector or key in a hash table.' },
    'rand': { sig: 'rand(min: int, max: int) : int', desc: 'Returns a random integer between min and max (inclusive).' },
    'randf': { sig: 'randf(min: float, max: float) : float', desc: 'Returns a random float between min and max.' },
    'seed': { sig: 'seed(val: int)', desc: 'Sets the random number generator seed.' },
    'choose': { sig: 'choose(...args: string) : string', desc: 'Randomly selects one of the provided strings.' },
    'wait': { sig: 'wait(seconds: float)', desc: 'Pauses execution for the specified time in seconds.' },
    'timepass': { sig: 'timepass() : float', desc: 'Returns elapsed time in seconds since program start.' },
    'exit': { sig: 'exit(code: int)', desc: 'Terminates the program immediately with an exit code.' },
    'exec': { sig: 'exec(cmd: string, mode?: string) : string', desc: 'Executes a shell command and returns output.', detail: 'Modes: nil (output), "wait", "silent", "code" (exit code).' },
    'toint': { sig: 'toint(val) : int', desc: 'Converts a value to an integer.', detail: 'Also accepts `toint(TargetType, value)` for width/signedness conversion.' },
    'tofloat': { sig: 'tofloat(val) : float', desc: 'Converts a value to a float.', detail: 'Also accepts `tofloat(TargetType, value)`.' },
    'tounion': { sig: 'tounion(UnionType, value) : UnionType', desc: 'Casts a value into a union type variant.' },
    'scast': { sig: 'scast(TargetType, value) : TargetType', desc: 'Highly safe cast between any two types. Aborts at runtime if the conversion would lose information (out-of-range, signedness, fractional/NaN to int, float precision, or malformed number strings).', detail: 'Checks are skipped inside unsafe functions.' },
    'tostring': { sig: 'tostring(val) : string', desc: 'Converts a numeric value to a string.' },
    'cstr': { sig: 'cstr(s: string) : *char', desc: 'Converts a Leash string to a C-style null-terminated char pointer.' },
    'lstr': { sig: 'lstr(c: *char) : string', desc: 'Converts a C-style char pointer to a Leash string.' },
    'sizeof': { sig: 'sizeof(type_or_expr) : int', desc: 'Returns the size in bytes of a type or expression result.' },
    'typeof': { sig: 'typeof(expr) : string', desc: 'Returns the type name of an expression as a string.' },
    'set': { sig: 'set(idx: int, val: T)', desc: 'Sets an element at a specific index in a vector.' },
    'extend': { sig: 'extend(arr: T[])', desc: 'Appends all elements from an array/slice to a vector.' },
    'extendv': { sig: 'extendv(other: vec<T>)', desc: 'Appends all elements from another vector.' },
    'normescape': { sig: 'normescape(s: string) : string', desc: 'Converts escape sequences in a string to actual characters.' },
    'inttobytes': { sig: 'inttobytes(size: int, value: int) : char[]', desc: 'Converts an integer to a byte array.' },
    'bytestoint': { sig: 'bytestoint(size: int, bytes: char[]) : int', desc: 'Converts a byte array back to an integer.' },
    'floattobytes': { sig: 'floattobytes(size: int, value: float) : char[]', desc: 'Converts a float to a byte array.' },
    'bytestofloat': { sig: 'bytestofloat(size: int, bytes: char[]) : float', desc: 'Converts a byte array back to a float.' },
    'printf': { sig: 'printf(format: string, ...args)', desc: 'Formats and prints text with %-placeholders (like C printf).', detail: 'Placeholders: %s string, %d int, %f float, %c char, %b bool, %x hex.' },
    'println': { sig: 'println(...args)', desc: 'Prints arguments followed by a newline.' },
    'print': { sig: 'print(...args)', desc: 'Prints arguments without a trailing newline.' },
    'panic': { sig: 'panic(msg: string)', desc: 'Aborts execution and prints a panic message.' },
    'assert': { sig: 'assert(cond: bool, msg?: string)', desc: 'Aborts execution if the condition is false.' },
    'swap': { sig: 'swap(a, b)', desc: 'Swaps the values of two variables.' },
    'min': { sig: 'min(a, b) : T', desc: 'Returns the smaller of two values.' },
    'max': { sig: 'max(a, b) : T', desc: 'Returns the larger of two values.' },
    'clamp': { sig: 'clamp(val: T, lo: T, hi: T) : T', desc: 'Constrains a value between lo and hi.' },
    'abs': { sig: 'abs(val) : T', desc: 'Returns the absolute value.' },
    'floor': { sig: 'floor(val: float) : float', desc: 'Rounds down to the nearest integer.' },
    'ceil': { sig: 'ceil(val: float) : float', desc: 'Rounds up to the nearest integer.' },
    'round': { sig: 'round(val: float) : float', desc: 'Rounds to the nearest integer.' },
    'sqrt': { sig: 'sqrt(val: float) : float', desc: 'Returns the square root.' },
    'pow': { sig: 'pow(base: float, exp: float) : float', desc: 'Raises base to the power of exp.' },
    'len': { sig: 'len(val) : int', desc: 'Returns the length of a string, vector, or array.' },
    'trim': { sig: 'trim(s: string) : string', desc: 'Removes leading and trailing whitespace.' },
    'split': { sig: 'split(s: string, sep: string) : vec<string>', desc: 'Splits a string into a vector of substrings.' },
    'join': { sig: 'join(parts: vec<string>, sep?: string) : string', desc: 'Joins vector elements into a single string.' },
    'replace': { sig: 'replace(s: string, old: string, new: string) : string', desc: 'Replaces all occurrences of a substring.' },
    'contains': { sig: 'contains(s: string, sub: string) : bool', desc: 'Checks whether a string contains a substring.' },
    'startswith': { sig: 'startswith(s: string, prefix: string) : bool', desc: 'Checks whether a string starts with a prefix.' },
    'endswith': { sig: 'endswith(s: string, suffix: string) : bool', desc: 'Checks whether a string ends with a suffix.' },
    'upper': { sig: 'upper(s: string) : string', desc: 'Converts a string to uppercase.' },
    'lower': { sig: 'lower(s: string) : string', desc: 'Converts a string to lowercase.' },
    'substr': { sig: 'substr(s: string, start: int, end?: int) : string', desc: 'Extracts a substring by character range.' },
    'find': { sig: 'find(s: string, sub: string) : int', desc: 'Returns the index of the first occurrence, or -1.' },
    'parseint': { sig: 'parseint(s: string) : int', desc: 'Parses a string as an integer.' },
    'parsefloat': { sig: 'parsefloat(s: string) : float', desc: 'Parses a string as a float.' },
    'tobool': { sig: 'tobool(val) : bool', desc: 'Converts a value to a boolean.' },
    'tochar': { sig: 'tochar(val) : char', desc: 'Converts a value to a char.' },
    'isnum': { sig: 'isnum(c: char) : bool', desc: 'Checks whether a char is a digit.' },
    'isalpha': { sig: 'isalpha(c: char) : bool', desc: 'Checks whether a char is a letter.' },
    'isalnum': { sig: 'isalnum(c: char) : bool', desc: 'Checks whether a char is alphanumeric.' },
    'isspace': { sig: 'isspace(c: char) : bool', desc: 'Checks whether a char is whitespace.' },
    'getKey': { sig: 'getKey(value: V) : K', desc: 'Returns the key for a given value in a hash table.' },
    'keys': { sig: 'keys() : vec<K>', desc: 'Returns all keys in a hash table.' },
    'values': { sig: 'values() : vec<V>', desc: 'Returns all values in a hash table.' },
    'delete': { sig: 'delete(key: K)', desc: 'Removes a key-value pair from a hash table.' },
    'push': { sig: 'push(key: K, value: V)', desc: 'Adds or updates a key-value pair in a hash table.' },
    'int': { sig: 'int | int<width>', desc: 'Standard signed integer type (1-512 bit widths).' },
    'uint': { sig: 'uint | uint<width>', desc: 'Unsigned integer type (1-512 bit widths).' },
    'float': { sig: 'float | float<width>', desc: 'Floating point type (16-512 bit widths).' },
    'bool': { sig: 'bool', desc: 'Boolean type (true or false).' },
    'char': { sig: 'char', desc: 'Character type (single byte).' },
    'string': { sig: 'string', desc: 'Immutable, managed string type.' },
    'void': { sig: 'void', desc: 'Represents the absence of a value.' },
    'hash': { sig: 'hash<K, V>', desc: 'Key-value hash table type with string keys.' },
    'vec': { sig: 'vec<T>', desc: 'Dynamic array (vector) type.' },
    'vector': { sig: 'vector<T>', desc: 'Alias for `vec<T>`, dynamic array (vector) type.' },
    'matrix': { sig: 'matrix<T>', desc: 'Dynamically-sized, heap-allocated flat array with parallel element-wise math.' },
    'array': { sig: 'T[N]', desc: 'Fixed-size array type.' },
    'pointer': { sig: '*T', desc: 'Pointer type for low-level memory access.' },
    'File': { sig: 'File', desc: 'Built-in file class. Open with `File.open(path, mode)`.' }
};

export const KEYWORD_DOCS: Record<string, string> = {
    'fnc': 'Starts a function definition.',
    'def': 'Declares a custom type (struct, class, union, enum, macro) or an alias.',
    'return': 'Exits current function and optionally returns a value.',
    'if': 'Conditional branching. Executes block when condition is true.',
    'else': 'Alternative branch executed when condition is false.',
    'also': 'Else-if conditional. Equivalent to `else if` in other languages.',
    'unless': 'Inverted if. Executes block when condition is FALSE.',
    'alsou': 'Else-unless. Combines `else` with a false-condition check.',
    'while': 'While loop. Repeats block while condition is true.',
    'with': 'Scoped block. Declares variables that exist only inside the block.',
    'for': 'For loop. Repeats block with an index variable.',
    'do': 'Do-while loop. Executes block at least once then repeats while condition is true.',
    'foreach': 'Iterates over elements of an array, vector, string, or struct.',
    'loop': 'Infinite loop. Repeats block until `stop` or `return`.',
    'in': 'Used in foreach syntax (`in<array>`, `in<vector>`) and hash membership check.',
    'stop': 'Exits the current loop immediately (like `break`).',
    'continue': 'Skips to the next iteration of the current loop.',
    'switch': 'Multi-way branch based on an expression value.',
    'case': 'A branch case within a switch block.',
    'default': 'Default case within a switch or default value for variables.',
    'empty': 'No-op statement. Does nothing.',
    'ignore': 'Returns the default value for the current function early.',
    'defer': 'Defers execution until the function returns (stacked LIFO order).',
    'throw': 'Throws a custom error caught by `works`/`otherwise`.',
    'works': 'Error handling block. If code fails, control jumps to `otherwise`.',
    'otherwise': 'Executes if the preceding `works` block encountered an error.',
    'struct': 'Defines a struct type (value type with named fields).',
    'union': 'Defines a union type (value type holding one of several variants).',
    'enum': 'Defines an enum type (named constants with optional types/values).',
    'class': 'Defines a class type (reference type with methods and inheritance).',
    'type': 'Creates a type alias: `def MyType : type ExistingType;`.',
    'template': 'Declares a template type parameter: `def T : template;`.',
    'macro': 'Defines a compile-time code transformation macro.',
    'opdef': 'Defines an operator overload or extension method for a type.',
    'error': 'Defines a custom error type for `throw`/`works`/`otherwise`.',
    'self': 'Contextual name string. Evaluates to function/method/class name.',
    'this': 'Reference to the current class/struct instance.',
    'thisop': 'References the inner type in `opdef` definitions (`thisop.typ`).',
    'pub': 'Public visibility. Item is accessible from other modules.',
    'priv': 'Private visibility. Item is only accessible within the current module.',
    'static': 'Static member. Belongs to the class itself rather than instances.',
    'pubif': 'Conditional compilation: `pubif(condition)` includes item only on matching platforms.',
    'extern': 'Declares an external function for FFI with native libraries.',
    'use': 'Imports an item from another module: `use module::Item;`.',
    'unsafe': 'Disables runtime safety checks (bounds checking, etc.) for performance.',
    'as': 'Type conversion or renaming keyword.',
    'inline': 'Compiler hint to insert function body at call sites.',
    'imut': 'Immutable qualifier. Variables marked `imut` cannot be reassigned.',
    'create': 'Allocates a class instance on the heap and calls its constructor.',
    'del': 'Destroys a class instance and calls its destructor.',
    'is': 'Type/value check or equality operator.',
    'isnt': 'Negated type/value check or inequality operator.',
    'worker': 'Declares a worker function that runs in its own thread.',
    'spawn': 'Launches a worker function in a new OS thread.',
    'shared': 'Declares a shared global variable (one writer, many readers).',
    'fusion': 'Declares a fusion global variable (multiple readers and writers, atomic).',
    'thisworker': 'Inside a worker, provides access to the worker\'s state (e.g. `thisworker.interrupted`).',
    'true': 'Boolean true literal.',
    'false': 'Boolean false literal.',
    'null': 'Null/nil literal representing absence of a value.',
    'nil': 'Null/nil literal (alias for `null`).'
};

export const KEYWORDS = Object.keys(KEYWORD_DOCS);

export const BUILTIN_FUNCTIONS = [
    'show', 'showb', 'get', 'keyget', 'set', 'toint', 'tofloat', 'tounion', 'scast', 'tostring',
    'cstr', 'lstr', 'normescape', 'sizeof', 'typeof', 'size', 'cur', 'pushb', 'popb', 'pushf', 'popf',
    'insert', 'clear', 'remove', 'extend', 'extendv', 'insertv', 'inserta', 'isin', 'rand', 'randf',
    'seed', 'choose', 'wait', 'timepass', 'exit', 'exec', 'inttobytes', 'bytestoint', 'floattobytes',
    'bytestofloat', 'getKey', 'getkey', 'keys', 'values', 'push', 'pop', 'front', 'back',
    'malloc', 'calloc', 'realloc', 'free',
    'printf', 'println', 'print', 'error', 'throw', 'works', 'otherwise', 'panic', 'assert', 'swap',
    'min', 'max', 'clamp', 'abs', 'floor', 'ceil', 'round', 'sqrt', 'pow', 'len', 'trim', 'split',
    'join', 'replace', 'contains', 'startswith', 'endswith', 'upper', 'lower', 'substr', 'find',
    'parseint', 'parsefloat', 'tobool', 'tochar', 'isnum', 'isalpha', 'isalnum', 'isspace'
];

export const BUILTIN_TYPES = [
    'int', 'uint', 'float', 'bool', 'char', 'string', 'void',
    'vec', 'vector', 'hash', 'matrix', 'array', 'pointer', 'File'
];

export interface BuiltinMember {
    name: string;
    kind: 'method' | 'field';
    sig: string;
    desc: string;
    params: ParamInfo[];
    returnType: string;
    isStatic: boolean;
}

export const BUILTIN_MEMBERS: Record<string, BuiltinMember[]> = {
    'vec': [
        { name: 'pushb', kind: 'method', sig: 'pushb(val: T)', desc: 'Appends an element to the back.', params: [{ name: 'val', type: 'T', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'popb', kind: 'method', sig: 'popb() : T', desc: 'Removes and returns the last element.', params: [], returnType: 'T', isStatic: false },
        { name: 'pushf', kind: 'method', sig: 'pushf(val: T)', desc: 'Inserts an element at the front.', params: [{ name: 'val', type: 'T', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'popf', kind: 'method', sig: 'popf() : T', desc: 'Removes and returns the first element.', params: [], returnType: 'T', isStatic: false },
        { name: 'insert', kind: 'method', sig: 'insert(idx: int, val: T)', desc: 'Inserts an element at an index, shifting subsequent elements.', params: [{ name: 'idx', type: 'int', variadic: false, hasDefault: false }, { name: 'val', type: 'T', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'insertv', kind: 'method', sig: 'insertv(pos: int, other: vec<T>)', desc: 'Inserts another vector at a position.', params: [{ name: 'pos', type: 'int', variadic: false, hasDefault: false }, { name: 'other', type: 'vec<T>', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'inserta', kind: 'method', sig: 'inserta(pos: int, arr: T[])', desc: 'Inserts an array or slice at a position.', params: [{ name: 'pos', type: 'int', variadic: false, hasDefault: false }, { name: 'arr', type: 'T[]', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'remove', kind: 'method', sig: 'remove(idx: int)', desc: 'Removes an element at an index.', params: [{ name: 'idx', type: 'int', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'set', kind: 'method', sig: 'set(idx: int, val: T)', desc: 'Sets the element at an index.', params: [{ name: 'idx', type: 'int', variadic: false, hasDefault: false }, { name: 'val', type: 'T', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'get', kind: 'method', sig: 'get(idx: int) : T', desc: 'Returns the element at an index.', params: [{ name: 'idx', type: 'int', variadic: false, hasDefault: false }], returnType: 'T', isStatic: false },
        { name: 'clear', kind: 'method', sig: 'clear()', desc: 'Removes all elements.', params: [], returnType: '', isStatic: false },
        { name: 'extend', kind: 'method', sig: 'extend(arr: T[])', desc: 'Appends an array/slice.', params: [{ name: 'arr', type: 'T[]', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'extendv', kind: 'method', sig: 'extendv(other: vec<T>)', desc: 'Appends another vector.', params: [{ name: 'other', type: 'vec<T>', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'isin', kind: 'method', sig: 'isin(val: T) : bool', desc: 'Checks if a value is in the vector.', params: [{ name: 'val', type: 'T', variadic: false, hasDefault: false }], returnType: 'bool', isStatic: false },
        { name: 'size', kind: 'field', sig: 'size : int', desc: 'Number of elements in the vector.', params: [], returnType: 'int', isStatic: false }
    ],
    'hash': [
        { name: 'push', kind: 'method', sig: 'push(key: K, value: V)', desc: 'Adds or updates a key-value pair.', params: [{ name: 'key', type: 'K', variadic: false, hasDefault: false }, { name: 'value', type: 'V', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'get', kind: 'method', sig: 'get(key: K) : V', desc: 'Returns the value for a key.', params: [{ name: 'key', type: 'K', variadic: false, hasDefault: false }], returnType: 'V', isStatic: false },
        { name: 'getKey', kind: 'method', sig: 'getKey(value: V) : K', desc: 'Returns the key for a value.', params: [{ name: 'value', type: 'V', variadic: false, hasDefault: false }], returnType: 'K', isStatic: false },
        { name: 'keys', kind: 'method', sig: 'keys() : vec<K>', desc: 'Returns all keys.', params: [], returnType: 'vec<K>', isStatic: false },
        { name: 'values', kind: 'method', sig: 'values() : vec<V>', desc: 'Returns all values.', params: [], returnType: 'vec<V>', isStatic: false },
        { name: 'delete', kind: 'method', sig: 'delete(key: K)', desc: 'Removes a key-value pair.', params: [{ name: 'key', type: 'K', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'clear', kind: 'method', sig: 'clear()', desc: 'Removes all key-value pairs.', params: [], returnType: '', isStatic: false },
        { name: 'isin', kind: 'method', sig: 'isin(key: K) : bool', desc: 'Checks if a key exists.', params: [{ name: 'key', type: 'K', variadic: false, hasDefault: false }], returnType: 'bool', isStatic: false },
        { name: 'size', kind: 'field', sig: 'size : int', desc: 'Number of key-value pairs.', params: [], returnType: 'int', isStatic: false }
    ],
    'matrix': [
        { name: 'size', kind: 'field', sig: 'size : int', desc: 'Number of elements in the matrix.', params: [], returnType: 'int', isStatic: false }
    ],
    'string': [
        { name: 'size', kind: 'field', sig: 'size : int', desc: 'Length of the string in characters.', params: [], returnType: 'int', isStatic: false },
        { name: 'cstr', kind: 'method', sig: 'cstr() : *char', desc: 'C-style null-terminated pointer to the string data.', params: [], returnType: '*char', isStatic: false }
    ],
    'File': [
        { name: 'open', kind: 'method', sig: 'open(path: string, mode: string) : File', desc: 'Opens a file. Returns nil on failure.', params: [{ name: 'path', type: 'string', variadic: false, hasDefault: false }, { name: 'mode', type: 'string', variadic: false, hasDefault: false }], returnType: 'File', isStatic: true },
        { name: 'read', kind: 'method', sig: 'read() : string', desc: 'Reads the entire file content.', params: [], returnType: 'string', isStatic: false },
        { name: 'readln', kind: 'method', sig: 'readln() : string', desc: 'Reads one line.', params: [], returnType: 'string', isStatic: false },
        { name: 'readb', kind: 'method', sig: 'readb() : char[]', desc: 'Reads the whole file as raw bytes.', params: [], returnType: 'char[]', isStatic: false },
        { name: 'readlnb', kind: 'method', sig: 'readlnb() : char[]', desc: 'Reads one line as raw bytes.', params: [], returnType: 'char[]', isStatic: false },
        { name: 'write', kind: 'method', sig: 'write(data: string) : int', desc: 'Writes a string. Returns 0 on success.', params: [{ name: 'data', type: 'string', variadic: false, hasDefault: false }], returnType: 'int', isStatic: false },
        { name: 'writeb', kind: 'method', sig: 'writeb(data: char[]) : int', desc: 'Writes raw bytes. Returns 0 on success.', params: [{ name: 'data', type: 'char[]', variadic: false, hasDefault: false }], returnType: 'int', isStatic: false },
        { name: 'close', kind: 'method', sig: 'close() : int', desc: 'Closes the file. Returns 0 on success.', params: [], returnType: 'int', isStatic: false },
        { name: 'rewind', kind: 'method', sig: 'rewind()', desc: 'Resets the file position to the beginning.', params: [], returnType: '', isStatic: false },
        { name: 'rename', kind: 'method', sig: 'rename(newPath: string)', desc: 'Renames the file on disk.', params: [{ name: 'newPath', type: 'string', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'replace', kind: 'method', sig: 'replace(old: string, new: string)', desc: 'Replaces text in the file.', params: [{ name: 'old', type: 'string', variadic: false, hasDefault: false }, { name: 'new', type: 'string', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'replaceall', kind: 'method', sig: 'replaceall(old: string, new: string)', desc: 'Replaces all occurrences of text in the file.', params: [{ name: 'old', type: 'string', variadic: false, hasDefault: false }, { name: 'new', type: 'string', variadic: false, hasDefault: false }], returnType: '', isStatic: false },
        { name: 'delete', kind: 'method', sig: 'delete()', desc: 'Deletes the file from disk.', params: [], returnType: '', isStatic: false }
    ]
};

export function builtinMemberDocs(name: string): BuiltinDoc | undefined {
    return BUILTIN_DOCS[name];
}

export function getBuiltinMembers(typeName: string): BuiltinMember[] {
    const base = typeName.replace(/<.*$/, '').trim();
    const arrMatch = /^([A-Za-z_]\w*)\[\d*\]$/.exec(base);
    if (arrMatch) {
        return [
            { name: 'size', kind: 'field', sig: 'size : int', desc: 'Number of elements in the array.', params: [], returnType: 'int', isStatic: false }
        ];
    }
    if (base === 'vec' || base === 'vector') return BUILTIN_MEMBERS['vec'];
    if (base === 'hash') return BUILTIN_MEMBERS['hash'];
    if (base === 'matrix') return BUILTIN_MEMBERS['matrix'];
    if (base === 'string') return BUILTIN_MEMBERS['string'];
    if (base === 'File') return BUILTIN_MEMBERS['File'];
    return [];
}

export function inferLiteralType(tokenText: string): string | null {
    const t = tokenText;
    if (t === 'true' || t === 'false') return 'bool';
    if (t.startsWith('"') || t.startsWith('\'')) return 'string';
    if (/^0[xX][0-9a-fA-F]+/.test(t) || /^0[bB][01]+/.test(t)) return 'int';
    if (/^\d+\.\d/.test(t)) return 'float';
    if (/^\d+$/.test(t)) return 'int';
    return null;
}

export function symbolToMarkdown(sym: LshSymbol): string {
    let md = `**${sym.kind === 'type' ? 'Type' : sym.kind === 'method' ? 'Method' : sym.kind === 'function' ? 'Function' : sym.kind === 'field' ? 'Field' : sym.kind === 'global' ? 'Global Variable' : sym.kind === 'param' ? 'Parameter' : sym.kind === 'enumMember' ? 'Enum Member' : sym.kind === 'macro' ? 'Macro' : sym.kind === 'opdef' ? 'Operator Definition' : sym.kind === 'errorType' ? 'Error Type' : sym.kind === 'nativeFunction' ? 'Native Function' : sym.kind === 'nativeVariable' ? 'Native Variable' : 'Symbol'}**`;
    if (sym.visibility) {
        md += `  \n_${sym.visibility}_`;
    }
    md += `  \n\`\`\`leash\n${sym.signature}\n\`\`\``;
    if (sym.docs) {
        md += `\n${sym.docs}`;
    }
    return md;
}