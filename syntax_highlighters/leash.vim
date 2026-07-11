" Vim syntax file for Leash (.lsh)
" Language: Leash - A strongly-typed compiled language on LLVM
" Version: 0.19.0 Beta
" Maintainer: foksiny <foksiny@github.com>
" Last Updated: 2026

if exists("b:current_syntax")
  finish
endif

" Keywords -------------------------------------------------------------------
syn keyword leashKeyword	fnc def struct union enum class type template macro opdef
syn keyword leashKeyword	return if also else unless alsou while for do loop in
syn keyword leashKeyword	imut pub priv static stop continue empty ignore use works otherwise
syn keyword leashKeyword	switch case default unsafe as inline defer throw error create del
syn keyword leashKeyword	worker spawn shared fusion thisworker extern sizeof typeof
syn keyword leashKeyword	cstr lstr normescape toint tofloat tostring tounion
syn keyword leashKeyword	inttobytes bytestoint floattobytes bytestofloat getKey keys values delete push

" Storage/Type Modifiers -----------------------------------------------------
syn keyword leashStorage	pub priv static imut inline unsafe worker shared fusion extern
syn keyword leashStorage	template macro type

" Built-in Types -------------------------------------------------------------
syn keyword leashType		int uint float bool string char void array vec hash pointer matrix

" Built-in Constants ---------------------------------------------------------
syn keyword leashConstant	true false null nil

" Built-in Compile-Time Variables --------------------------------------------
syn keyword leashBuiltinVar	_FILEPATH _FILENAME _PLATFORM

" Self/This Keywords ---------------------------------------------------------
syn keyword leashSelf		self
syn match	leashSelfOpType	/\<thisop\.typ\>/

" Built-in Functions ---------------------------------------------------------
syn keyword leashBuiltin	show showb get keyget set toint tofloat tounion tostring cstr lstr
syn keyword leashBuiltin	normescape sizeof typeof size cur name pushb popb pushf popf insert
syn keyword leashBuiltin	insertv inserta clear remove extend extendv isin rand randf seed choose
syn keyword leashBuiltin	wait timepass exit exec inttobytes bytestoint floattobytes bytestofloat
syn keyword leashBuiltin	getKey keys values delete push

" Built-in File Class Methods ------------------------------------------------
syn keyword leashBuiltin	File open close read write readln readb writeb readlnb replaceall
syn keyword leashBuiltin	rewind rename delete

" Built-in String Methods ----------------------------------------------------
syn keyword leashBuiltin	replace

" Types with Bit Widths (int<64>, uint<32>, float<64>, etc.) -----------------
syn match	leashType		/\<\(int\|uint\|float\)<\d\+>\>/

" Hash Table Type with Generics: hash<K, V> ---------------------------------
syn match	leashType		/\<hash<[^>]*>\>/

" Array Types with Fixed Sizes: int[5], char[10], int[n], etc. ---------------
syn match	leashArrayType	/\<\(int\|uint\|float\|bool\|string\|char\|void\)\[[^\]]*\]/

" Class Inheritance: class(Parent) -------------------------------------------
syn match	leashClassInheritance	/class\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)/

" Type Annotation with Parent: : Type(Parent) --------------------------------
syn match	leashTypeAnnotation	/:\s*[A-Z][a-zA-Z0-9_]*\s*(\s*[A-Z][a-zA-Z0-9_]*\s*)/

" Generic Type Parameters ----------------------------------------------------
syn match	leashGenericParam	/<\s*\zs[A-Z][a-zA-Z0-9_]*\ze/
syn match	leashGenericParam	/,\s*\zs[A-Z][a-zA-Z0-9_]*\ze/
syn match	leashStructType		/->\s*\zs[A-Z][a-zA-Z0-9_]*\ze/

" Multi-Type Syntax: [int, float] -------------------------------------------
syn match	leashMultiType	/\[\s*[a-z_][a-zA-Z0-9_]*\s*\(,\s*[a-z_][a-zA-Z0-9_]*\s*\)*\]/

" Multi-Return Type Syntax: (int, float) ------------------------------------
syn match	leashMultiReturn	/(\s*[a-zA-Z_][a-zA-Z0-9_<>\[\]&\*\s]*\s*\(,\s*[a-zA-Z_][a-zA-Z0-9_<>\[\]&\*\s]*\s*\)*)/

" Function Pointer Types: fnc(int, int) : int -------------------------------
syn match	leashFuncPointer	/fnc\s*(\s*[^)]*\s*)\s*:\s*[a-z_][a-zA-Z0-9_]*\s*\(\s*<\s*[0-9, ]\+\s*>\s*\)\?/

" Generic Method Call: VecMath<int>.sum(a, b) -------------------------------
syn match	leashGenericMethodCall	/\u[a-zA-Z0-9_]*<\([^>]\+\)>\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*(/

" Function Declarations (with or without parentheses) -----------------------
syn match	leashFunction	/\<fnc\s\+\zs[a-zA-Z_][a-zA-Z0-9_]*\ze/
syn match	leashMethod	/\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*(/
syn match	leashMethod	/\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*[;:]/
syn match	leashMethod	/\.\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*{/

" Enum Member with Custom Value: MEMBER: type = value -----------------------
syn match	leashEnumMember	/^\s*\zs[a-zA-Z_][a-zA-Z0-9_]*\ze\s*:/

" @from Native Import Directive ----------------------------------------------
syn match	leashNativeImport	/@from\s*(/me=s+1

" Operators ------------------------------------------------------------------
syn match	leashOperator		/|>\|&&\|||\|<<\|>>\|++\|--\|[+\-*/%&|^~!<>=]=\?\|<<=\|>>=\|&=\||=\|\^=/
syn match	leashOperator		/=?>\|::\|\.\.\.\|->\|\.\|[*&]/
syn match	leashTernary		"[?:]"
syn match	leashAutoDeclare	":="
syn match	leashPointerOp		"[*&]\|->"
syn match	leashNamespace		"::"

" Special: is / isnt / as / in / <> ------------------------------------------
syn keyword	leashTypeOp		is isnt as in
syn match		leashIsInOp		"<>"

" Numbers --------------------------------------------------------------------
" Hexadecimal: 0xFF, 0xDEADBEEF, 0x1.5p3 (hex float)
syn match	leashHex		/\<0[xX][0-9a-fA-F]\+\(\.[0-9a-fA-F]*\)\?\([pP][+-]\?\d\+\)\?/
" Binary: 0b1010
syn match	leashBinary		/\<0[bB][01]\+/
" Octal: 0o755
syn match	leashOctal		/\<0[oO][0-7]\+/
" Scientific notation: 1e10, 2.5E-3, .5
syn match	leashScientific	/\<\d\+\(\.\d*\)\?\([eE][+-]\?\d\+\)\+/
syn match	leashScientific	/\<\.\d\+\([eE][+-]\?\d\+\)\?/
" Decimal float: 3.14
syn match	leashFloat		/\<\d\+\.\d*\>/
" Decimal integer: 42
syn match	leashNumber		/\<\d\+\>/

" Strings and Comments -------------------------------------------------------
" Multi-line strings: """...""" and '''...'''
syn region	leashMLString	start=/"""/ end=/"""/ keepend
syn region	leashMLString	start=/'''/ end=/'''/ keepend
" Regular strings and chars
syn region	leashString	start=/"/ skip=/\\./ end=/"/ contains=leashInterpolation
syn region	leashChar		start=/'/ skip=/\\./ end=/'/
" String interpolation: {expr} inside strings (but not \{)
syn region leashInterpolation matchgroup=leashInterpolationDelimiter start='\%(\\\)\@<!{' end='\%(\\\)\@<!}' contained containedin=leashString
" Multi-line comments: /*...*/
syn region	leashMLComment	start=/\/\*/ end=/\*\// keepend
" Single-line comments: //...
syn match	leashComment		"//.*$"

" Highlighting Links ---------------------------------------------------------
hi def link leashKeyword		Keyword
hi def link leashStorage		StorageClass
hi def link leashType			Type
hi def link leashClassInheritance	Type
hi def link leashTypeAnnotation	Type
hi def link leashGenericParam		Type
hi def link leashStructType		Type
hi def link leashMultiType		Type
hi def link leashMultiReturn		Type
hi def link leashArrayType		Type
hi def link leashFuncPointer		Type
hi def link leashBoolean		Boolean
hi def link leashConstant		Constant
hi def link leashNull			Constant
hi def link leashSelf			Constant
hi def link leashSelfOpType		Special
hi def link leashBuiltin		Function
hi def link leashBuiltinVar		Special
hi def link leashFunction		Function
hi def link leashMethod		Function
hi def link leashOperator		Operator
hi def link leashTernary		Operator
hi def link leashAutoDeclare	Operator
hi def link leashPointerOp		Operator
hi def link leashNamespace		Special
hi def link leashMLString		String
hi def link leashString		String
hi def link leashChar		Character
hi def link leashInterpolation	Identifier
hi def link leashInterpolationDelimiter	Delimiter
hi def link leashMLComment		Comment
hi def link leashComment		Comment
hi def link leashNumber		Number
hi def link leashFloat		Float
hi def link leashHex		Number
hi def link leashBinary		Number
hi def link leashOctal		Number
hi def link leashScientific		Float
hi def link leashNativeImport	Special
hi def link leashGenericMethodCall	Function
hi def link leashEnumMember		Constant
hi def link leashTypeOp		Operator
hi def link leashIsInOp		Operator

let b:current_syntax = "leash"