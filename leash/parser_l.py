from .lexer import Lexer, Token
from .ast_nodes import (
    Program,
    StructDef,
    Function,
    Block,
    VariableDecl,
    Assignment,
    IfStatement,
    WhileStatement,
    ForStatement,
    ReturnStatement,
    StopStatement,
    ContinueStatement,
    ExpressionStatement,
    BinaryOp,
    UnaryOp,
    Call,
    Identifier,
    MemberAccess,
    NumberLiteral,
    StringLiteral,
    BoolLiteral,
    CastExpr,
    TypeAlias,
    StructInit,
    ArrayInit,
    IndexAccess,
    CharLiteral,
    NullLiteral,
    ForeachStructStatement,
    ForeachArrayStatement,
    ForeachStringStatement,
    ForeachVectorStatement,
    MethodCall,
    UnionDef,
    EnumMemberAccess,
    EnumDef,
    DoWhileStatement,
    FloatLiteral,
    TypeConvExpr,
    ShowStatement,
    ClassDef,
    ClassField,
    ClassMethod,
    ThisExpr,
    PointerMemberAccess,
    TemplateDef,
    GenericCall,
    GlobalVarDecl,
    ImportStmt,
    TernaryOp,
    WorksOtherwiseStatement,
    NativeImport,
    FilePathLiteral,
    SwitchStatement,
)
from .errors import LeashError


class Parser:
    def __init__(self, tokens, source_file=None):
        self.tokens = tokens
        self.pos = 0
        self.source_file = source_file  # Store for _FILEPATH/_FILENAME

    def _pos(self, node, tok=None):
        """Set line/col on an AST node from a token (defaults to current token)."""
        if tok is None:
            tok = self.current()
        node.line = tok.line
        node.col = tok.column
        return node

    def current(self):
        return self.tokens[self.pos]

    def peek(self, n=1):
        if self.pos + n < len(self.tokens):
            return self.tokens[self.pos + n]
        return None

    def eat(self, token_type):
        if self.current().type == token_type:
            tok = self.current()
            self.pos += 1
            return tok
        else:
            tok = self.current()
            tip = self._get_smart_tip(token_type, tok)
            raise LeashError(
                f"Expected {token_type}, but found {tok.type} ('{tok.value}')",
                tok.line,
                tok.column,
                tip=tip,
            )

    def _get_smart_tip(self, expected_type, token):
        obs_val = token.value.lower() if token.value else ""
        obs_type = token.type

        # 1. Smart detections (20+)
        # Python
        if obs_val == "elif":
            return "Leash uses the `also` keyword for 'else if' conditions! (e.g., `if a { ... } also b { ... }`)"
        if obs_val == "def" and expected_type == "FNC":
            return "In Leash, functions are declared using the `fnc` keyword, and top-level definitions (structs, unions, aliases) use `def`."
        if obs_val == "print" or obs_val == "println":
            return "Leash uses the built-in `show()` function for all of your console output needs!"

        # JS/TS
        if obs_val in ("const", "let", "var"):
            return "In Leash, variable declarations don't use `let` or `var` keywords. Just use the `name : type = value;` pattern."
        if obs_val == "function":
            return "Functions in Leash are defined with the short `fnc` keyword. (e.g., `fnc my_func() : void { ... }`)"
        if obs_val == "console.log":
            return "Did you come from JavaScript? In Leash, use `show(val1, val2, ...)` to print to the console!"

        # C/C++
        if obs_val == "printf":
            return "Leash uses `show()` instead of `printf`. It automatically handles formats and newlines for you!"
        if obs_val == "scanf":
            return "Global input operations (like scanf) are still being planned. Currently, focus on evaluating logic!"
        if obs_val == "unsigned":
            return "For unsigned types, Leash uses the `uint` prefix (e.g., `uint<8>`, `uint<32>`)."
        if obs_val == "long" or obs_val == "size_t":
            return "Looking for a 64-bit integer? Use `int<64>` or `uint<64>` for specific bit widths!"
        if obs_val == "double":
            return "The standard `float` type in Leash is double-precision (64-bit) by default. Use `float<32>` if you want single-precision."
        if obs_val == "bool" and obs_type == "IDENT":
            return "You used `bool` as an identifier, but it's a built-in type. Just use `myVar : bool = true;`."

        # Rust/Others
        if obs_val == "pub" or obs_val == "fn":
            return "Did you mean `fnc`? Note: `pub` and `priv` are used for global variable and class member visibility. Top-level variables are public by default."
        if obs_val == "interface":
            return (
                "Leash uses `struct` for grouping data and `union` for dynamic choices!"
            )
        if obs_val == "import" or obs_val == "include" or obs_val == "require":
            return "Modules are coming! For now, keep your logic within a single file or use a pre-processor."
        if obs_val == "null" and obs_type == "IDENT" and expected_type == "NULL":
            return "Use `(void)0` or leave a variable empty if you want a null-like state. Generic null support is coming."
        if obs_val == "switch" or obs_val == "match" or obs_val == "case":
            return "Leash uses switch-case statements! Use `switch expr { case val { ... } default { ... } }` syntax."
        if obs_val == "self":
            return "Leash uses the `this` keyword inside classes!"

        # More Specific patterns
        if obs_val == "main" and expected_type == "FNC":
            return "To define the entrypoint, use: `fnc main() : void { ... }`"
        if obs_val == "nil" or obs_val == "none":
            return "Looking for a null value? Leash uses `void` types and unions to represent optional values."
        if obs_val == "string" and obs_type == "IDENT":
            return "Note that `string` is a reserved type in Leash (all lowercase)."

        # 2. Simple but helpful tips (20+)
        tips = {
            "SEMI": "Semicolons (`;`) are mandatory at the end of every statement in Leash.",
            "LBRACE": "Braces `{ }` are required for all blocks (if, for, while, fnc) - even single-line ones!",
            "RBRACE": "Each opening brace `{` must have a matching closing brace `}`.",
            "COLON": "Colons `:` define types in Leash. Example: `age : int = 25;`",
            "ASSIGN": "Use `=` for assignment. Example: `a = b + 5;`",
            "LPAREN": "Functions and conditions often start with an open parenthesis `(`.",
            "RPAREN": "Check if you have an unclosed parenthetical `(` somewhere above!",
            "COMMENT": "Use `//` for single-line comments. Leash doesn't have multi-line comments yet.",
            "ARRAY": "Iterate over arrays with `foreach i, v in<array> arr { ... }`.",
            "STRUCT": "Iterate over struct members with `foreach k, v in<struct> s { ... }`.",
            "TYPE": "Define type aliases with `def NewName : type ExistingType;`.",
            "DOT": "Access members of a struct or union using the dot `.` operator.",
            "SIZE": "Get string lengths with `.size`. Example: `myString.size`",
            "CUR": "Use `.cur` on a union to print its currently active data dynamically.",
            "PLUS": "Strings can be concatenated using the `+` operator.",
            "MINUS": "You can remove parts of a string using the `-` operator!",
            "COMMA": "Function arguments are separated by commas (`,`).",
            "LT": "Bit widths for numbers are defined between `<` and `>`. Example: `int<64>`.",
            "INT": "Standard `int` is 32 bits. Use `int<64>` for bigger numbers.",
            "FLOAT": "Standard `float` is 64 bits. You can use any bit size with `float<n>`.",
            "ALSO": "Leash uses `also` as its `else if` keyword to keep conditions clean!",
            "ENUM": "Define a set of named constants with `def Name : enum { MEMBER1, MEMBER2 };`",
            "DCOLON": "Use double colons (::) to access enum members. Example: `Color::RED`",
        }

        if expected_type in tips:
            return tips[expected_type]

        return None

    def parse_type(self):
        is_imut = False
        if self.current().type == "IMUT":
            self.eat("IMUT")
            is_imut = True

        prefix = ""
        if self.current().type == "MUL":
            self.eat("MUL")
            prefix = "*"
        elif self.current().type == "BIT_AND":
            self.eat("BIT_AND")
            prefix = "&"

        # Handle function pointer types: fnc(int, int) : int
        if self.current().type == "FNC":
            fnc_type = self._parse_function_pointer_type()
            if prefix:
                fnc_type = prefix + fnc_type
            if is_imut:
                return f"imut {fnc_type}"
            return fnc_type

        # Handle multi-type syntax: [int, float, ...]
        if self.current().type == "LBRACKET":
            self.eat("LBRACKET")
            types = []
            while self.current().type != "RBRACKET":
                types.append(self.parse_type())
                if self.current().type == "COMMA":
                    self.eat("COMMA")
                else:
                    break
            self.eat("RBRACKET")
            result = f"[{', '.join(types)}]"
            if is_imut:
                return f"imut {result}"
            return result

        tok = self.current()
        if tok.type in (
            "INT",
            "VOID",
            "IDENT",
            "STRING",
            "CHAR",
            "BOOL",
            "FLOAT",
            "UINT",
            "VEC",
        ):
            base = self.eat(tok.type).value
            if tok.type not in ("IDENT", "VEC"):
                base = tok.type.lower()
            if self.current().type == "LT":
                self.eat("LT")
                is_type = False
                if self.current().type in self.TYPE_STARTERS or self.current().type in (
                    "IDENT",
                    "VEC",
                ):
                    # if it's a type name inside < >, it's a generic (like vec<string> or Hash<T1, T2>)
                    type_args = []
                    type_args.append(self.parse_type())
                    # Handle multiple type arguments (comma-separated)
                    while self.current().type == "COMMA":
                        self.eat("COMMA")
                        type_args.append(self.parse_type())
                    base = f"{base}<{', '.join(type_args)}>"
                    is_type = True

                if not is_type:
                    size = self.eat("NUMBER").value
                    base = f"{base}<{size}>"

                self.eat("GT")
            if self.current().type == "LBRACKET":
                self.eat("LBRACKET")
                arr_size = None
                if self.current().type == "NUMBER":
                    arr_size = self.eat("NUMBER").value
                self.eat("RBRACKET")
                if arr_size is not None:
                    base = f"{base}[{arr_size}]"
                else:
                    base = f"{base}[]"

            # Handle parent class annotation: Type(Parent)
            # e.g., Dog(Animal) means return type is Dog but compatible with Animal
            if self.current().type == "LPAREN" and tok.type == "IDENT":
                self.eat("LPAREN")
                parent_name = self.eat("IDENT").value
                self.eat("RPAREN")
                # The parent annotation is informational - inheritance is handled elsewhere

            res = f"{prefix}{base}"
            if is_imut:
                return f"imut {res}"
            return res
        raise LeashError(
            f"Unexpected token {tok.type} ('{tok.value}') where a type was expected",
            tok.line,
            tok.column,
        )

    def _parse_function_pointer_type(self):
        """Parse a function pointer type: fnc(param_types) : return_type"""
        self.eat("FNC")
        self.eat("LPAREN")
        param_types = []
        while self.current().type != "RPAREN":
            param_types.append(self.parse_type())
            if self.current().type == "COMMA":
                self.eat("COMMA")
        self.eat("RPAREN")
        self.eat("COLON")
        return_type = self.parse_type()
        return f"fnc({', '.join(param_types)}) : {return_type}"

    # Type token types that can start a type in a cast
    TYPE_STARTERS = {
        "INT",
        "UINT",
        "FLOAT",
        "STRING",
        "CHAR",
        "BOOL",
        "VOID",
        "IMUT",
        "VEC",
        "FNC",
    }

    def _is_cast(self):
        """Look ahead to determine if (type)expr pattern."""
        # Current token is LPAREN. We need to check if the next token(s) form a type, then RPAREN.
        saved = self.pos
        try:
            self.eat("LPAREN")
            tok = self.current()
            if tok.type in self.TYPE_STARTERS or tok.type == "IDENT":
                # Try consuming a type
                self.parse_type()
                # After the type, must be RPAREN
                if self.current().type == "RPAREN":
                    return True
            return False
        except (LeashError, IndexError):
            return False
        finally:
            self.pos = saved

    def parse(self):
        items = []
        while self.current().type != "EOF":
            if self.current().type in ("PUB", "PRIV"):
                # Peek ahead to see if next token is DEF or FNC or USE
                if self.peek() and self.peek().type == "DEF":
                    # It's a def with visibility
                    visibility = self.current().value.lower()
                    self.eat(self.current().type)  # eat PUB/PRIV
                    items.append(self.parse_def(visibility=visibility))
                elif self.peek() and self.peek().type == "FNC":
                    # It's a fnc with visibility
                    visibility = self.current().value.lower()
                    self.eat(self.current().type)  # eat PUB/PRIV
                    items.append(self.parse_function(visibility=visibility))
                elif self.peek() and self.peek().type == "USE":
                    # It's a use with visibility
                    visibility = self.current().value.lower()
                    self.eat(self.current().type)  # eat PUB/PRIV
                    items.append(self.parse_import(visibility=visibility))
                else:
                    # It's a global variable with visibility
                    items.append(self.parse_global_var())
            elif self.current().type == "DEF":
                items.append(self.parse_def())
            elif self.current().type == "FNC":
                items.append(self.parse_function())
            elif (
                self.current().type == "IDENT"
                and self.peek()
                and self.peek().type == "COLON"
            ):
                items.append(self.parse_global_var())
            elif self.current().type == "USE":
                items.append(self.parse_import())
            elif self.current().type == "AT":
                items.append(self.parse_native_import())
            else:
                tok = self.current()
                raise LeashError(
                    f"Unexpected token: {tok.type} ('{tok.value}')",
                    tok.line,
                    tok.column,
                    tip="Top-level items must be 'def' (for types), 'fnc' (for functions), 'use' (for imports), or variable declarations.",
                )
        return Program(items)

    def parse_import(self, visibility="pub"):
        """Parse a use statement: use module::item; or use subfolder::module::item;"""
        tok = self.current()
        self.eat("USE")

        # Parse the module path (one or more identifiers separated by ::)
        # We need to distinguish between path segments and the final module name
        # e.g., use math::operations::add;
        #   - path: ["math", "operations"], item: "add"
        # e.g., use math::add;
        #   - path: ["math"], item: "add"
        module_path = []
        module_path.append(self.eat("IDENT").value)

        # Keep parsing path segments as long as we see ::IDENT::
        # We need to look ahead to determine if the next IDENT is a path segment or item
        while self.current().type == "DCOLON":
            # Peek at what comes after ::
            if not self.peek():
                break

            if self.peek().type == "MUL":
                # This is ::* - end of module path, items will be wildcard
                break

            if self.peek().type != "IDENT":
                break

            # Look further ahead: if we see IDENT::IDENT or IDENT, then the first IDENT
            # is part of the path. If we see IDENT followed by comma/semi, it's an item.
            # For simplicity: if we see IDENT::IDENT, then first is path segment
            if (
                self.peek(2)
                and self.peek(2).type == "DCOLON"
                and self.peek(3)
                and self.peek(3).type in ("IDENT", "MUL")
            ):
                # This is ::IDENT::IDENT or ::IDENT::* - IDENT is a path segment
                self.eat("DCOLON")
                module_path.append(self.eat("IDENT").value)
            else:
                # This is ::IDENT followed by comma/semi - stop parsing path
                break

        # Now we should have :: followed by either * or item name(s)
        if self.current().type != "DCOLON":
            raise LeashError(
                f"Expected '::' after module path in import statement",
                self.current().line,
                self.current().column,
                tip="Use 'use module::Item;' or 'use path::to::module::Item;' syntax",
            )
        self.eat("DCOLON")

        imported_items = []
        # Check for wildcard import
        if self.current().type == "MUL":
            self.eat("MUL")
            imported_items = None  # wildcard means import all
        else:
            # Parse specific item(s)
            while True:
                item_name = self.eat("IDENT").value
                imported_items.append(item_name)
                if self.current().type == "COMMA":
                    self.eat("COMMA")
                    continue
                else:
                    break

        self.eat("SEMI")
        return self._pos(ImportStmt(module_path, imported_items, visibility), tok)

    def parse_native_import(self):
        """Parse a native library import: @from("lib.so") { ... };"""
        tok = self.current()
        self.eat("AT")
        self.eat("IDENT")  # eat "from"
        self.eat("LPAREN")
        lib_path = self.eat("STRING").value
        self.eat("RPAREN")
        self.eat("LBRACE")
        func_decls = []
        var_decls = []
        struct_decls = []
        union_decls = []
        enum_decls = []
        typedef_decls = []
        while self.current().type != "RBRACE":
            if self.current().type == "FNC":
                func_decls.append(self.parse_native_decl())
            elif self.current().type == "DEF":
                decl = self.parse_native_def_decl()
                decl_type = decl[0]
                if decl_type == "struct":
                    struct_decls.append(decl)
                elif decl_type == "union":
                    union_decls.append(decl)
                elif decl_type == "enum":
                    enum_decls.append(decl)
                elif decl_type == "typedef":
                    typedef_decls.append(decl)
                else:
                    var_decls.append(decl)
            else:
                var_decls.append(self.parse_native_var_decl())
        self.eat("RBRACE")
        self.eat("SEMI")
        return self._pos(
            NativeImport(
                lib_path,
                func_decls,
                var_decls,
                struct_decls,
                union_decls,
                enum_decls,
                typedef_decls,
            ),
            tok,
        )

    def parse_native_decl(self):
        """Parse a function declaration inside @from block (no body)."""
        self.eat("FNC")
        name = self.eat("IDENT").value
        self.eat("LPAREN")
        args = []
        while self.current().type != "RPAREN":
            arg_name = self.eat("IDENT").value
            arg_type = self.parse_type()
            args.append((arg_name, arg_type))
            if self.current().type == "COMMA":
                self.eat("COMMA")
        self.eat("RPAREN")
        self.eat("COLON")
        return_type = self.parse_type()
        self.eat("SEMI")
        return (name, tuple(args), return_type)

    def parse_native_var_decl(self):
        """Parse a variable declaration inside @from block (no initializer)."""
        name = self.eat("IDENT").value
        self.eat("COLON")
        var_type = self.parse_type()
        self.eat("SEMI")
        return (name, var_type)

    def parse_native_def_decl(self):
        """Parse a def declaration inside @from block (struct, union, enum, typedef, or var)."""
        self.eat("DEF")
        name = self.eat("IDENT").value
        self.eat("COLON")
        if self.current().type == "STRUCT":
            self.eat("STRUCT")
            self.eat("LBRACE")
            fields = []
            while self.current().type != "RBRACE":
                field_name = self.eat("IDENT").value
                self.eat("COLON")
                field_type = self.parse_type()
                self.eat("SEMI")
                fields.append((field_name, field_type))
            self.eat("RBRACE")
            self.eat("SEMI")
            return ("struct", name, fields)
        elif self.current().type == "UNION":
            self.eat("UNION")
            self.eat("LBRACE")
            variants = []
            while self.current().type != "RBRACE":
                var_name = self.eat("IDENT").value
                self.eat("COLON")
                var_type = self.parse_type()
                if self.current().type == "COMMA":
                    self.eat("COMMA")
                elif self.current().type == "SEMI":
                    self.eat("SEMI")
                variants.append((var_name, var_type))
            self.eat("RBRACE")
            self.eat("SEMI")
            return ("union", name, variants)
        elif self.current().type == "ENUM":
            self.eat("ENUM")
            self.eat("LBRACE")
            members = []
            while self.current().type != "RBRACE":
                member_name = self.eat("IDENT").value
                members.append(member_name)
                if self.current().type == "COMMA":
                    self.eat("COMMA")
                elif self.current().type == "SEMI":
                    self.eat("SEMI")
            self.eat("RBRACE")
            self.eat("SEMI")
            return ("enum", name, members)
        elif self.current().type == "TYPE":
            self.eat("TYPE")
            target_type = self.parse_type()
            self.eat("SEMI")
            return ("typedef", name, target_type)
        else:
            var_type = self.parse_type()
            self.eat("SEMI")
            return ("var", name, var_type)

    def parse_def(self, visibility="pub"):
        if self.current().type == "DEF":
            self.eat("DEF")
        # If visibility already consumed, we don't eat DEF again
        name = self.eat("IDENT").value
        self.eat("COLON")
        if self.current().type == "STRUCT":
            return self._parse_struct_body(name, visibility)
        elif self.current().type == "UNION":
            return self._parse_union_body(name, visibility)
        elif self.current().type == "TYPE":
            return self._parse_type_alias_body(name, visibility)
        elif self.current().type == "ENUM":
            return self._parse_enum_body(name, visibility)
        elif self.current().type == "CLASS":
            return self._parse_class_body(name, visibility)
        elif self.current().type == "TEMPLATE":
            return self._parse_template_body(name, visibility)
        else:
            tok = self.current()
            raise LeashError(
                f"Expected 'struct', 'union', 'enum', 'class', 'type' or 'template' after 'def {name} :', but found {tok.type} ('{tok.value}')",
                tok.line,
                tok.column,
                tip="Use `def Name : class { ... };`, `def Name : struct { ... };`, `def Name : enum { ... };`, or `def T : template;`",
            )

    def _parse_struct_body(self, name, visibility="pub"):
        self.eat("STRUCT")
        self.eat("LBRACE")
        fields = []
        while self.current().type != "RBRACE":
            field_name = self.eat("IDENT").value
            self.eat("COLON")
            field_type = self.parse_type()
            self.eat("SEMI")
            fields.append((field_name, field_type))
        self.eat("RBRACE")
        self.eat("SEMI")
        return StructDef(name, fields, visibility)

    def _parse_union_body(self, name, visibility="pub"):
        self.eat("UNION")
        self.eat("LBRACE")
        variants = []
        while self.current().type != "RBRACE":
            var_name = self.eat("IDENT").value
            self.eat("COLON")
            var_type = self.parse_type()
            if self.current().type == "COMMA":
                self.eat("COMMA")
            elif self.current().type == "SEMI":
                self.eat("SEMI")
            variants.append((var_name, var_type))
        self.eat("RBRACE")
        self.eat("SEMI")
        return UnionDef(name, variants, visibility)

    def _parse_enum_body(self, name, visibility="pub"):
        self.eat("ENUM")
        self.eat("LBRACE")
        members = []
        while self.current().type != "RBRACE":
            member_name = self.eat("IDENT").value
            members.append(member_name)
            if self.current().type == "COMMA":
                self.eat("COMMA")
            elif self.current().type == "SEMI":
                # Accept semicolons too if user prefers
                self.eat("SEMI")
        self.eat("RBRACE")
        self.eat("SEMI")
        return EnumDef(name, members, visibility)

    def _parse_type_alias_body(self, name, visibility="pub"):
        self.eat("TYPE")
        target_type = self.parse_type()
        self.eat("SEMI")
        return TypeAlias(name, target_type, visibility)

    def _parse_template_body(self, name, visibility="pub"):
        self.eat("TEMPLATE")
        self.eat("SEMI")
        return TemplateDef(name, visibility)

    def parse_global_var(self):
        """Parse a global variable declaration with optional visibility."""
        visibility = "pub"  # default visibility for top-level vars is public
        if self.current().type in ("PUB", "PRIV"):
            visibility = self.current().value.lower()
            self.eat(self.current().type)
        name = self.eat("IDENT").value
        self.eat("COLON")
        var_type = self.parse_type()
        value = None
        if self.current().type == "ASSIGN":
            self.eat("ASSIGN")
            value = self.parse_expression(no_struct_init=False)
        self.eat("SEMI")
        return GlobalVarDecl(name, var_type, value, visibility)

    def _parse_class_body(self, name, visibility="pub"):
        self.eat("CLASS")
        type_params = []
        parent = None
        # Check for generic parameters: class<T1, T2>
        if self.current().type == "LT":
            self.eat("LT")
            while self.current().type != "GT":
                type_params.append(self.eat("IDENT").value)
                if self.current().type == "COMMA":
                    self.eat("COMMA")
            self.eat("GT")
        # Check for inheritance: class(Parent)
        if self.current().type == "LPAREN":
            self.eat("LPAREN")
            parent = self.eat("IDENT").value
            self.eat("RPAREN")
        self.eat("LBRACE")
        fields = []
        methods = []
        while self.current().type != "RBRACE":
            member_visibility = "pub"
            is_static = False
            is_imut = False

            while self.current().type in ("PUB", "PRIV", "STATIC", "IMUT"):
                tok = self.eat(self.current().type)
                if tok.type == "STATIC":
                    is_static = True
                elif tok.type == "IMUT":
                    is_imut = True
                else:
                    member_visibility = tok.value.lower()

            if self.current().type == "FNC":
                fnc = self.parse_function()
                methods.append(ClassMethod(fnc, member_visibility, is_static, is_imut))
            else:
                field_name = self.eat("IDENT").value
                self.eat("COLON")
                field_type = self.parse_type()
                self.eat("SEMI")
                fields.append(ClassField(field_name, field_type, member_visibility))
        self.eat("RBRACE")
        # self.eat('SEMI') # Optional for classes
        if self.current().type == "SEMI":
            self.eat("SEMI")
        return ClassDef(name, fields, methods, parent, type_params, visibility)

    def parse_function(self, visibility="pub"):
        self.eat("FNC")
        name = self.eat("IDENT").value
        type_params = []
        # Check for generic parameters: fnc add<T>(a T, b T)
        if self.current().type == "LT":
            self.eat("LT")
            while self.current().type != "GT":
                type_params.append(self.eat("IDENT").value)
                if self.current().type == "COMMA":
                    self.eat("COMMA")
            self.eat("GT")
        self.eat("LPAREN")
        args = []
        while self.current().type != "RPAREN":
            arg_name = self.eat("IDENT").value
            arg_type = self.parse_type()
            args.append((arg_name, arg_type))
            if self.current().type == "COMMA":
                self.eat("COMMA")
        self.eat("RPAREN")
        if self.current().type != "COLON":
            tok = self.current()
            raise LeashError(
                f"Expected a colon (':') before the return type, but found {tok.type} ('{tok.value}')",
                tok.line,
                tok.column,
                tip="Leash functions require a colon before the return type: `fnc name() : type { ... }`",
            )
        self.eat("COLON")

        return_type = self.parse_type()

        self.eat("LBRACE")
        statements = self.parse_block()
        return Function(
            name, tuple(args), return_type, statements, type_params, visibility
        )

    def parse_block(self):
        statements = []
        while self.current().type != "RBRACE":
            statements.append(self.parse_statement())
        self.eat("RBRACE")
        return statements

    def parse_statement(self):
        current = self.current()
        if current.type == "IDENT":
            self._check_keyword_misuse(current)

        if current.type == "IF":
            tok = self.current()
            self.eat("IF")
            cond = self.parse_expression(no_struct_init=True)
            self.eat("LBRACE")
            then_block = self.parse_block()
            also_blocks = []
            while self.current().type == "ALSO":
                self.eat("ALSO")
                also_cond = self.parse_expression(no_struct_init=True)
                self.eat("LBRACE")
                also_blocks.append((also_cond, self.parse_block()))
            if (
                self.current().type == "ELSE"
                and self.peek()
                and self.peek().type == "IF"
            ):
                # Catch else if for helpful tip
                tok = self.current()
                raise LeashError(
                    "Found 'else if' instead of 'also'",
                    tok.line,
                    tok.column,
                    tip="Leash uses the `also` keyword for 'else if' conditions! (e.g., `if a { ... } also b { ... }`) ",
                )

            else_block = None
            if self.current().type == "ELSE":
                self.eat("ELSE")
                self.eat("LBRACE")
                else_block = self.parse_block()
            return self._pos(
                IfStatement(cond, then_block, also_blocks, else_block), tok
            )

        elif current.type == "WORKS":
            tok = self.current()
            self.eat("WORKS")
            self.eat("LBRACE")
            body = self.parse_block()
            self.eat("OTHERWISE")
            err_var = self.eat("IDENT").value
            self.eat("LBRACE")
            otherwise_block = self.parse_block()
            return self._pos(
                WorksOtherwiseStatement(body, err_var, otherwise_block), tok
            )

        elif current.type == "SWITCH":
            tok = self.current()
            self.eat("SWITCH")
            expr = self.parse_expression(no_struct_init=True)
            self.eat("LBRACE")
            cases = []
            default_block = None
            while self.current().type != "RBRACE":
                if self.current().type == "CASE":
                    self.eat("CASE")
                    case_expr = self.parse_expression(no_struct_init=True)
                    self.eat("LBRACE")
                    case_body = self.parse_block()
                    cases.append((case_expr, case_body))
                elif self.current().type == "DEFAULT":
                    self.eat("DEFAULT")
                    self.eat("LBRACE")
                    default_block = self.parse_block()
                else:
                    raise LeashError(
                        f"Expected 'case' or 'default' inside switch block, but found {self.current().type}",
                        self.current().line,
                        self.current().column,
                    )
            self.eat("RBRACE")
            return self._pos(SwitchStatement(expr, cases, default_block), tok)

        elif current.type == "WHILE":
            tok = self.current()
            self.eat("WHILE")
            cond = self.parse_expression(no_struct_init=True)
            self.eat("LBRACE")
            body = self.parse_block()
            return self._pos(WhileStatement(cond, body), tok)

        elif current.type == "FOR":
            tok = self.current()
            self.eat("FOR")
            init = self.parse_statement()
            cond = self.parse_expression(no_struct_init=True)
            self.eat("SEMI")
            step_target = (
                self.parse_primary()
            )  # For simplicity let's assume primary is LValue
            while self.current().type == "DOT":
                self.eat("DOT")
                prop = self.eat("IDENT").value
                step_target = MemberAccess(step_target, prop)
            self.eat("ASSIGN")
            step_val = self.parse_expression()
            step_stmt = Assignment(step_target, step_val)
            self.eat("LBRACE")
            body = self.parse_block()
            return self._pos(ForStatement(init, cond, step_stmt, body), tok)

        elif current.type == "DO":
            tok = self.current()
            self.eat("DO")
            self.eat("LBRACE")
            body = self.parse_block()
            self.eat("WHILE")
            cond = self.parse_expression()
            self.eat("SEMI")
            return self._pos(DoWhileStatement(cond, body), tok)

        elif current.type == "FOREACH":
            tok = self.current()
            self.eat("FOREACH")
            name_var = self.eat("IDENT").value
            self.eat("COMMA")
            value_var = self.eat("IDENT").value
            self.eat("IN")
            self.eat("LT")
            iterable_type = self.current().type  # STRUCT, ARRAY, STRING, VECTOR
            if iterable_type in ("STRUCT", "ARRAY", "STRING", "VECTOR"):
                self.eat(iterable_type)
            else:
                raise LeashError(
                    f"Expected 'struct', 'array', 'string' or 'vector' in foreach loop, but got {iterable_type}",
                    self.current().line,
                    self.current().column,
                )
            self.eat("GT")
            expr = self.parse_expression(no_struct_init=True)
            self.eat("LBRACE")
            body = self.parse_block()
            if iterable_type == "STRUCT":
                return self._pos(
                    ForeachStructStatement(name_var, value_var, expr, body), tok
                )
            elif iterable_type == "ARRAY":
                return self._pos(
                    ForeachArrayStatement(name_var, value_var, expr, body), tok
                )
            elif iterable_type == "STRING":
                return self._pos(
                    ForeachStringStatement(name_var, value_var, expr, body), tok
                )
            elif iterable_type == "VECTOR":
                return self._pos(
                    ForeachVectorStatement(name_var, value_var, expr, body), tok
                )
            else:
                raise LeashError(
                    f"INTERNAL ERROR: unhandled foreach type {iterable_type}",
                    tok.line,
                    tok.column,
                )

        elif current.type in ("RETURN",):
            tok = self.current()
            self.eat("RETURN")
            expr = self.parse_expression()
            self.eat("SEMI")
            return self._pos(ReturnStatement(expr), tok)

        elif current.type in ("STOP",):
            tok = self.current()
            self.eat("STOP")
            self.eat("SEMI")
            return self._pos(StopStatement(), tok)

        elif current.type in ("CONTINUE",):
            tok = self.current()
            self.eat("CONTINUE")
            self.eat("SEMI")
            return self._pos(ContinueStatement(), tok)

        elif self.current().type in ("IDENT", "THIS", "MUL", "BIT_AND"):
            # Could be assignment or function call or show
            if self.current().type == "IDENT" and self.current().value == "show":
                tok = self.current()
                self.eat("IDENT")
                self.eat("LPAREN")
                args = []
                while self.current().type != "RPAREN":
                    args.append(self.parse_expression())
                    if self.current().type == "COMMA":
                        self.eat("COMMA")
                self.eat("RPAREN")
                self.eat("SEMI")
                return self._pos(ShowStatement(args), tok)
            elif self.tokens[self.pos + 1].type == "COLON":
                # Variable declaration: name : type = expr ;
                tok = self.current()
                name = self.eat("IDENT").value
                self.eat("COLON")
                var_type = self.parse_type()
                expr = None
                if self.current().type == "ASSIGN":
                    self.eat("ASSIGN")
                    expr = self.parse_expression()
                self.eat("SEMI")
                return self._pos(VariableDecl(name, var_type, expr), tok)
            else:
                tok = self.current()
                expr = self.parse_expression()
                if self.current().type == "ASSIGN":
                    self.eat("ASSIGN")
                    val = self.parse_expression()
                    self.eat("SEMI")
                    return self._pos(Assignment(expr, val), tok)
                self.eat("SEMI")
                return self._pos(ExpressionStatement(expr), tok)
        else:
            tok = self.current()
            tip = self._get_smart_tip(None, tok)
            raise LeashError(
                f"Unexpected token {tok.type} ('{tok.value}') at start of statement",
                tok.line,
                tok.column,
                tip=tip,
            )

    def _check_keyword_misuse(self, token):
        val = token.value.lower()
        tips = {
            "elif": "Leash uses `also` for 'else if' conditions! (e.g., `if a { ... } also b { ... }`)",
            "elseif": "Leash uses `also` for 'else if' conditions! (e.g., `if a { ... } also b { ... }`)",
            "print": 'Leash uses `show()` for console output! Example: `show("Hello");`',
            "println": 'Leash uses `show()` for console output! Example: `show("Hello");`',
            "printf": "Leash uses `show()` for console output! (No format strings needed, it's smart!)",
            "def": "Top-level definitions use `def`, but functions use `fnc`! Example: `fnc main() : void { ... }`",
            "func": "Leash uses the short `fnc` keyword for functions!",
            "function": "Leash uses the short `fnc` keyword for functions!",
            "let": "Leash variables are declared as `name : type = value;`. No `let` or `var` needed!",
            "var": "Leash variables are declared as `name : type = value;`. No `let` or `var` needed!",
            "const": "Leash uses `imut` for immutable variables! Example: `a: imut int = 10;`",
            "import": "Leash currently supports single-file compilation. Modules are coming soon!",
            "include": "Looking for `#include`? Leash manages built-ins like `show` and `strlen` automatically.",
            "using": "Leash doesn't have namespaces or `using` statements yet.",
            "interface": "Leash uses `union` for flexible types and `struct` for data.",
            "switch": "Leash uses `switch / case / default` for multi-way branching! Example: `switch val { case 1 { ... } default { ... } }`",
            "match": "Leash uses `switch / case / default` for multi-way branching! Example: `switch val { case 1 { ... } default { ... } }`",
            "byte": "For 8-bit integers, use `int<8>` or `uint<8>`.",
            "long": "For 64-bit integers, use `int<64>` or `uint<64>`.",
            "double": "Leash `float` is 64-bit by default! Use `float<32>` if you want single-precision.",
            "self": "Leash uses `this` inside methods!",
            "nil": "Use a `void` member in a `union` for 'nothing' or 'null' values.",
            "null": "Use a `void` member in a `union` for 'nothing' or 'null' values.",
            "boolean": "Leash uses the short `bool` type name.",
        }
        if val in tips:
            raise LeashError(
                f"Found unsupported keyword '{token.value}'",
                token.line,
                token.column,
                tip=tips[val],
            )

    def parse_expression(self, no_struct_init=False):
        return self.parse_ternary(no_struct_init)

    def parse_ternary(self, no_struct_init=False):
        node = self.parse_logical_or(no_struct_init)
        if self.current().type == "QUESTION":
            self.eat("QUESTION")
            true_expr = self.parse_expression(no_struct_init)
            self.eat("COLON")
            false_expr = self.parse_expression(no_struct_init)
            node = TernaryOp(condition=node, true_expr=true_expr, false_expr=false_expr)
        return node

    def parse_logical_or(self, no_struct_init=False):
        node = self.parse_logical_and(no_struct_init)
        while self.current().type == "L_OR":
            op = self.eat("L_OR")
            right = self.parse_logical_and(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_logical_and(self, no_struct_init=False):
        node = self.parse_bitwise_or(no_struct_init)
        while self.current().type == "L_AND":
            op = self.eat("L_AND")
            right = self.parse_bitwise_or(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_bitwise_or(self, no_struct_init=False):
        node = self.parse_bitwise_xor(no_struct_init)
        while self.current().type == "BIT_OR":
            op = self.eat("BIT_OR")
            right = self.parse_bitwise_xor(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_bitwise_xor(self, no_struct_init=False):
        node = self.parse_bitwise_and(no_struct_init)
        while self.current().type == "BIT_XOR":
            op = self.eat("BIT_XOR")
            right = self.parse_bitwise_and(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_bitwise_and(self, no_struct_init=False):
        node = self.parse_comparison(no_struct_init)
        while self.current().type == "BIT_AND":
            op = self.eat("BIT_AND")
            right = self.parse_comparison(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_comparison(self, no_struct_init=False):
        node = self.parse_shift(no_struct_init)
        while self.current().type in ("EQ", "NEQ", "LT", "LTE", "GT", "GTE"):
            op = self.current()
            self.eat(op.type)
            right = self.parse_shift(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_shift(self, no_struct_init=False):
        node = self.parse_term(no_struct_init)
        while self.current().type in ("SHL", "SHR"):
            op = self.current()
            self.eat(op.type)
            right = self.parse_term(no_struct_init)
            node = BinaryOp(left=node, op=op.value, right=right)
        return node

    def parse_term(self, no_struct_init=False):
        left = self.parse_factor(no_struct_init)
        while self.current().type in ("PLUS", "MINUS"):
            op = self.eat(self.current().type).value
            right = self.parse_factor(no_struct_init)
            left = BinaryOp(left, op, right)
        return left

    def parse_factor(self, no_struct_init=False):
        left = self.parse_unary(no_struct_init)
        while self.current().type in ("MUL", "DIV", "MOD"):
            op = self.eat(self.current().type).value
            right = self.parse_unary(no_struct_init)
            left = BinaryOp(left, op, right)
        return left

    def parse_unary(self, no_struct_init=False):
        if self.current().type in ("NOT", "BIT_NOT", "MINUS", "MUL", "BIT_AND"):
            op = self.eat(self.current().type)
            expr = self.parse_unary(no_struct_init)
            return UnaryOp(op.value, expr)
        return self.parse_postfix(no_struct_init)

    def parse_postfix(self, no_struct_init=False):
        expr = self.parse_primary(no_struct_init)
        while self.current().type in ("DOT", "LBRACKET", "ARROW"):
            if self.current().type == "DOT":
                self.eat("DOT")
                member = self.eat("IDENT").value
                if self.current().type == "LPAREN":
                    self.eat("LPAREN")
                    args = []
                    while self.current().type != "RPAREN":
                        args.append(self.parse_expression())
                        if self.current().type == "COMMA":
                            self.eat("COMMA")
                    self.eat("RPAREN")
                    expr = MethodCall(expr, member, args)
                else:
                    expr = MemberAccess(expr, member)
            elif self.current().type == "ARROW":
                self.eat("ARROW")
                member = self.eat("IDENT").value
                expr = PointerMemberAccess(expr, member)
            elif self.current().type == "LBRACKET":
                self.eat("LBRACKET")
                index = self.parse_expression()
                self.eat("RBRACKET")
                expr = IndexAccess(expr, index)
        return expr

    def parse_primary(self, no_struct_init=False):
        if self.current().type == "NUMBER":
            tok = self.current()
            val = self.eat("NUMBER").value
            if isinstance(val, float):
                return self._pos(FloatLiteral(val), tok)
            return self._pos(NumberLiteral(val), tok)
        elif self.current().type == "STRING":
            tok = self.current()
            return self._pos(StringLiteral(self.eat("STRING").value), tok)
        elif self.current().type == "CHAR":
            tok = self.current()
            return self._pos(CharLiteral(self.eat("CHAR").value), tok)
        elif self.current().type == "TRUE":
            tok = self.current()
            self.eat("TRUE")
            return self._pos(BoolLiteral(True), tok)
        elif self.current().type == "FALSE":
            tok = self.current()
            self.eat("FALSE")
            return self._pos(BoolLiteral(False), tok)
        elif self.current().type == "NULL" or self.current().type == "NIL":
            tok = self.current()
            self.eat(self.current().type)
            return self._pos(NullLiteral(), tok)
        elif self.current().type == "THIS":
            tok = self.current()
            self.eat("THIS")
            return self._pos(ThisExpr(), tok)
        elif self.current().type == "IDENT":
            tok = self.current()
            name = self.eat("IDENT").value
            # Check for special file path literals
            if name in ("_FILEPATH", "_FILENAME"):
                return self._pos(FilePathLiteral(name, self.source_file), tok)
            if name in ("toint", "tofloat") and self.current().type == "LPAREN":
                self.eat("LPAREN")
                target_type = self.parse_type()
                self.eat("COMMA")
                value = self.parse_expression()
                self.eat("RPAREN")
                return self._pos(TypeConvExpr(name, target_type, value), tok)

            # Check for generic function call: name<T>(args)
            if self.current().type == "LT":
                # Look ahead to check if this is a generic call
                saved_pos = self.pos
                try:
                    self.eat("LT")
                    type_args = []
                    # Parse type arguments
                    while self.current().type != "GT":
                        # Type can be a simple identifier or a compound type like vec<T>
                        if self.current().type in (
                            "IDENT",
                            "VEC",
                            "INT",
                            "UINT",
                            "FLOAT",
                            "STRING",
                            "CHAR",
                            "BOOL",
                        ):
                            type_args.append(self.parse_type())
                        else:
                            # Not a valid type argument, not a generic call
                            self.pos = saved_pos
                            break
                        if self.current().type == "COMMA":
                            self.eat("COMMA")
                    else:
                        self.eat("GT")
                        if self.current().type == "LPAREN":
                            self.eat("LPAREN")
                            args = []
                            while self.current().type != "RPAREN":
                                args.append(self.parse_expression())
                                if self.current().type == "COMMA":
                                    self.eat("COMMA")
                            self.eat("RPAREN")
                            return self._pos(GenericCall(name, type_args, args), tok)
                        else:
                            # Not a generic call, restore position
                            self.pos = saved_pos
                except LeashError:
                    # Not a generic call, restore position
                    self.pos = saved_pos

            if self.current().type == "LPAREN":
                self.eat("LPAREN")
                args = []
                while self.current().type != "RPAREN":
                    args.append(self.parse_expression())
                    if self.current().type == "COMMA":
                        self.eat("COMMA")
                self.eat("RPAREN")
                return self._pos(Call(name, args), tok)
            elif not no_struct_init and self.current().type == "LBRACE":
                self.eat("LBRACE")
                kwargs = []
                while self.current().type != "RBRACE":
                    key = self.eat("IDENT").value
                    self.eat("COLON")
                    val = self.parse_expression()
                    kwargs.append((key, val))
                    if self.current().type == "COMMA":
                        self.eat("COMMA")
                self.eat("RBRACE")
                return self._pos(StructInit(name, kwargs), tok)

            if self.current().type == "DCOLON":
                self.eat("DCOLON")
                member = self.eat("IDENT").value
                return self._pos(EnumMemberAccess(name, member), tok)

            return self._pos(Identifier(name), tok)
        elif self.current().type == "LPAREN":
            # Check if this is a cast: (type)expr
            # We look ahead to see if there's a type name followed by RPAREN
            if self._is_cast():
                self.eat("LPAREN")
                cast_type = self.parse_type()
                self.eat("RPAREN")
                expr = self.parse_postfix(no_struct_init)
                return CastExpr(cast_type, expr)
            self.eat("LPAREN")
            expr = self.parse_expression()
            self.eat("RPAREN")
            return expr
        elif self.current().type == "LBRACE":
            self.eat("LBRACE")
            elements = []
            while self.current().type != "RBRACE":
                elements.append(self.parse_expression())
                if self.current().type == "COMMA":
                    self.eat("COMMA")
            self.eat("RBRACE")
            return ArrayInit(elements)
        else:
            tok = self.current()
            raise LeashError(
                f"Unexpected token {tok.type} ('{tok.value}') in expression",
                tok.line,
                tok.column,
            )
