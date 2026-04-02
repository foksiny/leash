" Vim syntax file for Leash (.lsh)
if exists("b:current_syntax")
  finish
endif

" Keywords
syn keyword leashKeyword fnc def struct union enum class type template return if also else while for do foreach in imut vector vec this pub priv static stop continue use
syn keyword leashBoolean true false
syn keyword leashNull null

" Types with optional bitwidths
syn match leashType "\b\(int\|uint\|float\|bool\|string\|char\|void\|array\|vec\)\b\(<\d+>\)\?"

" Class inheritance syntax: class(Parent)
syn match leashClassInheritance "class\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)"

" Type annotation with parent: : Type(Parent)
syn match leashTypeAnnotation ":\s*[A-Z][a-zA-Z0-9_]*\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)"
" Generic type parameters
syn match leashGenericParam "<\s*\zs[A-Z][a-zA-Z0-9_]*\ze"
syn match leashGenericParam ",\s*\zs[A-Z][a-zA-Z0-9_]*\ze"

" Builtin functions and properties
syn keyword leashBuiltin show get set toint tofloat tostring cstr lstr size cur name pushb popb pushf popf insert clear rand randf seed choose wait timepass exit

" Built-in File class and methods
syn keyword leashBuiltin File open close read write readln readb writeb readlnb replace replaceall rewind rename delete

" Operators
syn match leashOperator "&&\|||\|<<\|>>\|[+\-*/%&|^~!<>=]=\?\|!="

" Ternary operator
syn match leashTernary "?"

" Pointer operators
syn match leashPointerOperator "[*&]\|->"

" Import path separator
syn match leashNamespace "::"

" Strings and Comments
syn region leashString start=/"/ skip=/\\"/ end=/"/
syn region leashChar start=/'/ skip=/\\'/ end=/'/
syn match leashComment "//.*$"

" Numbers
syn match leashNumber "\b\d+\b"
syn match leashFloat "\b\d\+\.\d\*\b"

" Highlights
hi def link leashKeyword Keyword
hi def link leashType Type
hi def link leashClassInheritance Type
hi def link leashTypeAnnotation Type
hi def link leashGenericParam Type
hi def link leashBoolean Boolean
hi def link leashNull Constant
hi def link leashBuiltin Function
hi def link leashOperator Operator
hi def link leashTernary Operator
hi def link leashPointerOperator Operator
hi def link leashNamespace Special
hi def link leashString String
hi def link leashChar Character
hi def link leashComment Comment
hi def link leashNumber Number
hi def link leashFloat Float

let b:current_syntax = "leash"
