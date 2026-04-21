import re
from .errors import LeashError


class Token:
    def __init__(self, type, value, line, column):
        self.type = type
        self.value = value
        self.line = line
        self.column = column

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
        "else",
        "while",
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
    }

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
        ("PLUS", r"\+"),  # Addition operator
        ("ARROW", r"->"),  # Pointer member access
        ("MINUS", r"-"),  # Subtraction operator
        ("MUL", r"\*"),  # Multiplication operator
        ("COMMENT", r"//.*"),  # Comments
        ("MLCOMMENT", r"/\*[\s\S]*?\*/"),  # Multi-line comments
        ("DIV", r"/"),  # Division operator
        ("MOD", r"%"),  # Modulo operator
        ("EQ", r"=="),  # Equal to
        ("NEQ", r"!="),  # Not equal to
        ("LTE", r"<="),  # Less than or equal
        ("GTE", r">="),  # Greater than or equal
        ("SHL", r"<<"),  # Shift left
        ("SHR", r">>"),  # Shift right
        ("L_AND", r"&&"),  # Logical AND
        ("L_OR", r"\|\|"),  # Logical OR
        ("BIT_AND", r"&"),  # Bitwise AND
        ("PIPE", r"\|>"),  # Pipe operator
        ("BIT_OR", r"\|"),  # Bitwise OR
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

    def __init__(self, code):
        self.code = code

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
        tok_regex = "|".join("(?P<%s>%s)" % pair for pair in self.TOKEN_SPECIFICATION)
        line_num = 1
        line_start = 0
        tokens = []
        for mo in re.finditer(tok_regex, self.code):
            kind = mo.lastgroup
            value = mo.group(kind)
            column = mo.start() - line_start
            if kind == "NUMBER":
                value = self._parse_number(value)
            elif kind in ("STRING", "CHAR"):
                value = value[1:-1]  # strip quotes
                value = value.encode("utf-8").decode("unicode_escape")
            elif kind in ("MLSTRING_D", "MLSTRING_S"):
                value = value[3:-3]  # strip triple quotes
                value = value.encode("utf-8").decode("unicode_escape")
            elif kind == "IDENT" and value in self.KEYWORDS:
                kind = value.upper()
            elif kind == "NEWLINE":
                line_start = mo.end()
                line_num += 1
                continue
            elif kind == "SKIP" or kind == "COMMENT" or kind == "MLCOMMENT":
                continue
            elif kind == "MISMATCH":
                raise LeashError(f"Unexpected character: {value}", line_num, column)

            tokens.append(Token(kind, value, line_num, column))

        # Post-process tokens to split SHR into two GT when inside generic brackets
        depth = 0
        final_tokens = []
        for token in tokens:
            if token.type == "LT":
                depth += 1
                final_tokens.append(token)
            elif token.type == "GT":
                depth -= 1
                final_tokens.append(token)
            elif token.type == "SHR" and depth > 0:
                # Replace with two GT tokens
                final_tokens.append(Token("GT", ">", token.line, token.column))
                final_tokens.append(Token("GT", ">", token.line, token.column + 1))
                depth -= 2  # account for the two GT tokens we inserted
            else:
                final_tokens.append(token)
        tokens = final_tokens

        tokens.append(Token("EOF", "", line_num, len(self.code) - line_start))
        return tokens
