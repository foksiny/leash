import llvmlite.ir as ir
import llvmlite.binding as llvm
from .errors import LeashError

class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="leash_module")
        self.builder = None
        self.func_symtab = {}
        self.var_symtab = {}
        self.struct_symtab = {}
        self.type_aliases = {}   # name -> resolved type string
        self.union_symtab = {}   # name -> { 'type': ir_type, 'variants': [...], 'variant_types': {...}, 'max_size': int }
        self.printf = None
        self.current_target_type = None
        
        self.setup_builtins()

    def setup_builtins(self):
        printf_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], var_arg=True)
        self.printf = ir.Function(self.module, printf_ty, name="printf")

        strlen_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
        self.strlen = ir.Function(self.module, strlen_ty, name="strlen")

        malloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(32)])
        self.malloc = ir.Function(self.module, malloc_ty, name="malloc")

        strcpy_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
        self.strcpy = ir.Function(self.module, strcpy_ty, name="strcpy")

        strncpy_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(32)])
        self.strncpy = ir.Function(self.module, strncpy_ty, name="strncpy")

        strcat_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
        self.strcat = ir.Function(self.module, strcat_ty, name="strcat")

        strcmp_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
        self.strcmp = ir.Function(self.module, strcmp_ty, name="strcmp")

        strstr_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
        self.strstr = ir.Function(self.module, strstr_ty, name="strstr")

        exit_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
        self.exit_fn = ir.Function(self.module, exit_ty, name="exit")

        fprintf_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()], var_arg=True)
        self.fprintf = ir.Function(self.module, fprintf_ty, name="fprintf")

        # stderr - declared as external global
        self.stderr_var = ir.GlobalVariable(self.module, ir.IntType(8).as_pointer(), name="stderr")
        self.stderr_var.linkage = 'external'

    def generate_code(self, node):
        return self._codegen(node)

    def _codegen(self, node):
        method_name = f'_codegen_{type(node).__name__}'
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        else:
            raise NotImplementedError(f"No codegen for {type(node).__name__}")

    def _resolve_type_name(self, type_name):
        """Resolve type aliases to their underlying type."""
        visited = set()
        while type_name in self.type_aliases and type_name not in visited:
            visited.add(type_name)
            type_name = self.type_aliases[type_name]
        return type_name

    def _codegen_Program(self, node):
        for item in node.items:
            self._codegen(item)

    def _get_llvm_type(self, type_name, is_return=False):
        # Resolve aliases first
        type_name = self._resolve_type_name(type_name)

        if type_name.endswith(']') and '[' in type_name:
            base = type_name.split('[')[0]
            base_type = self._get_llvm_type(base)
            return ir.LiteralStructType([ir.IntType(32), base_type.as_pointer()])
            
        if type_name in ('int', 'uint'):
            return ir.IntType(32)
        elif type_name.startswith('int<'):
            size = int(type_name[4:-1])
            return ir.IntType(size)
        elif type_name.startswith('uint<'):
            size = int(type_name[5:-1])
            return ir.IntType(size)
        elif type_name == 'void':
            return ir.VoidType() if is_return else ir.IntType(8).as_pointer()
        elif type_name == 'string':
            return ir.IntType(8).as_pointer()
        elif type_name == 'char':
            return ir.IntType(8)
        elif type_name == 'bool':
            return ir.IntType(1)
        elif type_name == 'float':
            return ir.DoubleType() # standard float maps to standard float literals size
        elif type_name.startswith('float<'):
            size = int(type_name[6:-1])
            if size == 16: return ir.HalfType()
            elif size == 32: return ir.FloatType()
            elif size == 64: return ir.DoubleType()
            elif size == 128: return ir.FP128Type()
            else: return ir.DoubleType()
        elif type_name in self.union_symtab:
            return self.union_symtab[type_name]['type']
        elif type_name in self.struct_symtab:
            return self.struct_symtab[type_name]['type']
        return ir.IntType(32) # default fallback

    def _codegen_StructDef(self, node):
        llvm_types = []
        fields = {}
        field_types = {}
        for idx, (fname, ftype) in enumerate(node.fields):
            fields[fname] = idx
            field_types[fname] = ftype
            llvm_types.append(self._get_llvm_type(ftype))
        
        struct_type = ir.LiteralStructType(llvm_types)
        self.struct_symtab[node.name] = {
            'type': struct_type,
            'fields': fields,
            'field_types': field_types
        }

    def _codegen_TypeAlias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _codegen_UnionDef(self, node):
        # Compute the max size needed for any variant
        variant_info = {}
        max_size = 0
        for idx, (vname, vtype) in enumerate(node.variants):
            llvm_ty = self._get_llvm_type(vtype)
            # Estimate byte size from LLVM type
            size = self._type_byte_size(llvm_ty)
            if size > max_size:
                max_size = size
            variant_info[vname] = {'index': idx, 'type_name': vtype, 'llvm_type': llvm_ty, 'size': size}
        
        # Union layout: { i32 tag, [max_size x i8] }
        if max_size < 8:
            max_size = 8  # minimum 8 bytes for pointer-sized data
        union_type = ir.LiteralStructType([ir.IntType(32), ir.ArrayType(ir.IntType(8), max_size)])
        
        self.union_symtab[node.name] = {
            'type': union_type,
            'variants': variant_info,
            'max_size': max_size
        }

    def _type_byte_size(self, llvm_ty):
        """Estimate byte size of an LLVM type."""
        if isinstance(llvm_ty, ir.IntType):
            return max(1, llvm_ty.width // 8)
        elif isinstance(llvm_ty, ir.FloatType):
            return 4
        elif isinstance(llvm_ty, ir.DoubleType):
            return 8
        elif isinstance(llvm_ty, ir.HalfType):
            return 2
        elif isinstance(llvm_ty, ir.PointerType):
            return 8  # 64-bit
        elif isinstance(llvm_ty, ir.LiteralStructType):
            return sum(self._type_byte_size(e) for e in llvm_ty.elements)
        elif isinstance(llvm_ty, ir.ArrayType):
            return self._type_byte_size(llvm_ty.element) * llvm_ty.count
        return 8  # assume pointer size as fallback

    def _codegen_Function(self, node):
        self.var_symtab = {}
        
        # Determine return type
        ret_type = self._get_llvm_type(node.return_type, is_return=True)
        
        # Determine argument types
        arg_types = []
        for name, typ in node.args:
            arg_types.append(self._get_llvm_type(typ, is_return=False))
        
        func_type = ir.FunctionType(ret_type, arg_types)
        
        # Main function usually needs to be i32 main() in standard C compilation
        name = node.name
        if name == 'main' and node.return_type == 'void':
            ret_type = ir.IntType(32)
            func_type = ir.FunctionType(ret_type, arg_types)

        func = ir.Function(self.module, func_type, name=name)
        self.func_symtab[name] = func

        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        # Allocate args
        for i, (arg_name, arg_type_name) in enumerate(node.args):
            func.args[i].name = arg_name
            ptr = self.builder.alloca(func.args[i].type)
            self.builder.store(func.args[i], ptr)
            self.var_symtab[arg_name] = (ptr, arg_type_name)

        for stmt in node.body:
            self._codegen(stmt)

        if name == 'main':
            self.builder.ret(ir.Constant(ir.IntType(32), 0))
        elif node.return_type == 'void':
            if not self.builder.block.is_terminated:
                self.builder.ret_void()

    def _codegen_ReturnStatement(self, node):
        val = self._codegen(node.value)
        self.builder.ret(val)

    def _codegen_VariableDecl(self, node):
        resolved_type = self._resolve_type_name(node.var_type)
        
        # Check if the variable type is a union
        if resolved_type in self.union_symtab:
            union_info = self.union_symtab[resolved_type]
            union_type = union_info['type']
            ptr = self.builder.alloca(union_type)
            self.var_symtab[node.name] = (ptr, resolved_type)
            # Auto-assign the value into the union
            val = self._codegen(node.value)
            self._union_auto_store(ptr, val, union_info)
            return
        
        # Check if we should pass the target type to the expression (useful for ArrayInit)
        old_target = self.current_target_type
        self.current_target_type = node.var_type
        val = self._codegen(node.value)
        self.current_target_type = old_target

        # If the declared type maps to a different LLVM type, cast
        target_llvm = self._get_llvm_type(node.var_type)
        val = self._emit_cast(val, target_llvm)
        ptr = self.builder.alloca(val.type)
        self.builder.store(val, ptr)
        self.var_symtab[node.name] = (ptr, node.var_type)

    def _union_auto_store(self, union_ptr, val, union_info):
        """Store a value into a union, auto-detecting the matching variant by LLVM type."""
        matched_idx = None
        for vname, vdata in union_info['variants'].items():
            if vdata['llvm_type'] == val.type:
                matched_idx = vdata['index']
                break
        # Fallback: try to match int types by checking if both are IntType
        if matched_idx is None:
            for vname, vdata in union_info['variants'].items():
                if isinstance(vdata['llvm_type'], ir.IntType) and isinstance(val.type, ir.IntType):
                    val = self._emit_cast(val, vdata['llvm_type'])
                    matched_idx = vdata['index']
                    break
        # Fallback: try to match float types
        if matched_idx is None:
            for vname, vdata in union_info['variants'].items():
                if isinstance(vdata['llvm_type'], (ir.FloatType, ir.DoubleType)) and isinstance(val.type, (ir.FloatType, ir.DoubleType)):
                    val = self._emit_cast(val, vdata['llvm_type'])
                    matched_idx = vdata['index']
                    break
        if matched_idx is None:
            raise LeashError(f"Cannot store value of this type into the union")
        
        # Store tag
        tag_ptr = self.builder.gep(union_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True)
        self.builder.store(ir.Constant(ir.IntType(32), matched_idx), tag_ptr)
        # Store value into data region
        data_ptr = self.builder.gep(union_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True)
        typed_ptr = self.builder.bitcast(data_ptr, val.type.as_pointer())
        self.builder.store(val, typed_ptr)

    def _codegen_Assignment(self, node):
        from .ast_nodes import MemberAccess, Identifier
        
        # 1. Specialized logic: Assigning to a specific union variant (e.g., f.i = 10, s.y.b = 3.14)
        if isinstance(node.target, MemberAccess):
            try:
                # Get the base (the union itself)
                base_ptr, type_name = self._codegen_lvalue(node.target.expr)
                resolved = self._resolve_type_name(type_name)
                
                if resolved in self.union_symtab:
                    union_info = self.union_symtab[resolved]
                    member = node.target.member
                    
                    if member in union_info['variants']:
                        vdata = union_info['variants'][member]
                        val = self._codegen(node.value)
                        val = self._emit_cast(val, vdata['llvm_type'])
                        
                        # Store tag
                        tag_ptr = self.builder.gep(base_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True)
                        self.builder.store(ir.Constant(ir.IntType(32), vdata['index']), tag_ptr)
                        
                        # Store data
                        data_ptr = self.builder.gep(base_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True)
                        typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
                        self.builder.store(val, typed_ptr)
                        return
                    elif member == 'cur':
                        raise LeashError("Cannot assign to '.cur'. Assign to a specific union variant or directly to the union.")
            except LeashError:
                pass # Fallback to standard assignment
        
        # 2. General Assignment (handles Identifiers, IndexAccess, and Struct Members)
        ptr, target_type_name = self._codegen_lvalue(node.target)
        resolved_target_type = self._resolve_type_name(target_type_name)

        # 3. Auto-detect Union variant if target is a union (e.g., f = 10, s.y = 3.14)
        if resolved_target_type in self.union_symtab:
            val = self._codegen(node.value)
            self._union_auto_store(ptr, val, self.union_symtab[resolved_target_type])
            return

        # 4. Standard Typed Assignment
        old_target = self.current_target_type
        self.current_target_type = target_type_name
        val = self._codegen(node.value)
        self.current_target_type = old_target

        target_llvm = self._get_llvm_type(target_type_name)
        val = self._emit_cast(val, target_llvm)
        self.builder.store(val, ptr)

    def _codegen_lvalue(self, node):
        from .ast_nodes import Identifier, MemberAccess, IndexAccess
        if isinstance(node, Identifier):
            if node.name not in self.var_symtab:
                raise LeashError(f"Undefined variable: '{node.name}'")
            return self.var_symtab[node.name]
        elif isinstance(node, MemberAccess):
            base_ptr, type_name = self._codegen_lvalue(node.expr)
            resolved = self._resolve_type_name(type_name)
            if resolved in self.struct_symtab:
                struct_info = self.struct_symtab[resolved]
                idx = struct_info['fields'].get(node.member)
                if idx is None:
                    raise LeashError(f"Struct '{resolved}' has no member named '{node.member}'")
                field_type_name = struct_info['field_types'][node.member]
                return self.builder.gep(base_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]), field_type_name
            elif resolved in self.union_symtab:
                union_info = self.union_symtab[resolved]
                if node.member in union_info['variants']:
                    vdata = union_info['variants'][node.member]
                    data_ptr = self.builder.gep(base_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True)
                    typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
                    return typed_ptr, vdata['type_name']
                elif node.member == 'cur':
                    raise LeashError("Cannot use '.cur' as an l-value. Assign directly to the union or a specific variant member.")
                else:
                    raise LeashError(f"Union '{resolved}' has no variant named '{node.member}'")
            else:
                raise LeashError(f"Cannot access member '{node.member}': '{type_name}' is not a struct or union")
        elif isinstance(node, IndexAccess):
            slice_ptr, slice_type_name = self._codegen_lvalue(node.expr)
            if slice_type_name == 'string':
                str_ptr = self.builder.load(slice_ptr)
                idx_val = self._codegen(node.index)
                ptr = self.builder.gep(str_ptr, [idx_val], inbounds=True)
                return (ptr, 'char')
            slice_val = self.builder.load(slice_ptr)
            data_ptr = self.builder.extract_value(slice_val, 1)
            idx_val = self._codegen(node.index)
            ptr = self.builder.gep(data_ptr, [idx_val], inbounds=True)
            elem_type_name = slice_type_name.split('[')[0] if '[' in slice_type_name else 'int'
            return (ptr, elem_type_name)
        else:
            raise LeashError(f"Invalid l-value: {type(node).__name__}")

    def _codegen_ExpressionStatement(self, node):
        self._codegen(node.expr)

    def _codegen_ShowStatement(self, node):
        from .ast_nodes import Identifier, MemberAccess
        
        # Check if any arg is a union variable or union .cur — those need special per-variant printing
        # First, collect which args are "union-cur" args
        union_arg_indices = set()
        for i, arg_node in enumerate(node.args):
            union_name = self._get_union_type_for_node(arg_node)
            if union_name:
                union_arg_indices.add(i)
        
        if not union_arg_indices:
            # No union args: standard show path
            self._show_standard(node.args)
            return
        
        # For simplicity, handle the common case: all non-union args come before or after union args
        # We'll print non-union prefix first, then branch for union args, then non-union suffix
        # Actually, the simplest correct approach: print all non-union args as prefix,
        # then for each union arg, emit a per-variant branching printf
        
        # Split into segments: print non-union prefix
        prefix_args = []
        for i, arg_node in enumerate(node.args):
            if i in union_arg_indices:
                break
            prefix_args.append(arg_node)
        
        if prefix_args:
            self._show_standard(prefix_args, newline=False)
        
        # Now handle each union arg
        for i, arg_node in enumerate(node.args):
            if i not in union_arg_indices:
                if i > max(union_arg_indices):
                    # suffix non-union args handled below
                    continue
                continue
            union_name = self._get_union_type_for_node(arg_node)
            union_info = self.union_symtab[union_name]
            
            # Get the union pointer
            if isinstance(arg_node, Identifier):
                ptr, _ = self.var_symtab[arg_node.name]
            elif isinstance(arg_node, MemberAccess) and arg_node.member == 'cur' and isinstance(arg_node.expr, Identifier):
                ptr, _ = self.var_symtab[arg_node.expr.name]
            else:
                # fallback
                self._show_standard([arg_node], newline=False)
                continue
            
            tag_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True)
            tag_val = self.builder.load(tag_ptr)
            data_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True)
            
            self._union_show_branched(tag_val, data_ptr, union_info)
        
        # suffix non-union args
        suffix_args = []
        if union_arg_indices:
            last_union = max(union_arg_indices)
            for i in range(last_union + 1, len(node.args)):
                suffix_args.append(node.args[i])
        
        if suffix_args:
            self._show_standard(suffix_args, newline=True)
        else:
            # Print newline
            self._print_raw("\n")

    def _get_union_type_for_node(self, arg_node):
        """Check if an AST node refers to a union variable or union.cur."""
        from .ast_nodes import Identifier, MemberAccess
        if isinstance(arg_node, Identifier):
            if arg_node.name in self.var_symtab:
                _, type_name = self.var_symtab[arg_node.name]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        elif isinstance(arg_node, MemberAccess) and arg_node.member == 'cur':
            if isinstance(arg_node.expr, Identifier) and arg_node.expr.name in self.var_symtab:
                _, type_name = self.var_symtab[arg_node.expr.name]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        return None

    def _print_raw(self, text):
        """Print a raw string via printf."""
        s = bytearray(text.encode("utf8") + b'\0')
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        g = ir.GlobalVariable(self.module, c_str.type, name=self.module.get_unique_name("raw"))
        g.linkage = 'internal'
        g.global_constant = True
        g.initializer = c_str
        ptr = self.builder.bitcast(g, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [ptr])

    def _union_show_branched(self, tag_val, data_ptr, union_info):
        """Print the current union value by branching on the tag and calling printf per variant."""
        variants = list(union_info['variants'].items())
        merge_bb = self.builder.function.append_basic_block('union_show_merge')
        
        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f'union_show_{vname}')
            var_bbs.append(bb)
        
        # Build if-else chain
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed('==', tag_val, ir.Constant(ir.IntType(32), vdata['index']))
            next_check = self.builder.function.append_basic_block(f'union_show_check_{i+1}')
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)
        self.builder.branch(var_bbs[-1])
        
        # In each variant BB, printf the loaded value with appropriate format
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
            loaded = self.builder.load(typed_ptr)
            
            # Determine format
            fmt, val = self._format_value(loaded)
            fmt_bytes = bytearray(fmt.encode("utf8") + b'\0')
            c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
            g_fmt = ir.GlobalVariable(self.module, c_fmt.type, name=self.module.get_unique_name("ufmt"))
            g_fmt.linkage = 'internal'
            g_fmt.global_constant = True
            g_fmt.initializer = c_fmt
            fmt_ptr = self.builder.bitcast(g_fmt, ir.IntType(8).as_pointer())
            self.builder.call(self.printf, [fmt_ptr, val])
            self.builder.branch(merge_bb)
        
        self.builder.position_at_end(merge_bb)

    def _format_value(self, val):
        """Return (format_str, possibly_cast_val) for a single value."""
        if isinstance(val.type, ir.IntType):
            width = val.type.width
            if width < 32:
                val = self.builder.zext(val, ir.IntType(32))
            if width == 64:
                return ("%lld", val)
            elif width == 8:
                return ("%c", val)
            else:
                return ("%d", val)
        elif isinstance(val.type, ir.FloatType):
            val = self.builder.fpext(val, ir.DoubleType())
            return ("%f", val)
        elif isinstance(val.type, ir.DoubleType):
            return ("%f", val)
        elif isinstance(val.type, ir.PointerType):
            return ("%s", val)
        return ("%s", val)

    def _show_standard(self, arg_nodes, newline=True):
        """Standard show() implementation for non-union args."""
        format_str = ""
        args = []
        for arg_node in arg_nodes:
            val = self._codegen(arg_node)
            if isinstance(val.type, ir.IntType):
                width = val.type.width
                if width < 32:
                    val = self.builder.zext(val, ir.IntType(32))
                
                if width == 64:
                    format_str += "%lld"
                elif width == 8:
                    format_str += "%c"
                else:
                    format_str += "%d"
            elif isinstance(val.type, ir.FloatType):
                val = self.builder.fpext(val, ir.DoubleType())
                format_str += "%f"
            elif isinstance(val.type, ir.DoubleType):
                format_str += "%f"
            elif isinstance(val.type, ir.PointerType):
                format_str += "%s"
            else:
                format_str += "%s" # fallback
            args.append(val)
        
        if newline:
            format_str += "\n"
        
        # Create global string for format
        fmt_bytes = bytearray(format_str.encode("utf8") + b'\0')
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(self.module, c_fmt.type, name=self.module.get_unique_name("fmt"))
        global_fmt.linkage = 'internal'
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt
        
        fmt_ptr = self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [fmt_ptr] + args)


    def _cast_bool(self, cond_val):
        if not isinstance(cond_val.type, ir.IntType) or cond_val.type.width != 1:
            zero = ir.Constant(cond_val.type, 0)
            if isinstance(cond_val.type, (ir.FloatType, ir.DoubleType)):
                return self.builder.fcmp_ordered('!=', cond_val, zero)
            else:
                return self.builder.icmp_signed('!=', cond_val, zero)
        return cond_val

    def _codegen_IfStatement(self, node):
        cond_val = self._cast_bool(self._codegen(node.condition))
        then_bb = self.builder.function.append_basic_block('then')
        
        also_bbs = []
        for _ in node.also_blocks:
            also_cond_bb = self.builder.function.append_basic_block('also_cond')
            also_body_bb = self.builder.function.append_basic_block('also_body')
            also_bbs.append((also_cond_bb, also_body_bb))
            
        else_bb = self.builder.function.append_basic_block('else') if node.else_block else None
        merge_bb = self.builder.function.append_basic_block('if_merge')

        next_bb = also_bbs[0][0] if also_bbs else (else_bb if else_bb else merge_bb)
        self.builder.cbranch(cond_val, then_bb, next_bb)

        self.builder.position_at_end(then_bb)
        for stmt in node.then_block:
            self._codegen(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)
            
        for i, (also_cond, also_body) in enumerate(node.also_blocks):
            cond_bb, body_bb = also_bbs[i]
            self.builder.position_at_end(cond_bb)
            a_cond_val = self._cast_bool(self._codegen(also_cond))
            next_also = also_bbs[i+1][0] if i+1 < len(also_bbs) else (else_bb if else_bb else merge_bb)
            self.builder.cbranch(a_cond_val, body_bb, next_also)
            
            self.builder.position_at_end(body_bb)
            for stmt in also_body:
                self._codegen(stmt)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        if else_bb:
            self.builder.position_at_end(else_bb)
            for stmt in node.else_block:
                self._codegen(stmt)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        
        self.builder.position_at_end(merge_bb)

    def _codegen_WhileStatement(self, node):
        cond_bb = self.builder.function.append_basic_block('while_cond')
        body_bb = self.builder.function.append_basic_block('while_body')
        merge_bb = self.builder.function.append_basic_block('while_merge')

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)
        
        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
            
        self.builder.position_at_end(merge_bb)

    def _codegen_ForStatement(self, node):
        self._codegen(node.init)
        cond_bb = self.builder.function.append_basic_block('for_cond')
        body_bb = self.builder.function.append_basic_block('for_body')
        merge_bb = self.builder.function.append_basic_block('for_merge')

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)
        
        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
        if not self.builder.block.is_terminated:
            self._codegen(node.step)
            self.builder.branch(cond_bb)
            
        self.builder.position_at_end(merge_bb)

    def _codegen_DoWhileStatement(self, node):
        body_bb = self.builder.function.append_basic_block('do_body')
        cond_bb = self.builder.function.append_basic_block('do_cond')
        merge_bb = self.builder.function.append_basic_block('do_merge')

        self.builder.branch(body_bb)
        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)
            
        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)
        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachStructStatement(self, node):
        struct_ptr, struct_type_name = self._codegen_lvalue(node.struct_expr)
        struct_ir_type = struct_ptr.type.pointee
        
        struct_meta = None
        for sname, sdata in self.struct_symtab.items():
            if sdata['type'] == struct_ir_type:
                struct_meta = sdata
                break
                
        if not struct_meta:
            raise LeashError("Cannot use 'foreach' with 'in<struct>' on a non-struct type")
            
        from .ast_nodes import StringLiteral
        for i, (field_name, field_type_name) in enumerate(struct_meta['fields'].items()):
            name_val = self._codegen(StringLiteral(field_name))
            name_ptr = self.builder.alloca(name_val.type, name=node.name_var)
            self.builder.store(name_val, name_ptr)
            self.var_symtab[node.name_var] = (name_ptr, 'string')
            
            field_ptr = self.builder.gep(struct_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)])
            field_val = self.builder.load(field_ptr)
            val_ptr = self.builder.alloca(field_val.type, name=node.value_var)
            self.builder.store(field_val, val_ptr)
            self.var_symtab[node.value_var] = (val_ptr, field_type_name)
            
            for stmt in node.body:
                self._codegen(stmt)

    def _codegen_ForeachArrayStatement(self, node):
        slice_val = self._codegen(node.array_expr)
        
        length_val = self.builder.extract_value(slice_val, 0)
        data_ptr = self.builder.extract_value(slice_val, 1)
        
        idx_ptr = self.builder.alloca(ir.IntType(32), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(32), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, 'int')
        
        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)
        self.var_symtab[node.value_var] = (val_ptr, 'int') # Default to int fallback
        
        cond_bb = self.builder.function.append_basic_block('foreach_cond')
        body_bb = self.builder.function.append_basic_block('foreach_body')
        merge_bb = self.builder.function.append_basic_block('foreach_merge')
        
        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        
        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed('<', curr_idx, length_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)
        
        self.builder.position_at_end(body_bb)
        
        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)
        
        for stmt in node.body:
            self._codegen(stmt)
            
        if not self.builder.block.is_terminated:
            next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(32), 1))
            self.builder.store(next_idx, idx_ptr)
            self.builder.branch(cond_bb)
            
        self.builder.position_at_end(merge_bb)

    def _codegen_BinaryOp(self, node):
        left = self._codegen(node.left)
        right = self._codegen(node.right)
        
        is_string = getattr(left.type, 'pointee', None) == ir.IntType(8) and getattr(right.type, 'pointee', None) == ir.IntType(8)
        if hasattr(ir, 'PointerType') and getattr(ir, 'PointerType') is not None:
            is_string = is_string and isinstance(left.type, ir.PointerType) and isinstance(right.type, ir.PointerType)
        elif getattr(left.type, 'is_pointer', False):
            is_string = is_string and left.type.is_pointer and right.type.is_pointer

        if is_string:
            if node.op == '+':
                len_l = self.builder.call(self.strlen, [left])
                len_r = self.builder.call(self.strlen, [right])
                total_len = self.builder.add(len_l, len_r)
                total_len_plus_1 = self.builder.add(total_len, ir.Constant(ir.IntType(32), 1))
                new_str = self.builder.call(self.malloc, [total_len_plus_1])
                self.builder.call(self.strcpy, [new_str, left])
                self.builder.call(self.strcat, [new_str, right])
                return new_str
            elif node.op == '-':
                p = self.builder.call(self.strstr, [left, right])
                null_ptr = getattr(ir.IntType(8), 'as_pointer')()(None) if hasattr(ir.IntType(8), 'as_pointer') else ir.Constant(left.type, None)
                null_ptr = ir.Constant(left.type, None) if not null_ptr else null_ptr
                is_not_null = self.builder.icmp_unsigned('!=', p, null_ptr)
                
                found_bb = self.builder.function.append_basic_block('strstr_found')
                not_found_bb = self.builder.function.append_basic_block('strstr_not_found')
                merge_bb = self.builder.function.append_basic_block('strstr_merge')
                
                self.builder.cbranch(is_not_null, found_bb, not_found_bb)
                
                self.builder.position_at_end(found_bb)
                len_l = self.builder.call(self.strlen, [left])
                len_r = self.builder.call(self.strlen, [right])
                p_int = self.builder.ptrtoint(p, ir.IntType(64))
                l_int = self.builder.ptrtoint(left, ir.IntType(64))
                prefix_len_64 = self.builder.sub(p_int, l_int)
                prefix_len = self.builder.trunc(prefix_len_64, ir.IntType(32))
                
                new_len = self.builder.sub(len_l, len_r)
                new_len_plus_1 = self.builder.add(new_len, ir.Constant(ir.IntType(32), 1))
                res_found = self.builder.call(self.malloc, [new_len_plus_1])
                
                self.builder.call(self.strncpy, [res_found, left, prefix_len])
                zero = ir.Constant(ir.IntType(32), 0)
                res_null_pos = self.builder.gep(res_found, [prefix_len], inbounds=True)
                self.builder.store(ir.Constant(ir.IntType(8), 0), res_null_pos)
                
                suffix_ptr = self.builder.gep(p, [len_r], inbounds=True)
                self.builder.call(self.strcat, [res_found, suffix_ptr])
                self.builder.branch(merge_bb)
                
                self.builder.position_at_end(not_found_bb)
                len_l2 = self.builder.call(self.strlen, [left])
                len_l2_plus_1 = self.builder.add(len_l2, ir.Constant(ir.IntType(32), 1))
                res_not_found = self.builder.call(self.malloc, [len_l2_plus_1])
                self.builder.call(self.strcpy, [res_not_found, left])
                self.builder.branch(merge_bb)
                
                self.builder.position_at_end(merge_bb)
                phi = self.builder.phi(left.type, name='str_sub_res')
                phi.add_incoming(res_found, found_bb)
                phi.add_incoming(res_not_found, not_found_bb)
                return phi
            elif node.op == '==':
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed('==', cmp, ir.Constant(ir.IntType(32), 0))
            elif node.op == '!=':
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed('!=', cmp, ir.Constant(ir.IntType(32), 0))
            else:
                raise Exception(f"Unknown string binary op {node.op}")

        # Determine if float or int based on types (assume matching types for now)
        is_float = isinstance(left.type, (ir.FloatType, ir.DoubleType))
        
        if node.op == '+':
            return self.builder.fadd(left, right) if is_float else self.builder.add(left, right)
        elif node.op == '-':
            return self.builder.fsub(left, right) if is_float else self.builder.sub(left, right)
        elif node.op == '*':
            return self.builder.fmul(left, right) if is_float else self.builder.mul(left, right)
        elif node.op == '/':
            return self.builder.fdiv(left, right) if is_float else self.builder.sdiv(left, right)
        elif node.op == '==':
            return self.builder.fcmp_ordered('==', left, right) if is_float else self.builder.icmp_signed('==', left, right)
        elif node.op == '!=':
            return self.builder.fcmp_ordered('!=', left, right) if is_float else self.builder.icmp_signed('!=', left, right)
        elif node.op == '<':
            return self.builder.fcmp_ordered('<', left, right) if is_float else self.builder.icmp_signed('<', left, right)
        elif node.op == '<=':
            return self.builder.fcmp_ordered('<=', left, right) if is_float else self.builder.icmp_signed('<=', left, right)
        elif node.op == '>':
            return self.builder.fcmp_ordered('>', left, right) if is_float else self.builder.icmp_signed('>', left, right)
        elif node.op == '>=':
            return self.builder.fcmp_ordered('>=', left, right) if is_float else self.builder.icmp_signed('>=', left, right)
        
        raise LeashError(f"Unknown binary operator: '{node.op}'")

    def _codegen_Call(self, node):
        func = self.func_symtab.get(node.name)
        if not func:
            raise LeashError(f"Call to undefined function: '{node.name}'")
        
        # Get function argument types if available
        arg_types = []
        if node.name in self.func_symtab:
            # We don't store sig in func_symtab directly, it's the ir.Function
            # but we can get it from attributes or a separate table if we had one.
            # For now, let's just use the func.args if it's external or defined.
            pass

        args = []
        for i, arg_expr in enumerate(node.args):
            old_target = self.current_target_type
            # If we know the function signature, set target type
            # (In a more complete compiler we'd have a function signature table)
            # Use None for now or try to infer from func object
            self.current_target_type = None 
            v = self._codegen(arg_expr)
            self.current_target_type = old_target
            args.append(v)

        return self.builder.call(func, args)
        
    def _codegen_StructInit(self, node):
        struct_info = self.struct_symtab.get(node.name)
        if not struct_info:
            raise LeashError(f"Undefined struct: '{node.name}'")
        struct_type = struct_info['type']
        val = ir.Constant(struct_type, ir.Undefined)
        for key, expr in node.kwargs:
            idx = struct_info['fields'].get(key)
            if idx is None:
                raise LeashError(f"Struct '{node.name}' has no member named '{key}'")
            field_val = self._codegen(expr)
            val = self.builder.insert_value(val, field_val, idx)
        return val

    def _codegen_Identifier(self, node):
        ptr, _ = self.var_symtab.get(node.name, (None, None))
        if not ptr:
            raise LeashError(f"Undefined variable: '{node.name}'")
        return self.builder.load(ptr)

    def _codegen_MemberAccess(self, node):
        from .ast_nodes import Identifier
        val = self._codegen(node.expr)
        
        # Check if this is a string .size
        is_string = getattr(val.type, 'pointee', None) == ir.IntType(8)
        if hasattr(ir, 'PointerType') and getattr(ir, 'PointerType') is not None:
            is_string = is_string and isinstance(val.type, ir.PointerType)
        elif getattr(val.type, 'is_pointer', False):
            is_string = is_string and val.type.is_pointer

        if is_string and node.member == 'size':
            return self.builder.call(self.strlen, [val])

        # Check if this is a union member access
        if isinstance(node.expr, Identifier) and node.expr.name in self.var_symtab:
            ptr, type_name = self.var_symtab[node.expr.name]
            resolved = self._resolve_type_name(type_name)
            if resolved in self.union_symtab:
                union_info = self.union_symtab[resolved]
                data_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True)
                
                if node.member == 'cur':
                    # Smart-cast: read tag, branch to load appropriate type
                    tag_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True)
                    tag_val = self.builder.load(tag_ptr)
                    return self._union_cur_load(tag_val, data_ptr, union_info)
                elif node.member in union_info['variants']:
                    vdata = union_info['variants'][node.member]
                    # Runtime tag check
                    tag_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True)
                    tag_val = self.builder.load(tag_ptr)
                    self._emit_union_tag_check(tag_val, vdata['index'], node.member, resolved)
                    typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
                    return self.builder.load(typed_ptr)
                else:
                    raise LeashError(f"Union '{resolved}' has no variant named '{node.member}'")
            
        ptr, _ = self._codegen_lvalue(node)
        return self.builder.load(ptr)

    def _emit_union_tag_check(self, tag_val, expected_idx, member_name, union_name):
        """Emit runtime check: if tag != expected_idx, print error to stderr and exit(1)."""
        ok_bb = self.builder.function.append_basic_block('union_check_ok')
        fail_bb = self.builder.function.append_basic_block('union_check_fail')
        
        cmp = self.builder.icmp_signed('==', tag_val, ir.Constant(ir.IntType(32), expected_idx))
        self.builder.cbranch(cmp, ok_bb, fail_bb)
        
        # Fail path: print error and exit
        self.builder.position_at_end(fail_bb)
        
        # Build a descriptive error message
        # Find the name of the actual active variant based on the tag
        # We can't do that statically, so we'll print the tag index
        err_msg = f"Runtime error: Attempted to access '.{member_name}' on union '{union_name}', but the active variant is different.\n"
        err_bytes = bytearray(err_msg.encode("utf8") + b'\0')
        c_err = ir.Constant(ir.ArrayType(ir.IntType(8), len(err_bytes)), err_bytes)
        g_err = ir.GlobalVariable(self.module, c_err.type, name=self.module.get_unique_name("union_err"))
        g_err.linkage = 'internal'
        g_err.global_constant = True
        g_err.initializer = c_err
        err_ptr = self.builder.bitcast(g_err, ir.IntType(8).as_pointer())
        
        # Load stderr and call fprintf
        stderr_val = self.builder.load(self.stderr_var)
        self.builder.call(self.fprintf, [stderr_val, err_ptr])
        self.builder.call(self.exit_fn, [ir.Constant(ir.IntType(32), 1)])
        self.builder.unreachable()
        
        # Continue on the ok path
        self.builder.position_at_end(ok_bb)

    def _union_cur_load(self, tag_val, data_ptr, union_info):
        """Load the current value from a union based on the runtime tag."""
        variants = list(union_info['variants'].items())
        
        if len(variants) == 1:
            vname, vdata = variants[0]
            typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
            return self.builder.load(typed_ptr)
        
        # Determine the common type to promote all variants into
        has_float = any(isinstance(vd['llvm_type'], (ir.FloatType, ir.DoubleType)) for _, vd in variants)
        has_ptr = any(isinstance(vd['llvm_type'], ir.PointerType) for _, vd in variants)
        
        if has_ptr:
            common_type = ir.IntType(8).as_pointer()
        elif has_float:
            common_type = ir.DoubleType()
        else:
            common_type = ir.IntType(64)
        
        merge_bb = self.builder.function.append_basic_block('union_cur_merge')
        
        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f'union_cur_{vname}')
            var_bbs.append(bb)
        
        # Build the if-else chain from current position
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed('==', tag_val, ir.Constant(ir.IntType(32), vdata['index']))
            next_check = self.builder.function.append_basic_block(f'union_cur_check_{i+1}')
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)
        
        # Default: last variant
        self.builder.branch(var_bbs[-1])
        
        # In each variant BB: load, convert to common type, branch to merge
        incoming = []
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata['llvm_type'].as_pointer())
            loaded = self.builder.load(typed_ptr)
            
            # Convert to common type
            converted = self._convert_to_common(loaded, common_type)
            incoming.append((converted, var_bbs[i]))
            self.builder.branch(merge_bb)
        
        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(common_type)
        for val, bb in incoming:
            phi.add_incoming(val, bb)
        return phi

    def _convert_to_common(self, val, common_type):
        """Convert a loaded union variant value to the common type for phi."""
        src = val.type
        dst = common_type
        if src == dst:
            return val
        
        src_is_int = isinstance(src, ir.IntType)
        src_is_float = isinstance(src, (ir.FloatType, ir.DoubleType))
        src_is_ptr = isinstance(src, ir.PointerType)
        dst_is_int = isinstance(dst, ir.IntType)
        dst_is_float = isinstance(dst, (ir.FloatType, ir.DoubleType))
        dst_is_ptr = isinstance(dst, ir.PointerType)
        
        if src_is_int and dst_is_int:
            if src.width < dst.width:
                return self.builder.sext(val, dst)
            elif src.width > dst.width:
                return self.builder.trunc(val, dst)
        elif src_is_int and dst_is_float:
            return self.builder.sitofp(val, dst)
        elif src_is_float and dst_is_float:
            if isinstance(src, ir.FloatType):
                return self.builder.fpext(val, dst)
        elif src_is_float and dst_is_int:
            return self.builder.fptosi(val, dst)
        elif src_is_ptr and dst_is_ptr:
            return self.builder.bitcast(val, dst)
        elif src_is_int and dst_is_ptr:
            return self.builder.inttoptr(val, dst)
        elif src_is_ptr and dst_is_int:
            return self.builder.ptrtoint(val, dst)
        
        return val



    def _codegen_NumberLiteral(self, node):
        return ir.Constant(ir.IntType(32), node.value)

    def _codegen_FloatLiteral(self, node):
        return ir.Constant(ir.DoubleType(), node.value)

    def _codegen_CharLiteral(self, node):
        return ir.Constant(ir.IntType(8), ord(node.value))

    def _codegen_BoolLiteral(self, node):
        return ir.Constant(ir.IntType(1), 1 if node.value else 0)

    def _codegen_NullLiteral(self, node):
        return ir.Constant(ir.IntType(8).as_pointer(), None)

    def _codegen_StringLiteral(self, node):
        s = bytearray(node.value.encode('utf-8') + b'\0')
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        global_str = ir.GlobalVariable(self.module, c_str.type, name=self.module.get_unique_name("str"))
        global_str.linkage = 'internal'
        global_str.global_constant = True
        global_str.initializer = c_str
        return self.builder.bitcast(global_str, ir.IntType(8).as_pointer())

    def _codegen_ArrayInit(self, node):
        target = self.current_target_type
        elem_type = None
        if target and '[' in target:
            elem_type_name = target.split('[')[0]
            elem_type = self._get_llvm_type(elem_type_name)
            
        old_target = self.current_target_type
        if target and '[' in target:
            self.current_target_type = target.split('[')[0]
        else:
            self.current_target_type = None

        vals = []
        for e in node.elements:
            v = self._codegen(e)
            if elem_type:
                v = self._emit_cast(v, elem_type)
            vals.append(v)
            
        self.current_target_type = old_target
        
        length = len(vals)
        if not elem_type:
            elem_type = ir.IntType(32) if not vals else vals[0].type
        
        arr_type = ir.ArrayType(elem_type, length)
        
        is_const = all(isinstance(v, ir.Constant) for v in vals)
        if is_const:
            c_arr = ir.Constant(arr_type, vals)
            global_arr = ir.GlobalVariable(self.module, arr_type, name=self.module.get_unique_name("const_arr"))
            global_arr.linkage = 'internal'
            global_arr.global_constant = True
            global_arr.initializer = c_arr
            arr_ptr = global_arr
        else:
            arr_ptr = self.builder.alloca(arr_type)
            for i, v in enumerate(vals):
                ptr = self.builder.gep(arr_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)], inbounds=True)
                self.builder.store(v, ptr)
            
        slice_type = ir.LiteralStructType([ir.IntType(32), elem_type.as_pointer()])
        slice_val = ir.Constant(slice_type, ir.Undefined)
        
        slice_val = self.builder.insert_value(slice_val, ir.Constant(ir.IntType(32), length), 0)
        
        elem_ptr = self.builder.bitcast(arr_ptr, elem_type.as_pointer())
        slice_val = self.builder.insert_value(slice_val, elem_ptr, 1)
        
        return slice_val

    def _codegen_IndexAccess(self, node):
        ptr, _ = self._codegen_lvalue(node)
        return self.builder.load(ptr)

    def _codegen_CastExpr(self, node):
        val = self._codegen(node.expr)
        target_type = self._get_llvm_type(node.target_type)
        return self._emit_cast(val, target_type)

    def _emit_cast(self, val, target_type):
        """Cast a value to the target LLVM type."""
        src = val.type
        dst = target_type
        if src == dst:
            return val
        
        src_is_int = isinstance(src, ir.IntType)
        dst_is_int = isinstance(dst, ir.IntType)
        src_is_float = isinstance(src, (ir.FloatType, ir.DoubleType, ir.HalfType))
        dst_is_float = isinstance(dst, (ir.FloatType, ir.DoubleType, ir.HalfType))
        src_is_ptr = isinstance(src, ir.PointerType)
        dst_is_ptr = isinstance(dst, ir.PointerType)
        
        # int -> int (trunc / zext / sext)
        if src_is_int and dst_is_int:
            if src.width > dst.width:
                return self.builder.trunc(val, dst)
            elif src.width < dst.width:
                return self.builder.zext(val, dst)
            return val
        # float -> float (fpext / fptrunc)
        elif src_is_float and dst_is_float:
            src_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(src), 64)
            dst_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(dst), 64)
            if src_size < dst_size:
                return self.builder.fpext(val, dst)
            elif src_size > dst_size:
                return self.builder.fptrunc(val, dst)
            return val
        # int -> float
        elif src_is_int and dst_is_float:
            return self.builder.sitofp(val, dst)
        # float -> int
        elif src_is_float and dst_is_int:
            return self.builder.fptosi(val, dst)
        # ptr -> ptr (bitcast)
        elif src_is_ptr and dst_is_ptr:
            return self.builder.bitcast(val, dst)
        # ptr -> int (ptrtoint)
        elif src_is_ptr and dst_is_int:
            return self.builder.ptrtoint(val, dst)
        # int -> ptr (inttoptr)
        elif src_is_int and dst_is_ptr:
            return self.builder.inttoptr(val, dst)
        
        # Struct-to-struct (slices)
        if isinstance(src, ir.LiteralStructType) and isinstance(dst, ir.LiteralStructType):
            if len(src.elements) == 2 and len(dst.elements) == 2:
                # If it's a slice {i32, T*} to {i32, U*}
                if isinstance(src.elements[1], ir.PointerType) and isinstance(dst.elements[1], ir.PointerType):
                    length = self.builder.extract_value(val, 0)
                    ptr = self.builder.extract_value(val, 1)
                    # Bitcast the pointer part
                    new_ptr = self.builder.bitcast(ptr, dst.elements[1])
                    new_slice = ir.Constant(dst, ir.Undefined)
                    new_slice = self.builder.insert_value(new_slice, length, 0)
                    new_slice = self.builder.insert_value(new_slice, new_ptr, 1)
                    return new_slice

        # Fallback: bitcast if same size, otherwise error
        return self.builder.bitcast(val, dst)

    def get_ir(self):
        return str(self.module)
