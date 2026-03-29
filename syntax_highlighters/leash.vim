" Vim syntax file for Leash (.lsh)
if exists("b:current_syntax")
  finish
endif

" Keywords
syn keyword leashKeyword fnc def struct union enum class type return if also else while for do foreach in imut vector vec this pub priv static
syn keyword leashBoolean true false
syn keyword leashNull null

" Types with optional bitwidths
syn match leashType "\b\(int\|uint\|float\|bool\|string\|char\|void\|array\|vec\)\b\(<\d+>\)\?"

" Builtin functions and properties
syn keyword leashBuiltin show get set toint tofloat tostring cstr lstr size cur name pushb popb pushf popf insert clear

" Operators
syn match leashOperator "&&\|||\|<<\|>>\|[+\-*/%&|^~!<>=]=\?\|!="

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
hi def link leashBoolean Boolean
hi def link leashNull Constant
hi def link leashBuiltin Function
hi def link leashOperator Operator
hi def link leashString String
hi def link leashChar Character
hi def link leashComment Comment
hi def link leashNumber Number
hi def link leashFloat Float

let b:current_syntax = "leash"
