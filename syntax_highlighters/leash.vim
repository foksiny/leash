" Vim syntax file for Leash (.lsh)
if exists("b:current_syntax")
  finish
endif

" Keywords
syn keyword leashKeyword fnc def struct union type return if also else while for do foreach in
syn keyword leashBoolean true false

" Types with optional bitwidths
syn match leashType "\b\(int\|uint\|float\|bool\|string\|char\|void\)\b\(<\d+>\)\?"

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
hi def link leashString String
hi def link leashChar Character
hi def link leashComment Comment
hi def link leashNumber Number
hi def link leashFloat Float

let b:current_syntax = "leash"
