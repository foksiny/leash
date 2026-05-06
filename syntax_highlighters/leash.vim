" Vim syntax file for Leash (.lsh)
if exists("b:current_syntax")
  finish
endif

" Keywords
syn keyword leashKeyword fnc def struct union enum class type template macro return if also else unless while for do foreach in imut vector vec this pub priv static stop continue use works otherwise switch case default unsafe as inline defer error throw self alsou create del

" Enum member with custom value: MEMBER: type = value
syn match leashEnumMember /^\s*\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*:/
syn keyword leashBoolean true false
syn keyword leashNull null nil

" Special file path literals and built-in variables
syn keyword leashBuiltin _FILEPATH _FILENAME _PLATFORM
syn match leashSelf /\<self\(::[a-zA-Z_][a-zA-Z0-9_]*\)\?\>/

" @from native import directive
syn match leashNativeImport /@from\s*(/me=s+1

" Function Declarations
syn match leashFunction /\<fnc\s\+\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*(/
syn match leashMethod /\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*(/

" Types with optional bitwidths
syn match leashType /\<\(int\|uint\|float\|bool\|string\|char\|void\|array\|vec\)\>\(<\d\+>\)\?/

" Array types with fixed sizes: int[5], char[10], int[n], char[str.size], int[x + y]
syn match leashArrayType /\<\(int\|uint\|float\|bool\|string\|char\|void\)\[[^\]]*\]/

" Class inheritance syntax: class(Parent)
syn match leashClassInheritance /class\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)/

" Type annotation with parent: : Type(Parent)
syn match leashTypeAnnotation /:\s*[A-Z][a-zA-Z0-9_]*\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)/
" Generic type parameters
syn match leashGenericParam /<\s*\zs[A-Z][a-zA-Z0-9_]*\ze/
syn match leashGenericParam /,\s*\zs[A-Z][a-zA-Z0-9_]*\ze/

" Multi-type syntax: [int, float]
syn match leashMultiType /\[\s*[a-z_][a-zA-Z0-9_]*\s*\(,\s*[a-z_][a-zA-Z0-9_]*\s*\)*\]/

" Function pointer types: fnc(int, int) : int
syn match leashFuncPointer /fnc\s*(\s*[^)]*\s*)\s*:\s*[a-z_][a-zA-Z0-9_]*\s*\(\s*<\s*[0-9, ]\+\s*>\s*\)\?/

" Generic type instantiation for static method calls: VecMath<int>.sum(a, b)
syn match leashGenericMethodCall /\u[a-zA-Z0-9_]*<\([^>]\+\)>\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*(/

" Builtin functions and properties
syn keyword leashBuiltin show showb get set toint tofloat tostring cstr lstr size cur name pushb popb pushf popf insert clear remove extend extendv isin rand randf seed choose wait timepass exit exec inttobytes bytestoint floattobytes bytestofloat

" Built-in File class and methods
syn keyword leashBuiltin File open close read write readln readb writeb readlnb replaceall rewind rename delete

" String methods
syn keyword leashBuiltin replace

" Operators
syn match leashOperator /|>\?\|&&\|||\|<<\|>>\|[+\-*/%&|^~!<>=]=\?/

" Ternary operator
syn match leashTernary "?"

" Auto-type declaration operator (:=)
syn match leashAutoDeclare ":="

" Pointer operators
syn match leashPointerOperator "[*&]\|->"

" Import path separator
syn match leashNamespace "::"

" Strings and Comments
" Multi-line strings: """...""" and '''...'''
syn region leashMLString start=/"""/ end=/"""/ keepend
syn region leashMLString start=/'''/ end=/'''/ keepend
" Regular strings and chars
syn region leashString start=/"/ skip=/\\./ end=/"/
syn region leashChar start=/'/ skip=/\\./ end=/'/
" Multi-line comments: /*...*/
syn region leashMLComment start=/\/\*/ end=/\*\// keepend
syn match leashComment "//.*$"

" Numbers
" Hexadecimal: 0xFF, 0xDEADBEEF, 0x1.5p3 (hex float)
syn match leashHex /\<0[xX][0-9a-fA-F]\+\(\.[0-9a-fA-F]*\)\?\([pP][+-]\?\d\+\)\?/
" Binary: 0b1010
syn match leashBinary /\<0[bB][01]\+/
" Octal: 0o755
syn match leashOctal /\<0[oO][0-7]\+/
" Scientific notation: 1e10, 2.5E-3, .5
syn match leashScientific /\<\d\+\(\.\d*\)\?\([eE][+-]\?\d\+\)\+/
syn match leashScientific /\<\.\d\+\([eE][+-]\?\d\+\)\?/
" Decimal float: 3.14
syn match leashFloat /\<\d\+\.\d*\>/
" Decimal integer: 42
syn match leashNumber /\<\d\+\>/

" Highlights
hi def link leashKeyword Keyword
hi def link leashType Type
hi def link leashClassInheritance Type
hi def link leashTypeAnnotation Type
hi def link leashGenericParam Type
hi def link leashMultiType Type
hi def link leashArrayType Type
hi def link leashFuncPointer Type
hi def link leashBoolean Boolean
hi def link leashNull Constant
hi def link leashSelf Constant
hi def link leashBuiltin Function
hi def link leashFunction Function
hi def link leashMethod Function
hi def link leashOperator Operator
hi def link leashTernary Operator
hi def link leashAutoDeclare Operator
hi def link leashPointerOperator Operator
hi def link leashNamespace Special
hi def link leashMLString String
hi def link leashString String
hi def link leashChar Character
hi def link leashMLComment Comment
hi def link leashComment Comment
hi def link leashNumber Number
hi def link leashFloat Float
hi def link leashHex Number
hi def link leashBinary Number
hi def link leashOctal Number
hi def link leashScientific Float
hi def link leashNativeImport Special
hi def link leashGenericMethodCall Function

let b:current_syntax = "leash"
