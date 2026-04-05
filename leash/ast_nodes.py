class ASTNode:
    def __init__(self):
        self.line = None
        self.col = None


class Program(ASTNode):
    def __init__(self, items):
        self.items = items


class StructDef(ASTNode):
    def __init__(self, name, fields, visibility="pub"):
        self.name = name
        self.fields = fields  # list of (name, type) tuples
        self.visibility = visibility  # 'pub' or 'priv'


class TypeAlias(ASTNode):
    def __init__(self, name, target_type, visibility="pub"):
        self.name = name
        self.target_type = target_type  # string type name
        self.visibility = visibility  # 'pub' or 'priv'


class UnionDef(ASTNode):
    def __init__(self, name, variants, visibility="pub"):
        self.name = name
        self.variants = variants  # list of (name, type) tuples
        self.visibility = visibility  # 'pub' or 'priv'


class EnumDef(ASTNode):
    def __init__(self, name, members, visibility="pub"):
        self.name = name
        self.members = members  # list of member names
        self.visibility = visibility  # 'pub' or 'priv'


class Function(ASTNode):
    def __init__(
        self, name, args, return_type, body, type_params=None, visibility="pub"
    ):
        self.name = name
        self.args = args  # list of (name, type) tuples
        self.return_type = return_type
        self.body = body  # Block
        self.type_params = type_params or []  # list of template parameter names
        self.visibility = visibility  # 'pub' or 'priv'


class Block(ASTNode):
    def __init__(self, statements):
        self.statements = statements


class Statement(ASTNode):
    pass


class VariableDecl(Statement):
    def __init__(self, name, var_type, value):
        self.name = name
        self.var_type = var_type
        self.value = value


class Assignment(Statement):
    def __init__(self, target, value):
        self.target = target
        self.value = value


class IfStatement(Statement):
    def __init__(self, condition, then_block, also_blocks, else_block):
        self.condition = condition
        self.then_block = then_block
        self.also_blocks = also_blocks  # list of (condition, block)
        self.else_block = else_block


class WhileStatement(Statement):
    def __init__(self, condition, body):
        self.condition = condition
        self.body = body


class ForStatement(Statement):
    def __init__(self, init, condition, step, body):
        self.init = init
        self.condition = condition
        self.step = step
        self.body = body


class DoWhileStatement(Statement):
    def __init__(self, condition, body):
        self.condition = condition
        self.body = body


class ForeachStructStatement(Statement):
    def __init__(self, name_var, value_var, struct_expr, body):
        self.name_var = name_var
        self.value_var = value_var
        self.struct_expr = struct_expr
        self.body = body


class ForeachArrayStatement(Statement):
    def __init__(self, index_var, value_var, array_expr, body):
        self.index_var = index_var
        self.value_var = value_var
        self.array_expr = array_expr
        self.body = body


class ForeachStringStatement(Statement):
    def __init__(self, index_var, char_var, string_expr, body):
        self.index_var = index_var
        self.char_var = char_var
        self.string_expr = string_expr
        self.body = body


class ForeachVectorStatement(Statement):
    def __init__(self, index_var, value_var, vector_expr, body):
        self.index_var = index_var
        self.value_var = value_var
        self.vector_expr = vector_expr
        self.body = body


class ReturnStatement(Statement):
    def __init__(self, value):
        self.value = value


class StopStatement(Statement):
    def __init__(self):
        pass


class ContinueStatement(Statement):
    def __init__(self):
        pass


class ExpressionStatement(Statement):
    def __init__(self, expr):
        self.expr = expr


class ShowStatement(Statement):
    def __init__(self, args):
        self.args = args


class Expression(ASTNode):
    pass


class BinaryOp(Expression):
    def __init__(self, left, op, right):
        self.left = left
        self.op = op
        self.right = right


class UnaryOp(Expression):
    def __init__(self, op, expr):
        self.op = op
        self.expr = expr


class Call(Expression):
    def __init__(self, name, args):
        self.name = name
        self.args = args


class MethodCall(Expression):
    def __init__(self, expr, method, args):
        self.expr = expr
        self.method = method
        self.args = args


class Identifier(Expression):
    def __init__(self, name):
        self.name = name


class MemberAccess(Expression):
    def __init__(self, expr, member):
        self.expr = expr
        self.member = member


class PointerMemberAccess(Expression):
    def __init__(self, expr, member):
        self.expr = expr
        self.member = member


class EnumMemberAccess(Expression):
    def __init__(self, enum_name, member_name):
        self.enum_name = enum_name
        self.member_name = member_name


class CastExpr(Expression):
    def __init__(self, target_type, expr):
        self.target_type = target_type
        self.expr = expr


class IndexAccess(Expression):
    def __init__(self, expr, index):
        self.expr = expr
        self.index = index


class StructInit(Expression):
    def __init__(self, name, kwargs):
        self.name = name
        self.kwargs = kwargs  # list of (field_name, expr)


class ArrayInit(Expression):
    def __init__(self, elements):
        self.elements = elements


class NumberLiteral(Expression):
    def __init__(self, value):
        self.value = value


class FloatLiteral(Expression):
    def __init__(self, value):
        self.value = value


class StringLiteral(Expression):
    def __init__(self, value):
        self.value = value


class CharLiteral(Expression):
    def __init__(self, value):
        self.value = value


class BoolLiteral(Expression):
    def __init__(self, value):
        self.value = value


class NullLiteral(Expression):
    def __init__(self):
        pass


class FilePathLiteral(Expression):
    """Represents a special file path literal like _FILEPATH or _FILENAME."""

    def __init__(self, name, source_file=None):
        self.name = name  # "_FILEPATH" or "_FILENAME"
        self.source_file = source_file  # Path to the source file


class BuiltinVarLiteral(Expression):
    """Represents a special built-in variable like _PLATFORM."""

    def __init__(self, name):
        self.name = name  # "_PLATFORM"


class ClassDef(ASTNode):
    def __init__(
        self, name, fields, methods, parent=None, type_params=None, visibility="pub"
    ):
        self.name = name
        self.fields = fields  # list of ClassField
        self.methods = methods  # list of ClassMethod
        self.parent = parent  # parent class name (for inheritance)
        self.type_params = type_params or []  # list of template parameter names
        self.visibility = visibility  # 'pub' or 'priv'


class ClassField(ASTNode):
    def __init__(self, name, var_type, visibility):
        self.name = name
        self.var_type = var_type
        self.visibility = visibility  # 'pub' or 'priv'


class ClassMethod(ASTNode):
    def __init__(self, fnc, visibility, is_static=False, is_imut=False):
        self.fnc = fnc  # Function node
        self.visibility = visibility  # 'pub' or 'priv'
        self.is_static = is_static
        self.is_imut = is_imut  # If true, method cannot be overridden by subclasses


class ThisExpr(Expression):
    def __init__(self):
        pass


class TypeConvExpr(Expression):
    def __init__(self, name, target_type, expr):
        self.name = name  # 'toint', 'tofloat', etc.
        self.target_type = target_type
        self.expr = expr


class TemplateDef(ASTNode):
    """Represents a template parameter definition like 'def T1 : template;'"""

    def __init__(self, name, visibility="pub"):
        self.name = name
        self.visibility = visibility  # 'pub' or 'priv'


class GlobalVarDecl(ASTNode):
    """Represents a global variable declaration with visibility (pub/priv)."""

    def __init__(self, name, var_type, value, visibility):
        self.name = name
        self.var_type = var_type
        self.value = value  # expression or None
        self.visibility = visibility  # 'pub' or 'priv'


class ImportStmt(ASTNode):
    """Represents an import statement like 'use hash::Hash;' or 'use subfolder::module::Item;'"""

    def __init__(self, module_path, imported_items, visibility="pub"):
        super().__init__()
        # module_path is a list: ["hash"] or ["subfolder", "helpers"]
        self.module_path = module_path
        self.imported_items = (
            imported_items  # list of item names to import, or None for all
        )
        self.visibility = visibility  # 'pub' or 'priv'

    @property
    def module_name(self):
        """Backward compatibility: returns the first path segment"""
        return self.module_path[0] if self.module_path else None


class TernaryOp(Expression):
    def __init__(self, condition, true_expr, false_expr):
        self.condition = condition
        self.true_expr = true_expr
        self.false_expr = false_expr


class GenericCall(Expression):
    """Represents a generic function call like 'add<int>(10, 20)'"""

    def __init__(self, name, type_args, args):
        self.name = name
        self.type_args = type_args  # list of type strings
        self.args = args  # list of expressions


class WorksOtherwiseStatement(Statement):
    """Try-catch-like construct: works { body } otherwise err_var { handler }"""

    def __init__(self, body, err_var, otherwise_block):
        self.body = body  # list of statements in the works block
        self.err_var = err_var  # string: name of the error variable
        self.otherwise_block = (
            otherwise_block  # list of statements in the otherwise block
        )


class SwitchStatement(Statement):
    """Switch-case construct: switch expr { case val { body } ... default { body } }"""

    def __init__(self, expression, cases, default_block=None):
        self.expression = expression  # expression to switch on
        self.cases = cases  # list of (case_expression, block) tuples
        self.default_block = (
            default_block  # optional default block (list of statements)
        )


class NativeImport(ASTNode):
    """Represents a native library import with functions, variables, structs, unions, enums, and typedefs."""

    def __init__(
        self,
        lib_path,
        func_declarations,
        var_declarations,
        struct_declarations,
        union_declarations,
        enum_declarations,
        typedef_declarations,
    ):
        super().__init__()
        self.lib_path = lib_path
        self.func_declarations = (
            func_declarations  # list of (name, args, return_type) tuples
        )
        self.var_declarations = var_declarations  # list of (name, var_type) tuples
        self.struct_declarations = (
            struct_declarations  # list of ("struct", name, fields) tuples
        )
        self.union_declarations = (
            union_declarations  # list of ("union", name, variants) tuples
        )
        self.enum_declarations = (
            enum_declarations  # list of ("enum", name, members) tuples
        )
        self.typedef_declarations = (
            typedef_declarations  # list of ("typedef", name, target_type) tuples
        )
