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
    }

    # regexes
    TOKEN_SPECIFICATION = [
        ("STRING", r'"[^"\\]*(\\.[^"\\]*)*"'),  # String literal
        ("NUMBER", r"\d+(\.\d*)?"),  # Integer or decimal number
        ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),  # Identifiers
        ("PLUS", r"\+"),  # Addition operator
        ("ARROW", r"->"),  # Pointer member access
        ("MINUS", r"-"),  # Subtraction operator
        ("MUL", r"\*"),  # Multiplication operator
        ("COMMENT", r"//.*"),  # Comments
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
        ("COLON", r":"),  # Colon
        ("COMMA", r","),  # Comma
        ("SEMI", r";"),  # Statement terminator
        ("DOT", r"\."),  # Dot operator
        ("LT", r"<"),  # Less than
        ("GT", r">"),  # Greater than
        ("CHAR", r"'[^'\\]*(\\.[^'\\]*)*'"),  # Char literal
        ("NEWLINE", r"\n"),  # Line endings
        ("SKIP", r"[ \t]+"),  # Skip over spaces and tabs
        ("MISMATCH", r"."),  # Any other character
    ]

    def __init__(self, code):
        self.code = code

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
                # determine if int or float (for now just int based on grammar)
                value = int(value) if "." not in value else float(value)
            elif kind == "STRING" or kind == "CHAR":
                value = value[1:-1]  # strip quotes
                # naive unescape
                value = value.encode("utf-8").decode("unicode_escape")
            elif kind == "IDENT" and value in self.KEYWORDS:
                kind = value.upper()
            elif kind == "NEWLINE":
                line_start = mo.end()
                line_num += 1
                continue
            elif kind == "SKIP" or kind == "COMMENT":
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
