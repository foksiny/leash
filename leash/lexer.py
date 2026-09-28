import re
from .errors import LeashError

_LEASH_ESCAPE_MAP = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "?": "?",
    "{": "{",
    "}": "}",
}


def leash_unescape(text, line=None, col=None):
    """Unescape a Leash string: \\{ -> {, \\} -> }, \\n, \\t, \\uXXXX, \\xNN, etc.

    Unlike Python's unicode_escape codec, this never mangles non-ASCII
    characters (unicode_escape decodes UTF-8 bytes as Latin-1, corrupting
    every non-ASCII char in the literal). Unknown escapes keep their
    backslash, matching Python 3.12+'s unicode_escape behavior.

    Fixed-width escapes (\\x, \\u, \\U) must have exactly 2/4/8 hex digits
    and a valid, non-surrogate code point; anything else raises LeashError
    (with line/col when the caller can supply a source position).
    """
    out = []
    i = 0
    n = len(text)

    def _bad(msg):
        return LeashError(msg, line, col)

    def _hex_cp(digits, what, width, limit):
        if len(digits) != width or any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise _bad(
                f"Invalid {what} escape: expected exactly {width} hex digits "
                f"(e.g. \\{'x' if width == 2 else 'u' if width == 4 else 'U'}"
                f"{'41'.zfill(width)})."
            )
        cp = int(digits, 16)
        if cp > limit:
            raise _bad(
                f"Invalid {what} escape: code point U+{cp:0{width}X} is out of range "
                f"(max U+{limit:X})."
            )
        if 0xD800 <= cp <= 0xDFFF:
            raise _bad(
                f"Invalid {what} escape: U+{cp:04X} is a lone UTF-16 surrogate; "
                "encode non-BMP characters directly instead."
            )
        return chr(cp)

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in _LEASH_ESCAPE_MAP:
                out.append(_LEASH_ESCAPE_MAP[nxt])
                i += 2
                continue
            if nxt == "x":
                out.append(_hex_cp(text[i + 2 : i + 4], "\\x", 2, 0xFF))
                i += 4
                continue
            if nxt == "u":
                out.append(_hex_cp(text[i + 2 : i + 6], "\\u", 4, 0xFFFF))
                i += 6
                continue
            if nxt == "U":
                out.append(_hex_cp(text[i + 2 : i + 10], "\\U", 8, 0x10FFFF))
                i += 10
                continue
            out.append("\\")
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class Token:
    __slots__ = ("type", "value", "line", "column", "raw")

    def __init__(self, type, value, line, column):
        self.type = type
        self.value = value
        self.line = line
        self.column = column
        self.raw = None

    def __repr__(self):
        return f"Token({self.type}, {repr(self.value)}, line={self.line}, col={self.column})"


class Lexer:
    # Token types
    KEYWORDS = {

        "fnc",
        "return",
        "int",
        "void",
        "def",
        "struct",
        "true",
        "false",
        "null",
        "string",
        "char",
        "bool",
        "float",
        "uint",
        "if",
        "also",
        "alsou",
        "else",
        "unless",
        "while",
        "with",
        "for",
        "do",
        "foreach",
        "in",
        "array",
        "type",
        "union",
        "enum",
        "imut",
        "vec",
        "vector",
        "class",
        "this",
        "pub",
        "priv",
        "static",
        "stop",
        "continue",
        "template",
        "nil",
        "use",
        "alias",
        "works",
        "otherwise",
        "switch",
        "case",
        "default",
        "pubif",
        "unsafe",
        "as",
        "inline",
        "defer",
        "error",
        "throw",
        "self",
    "macro",
    "create",
    "del",
"is",
        "isnt",
        "loop",
        "empty",
        "ignore",
        "opdef",
        "thisop",
        "shared",
        "fusion",
        "worker",
        "spawn",
        "async",
        "await",
        "thisworker",
        "matrix",
        "nogc",
    }

    KEYWORD_MAP = {k: k.upper() for k in KEYWORDS}

    # regexes
    TOKEN_SPECIFICATION = [
        (
            "MLSTRING_D",
            r'"""[\s\S]*?"""',
        ),  # Multi-line string double (non-greedy: stops at the first closing """)
        (
            "MLSTRING_S",
            r"'''[\s\S]*?'''",
        ),  # Multi-line string single (non-greedy: stops at the first closing ''')
        (
            "STRING",
            r'"(?:[^"\\\n]|\\.)*"(?!["])',  # String literal (no raw newlines, not followed by another ")
        ),
        (
            "BADNUM",
            r"0[bB](?:[01_]*[2-9a-zA-Z]|(?![01]))"
            r"|0[oO](?:[0-7_]*[8-9a-zA-Z]|(?![0-7]))"
            r"|0[xX](?:[0-9a-fA-F_]*[g-oq-zG-OQ-Z]|(?![0-9a-fA-F]))",
        ),  # Malformed binary/octal/hex literal (invalid digit or missing digits)
        (
            "NUMBER",
            r"(?:0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*(?:\.[0-9a-fA-F](?:_?[0-9a-fA-F])*)?(?:[pP][+-]?\d+)?|0[bB][01](?:_?[01])*|0[oO][0-7](?:_?[0-7])*|\d(?:_?\d)*(?:\.(?:\d(?:_?\d)*)?)?(?:[eE][+-]?\d+)?|\.\d(?:_?\d)*(?:[eE][+-]?\d+)?)",
        ),  # Numeric literal; integer digit separators (1_000) supported
        ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),  # Identifiers
        ("INC", r"\+\+"),  # Increment
        ("PLUS_ASSIGN", r"\+="),  # Plus-equals
        ("PLUS", r"\+"),  # Addition operator
        ("DEC", r"--"),  # Decrement
        ("MINUS_ASSIGN", r"-="),  # Minus-equals
        ("ARROW", r"->"),  # Pointer member access
        ("MINUS", r"-"),  # Subtraction operator
        ("MUL_ASSIGN", r"\*="),  # Multiply-equals
        ("MUL", r"\*"),  # Multiplication operator
        ("COMMENT", r"//.*"),  # Comments
        ("MLCOMMENT", r"/\*[\s\S]*?\*/"),  # Multi-line comments
        ("BADCOMMENT", r"/\*(?:[^*]|\*(?!/))*"),  # Unterminated block comment (no closing */)
        ("DIV_ASSIGN", r"/="),  # Divide-equals
        ("DIV", r"/"),  # Division operator
        ("MOD_ASSIGN", r"%="),  # Modulo-equals
        ("MOD", r"%"),  # Modulo operator
        ("EQ", r"=="),  # Equal to
        ("NEQ", r"!="),  # Not equal to
        ("LTE", r"<="),  # Less than or equal
        ("GTE", r">="),  # Greater than or equal
        ("SHL_ASSIGN", r"<<="),  # Shift-left-equals
        ("SHL", r"<<"),  # Shift left
        ("SHR_ASSIGN", r">>="),  # Shift-right-equals
        ("SHR", r">>"),  # Shift right
        ("L_AND", r"&&"),  # Logical AND
        ("L_OR", r"\|\|"),  # Logical OR
        ("BIT_AND_ASSIGN", r"&="),  # Bitwise AND-equals
        ("BIT_AND", r"&"),  # Bitwise AND
        ("PIPE", r"\|>"),  # Pipe operator
        ("BIT_OR_ASSIGN", r"\|="),  # Bitwise OR-equals
        ("BIT_OR", r"\|"),  # Bitwise OR
        ("BIT_XOR_ASSIGN", r"\^="),  # Bitwise XOR-equals
        ("BIT_XOR", r"\^"),  # Bitwise XOR
        ("BIT_NOT", r"~"),  # Bitwise NOT/Tilde
        ("NOT", r"!"),  # Logical NOT/Bang
        ("ASSIGN", r"="),  # Assignment operator
        ("LPAREN", r"\("),  # Left parenthesis
        ("RPAREN", r"\)"),  # Right parenthesis
        ("LBRACE", r"\{"),  # Left brace
        ("RBRACE", r"\}"),  # Right brace
        ("LBRACKET", r"\["),  # Left bracket
        ("RBRACKET", r"\]"),  # Right bracket
        ("DCOLON", r"::"),  # Double colon
        ("COLON_ASSIGN", r":="),  # Auto-type declaration
        ("QUESTION", r"\?"),  # Ternary operator
        ("COLON", r":"),  # Colon
        ("COMMA", r","),  # Comma
        ("SEMI", r";"),  # Statement terminator
        ("DOT", r"\."),  # Dot operator
        ("ISIN", r"<>"),  # Is-in operator for arrays/pointers
        ("LT", r"<"),  # Less than
        ("GT", r">"),  # Greater than
        ("CHAR", r"'(?:[^'\\\n]|\\.)*'"),  # Char literal (no raw newlines; inner group non-capturing:
        # every alternative in TOKEN_SPECIFICATION must own exactly ONE numbered
        # group so mo.lastindex maps 1:1 to a kind name in tokenize)
        ("AT", r"@"),  # @ symbol for native imports
        ("NEWLINE", r"\n"),  # Line endings
        ("SKIP", r"[ \t\r]+"),  # Skip over spaces, tabs, and CR (CRLF line endings)
        ("MISMATCH", r"."),  # Any other character
    ]

    _regex = None
    _regex_source = None

    def __init__(self, code):
        self.code = code

    @classmethod
    def _ensure_regex(cls):
        """Build and cache the combined regex from TOKEN_SPECIFICATION."""
        src = "|".join("(?P<%s>%s)" % pair for pair in cls.TOKEN_SPECIFICATION)
        if cls._regex is None or src != cls._regex_source:
            cls._regex = re.compile(src)
            cls._regex_source = src
        return cls._regex

    @staticmethod
    def _parse_number(raw):
        """Parse a numeric literal into an int or float.

        Supported forms:
          - Decimal:  42, 3.14, .5, 1e10, 2.5E-3, digit separators 1_000
          - Hex:      0xFF, 0xDEAD.BEEF, 0x1p10, 0x1_FFFF
          - Binary:   0b1010, 0b1111_0000
          - Octal:    0o755, 0o7_55

        Raises ValueError for malformed literals (leading zeros in decimal,
        bad digits) or values that overflow to infinity.
        """
        lower = raw.lower()

        # Hexadecimal (with optional hex-float exponent p/P)
        if lower.startswith("0x"):
            if "." in raw or "p" in lower:
                # float.fromhex does not accept digit separators.
                return float.fromhex(raw.replace("_", ""))
            return int(raw, 16)

        # Binary
        if lower.startswith("0b"):
            return int(raw, 2)

        # Octal
        if lower.startswith("0o"):
            return int(raw, 8)

        # Decimal with exponent or dot → float
        if "e" in lower or "." in raw:
            v = float(raw)
            if v == float("inf") or v == float("-inf"):
                raise ValueError(f"'{raw}' is out of range (evaluates to infinity)")
            return v

        # Plain decimal integer — reject ambiguous leading zeros (007)
        if len(raw) > 1 and raw[0] == "0" and raw.replace("_", "").isdigit():
            raise ValueError("leading zeros are not allowed in decimal literals")

        return int(raw, 10)

    # Class-level table mapping the combined regex's numbered groups back to
    # kind names. _KIND_NAMES[i-1] is the kind of group i — indexed via
    # mo.lastindex, which is much cheaper than the string-based lastgroup/
    # group(name) lookups used previously in the hot tokenize loop.
    _KIND_NAMES = tuple(name for name, _ in TOKEN_SPECIFICATION)

    def tokenize(self):
        regex = self._ensure_regex()
        code = self.code
        line_num = 1
        line_start = 0
        tokens = []
        tokens_append = tokens.append
        keywords = self.KEYWORD_MAP
        kind_names = self._KIND_NAMES
        parse_number = self._parse_number

        for mo in regex.finditer(code):
            idx = mo.lastindex
            kind = kind_names[idx - 1]
            value = mo.group(idx)
            start = mo.start()
            column = start - line_start

            # Dispatch ordered by token frequency: identifiers/keywords are by
            # far the most common, then punctuation (fallthrough), then
            # newline/whitespace/comment, numbers, strings.
            if kind == "IDENT":
                kw = keywords.get(value)
                if kw is not None:
                    tokens_append(Token(kw, value, line_num, column))
                else:
                    tokens_append(Token(kind, value, line_num, column))
                continue
            if kind == "NEWLINE":
                line_start = mo.end()
                line_num += 1
                continue
            if kind == "SKIP" or kind == "COMMENT":
                continue
            if kind == "MLCOMMENT":
                # Multi-line comments contain newlines: advance line tracking.
                if "\n" in value:
                    line_num += value.count("\n")
                    line_start = start + value.rfind("\n") + 1
                continue
            if kind == "BADCOMMENT":
                raise LeashError(
                    "Unterminated block comment: missing '*/'.",
                    line_num,
                    column,
                    tip="Block comments look like /* ... */ and cannot span to the end of the file without closing.",
                )
            if kind == "NUMBER":
                try:
                    num = parse_number(value)
                except (ValueError, OverflowError) as e:
                    raise LeashError(
                        f"Invalid numeric literal '{value}': {e}.", line_num, column
                    )
                tokens_append(Token(kind, num, line_num, column))
                continue
            if kind == "BADNUM":
                if value[:2].lower() == "0b":
                    what = "binary"
                elif value[:2].lower() == "0o":
                    what = "octal"
                else:
                    what = "hexadecimal"
                raise LeashError(
                    f"Invalid {what} numeric literal '{value}'.",
                    line_num,
                    column,
                    tip=f"Use only valid digits for {what} literals (separators like 1_000 must be between digits).",
                )
            if kind == "MISMATCH":
                if value == '"' and code[start : start + 3] == '"""':
                    raise LeashError(
                        "Unterminated multi-line string literal: missing closing \"\"\".",
                        line_num,
                        column,
                    )
                if value == '"':
                    raise LeashError(
                        "Unterminated or malformed string literal.",
                        line_num,
                        column,
                        tip="Strings must close on the same line; use \"\"\" ... \"\"\" for multi-line text.",
                    )
                if value == "'":
                    raise LeashError(
                        "Unterminated or malformed character literal.",
                        line_num,
                        column,
                        tip="Character literals hold exactly one character, e.g. 'a', '\\n'.",
                    )
                raise LeashError(f"Unexpected character: {value!r}", line_num, column)
            if kind == "STRING":
                raw = value[1:-1]
                text = leash_unescape(raw, line_num, column)
                if "\x00" in text:
                    raise LeashError(
                        "NUL byte ('\\0') is not allowed inside string literals: Leash strings cannot hold embedded NULs.",
                        line_num, column,
                    )
                t = Token(kind, text, line_num, column)
                t.raw = raw
                tokens_append(t)
                # Strings may span lines only via their content tokens? No — but
                # keep line tracking correct for any raw newlines defensively.
                if "\n" in value:
                    line_num += value.count("\n")
                    line_start = start + value.rfind("\n") + 1
                continue

            if kind == "CHAR":
                inner_raw = value[1:-1]
                text = leash_unescape(inner_raw, line_num, column)
                if len(text) != 1:
                    raise LeashError(
                        "Character literal must contain exactly one character "
                        f"(found {len(text)}).",
                        line_num,
                        column,
                        tip="For longer text use a string literal: \"abc\" instead of 'abc'.",
                    )
                tokens_append(Token(kind, text, line_num, column))
                if "\n" in value:
                    line_num += value.count("\n")
                    line_start = start + value.rfind("\n") + 1
                continue
            if kind in ("MLSTRING_D", "MLSTRING_S"):
                inner_raw = value[3:-3]
                text = leash_unescape(inner_raw, line_num, column)
                if "\x00" in text:
                    raise LeashError(
                        "NUL byte ('\\0') is not allowed inside string literals: Leash strings cannot hold embedded NULs.",
                        line_num, column,
                    )
                t = Token(kind, text, line_num, column)
                t.raw = inner_raw  # enables {expr} interpolation in """ strings
                tokens_append(t)
                if "\n" in value:
                    line_num += value.count("\n")
                    line_start = start + value.rfind("\n") + 1
                continue

            # NOTE: '>>' is always emitted as a single SHR token. Whether it is a
            # right-shift or the closing brackets of nested generics (e.g.
            # vec<vec<int>>) is decided by the parser, which can split the token.

            tokens_append(Token(kind, value, line_num, column))

        tokens.append(Token("EOF", "", line_num, len(code) - line_start))
        return tokens
