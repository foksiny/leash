class ASTNode:
    def __init__(self):
        self.line = None
        self.col = None

class Program(ASTNode):
    def __init__(self, items):
        self.items = items

class StructDef(ASTNode):
    def __init__(self, name, fields):
        self.name = name
        self.fields = fields # list of (name, type) tuples

class TypeAlias(ASTNode):
    def __init__(self, name, target_type):
        self.name = name
        self.target_type = target_type # string type name

class UnionDef(ASTNode):
    def __init__(self, name, variants):
        self.name = name
        self.variants = variants # list of (name, type) tuples

class EnumDef(ASTNode):
    def __init__(self, name, members):
        self.name = name
        self.members = members # list of member names

class Function(ASTNode):
    def __init__(self, name, args, return_type, body):
        self.name = name
        self.args = args # list of (name, type) tuples
        self.return_type = return_type
        self.body = body # Block

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
        self.also_blocks = also_blocks # list of (condition, block)
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

class ReturnStatement(Statement):
    def __init__(self, value):
        self.value = value

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

class Identifier(Expression):
    def __init__(self, name):
        self.name = name

class MemberAccess(Expression):
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
        self.kwargs = kwargs # list of (field_name, expr)

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

class TypeConvExpr(Expression):
    def __init__(self, name, target_type, expr):
        self.name = name # 'toint', 'tofloat', etc.
        self.target_type = target_type
        self.expr = expr
