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


def leash_unescape(text):
    """Unescape a Leash string: \\{ -> {, \\} -> }, \\n, \\t, \\uXXXX, \\xNN, etc.

    Unlike Python's unicode_escape codec, this never mangles non-ASCII
    characters (unicode_escape decodes UTF-8 bytes as Latin-1, corrupting
    every non-ASCII char in the literal). Unknown escapes keep their
    backslash, matching Python 3.12+'s unicode_escape behavior.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in _LEASH_ESCAPE_MAP:
                out.append(_LEASH_ESCAPE_MAP[nxt])
                i += 2
                continue
            if nxt == "x":
                digits = text[i + 2 : i + 4]
                try:
                    out.append(chr(int(digits, 16)))
                    i += 4
                except ValueError:
                    out.append("\\x")
                    i += 2
                continue
            if nxt == "u":
                digits = text[i + 2 : i + 6]
                try:
                    out.append(chr(int(digits, 16)))
                    i += 6
                except ValueError:
                    out.append("\\u")
                    i += 2
                continue
            if nxt == "U":
                digits = text[i + 2 : i + 10]
                try:
                    out.append(chr(int(digits, 16)))
                    i += 10
                except ValueError:
                    out.append("\\U")
                    i += 2
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
        "thisworker",
        "matrix",
        "nogc",
    }

    KEYWORD_MAP = {k: k.upper() for k in KEYWORDS}

    # regexes
    TOKEN_SPECIFICATION = [
        (
            "MLSTRING_D",
            r'"""[\s\S]*"""',
        ),  # Multi-line string double (greedy to last """")
        (
            "MLSTRING_S",
            r"'''[\s\S]*'''",
        ),  # Multi-line string single (greedy to last ''')
        (
            "STRING",
            r'"(?:[^"\\]|\\.)*"(?!["])',  # String literal (not followed by another ")
        ),
        (
            "NUMBER",
            r"(?:0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|0[bB][01]+|0[oO][0-7]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)",
        ),  # Integer, float, hex, binary, octal, scientific
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
        ("CHAR", r"'[^'\\]*(\\.[^'\\]*)*'"),  # Char literal
        ("AT", r"@"),  # @ symbol for native imports
        ("NEWLINE", r"\n"),  # Line endings
        ("SKIP", r"[ \t]+"),  # Skip over spaces and tabs
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
          - Decimal:  42, 3.14, .5, 1e10, 2.5E-3
          - Hex:      0xFF, 0xDEAD.BEEF, 0x1p10
          - Binary:   0b1010
          - Octal:    0o755
        """
        lower = raw.lower()

        # Hexadecimal (with optional hex-float exponent p/P)
        if lower.startswith("0x"):
            if "." in raw or "p" in lower:
                return float.fromhex(raw)
            return int(raw, 16)

        # Binary
        if lower.startswith("0b"):
            return int(raw, 2)

        # Octal
        if lower.startswith("0o"):
            return int(raw, 8)

        # Decimal with exponent or dot → float
        if "e" in lower or "." in raw:
            return float(raw)

        # Plain decimal integer
        return int(raw, 10)

    def tokenize(self):
        regex = self._ensure_regex()
        code = self.code
        line_num = 1
        line_start = 0
        tokens = []
        tokens_append = tokens.append
        keywords = self.KEYWORD_MAP

        for mo in regex.finditer(code):
            kind = mo.lastgroup
            value = mo.group(kind)
            start = mo.start()
            column = start - line_start

            if kind == "NEWLINE":
                line_start = mo.end()
                line_num += 1
                continue
            if kind == "SKIP" or kind == "COMMENT" or kind == "MLCOMMENT":
                continue
            if kind == "NUMBER":
                tokens_append(Token(kind, self._parse_number(value), line_num, column))
                continue
            if kind == "MISMATCH":
                raise LeashError(f"Unexpected character: {value}", line_num, column)
            if kind == "STRING":
                raw = value[1:-1]
                text = leash_unescape(raw)
                if "\x00" in text:
                    raise LeashError(
                        "NUL byte ('\\0') is not allowed inside string literals: Leash strings cannot hold embedded NULs.",
                        line_num, column,
                    )
                t = Token(kind, text, line_num, column)
                t.raw = raw
                tokens_append(t)
                continue

            if kind == "CHAR":
                value = value[1:-1]
                value = leash_unescape(value)
            elif kind in ("MLSTRING_D", "MLSTRING_S"):
                value = value[3:-3]
                value = leash_unescape(value)
                if "\x00" in value:
                    raise LeashError(
                        "NUL byte ('\\0') is not allowed inside string literals: Leash strings cannot hold embedded NULs.",
                        line_num, column,
                    )
            elif kind == "IDENT" and value in keywords:
                kind = keywords[value]

            # NOTE: '>>' is always emitted as a single SHR token. Whether it is a
            # right-shift or the closing brackets of nested generics (e.g.
            # vec<vec<int>>) is decided by the parser, which can split the token.

            tokens_append(Token(kind, value, line_num, column))

        tokens.append(Token("EOF", "", line_num, len(code) - line_start))
        return tokens
