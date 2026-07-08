import sys
import llvmlite.ir as ir
import llvmlite.binding as llvm
from .errors import LeashError
from .ast_nodes import (
    TypeAlias,
    StructDef,
    EnumDef,
    UnionDef,
    ErrorDef,
    ClassDef,
    Function,
    GenericCall,
    TemplateDef,
    GlobalVarDecl,
    WorksOtherwiseStatement,
    SpawnStatement,
    ThrowStatement,
    SelfExpr,
    NativeImport,
    DeferStatement,
    Lambda,
    SizeofExpr,
    Identifier,
    MacroDef,
    CreateExpr,
    DelStatement,
    StringLiteral,
    NumberLiteral,
    FloatLiteral,
    BoolLiteral,
    CharLiteral,
    IsExpr,
    LoopStatement,
    ThisOpTypeExpr,
    ThisWorkerExpr,
    OpDef,
)


class CodeGen:
    def __init__(self, target_platform=None):
        self.module = ir.Module(name="leash_module")
        # Initialize data layout for type size calculations
        try:
            llvm.initialize()
            llvm.initialize_native_target()
            llvm.initialize_native_asmprinter()
            target = llvm.get_default_target_triple()
            target_machine = llvm.create_target_machine(target)
            self.module.data_layout = target_machine.target_data
        except:
            # Fallback if LLVM binding fails
            pass
        self.builder = None
        self.func_symtab = {}
        self.var_symtab = {}
        self.struct_symtab = {}
        self.type_aliases = {}  # name -> resolved type string
        self.union_symtab = {}  # name -> { 'type': ir_type, 'variants': [...], 'variant_types': {...}, 'max_size': int }
        self.enum_symtab = {}  # name -> { 'members': [names], 'names_arr': ir.GlobalVariable }
        self.class_symtab = {}  # name -> { 'type': ir_type, 'fields': {...}, 'methods': {...} }
        self.global_var_ptrs = {}  # name -> (ir.GlobalVariable, leash_type_string) for module-level variables
        self.global_init_list = []  # list of (gv, init_expr, leash_type) for globals with initializers
        self.init_func = (
            None  # The _leash_init_globals function if any globals need init
        )
        self.printf = None
        self.current_target_type = None
        self.target_platform = target_platform  # Store target platform (e.g., "win64", "linux64")
        self.loop_stack = []  # Stack of (break_bb, continue_bb) for nested loops
        self.seed_called = False  # Track if seed() was explicitly called

        # For works...otherwise error handling
        self.in_works_block = False  # Track if we're generating code for a works block
        self.works_error_occured = False  # Flag if error occurred in works block
        self.works_error_info = None  # Store error info for otherwise block
        self.current_error_name = None  # Name of error being defined
        self.current_func_name = None  # Name of function being defined
        self.current_class_name = None  # Name of class being defined

        # For unsafe functions - skip runtime safety checks
        self.in_unsafe_func = (
            False  # Track if we're generating code for an unsafe function
        )

        # stderr - REMOVED, now using printf for error output

        # Native libraries for FFI (from @from statements)
        self.native_libs = []  # list of (lib_path, declarations) tuples

        # Boehm GC configuration (replacing legacy SAMM)
        self.current_func_alloc_limit = 0  # Not used with GC

        # Deferred function calls stack (per scope)
        self.defer_stack = []  # list of lists of Call nodes

        # Global start time for timepass() - { i64 tv_sec, i64 tv_nsec }
        timespec_ty = ir.LiteralStructType([ir.IntType(64), ir.IntType(64)])
        self.start_time_gv = ir.GlobalVariable(
            self.module, timespec_ty, name="start_time"
        )
        self.start_time_gv.linkage = "internal"
        self.start_time_gv.initializer = ir.Constant(
            timespec_ty,
            [ir.Constant(ir.IntType(64), 0), ir.Constant(ir.IntType(64), 0)],
        )

        # Global buffer for showb()
        self.showb_buffer_gv = ir.GlobalVariable(
            self.module, ir.IntType(8).as_pointer(), name="_leash_showb_buffer"
        )
        self.showb_buffer_gv.linkage = "internal"
        self.showb_buffer_gv.initializer = ir.Constant(ir.IntType(8).as_pointer(), None)

        self.showb_size_gv = ir.GlobalVariable(
            self.module, ir.IntType(64), name="_leash_showb_size"
        )
        self.showb_size_gv.linkage = "internal"
        self.showb_size_gv.initializer = ir.Constant(ir.IntType(64), 0)

        self.showb_cap_gv = ir.GlobalVariable(
            self.module, ir.IntType(64), name="_leash_showb_cap"
        )
        self.showb_cap_gv.linkage = "internal"
        self.showb_cap_gv.initializer = ir.Constant(ir.IntType(64), 0)

        self.setup_builtins()

    def setup_builtins(self):
        printf_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(8).as_pointer()], var_arg=True
        )
        self.printf = ir.Function(self.module, printf_ty, name="printf")

        strlen_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(8).as_pointer()])
        self.strlen = ir.Function(self.module, strlen_ty, name="strlen")

        strcpy_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strcpy = ir.Function(self.module, strcpy_ty, name="strcpy")

        strncpy_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)],
        )
        self.strncpy = ir.Function(self.module, strncpy_ty, name="strncpy")

        strcat_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strcat = ir.Function(self.module, strcat_ty, name="strcat")

        strcmp_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()]
        )
        self.strcmp = ir.Function(self.module, strcmp_ty, name="strcmp")

        strstr_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
        )
        self.strstr = ir.Function(self.module, strstr_ty, name="strstr")

        exit_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
        self.exit_fn = ir.Function(self.module, exit_ty, name="exit")

        fprintf_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
            var_arg=True,
        )
        self.fprintf = ir.Function(self.module, fprintf_ty, name="fprintf")

        # Custom Leash GC functions
        gc_init_ty = ir.FunctionType(ir.VoidType(), [])
        self.gc_init = ir.Function(self.module, gc_init_ty, name="leash_gc_init")

        gc_malloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64)])
        self.malloc = ir.Function(self.module, gc_malloc_ty, name="leash_gc_malloc")

        gc_realloc_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(), [ir.IntType(8).as_pointer(), ir.IntType(64)]
        )
        self.realloc = ir.Function(self.module, gc_realloc_ty, name="leash_gc_realloc")

        # String allocation helper
        gc_alloc_string_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64)])
        self.gc_alloc_string = ir.Function(
            self.module, gc_alloc_string_ty, name="leash_gc_alloc_string"
        )

        # GC collect function
        gc_collect_ty = ir.FunctionType(ir.VoidType(), [])
        self.gc_collect = ir.Function(
            self.module, gc_collect_ty, name="leash_gc_collect"
        )

        # GC root management (used for multi-threading - spawn args)
        gc_root_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        self.gc_unregister_root = ir.Function(
            self.module, gc_root_ty, name="leash_gc_unregister_root"
        )

        # Free is still declared just in case, but GC_malloc doesn't need it.
        free_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        self.free = ir.Function(self.module, free_ty, name="free")

        getchar_ty = ir.FunctionType(ir.IntType(32), [])
        self.getchar = ir.Function(self.module, getchar_ty, name="getchar")

        keyget_ty = ir.FunctionType(ir.IntType(32), [])
        self.keyget = ir.Function(self.module, keyget_ty, name="leash_keyget")

        putchar_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(32)])
        self.putchar = ir.Function(self.module, putchar_ty, name="putchar")

        atoll_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(8).as_pointer()])
        self.atoll = ir.Function(self.module, atoll_ty, name="atoll")

        atof_ty = ir.FunctionType(ir.DoubleType(), [ir.IntType(8).as_pointer()])
        self.atof = ir.Function(self.module, atof_ty, name="atof")

        sprintf_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],
            var_arg=True,
        )
        self.sprintf = ir.Function(self.module, sprintf_ty, name="sprintf")

        memmove_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)],
        )
        self.memmove = ir.Function(self.module, memmove_ty, name="memmove")

        memcmp_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)],
        )
        self.memcmp = ir.Function(self.module, memcmp_ty, name="memcmp")

        # Random functions
        rand_ty = ir.FunctionType(ir.IntType(32), [])
        self.rand = ir.Function(self.module, rand_ty, name="rand")

        srand_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(32)])
        self.srand = ir.Function(self.module, srand_ty, name="srand")

        # Time function for auto-seeding
        time_ty = ir.FunctionType(ir.IntType(64), [ir.IntType(64).as_pointer()])
        self.time = ir.Function(self.module, time_ty, name="time")

        # Wait function (usleep)
        usleep_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(64)])
        self.usleep = ir.Function(self.module, usleep_ty, name="usleep")

        # Clock gettime for timepass
        timespec_ty = ir.LiteralStructType(
            [ir.IntType(64), ir.IntType(64)]
        )  # tv_sec, tv_nsec
        clock_gettime_ty = ir.FunctionType(
            ir.IntType(32), [ir.IntType(32), timespec_ty.as_pointer()]
        )
        self.clock_gettime = ir.Function(
            self.module, clock_gettime_ty, name="clock_gettime"
        )
        # CLOCK_MONOTONIC constant (typically 1 on Linux)
        self.CLOCK_MONOTONIC = ir.Constant(ir.IntType(32), 1)

        # File I/O functions (FILE* is i8*)
        fopen_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),  # FILE*
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],  # filename, mode
        )
        self.fopen = ir.Function(self.module, fopen_ty, name="fopen")

        fclose_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.fclose = ir.Function(self.module, fclose_ty, name="fclose")

        fread_ty = ir.FunctionType(
            ir.IntType(64),  # size_t
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(64),
                ir.IntType(64),
                ir.IntType(8).as_pointer(),
            ],  # ptr, size, nmemb, FILE*
        )
        self.fread = ir.Function(self.module, fread_ty, name="fread")

        fwrite_ty = ir.FunctionType(
            ir.IntType(64),  # size_t
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(64),
                ir.IntType(64),
                ir.IntType(8).as_pointer(),
            ],  # ptr, size, nmemb, FILE*
        )
        self.fwrite = ir.Function(self.module, fwrite_ty, name="fwrite")

        fgets_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),  # char*
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(32),
                ir.IntType(8).as_pointer(),
            ],  # buf, n, FILE*
        )
        self.fgets = ir.Function(self.module, fgets_ty, name="fgets")

        # exec functions use the same fgets (and alias existing functions)
        self.fgets_fn = self.fgets
        self.malloc_fn = self.malloc
        self.realloc_fn = self.realloc
        self.strlen_fn = self.strlen
        self.strcpy_fn = self.strcpy
        self.sprintf_fn = self.sprintf

        fseek_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(64),
                ir.IntType(32),
            ],  # FILE*, offset, whence
        )
        self.fseek = ir.Function(self.module, fseek_ty, name="fseek")

        ftell_ty = ir.FunctionType(
            ir.IntType(64),  # long
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.ftell = ir.Function(self.module, ftell_ty, name="ftell")

        frewind_ty = ir.FunctionType(
            ir.VoidType(),
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.frewind = ir.Function(self.module, frewind_ty, name="rewind")

        rename_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [
                ir.IntType(8).as_pointer(),
                ir.IntType(8).as_pointer(),
            ],  # oldpath, newpath
        )
        self.rename_fn = ir.Function(self.module, rename_ty, name="rename")

        remove_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [ir.IntType(8).as_pointer()],  # pathname
        )
        self.remove_fn = ir.Function(self.module, remove_ty, name="remove")

        fflush_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.fflush = ir.Function(self.module, fflush_ty, name="fflush")

        feof_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.feof = ir.Function(self.module, feof_ty, name="feof")

        malloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64)])
        # Note: malloc is already declared above as GC_malloc, but we need C malloc for file buffers
        self.c_malloc = ir.Function(self.module, malloc_ty, name="malloc")

        fileno_ty = ir.FunctionType(
            ir.IntType(32),  # int (file descriptor)
            [ir.IntType(8).as_pointer()],  # FILE*
        )
        self.fileno_fn = ir.Function(self.module, fileno_ty, name="fileno")

        ftruncate_fn_ty = ir.FunctionType(
            ir.IntType(32),  # int
            [ir.IntType(32), ir.IntType(64)],  # int fd, off_t length
        )
        self.ftruncate_fn = ir.Function(self.module, ftruncate_fn_ty, name="ftruncate")

        # exec functions
        system_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
        self.system_fn = ir.Function(self.module, system_ty, name="system")

        popen_ty = ir.FunctionType(
            ir.IntType(8).as_pointer(),  # FILE*
            [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()],  # command, mode
        )
        self.popen_fn = ir.Function(self.module, popen_ty, name="popen")

        pclose_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
        self.pclose_fn = ir.Function(self.module, pclose_ty, name="pclose")

        # Helper to get stdout portably
        get_stdout_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [])
        self.get_stdout_fn = ir.Function(
            self.module, get_stdout_ty, name="_leash_get_stdout"
        )

        # setbuf
        setbuf_ty = ir.FunctionType(
            ir.VoidType(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()]
        )
        self.setbuf_fn = ir.Function(self.module, setbuf_ty, name="setbuf")

        # Threading support
        void_ptr = ir.IntType(8).as_pointer()
        leash_spawn_ty = ir.FunctionType(ir.IntType(32), [ir.FunctionType(void_ptr, [void_ptr]).as_pointer(), void_ptr])
        self.leash_spawn_worker_fn = ir.Function(self.module, leash_spawn_ty, name="leash_spawn_worker")

        leash_is_interrupted_ty = ir.FunctionType(ir.IntType(32), [])
        self.leash_is_interrupted_fn = ir.Function(self.module, leash_is_interrupted_ty, name="leash_is_interrupted")

        leash_setup_interrupt_ty = ir.FunctionType(ir.VoidType(), [])
        self.leash_setup_interrupt_fn = ir.Function(self.module, leash_setup_interrupt_ty, name="leash_setup_interrupt_handler")

        leash_wait_workers_ty = ir.FunctionType(ir.VoidType(), [])
        self.leash_wait_workers_fn = ir.Function(self.module, leash_wait_workers_ty, name="leash_wait_for_workers")

        # Aligned allocation for matrix data
        aligned_alloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64), ir.IntType(64)])
        self.aligned_alloc = ir.Function(self.module, aligned_alloc_ty, name="leash_gc_aligned_alloc")

        # Optimized matrix binary op functions (called for float/double/int32/int64 element types)
        i8ptr = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)
        mat_binop_ty = ir.FunctionType(ir.VoidType(), [i8ptr, i8ptr, i8ptr, i64, ir.IntType(32)])
        self.mat_binop_float = ir.Function(self.module, mat_binop_ty, name="leash_matrix_binary_op_float")
        self.mat_binop_double = ir.Function(self.module, mat_binop_ty, name="leash_matrix_binary_op_double")
        self.mat_binop_int32 = ir.Function(self.module, mat_binop_ty, name="leash_matrix_binary_op_int32")
        self.mat_binop_int64 = ir.Function(self.module, mat_binop_ty, name="leash_matrix_binary_op_int64")

        # Parallel (threaded) matrix binary op functions
        self.mat_parop_float = ir.Function(self.module, mat_binop_ty, name="leash_matrix_parallel_op_float")
        self.mat_parop_double = ir.Function(self.module, mat_binop_ty, name="leash_matrix_parallel_op_double")
        self.mat_parop_int32 = ir.Function(self.module, mat_binop_ty, name="leash_matrix_parallel_op_int32")
        self.mat_parop_int64 = ir.Function(self.module, mat_binop_ty, name="leash_matrix_parallel_op_int64")

    def _emit_const_str(self, string_val):
        """Create a global string constant and return a pointer to it (i8*)."""
        # Search for existing constant
        const_name = f"const_str_{hash(string_val)}"
        for g in self.module.global_values:
            if g.name == const_name:
                return self.builder.bitcast(g, ir.IntType(8).as_pointer())

        fmt_bytes = bytearray(string_val.encode("utf8") + b"\0")
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(self.module, c_fmt.type, name=const_name)
        global_fmt.linkage = "internal"
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt
        return self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())

    def _emit_tostring(self, val, llvm_ty):
        """Convert any basic value to a Leash string (i8*). Allocation via GC_malloc."""
        # Buffer for conversion (64 bytes is plenty for any numeric)
        buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 64)])
        self._track_alloc(buf)

        fmt = ""
        casted_val = val
        if isinstance(llvm_ty, ir.IntType):
            if llvm_ty.width == 1:  # bool
                # Special case for bool: "true" or "false"
                true_str = self._emit_const_str("true")
                false_str = self._emit_const_str("false")
                return self.builder.select(val, true_str, false_str)
            elif llvm_ty.width == 8:  # char
                # Create a string of length 1
                self.builder.store(
                    val, self.builder.gep(buf, [ir.Constant(ir.IntType(32), 0)])
                )
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(buf, [ir.Constant(ir.IntType(32), 1)]),
                )
                return buf
            elif llvm_ty.width == 64:
                fmt = "%lld"
            elif llvm_ty.width < 32:
                fmt = "%d"
                casted_val = self.builder.sext(val, ir.IntType(32))
            elif llvm_ty.width <= 64:
                fmt = "%lld"
                casted_val = self.builder.sext(val, ir.IntType(64))
            else:
                fmt = "%lld"
                casted_val = self.builder.trunc(val, ir.IntType(64))
        elif isinstance(llvm_ty, (ir.HalfType, ir.FloatType, ir.DoubleType)):
            fmt = "%f"
            if not isinstance(llvm_ty, ir.DoubleType):
                casted_val = self.builder.fpext(val, ir.DoubleType())
        else:
            return val  # already a string?

        fmt_ptr = self._emit_const_str(fmt)
        self.builder.call(self.sprintf, [buf, fmt_ptr, casted_val])
        return buf

    def _track_alloc(self, ptr):
        """No-op since we are using Boeing GC."""
        return ptr

    def _emit_cleanup(self, ret_val=None):
        """No-op since we are using Boeing GC."""
        pass

    def _emit_default_value(self, type_name):
        resolved = self._resolve_type_name(type_name)
        llvm_type = self._get_llvm_type(resolved)

        if (
            resolved in ("int", "uint")
            or resolved.startswith("int<")
            or resolved.startswith("uint<")
        ):
            return ir.Constant(llvm_type, 0)
        if resolved == "float" or resolved.startswith("float<"):
            return ir.Constant(llvm_type, 0.0)
        if resolved == "bool" or resolved == "char":
            return ir.Constant(llvm_type, 0)
        if resolved == "string":
            return self._emit_const_str("")
        if resolved.startswith("vec<"):
            # Empty vector: { null, 0, 0 }
            # First element's element type's pointer type
            ptr_ty = llvm_type.elements[0]
            return ir.Constant(
                llvm_type,
                [
                    ir.Constant(ptr_ty, None),
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(64), 0),
                ],
            )
        if resolved.startswith("matrix<"):
            ptr_ty = llvm_type.elements[0]
            return ir.Constant(
                llvm_type,
                [
                    ir.Constant(ptr_ty, None),
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(64), 0),
                ],
            )
        if resolved.startswith("hash<") and resolved.endswith(">"):
            # Empty hash: { 0, 0, null } with empty key/value ptr lists
            ptr_ty = ir.IntType(8).as_pointer()
            result = ir.Constant(
                llvm_type,
                [
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ptr_ty, None),
                ],
            )
            result.hash_key_ptrs = []
            result.hash_value_ptrs = []
            return result

        if resolved in self.class_symtab:
            # Return null pointer for class types as fallback
            return ir.Constant(llvm_type, None)

        if resolved.endswith("]") and "[" in resolved:
            # Empty array/slice: { 0, null }
            ptr_ty = llvm_type.elements[1]
            return ir.Constant(
                llvm_type, [ir.Constant(ir.IntType(64), 0), ir.Constant(ptr_ty, None)]
            )

        return ir.Constant(
            llvm_type,
            [
                ir.Constant(e, 0) if isinstance(e, ir.IntType) else ir.Constant(e, None)
                for e in llvm_type.elements
            ],
        )

    def generate_code(self, node, file_path="unknown"):
        self.program = node
        self.program_file = file_path
        return self._codegen(node)

    def _codegen(self, node):
        # ThisOpTypeExpr should not appear at codegen time (resolved during typechecking)
        if isinstance(node, ThisOpTypeExpr):
            raise LeashError(
                "Internal: thisop.typ not resolved at codegen time",
                line=getattr(node, "line", None), col=getattr(node, "col", None)
            )

        method_name = f"_codegen_{type(node).__name__}"
        method = getattr(self, method_name, None)
        if method:
            return method(node)
        else:
            raise NotImplementedError(f"No codegen for {type(node).__name__}")

    def _get_leash_type_name(self, node):
        """Helper to try and get the Leash type name for an AST node during codegen."""
        from .ast_nodes import (
            Identifier,
            MemberAccess,
            IndexAccess,
            Call,
            GenericCall,
            EnumMemberAccess,
            CastExpr,
            AsExpr,
            TypeConvExpr,
            MethodCall,
            ThisExpr,
            UnaryOp,
            PointerMemberAccess,
            SizeofExpr,
            ByteConvExpr,
            BinaryOp,
            StructInit,
            NumberLiteral,
            FloatLiteral,
            StringLiteral,
            CharLiteral,
            BoolLiteral,
        )

        if isinstance(node, ThisExpr):
            if "this" in self.var_symtab:
                return self.var_symtab["this"][1]
            return "int"
        if isinstance(node, ThisWorkerExpr):
            return "thisworker"
        if isinstance(node, Identifier):
            if node.name in self.var_symtab:
                return self.var_symtab[node.name][1]
            if node.name in self.global_var_ptrs:
                return self.global_var_ptrs[node.name][1]
        elif isinstance(node, BinaryOp):
            lt = self._get_leash_type_name(node.left)
            rt = self._get_leash_type_name(node.right)
            if node.op in ("==", "!=", "<", "<=", ">", ">=", "&&", "||"):
                return "bool"
            if lt == "float" or rt == "float":
                return "float"
            return lt
        elif isinstance(node, StructInit):
            return node.name
        elif isinstance(node, NumberLiteral):
            return "int"
        elif isinstance(node, FloatLiteral):
            return "float"
        elif isinstance(node, StringLiteral):
            return "string"
        elif isinstance(node, CharLiteral):
            return "char"
        elif isinstance(node, BoolLiteral):
            return "bool"
        elif isinstance(node, MemberAccess):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            if resolved.startswith("&"):
                resolved = self._resolve_type_name(resolved[1:])
            if resolved in self.struct_symtab:
                return self.struct_symtab[resolved]["field_types"].get(
                    node.member, "int"
                )
            if resolved in self.class_symtab:
                return self.class_symtab[resolved]["field_types"].get(
                    node.member, "int"
                )
            if resolved in self.union_symtab:
                return (
                    self.union_symtab[resolved]["variants"]
                    .get(node.member, {})
                    .get("type_name", "int")
                )
        elif isinstance(node, PointerMemberAccess):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            underlying = self._resolve_type_name(resolved[1:])
            if underlying in self.struct_symtab:
                return self.struct_symtab[underlying]["field_types"].get(
                    node.member, "int"
                )
            if underlying in self.class_symtab:
                return self.class_symtab[underlying]["field_types"].get(
                    node.member, "int"
                )
        elif isinstance(node, UnaryOp):
            if node.op == "*":
                t = self._get_leash_type_name(node.expr)
                res = self._resolve_type_name(t)
                return res[1:]
            if node.op == "&":
                t = self._get_leash_type_name(node.expr)
                return f"*{t}"
        elif isinstance(node, IndexAccess):
            base_type = self._get_leash_type_name(node.expr)
            if "[" in base_type:
                return base_type.split("[")[0]
            elif base_type == "string":
                return "char"
        elif isinstance(node, EnumMemberAccess):
            # Check if enum member has a custom type
            enum_info = self.enum_symtab.get(node.enum_name)
            if enum_info:
                member_dict = enum_info.get("member_dict", {})
                if node.member_name in member_dict:
                    mtype, _ = member_dict[node.member_name]
                    if mtype is not None:
                        return mtype
            return node.enum_name
        elif isinstance(node, CastExpr):
            return node.target_type
        elif isinstance(node, AsExpr):
            return node.target_type
        elif isinstance(node, IsExpr):
            # 'is' and 'isnt' always return bool
            return "bool"
        elif isinstance(node, TypeConvExpr):
            return node.target_type
        elif isinstance(node, ByteConvExpr):
            if node.name in ("inttobytes", "floattobytes"):
                return "char[]"
            else:
                return self._get_leash_type_name(node.value_expr)
        elif isinstance(node, SizeofExpr):
            return "int"
        elif isinstance(node, Call):
            if node.name == "tostring":
                return "string"
            if node.name == "get":
                return "string"
            if node.name == "keyget":
                return "char"
            if node.name == "rand":
                return "int"
            if node.name == "randf":
                return "float"
            if node.name == "seed":
                return "void"
            if node.name == "choose":
                return "string"
            if node.name == "wait":
                return "void"
            if node.name == "timepass":
                return "float"
            if node.name == "normescape":
                return "string"
            if node.name in self.func_symtab:
                # We don't store return types in func_symtab currently...
                pass
        elif isinstance(node, GenericCall):
            # Return type is stored in the return type of the mangled function
            type_args_str = "_".join(
                t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
                for t in node.type_args
            )
            mangled_name = f"{node.name}_{type_args_str}"
            # Try to get return type from func_symtab
            if mangled_name in self.func_symtab:
                func = self.func_symtab[mangled_name]
                ret_type = func.function_type.return_type
                # Map LLVM type to Leash type name
                return self._llvm_type_to_leash_name(ret_type)
            return "int"  # fallback
        elif isinstance(node, MethodCall):
            base_type = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(base_type)
            if resolved.startswith("&"):
                resolved = self._resolve_type_name(resolved[1:])
            if resolved.startswith("vec<"):
                inner = resolved[4:-1]
                if node.method in ("get", "popb", "popf"):
                    return inner
                if node.method == "size":
                    return "int"
            if resolved == "string" or resolved.endswith("]"):
                if node.method == "size":
                    return "int"
                if resolved == "string" and node.method == "replace":
                    return "string"
            # Handle File static methods
            from .ast_nodes import Identifier, GenericTypeExpr

            # Handle static method calls on generic classes (e.g., VecMath<int>.sum(...))
            if isinstance(node.expr, GenericTypeExpr):
                type_args_str = "_".join(
                    t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
                    for t in node.expr.type_args
                )
                resolved = f"{node.expr.name}_{type_args_str}"

            if isinstance(node.expr, Identifier) and node.expr.name == "File":
                if node.method == "open":
                    return "File"
                elif node.method in ("rename", "delete"):
                    return "int"
            # Handle File instance methods
            if resolved == "File":
                file_method_returns = {
                    "read": "string",
                    "write": "int",
                    "close": "int",
                    "writeb": "int",
                    "readb": "char[]",
                    "readln": "string",
                    "readlnb": "char[]",
                    "replace": "int",
                    "replaceall": "int",
                    "rewind": "void",
                }
                return file_method_returns.get(node.method, "int")
            # Handle class methods - get return type from the method's function signature
            if resolved in self.class_symtab:
                cls_info = self.class_symtab[resolved]
                func = cls_info["methods"].get(node.method)
                if func:
                    ret_type = func.function_type.return_type
                    return self._llvm_type_to_leash_name(ret_type)
        return "int"

    def _llvm_type_to_leash_name(self, llvm_type):
        """Convert an LLVM type to a Leash type name."""
        if isinstance(llvm_type, ir.IntType):
            if llvm_type.width == 1:
                return "bool"
            elif llvm_type.width == 8:
                return "char"
            elif llvm_type.width == 32:
                return "int"
            elif llvm_type.width == 64:
                return "int<64>"
        elif isinstance(llvm_type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
            return "float"
        elif isinstance(llvm_type, ir.PointerType):
            if llvm_type.pointee == ir.IntType(8):
                return "string"
            return "ptr"
        elif isinstance(llvm_type, ir.VoidType):
            return "void"
        elif isinstance(llvm_type, ir.LiteralStructType):
            # Find the struct name by matching the LLVM type
            for name, info in self.struct_symtab.items():
                if info["type"] == llvm_type:
                    return name
        return "int"  # fallback

    def _resolve_type_name(self, type_name):
        """Resolve type aliases to their underlying type (strips imut qualifier).
        Also resolves generic type names like Hash<string, int> to Hash_string_int.
        """
        # Strip imut qualifier — it's a compile-time-only concept
        if isinstance(type_name, str) and type_name.startswith("imut "):
            type_name = type_name[5:]

        prefix = ""
        if isinstance(type_name, str):
            while type_name.startswith("*") or type_name.startswith("&"):
                prefix += type_name[0]
                type_name = type_name[1:]

        visited = set()
        while (
            isinstance(type_name, str)
            and type_name in self.type_aliases
            and type_name not in visited
        ):
            visited.add(type_name)
            type_name = self.type_aliases[type_name]

        # Handle generic type names like Hash<string, int> -> Hash_string_int
        # But don't mangle vec<T> types - they are handled specially
        if isinstance(type_name, str) and "<" in type_name and type_name.endswith(">"):
            base_class = type_name.split("<")[0]
            # Don't mangle vec types or built-in sized types (int<>, uint<>, float<>) or hash
            if base_class not in ("vec", "matrix", "int", "uint", "float", "hash"):
                type_args_str = type_name[len(base_class) + 1 : -1]
                type_args = [a.strip() for a in type_args_str.split(",")]
                mangled_name = f"{base_class}_{'_'.join(t.replace('<', '_').replace('>', '_').replace(',', '_').replace(' ', '') for t in type_args)}"
                type_name = mangled_name

        if prefix:
            return f"{prefix}{type_name}"
        return type_name

    def _is_function_pointer_type(self, type_name):
        """Check if a type string represents a function pointer type."""
        if not isinstance(type_name, str):
            return False
        return type_name.startswith("fnc(") and " : " in type_name

    def _get_function_pointer_signature(self, type_name):
        """Extract (param_types, return_type) from a function pointer type string."""
        if not self._is_function_pointer_type(type_name):
            return None, None
        # Format: fnc(param1, param2) : return_type
        inner = type_name[4 : type_name.index(")")]
        return_type = type_name.split(" : ")[-1]
        param_types = [p.strip() for p in inner.split(",")] if inner else []
        return param_types, return_type

    def _codegen_Program(self, node):
        # Reset native libs for this compilation
        self.native_libs = []

        # Create showb helpers
        self._create_showb_helpers()

        # First pass: register type aliases and struct/enum definitions
        for item in node.items:
            if isinstance(item, TypeAlias):
                self._codegen(item)
            elif isinstance(item, StructDef):
                self._codegen(item)
            elif isinstance(item, EnumDef):
                self._codegen(item)
            elif isinstance(item, ErrorDef):
                self._codegen(item)
            elif isinstance(item, UnionDef):
                self._codegen(item)
            elif isinstance(item, NativeImport):
                self._codegen(item)

        # Second pass: global variable declarations
        # Initialize storage for globals (reset in case CodeGen is reused)
        self.global_var_ptrs = {}
        self.global_init_list = []
        self.init_func = None
        for item in node.items:
            if isinstance(item, GlobalVarDecl):
                self._codegen(item)
            elif isinstance(item, NativeImport):
                self._codegen_native_import_vars(item)

        # Third pass: generate non-generic classes (which may reference structs)
        for item in node.items:
            if isinstance(item, ClassDef):
                # Skip generic classes - they will be generated when instantiated
                if not item.type_params:
                    self._codegen(item)

        # Fourth pass: generate generic class instantiations
        self._codegen_instantiated_generics()

        # Fifth pass: generate init function for globals if needed
        if self.global_init_list:
            self._codegen_global_init_function()

        # Sixth pass: pre-declare all functions (including opdefs) so they're in func_symtab
        for item in node.items:
            if isinstance(item, Function):
                self._codegen_predeclare_function(item)
            elif isinstance(item, OpDef):
                self._codegen_predeclare_opdef(item)

        # Seventh pass: generate function bodies
        for item in node.items:
            if isinstance(item, Function):
                self._codegen(item)
            elif isinstance(item, OpDef):
                self._codegen(item)

    def _codegen_GlobalVarDecl(self, node):
        """Generate LLVM global variable for a top-level pub/priv declaration."""
        # Resolve the Leash type to LLVM type
        llvm_type = self._get_llvm_type(node.var_type)
        # Create the global variable
        gv = ir.GlobalVariable(self.module, llvm_type, name=node.name)
        gv.linkage = (
            "internal"  # module-local; could use external for pub but not needed
        )
        # Store in global symbol table for visibility in functions
        self.global_var_ptrs[node.name] = (gv, node.var_type)
        # If there is an initializer, schedule it for runtime initialization
        if node.value is not None:
            self.global_init_list.append((gv, node.value, node.var_type))

    def _codegen_NativeImport(self, node):
        """Generate external function declarations for native library imports (first pass)."""
        for name, args, return_type in node.func_declarations:
            arg_types = [self._get_llvm_type(arg_type) for _, arg_type, _ in args]
            ret_type = self._get_llvm_type(return_type, is_return=True)
            func_type = ir.FunctionType(ret_type, arg_types)
            func = ir.Function(self.module, func_type, name=name)
            func.linkage = "external"
            self.func_symtab[name] = func
        self._codegen_native_import_structs_unions_enums(node)
        self.native_libs.append(
            (
                node.lib_path,
                node.func_declarations,
                node.var_declarations,
                node.struct_declarations,
                node.union_declarations,
                node.enum_declarations,
                node.typedef_declarations,
            )
        )

    def _codegen_native_import_vars(self, node):
        """Generate external global variable declarations for native library imports (second pass)."""
        for name, var_type in node.var_declarations:
            llvm_type = self._get_llvm_type(var_type)
            gv = ir.GlobalVariable(self.module, llvm_type, name=name)
            gv.linkage = "external"
            self.global_var_ptrs[name] = (gv, var_type)

    def _codegen_native_import_structs_unions_enums(self, node):
        """Generate struct, union, and enum types for native library imports."""
        for _, name, fields in node.struct_declarations:
            llvm_types = [self._get_llvm_type(ftype) for _, ftype in fields]
            struct_type = ir.LiteralStructType(llvm_types)
            self.struct_symtab[name] = {
                "type": struct_type,
                "fields": {fname: idx for idx, (fname, _) in enumerate(fields)},
                "field_types": {fname: ftype for fname, ftype in fields},
                "methods": {},
            }
        for _, name, variants in node.union_declarations:
            variant_info = {}
            max_size = 0
            for idx, (vname, vtype) in enumerate(variants):
                llvm_ty = self._get_llvm_type(vtype)
                size = self._type_byte_size(llvm_ty)
                if size > max_size:
                    max_size = size
                variant_info[vname] = {
                    "index": idx,
                    "type_name": vtype,
                    "llvm_type": llvm_ty,
                    "size": size,
                }
            if max_size < 8:
                max_size = 8
            union_type = ir.LiteralStructType(
                [ir.IntType(32), ir.ArrayType(ir.IntType(8), max_size)]
            )
            self.union_symtab[name] = {
                "type": union_type,
                "variants": variant_info,
                "max_size": max_size,
            }
        for _, name, members in node.enum_declarations:
            names = []
            for mname in members:
                s = bytearray(mname.encode("utf8") + b"\0")
                c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
                g = ir.GlobalVariable(
                    self.module,
                    c_str.type,
                    name=self.module.get_unique_name(f"enum_{name}_{mname}"),
                )
                g.linkage = "internal"
                g.global_constant = True
                g.initializer = c_str
                names.append(g.bitcast(ir.IntType(8).as_pointer()))
            ptr_type = ir.IntType(8).as_pointer()
            arr_type = ir.ArrayType(ptr_type, len(names))
            c_names_arr = ir.Constant(arr_type, names)
            g_names = ir.GlobalVariable(
                self.module, arr_type, name=f"enum_names_{name}"
            )
            g_names.linkage = "internal"
            g_names.global_constant = True
            g_names.initializer = c_names_arr
            self.enum_symtab[name] = {"members": members, "names_arr": g_names}
        for _, name, target_type in node.typedef_declarations:
            self.type_aliases[name] = target_type

    def _codegen_global_init_function(self):
        """Generate a function that initializes all globals with initializers."""
        init_func = ir.Function(
            self.module, ir.FunctionType(ir.VoidType(), []), name="_leash_init_globals"
        )
        block = init_func.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        for gv, init_expr, leash_type in self.global_init_list:
            init_val = self._codegen(init_expr)
            self.builder.store(init_val, gv)
        self.builder.ret_void()
        self.builder = None
        self.init_func = init_func

    def _codegen_instantiated_generics(self):
        """Generate code for all instantiated generic classes and functions.
        This is called after type checking has created the instantiations.
        """
        from .typechecker import TypeChecker

        # Generate code for instantiated generic classes
        # Skip placeholder classes (those with underscore-prefixed type params like _T1)
        for mangled_name, class_node in TypeChecker.instantiated_class_nodes.items():
            if mangled_name not in self.class_symtab:
                # Skip placeholder classes (e.g., Hash__T1__T2)
                # These have underscore-prefixed type parameters
                parts = mangled_name.split("_")
                is_placeholder = any(
                    p.startswith("T") and p[1:].isalnum()
                    for p in parts[1:]
                    if len(p) > 1
                )
                if not is_placeholder:
                    self._codegen(class_node)

        # Generate code for instantiated generic functions
        for mangled_name, func_node in TypeChecker.instantiated_func_nodes.items():
            if mangled_name not in self.func_symtab:
                self._codegen(func_node)

    def _get_llvm_type(self, type_name, is_return=False):
        # Resolve aliases first
        type_name = self._resolve_type_name(type_name)

        if type_name.startswith("*") or type_name.startswith("&"):
            inner_type = type_name[1:] if type_name.startswith("*") else type_name[1:]
            # If inner type is a function pointer, we still need to return a pointer type
            if self._is_function_pointer_type(inner_type):
                param_types, return_type = self._get_function_pointer_signature(
                    inner_type
                )
                llvm_params = [self._get_llvm_type(t) for t in param_types]
                llvm_ret = self._get_llvm_type(return_type, is_return=True)
                fn_type = ir.FunctionType(llvm_ret, llvm_params)
                return fn_type.as_pointer()
            return self._get_llvm_type(inner_type).as_pointer()

        # Handle multi-return type: (int, float, ...) -> struct { int, float, ... }
        # Must come after function pointer check (fnc(...) starts with fnc not just ()
        if type_name.startswith("(") and type_name.endswith(")") and not type_name.startswith("fnc("):
            inner = type_name[1:-1]
            elem_types_str = [t.strip() for t in inner.split(",")]
            llvm_elem_types = []
            for et in elem_types_str:
                llvm_elem_types.append(self._get_llvm_type(et, is_return=is_return))
            return ir.LiteralStructType(llvm_elem_types)

        # Handle function pointer types: fnc(param_types) : return_type
        if self._is_function_pointer_type(type_name):
            param_types, return_type = self._get_function_pointer_signature(type_name)
            llvm_params = [self._get_llvm_type(t) for t in param_types]
            llvm_ret = self._get_llvm_type(return_type, is_return=True)
            fn_type = ir.FunctionType(llvm_ret, llvm_params)
            return fn_type.as_pointer()

        if type_name.endswith("]") and "[" in type_name:
            base = type_name.split("[")[0]
            base_type = self._get_llvm_type(base)
            return ir.LiteralStructType([ir.IntType(64), base_type.as_pointer()])

        if type_name.startswith("vec<") and type_name.endswith(">"):
            inner = type_name[4:-1]
            inner_llvm = self._get_llvm_type(inner)
            return ir.LiteralStructType(
                [inner_llvm.as_pointer(), ir.IntType(64), ir.IntType(64)]
            )

        if type_name.startswith("matrix<") and type_name.endswith(">"):
            inner = type_name[7:-1]
            inner_llvm = self._get_llvm_type(inner)
            return ir.LiteralStructType(
                [inner_llvm.as_pointer(), ir.IntType(64), ir.IntType(64)]
            )

        # Hash table type: hash<K, V> -> {i64 size, i64 cap, ptr entries}
        # entries is an array of key-value pairs: {ptr key, ptr value}
        if type_name.startswith("hash<") and type_name.endswith(">"):
            # For now, we store keys as string pointers and values as void pointers
            # The entries pointer will point to an array of Entry structs
            entry_ptr_type = ir.IntType(8).as_pointer()
            return ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(64), entry_ptr_type]
            )

        if type_name in ("int", "uint"):
            return ir.IntType(32)
        elif type_name.startswith("int<"):
            size = int(type_name[4:-1])
            return ir.IntType(size)
        elif type_name.startswith("uint<"):
            size = int(type_name[5:-1])
            return ir.IntType(size)
        elif type_name == "void":
            return ir.VoidType() if is_return else ir.IntType(8).as_pointer()
        elif type_name == "string":
            return ir.IntType(8).as_pointer()
        elif type_name == "char":
            return ir.IntType(8)
        elif type_name == "bool":
            return ir.IntType(1)
        elif type_name == "float":
            return (
                ir.DoubleType()
            )  # standard float maps to standard float literals size
        elif type_name.startswith("float<"):
            size = int(type_name[6:-1])
            if size <= 16:
                return ir.HalfType()
            elif size <= 32:
                return ir.FloatType()
            else:
                return ir.DoubleType()
        elif type_name in self.union_symtab:
            return self.union_symtab[type_name]["type"]
        elif type_name in self.struct_symtab:
            return self.struct_symtab[type_name]["type"]
        elif type_name in self.enum_symtab:
            return ir.IntType(32)
        elif type_name in self.class_symtab:
            return self.class_symtab[type_name]["type"].as_pointer()
        # File type - represented as an opaque pointer (FILE*)
        elif type_name == "File":
            return ir.IntType(8).as_pointer()  # FILE* is i8*
        return ir.IntType(32)  # default fallback

    def _get_type_size(self, llvm_type):
        """Get the size of an LLVM type in bytes."""
        # Use the target data layout to get the size
        if hasattr(self.module, 'data_layout') and self.module.data_layout:
            return self.module.data_layout.get_abi_size(llvm_type)
        # Fallback: manual calculation for common types
        if isinstance(llvm_type, ir.IntType):
            return (llvm_type.width + 7) // 8
        elif isinstance(llvm_type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
            return 2 if isinstance(llvm_type, ir.HalfType) else (4 if isinstance(llvm_type, ir.FloatType) else 8)
        elif isinstance(llvm_type, ir.PointerType):
            return 8  # Pointer size on 64-bit
        elif isinstance(llvm_type, ir.LiteralStructType):
            # Sum up field sizes (simplified - doesn't account for padding)
            total = 0
            for elem in llvm_type.elements:
                total += self._get_type_size(elem)
            return total
        return 8  # Default fallback

    def _codegen_StructDef(self, node):
        llvm_types = []
        fields = {}
        field_types = {}
        field_defaults = {}
        for idx, (fname, ftype, fdefault) in enumerate(node.fields):
            fields[fname] = idx
            field_types[fname] = ftype
            if fdefault is not None:
                field_defaults[fname] = fdefault
            llvm_types.append(self._get_llvm_type(ftype))

        struct_type = ir.LiteralStructType(llvm_types)
        self.struct_symtab[node.name] = {
            "type": struct_type,
            "fields": fields,
            "field_types": field_types,
            "field_defaults": field_defaults,
            "methods": {},
        }

    def _codegen_TypeAlias(self, node):
        self.type_aliases[node.name] = node.target_type

    def _codegen_ClassDef(self, node):
        old_class_name = self.current_class_name
        self.current_class_name = node.name

        # Vtable pointer type (i8*)
        vtable_ptr_type = ir.IntType(8).as_pointer()

        llvm_types = []  # Will build from parent or start fresh
        fields = {}
        field_types = {}
        field_defaults = {}

        # Inherit parent struct type (includes vtable pointer and parent fields)
        if node.parent and node.parent in self.class_symtab:
            parent_info = self.class_symtab[node.parent]
            # Copy all parent types (vtable pointer + parent fields)
            for i, elem_type in enumerate(parent_info["type"].elements):
                llvm_types.append(elem_type)
            # Copy parent field indices (they already include vtable offset)
            for fname, fidx in parent_info["fields"].items():
                fields[fname] = fidx
                field_types[fname] = parent_info["field_types"][fname]
                if (
                    "field_defaults" in parent_info
                    and fname in parent_info["field_defaults"]
                ):
                    field_defaults[fname] = parent_info["field_defaults"][fname]

        # If no parent, start with vtable pointer
        if not node.parent:
            llvm_types.append(vtable_ptr_type)

        # Add child's own fields
        idx = len(llvm_types)
        static_fields = {}
        for f in node.fields:
            if getattr(f, "is_static", False):
                # Static field - generate a global variable
                static_llvm_type = self._get_llvm_type(f.var_type)
                static_gv = ir.GlobalVariable(
                    self.module, static_llvm_type, name=f"{node.name}_static_{f.name}"
                )
                static_gv.linkage = "internal"
                static_fields[f.name] = {
                    "global": static_gv,
                    "type": f.var_type,
                    "value": f.value,
                }
                # If there's a default value, schedule for initialization
                if f.value is not None:
                    self.global_init_list.append((static_gv, f.value, f.var_type))
            else:
                fields[f.name] = idx
                field_types[f.name] = f.var_type
                if f.value is not None:
                    field_defaults[f.name] = f.value
                llvm_types.append(self._get_llvm_type(f.var_type))
                idx += 1

        struct_type = ir.LiteralStructType(llvm_types)
        self.class_symtab[node.name] = {
            "type": struct_type,
            "fields": fields,
            "field_types": field_types,
            "field_defaults": field_defaults,
            "static_fields": static_fields,
            "methods": {},
            "method_static": {},
            "method_imut": {},  # Track which methods are imut (non-overridable)
            "method_order": [],  # Order of virtual methods for vtable
            "vtable_type": None,
            "vtable_global": None,
            "vtable_indices": {},
            "constructor_args": None,  # AST args tuple for the constructor (with 'this')
            "parent": node.parent,
        }

        # Build method order - inherit from parent first
        if node.parent and node.parent in self.class_symtab:
            parent_info = self.class_symtab[node.parent]
            # Copy parent's method order
            for mname in parent_info.get("method_order", []):
                if not parent_info["method_static"].get(mname, False):
                    if mname not in self.class_symtab[node.name]["method_order"]:
                        self.class_symtab[node.name]["method_order"].append(mname)
            # Inherit parent methods
            for mname, mfunc in parent_info["methods"].items():
                self.class_symtab[node.name]["methods"][mname] = mfunc
                self.class_symtab[node.name]["method_static"][mname] = parent_info[
                    "method_static"
                ].get(mname, False)
                self.class_symtab[node.name]["method_imut"][mname] = parent_info[
                    "method_imut"
                ].get(mname, False)

        # Add new methods to method order BEFORE codegen
        for m in node.methods:
            if (
                not m.is_static
                and m.fnc.name not in self.class_symtab[node.name]["method_order"]
            ):
                self.class_symtab[node.name]["method_order"].append(m.fnc.name)

        # Create vtable type and placeholder global BEFORE codegen methods
        # so StructInit can store the vtable pointer
        self._create_vtable_placeholder(node.name)

        # Two-pass method codegen to handle recursive/self-referential calls:
        # Pass 1: Create function declarations and register them in the method table
        # Pass 2: Fill in function bodies
        method_info = []
        for m in node.methods:
            orig_name = m.fnc.name
            m.fnc.name = f"{node.name}_{orig_name}"

            # If not static, prepend 'this' to args
            if not m.is_static:
                new_args = [(name, typ, default) for name, typ, default in m.fnc.args]
                new_args = [("this", node.name, None)] + new_args
                m.fnc.args = tuple(new_args)

            # Determine function type
            ret_type = self._get_llvm_type(m.fnc.return_type, is_return=True)
            arg_types = [self._get_llvm_type(t, is_return=False) for _, t, _ in m.fnc.args]
            func_type = ir.FunctionType(ret_type, arg_types)

            # Create function declaration (no body yet)
            func = ir.Function(self.module, func_type, name=m.fnc.name)
            self.func_symtab[m.fnc.name] = func

            # Register in method table immediately so recursive calls work
            self.class_symtab[node.name]["methods"][orig_name] = func
            if orig_name == node.name:  # this is the constructor
                self.class_symtab[node.name]["constructor_args"] = m.fnc.args
            self.class_symtab[node.name]["method_static"][orig_name] = m.is_static
            self.class_symtab[node.name]["method_imut"][orig_name] = m.is_imut

            method_info.append((m.fnc, orig_name))

        # Pass 2: Fill in function bodies
        for fnc_node, orig_name in method_info:
            func = self.func_symtab[fnc_node.name]
            self._codegen_Function_body(fnc_node, func)

        # Update vtable with actual function pointers
        self._update_vtable(node.name)
        self.current_class_name = old_class_name

    def _create_vtable_placeholder(self, class_name):
        """Create a vtable type and placeholder global for the given class.
        This is called before methods are codegen'd so StructInit can use it.
        The actual function pointers will be filled in later by _update_vtable.
        """
        cls_info = self.class_symtab[class_name]
        method_order = cls_info.get("method_order", [])

        if not method_order:
            # No virtual methods, no vtable needed
            cls_info["vtable_type"] = None
            cls_info["vtable_global"] = None
            cls_info["vtable_indices"] = {}
            return

        # Build vtable type: struct of function pointers
        ptr_type = ir.IntType(8).as_pointer()
        vtable_elements = []
        vtable_indices = {}

        for i, method_name in enumerate(method_order):
            vtable_indices[method_name] = i
            vtable_elements.append(ptr_type)

        vtable_type = ir.LiteralStructType(vtable_elements)

        # Create placeholder vtable with null pointers
        vtable_values = [ir.Constant(ptr_type, None) for _ in method_order]
        vtable_const = ir.Constant(vtable_type, vtable_values)
        vtable_global = ir.GlobalVariable(
            self.module, vtable_type, f"{class_name}_vtable"
        )
        vtable_global.linkage = "internal"
        vtable_global.global_constant = True
        vtable_global.initializer = vtable_const

        cls_info["vtable_type"] = vtable_type
        cls_info["vtable_global"] = vtable_global
        cls_info["vtable_indices"] = vtable_indices

    def _update_vtable(self, class_name):
        """Update the vtable with actual function pointers after methods are codegen'd."""
        cls_info = self.class_symtab[class_name]
        method_order = cls_info.get("method_order", [])

        if not method_order or not cls_info.get("vtable_global"):
            return

        ptr_type = ir.IntType(8).as_pointer()
        vtable_values = []

        for method_name in method_order:
            # Get the function for this class (could be inherited or overridden)
            func = cls_info["methods"].get(method_name)
            if func:
                func_ptr = func.bitcast(ptr_type)
                vtable_values.append(func_ptr)
            else:
                vtable_values.append(ir.Constant(ptr_type, None))

        # Update the vtable initializer
        vtable_const = ir.Constant(cls_info["vtable_type"], vtable_values)
        cls_info["vtable_global"].initializer = vtable_const

    def _create_vtable(self, class_name):
        """Create a vtable for the given class (combined placeholder + update)."""
        self._create_vtable_placeholder(class_name)
        self._update_vtable(class_name)

    def _codegen_ThisExpr(self, node):
        if "this" not in self.var_symtab:
            raise LeashError("'this' is not available in the current context", node=node)
        var_info = self.var_symtab["this"]
        ptr = var_info[0]
        return self.builder.load(ptr)

    def _codegen_SelfExpr(self, node):
        if node.member:
            if node.member == "Parent":
                if not self.current_class_name:
                    raise LeashError("'self::Parent' is not available in the current context", node=node)
                parent = self.class_symtab[self.current_class_name].get("parent")
                if not parent:
                    raise LeashError(f"Class '{self.current_class_name}' has no parent class", node=node)
                return self._emit_const_str(parent)
            elif node.member == "Class":
                if not self.current_class_name:
                    raise LeashError("'self::Class' is not available in the current context", node=node)
                return self._emit_const_str(self.current_class_name)
            else:
                raise LeashError(f"Unknown self member '{node.member}'", node=node)

        if self.current_func_name:
            # For class methods, name is usually Class_Method. 
            # If the user wants just Method, we might need to strip Class_
            # but usually self in a method refers to the method name.
            # Let's check if it's a mangled method name.
            name = self.current_func_name
            if self.current_class_name and name.startswith(self.current_class_name + "_"):
                name = name[len(self.current_class_name) + 1:]
            return self._emit_const_str(name)
        if self.current_error_name:
            return self._emit_const_str(self.current_error_name)
        if self.current_class_name:
            return self._emit_const_str(self.current_class_name)
            
        raise LeashError("'self' is not available in the current context", node=node)

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
            variant_info[vname] = {
                "index": idx,
                "type_name": vtype,
                "llvm_type": llvm_ty,
                "size": size,
            }

        # Union layout: { i64 tag (padded for alignment), [max_size x i8] }
        # Tag is i64 so data region starts at offset 8, ensuring proper alignment
        # for i64/double variants when bitcasting the data pointer.
        if max_size < 8:
            max_size = 8  # minimum 8 bytes for pointer-sized data
        union_type = ir.LiteralStructType(
            [ir.IntType(64), ir.ArrayType(ir.IntType(8), max_size)]
        )

        self.union_symtab[node.name] = {
            "type": union_type,
            "variants": variant_info,
            "max_size": max_size,
        }

    def _codegen_EnumDef(self, node):
        # Create a global array of strings for member names
        names = []
        members_info = []  # (name, type, value_ptr or None)
        custom_values = {}  # member_name -> (llvm_type, llvm_value) for literals
        
        for member in node.members:
            if isinstance(member, tuple):
                mname, mtype, mvalue = member
            else:
                # Backward compatibility
                mname = member
                mtype = None
                mvalue = None
            
            # Create a global string for this member name
            s = bytearray(mname.encode("utf8") + b"\0")
            c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
            g = ir.GlobalVariable(
                self.module,
                c_str.type,
                name=self.module.get_unique_name(f"enum_{node.name}_{mname}"),
            )
            g.linkage = "internal"
            g.global_constant = True
            g.initializer = c_str
            # Store pointer to this global string
            names.append(
                self.builder.bitcast(g, ir.IntType(8).as_pointer())
                if self.builder
                else g.bitcast(ir.IntType(8).as_pointer())
            )
            
            # Handle custom value if present
            value_ptr = None
            if mvalue is not None:
                # Generate the value based on the type
                if isinstance(mvalue, StringLiteral):
                    val_bytes = bytearray(mvalue.value.encode("utf8") + b"\0")
                    val_cstr = ir.Constant(ir.ArrayType(ir.IntType(8), len(val_bytes)), val_bytes)
                    val_g = ir.GlobalVariable(
                        self.module,
                        val_cstr.type,
                        name=self.module.get_unique_name(f"enum_val_{node.name}_{mname}"),
                    )
                    val_g.linkage = "internal"
                    val_g.global_constant = True
                    val_g.initializer = val_cstr
                    value_ptr = val_g.bitcast(ir.IntType(8).as_pointer())
                    custom_values[mname] = ("string", value_ptr)
                elif isinstance(mvalue, NumberLiteral):
                    llvm_type = ir.IntType(32)
                    llvm_val = ir.Constant(llvm_type, mvalue.value)
                    custom_values[mname] = ("int", llvm_val)
                elif isinstance(mvalue, FloatLiteral):
                    llvm_type = ir.DoubleType()
                    llvm_val = ir.Constant(llvm_type, mvalue.value)
                    custom_values[mname] = ("float", llvm_val)
                elif isinstance(mvalue, BoolLiteral):
                    llvm_type = ir.IntType(1)
                    llvm_val = ir.Constant(llvm_type, 1 if mvalue.value else 0)
                    custom_values[mname] = ("bool", llvm_val)
                # TODO: Handle other constant types
            
            members_info.append((mname, mtype, value_ptr))

        # Now create an array of these pointers
        ptr_type = ir.IntType(8).as_pointer()
        arr_type = ir.ArrayType(ptr_type, len(names))
        c_names_arr = ir.Constant(arr_type, names)

        g_names = ir.GlobalVariable(
            self.module, arr_type, name=f"enum_names_{node.name}"
        )
        g_names.linkage = "internal"
        g_names.global_constant = True
        g_names.initializer = c_names_arr

        self.enum_symtab[node.name] = {
            "members": members_info, 
            "names_arr": g_names,
            "member_dict": {m[0]: (m[1], m[2]) for m in members_info},
            "custom_values": custom_values,
        }

    def _codegen_ErrorDef(self, node):
        # Generate a function for this error that returns its formatted message
        ret_type = ir.IntType(8).as_pointer()
        arg_types = [self._get_llvm_type(t) for _, t in node.args]
        func_type = ir.FunctionType(ret_type, arg_types)

        func = ir.Function(self.module, func_type, name=f"_error_{node.name}")
        self.func_symtab[f"_error_{node.name}"] = func

        # Build the function body
        block = func.append_basic_block("entry")
        saved_builder = self.builder
        self.builder = ir.IRBuilder(block)

        # Register args in symtab
        saved_vars = self.var_symtab.copy()
        for i, (arg_name, arg_type) in enumerate(node.args):
            ptr = self.builder.alloca(func.args[i].type)
            self.builder.store(func.args[i], ptr)
            self.var_symtab[arg_name] = (ptr, arg_type)

        saved_error_name = self.current_error_name
        self.current_error_name = node.name

        # Codegen the message expression
        msg_val = self._codegen(node.message_expr)
        self.builder.ret(msg_val)

        # Restore builder and symtab
        self.builder = saved_builder
        self.var_symtab = saved_vars
        self.current_error_name = saved_error_name

    def _codegen_ThrowStatement(self, node):
        # 1. Generate the error message by calling the error function
        error_func_name = f"_error_{node.error_name}"
        error_func = self.func_symtab.get(error_func_name)
        if not error_func:
            # Maybe it's defined later or in another module (though currently we expect it in func_symtab)
            # Find the ErrorDef to get its signature if not in func_symtab yet?
            # Actually, first pass should have registered it.
            raise LeashError(f"Internal error: could not find codegen for error '{node.error_name}'", node=node)

        args = [self._codegen(a) for a in node.args]
        msg_val = self.builder.call(error_func, args)

        # 2. Print error info: "Runtime error: [msg] at [file]:[line]:[col]"
        fmt_ptr = self._emit_const_str("\nRuntime error: %s\n  --> %s:%d:%d\n")

        file_name = self.program_file if hasattr(self, "program_file") else "unknown"
        file_ptr = self._emit_const_str(file_name)

        line_val = ir.Constant(ir.IntType(32), node.line if node.line is not None else 0)
        col_val = ir.Constant(ir.IntType(32), node.col if node.col is not None else 0)

        self.builder.call(self.printf, [fmt_ptr, msg_val, file_ptr, line_val, col_val])

        # 3. If in works block, we might want to "catch" it?
        if self.in_works_block:
            self.works_error_occured = True
            self.works_error_info = msg_val
            # The WorksOtherwiseStatement codegen handles the branching if works_error_occured is true.
            return

        # 4. Exit the program
        self.builder.call(self.exit_fn, [ir.Constant(ir.IntType(32), 1)])
        # Use a dummy branch to a new unreachable block to satisfy LLVM if needed,
        # but ret/unreachable works after exit if it returns void.
        self.builder.unreachable()

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

    def _codegen_predeclare_function(self, node):
        """Pre-declare a function so it's available in func_symtab before body codegen."""
        if node.name in self.func_symtab:
            return
        if node.name == "main":
            return
        is_worker = getattr(node, "is_worker", False)
        if is_worker:
            void_ptr = ir.IntType(8).as_pointer()
            func_type = ir.FunctionType(void_ptr, [void_ptr])
            func = ir.Function(self.module, func_type, name=node.name)
            self.func_symtab[node.name] = func
            return
        arg_types = []
        struct_type_name = getattr(node, 'struct_type', None)
        if struct_type_name:
            this_ptr_type = self._get_llvm_type(struct_type_name).as_pointer()
            arg_types.append(this_ptr_type)
        for arg_name, arg_type, default in node.args:
            llvm_arg_type = self._get_llvm_type(arg_type)
            arg_types.append(llvm_arg_type)
        ret_type = self._get_llvm_type(node.return_type, is_return=True)
        func_type = ir.FunctionType(ret_type, arg_types)
        func = ir.Function(self.module, func_type, name=node.name)
        self.func_symtab[node.name] = func
        if struct_type_name and struct_type_name in self.struct_symtab:
            self.struct_symtab[struct_type_name]["methods"][node.name] = func

    def _codegen_predeclare_opdef(self, node):
        """Pre-declare a non-generic opdef function."""
        if getattr(node, '_opdef_is_generic', False):
            return
        mangled_name = getattr(node, '_opdef_mangled_name', None)
        if not mangled_name:
            return
        if mangled_name in self.func_symtab:
            return
        arg_types = []
        for arg_name, arg_type, default in node.args:
            llvm_arg_type = self._get_llvm_type(arg_type)
            arg_types.append(llvm_arg_type)
        ret_type = self._get_llvm_type(node.return_type, is_return=True)
        func_type = ir.FunctionType(ret_type, arg_types)
        func = ir.Function(self.module, func_type, name=mangled_name)
        self.func_symtab[mangled_name] = func

    def _codegen_OpDef(self, node):
        if getattr(node, '_opdef_is_generic', False):
            return
        mangled_name = getattr(node, '_opdef_mangled_name', None)
        if not mangled_name:
            return
        func_node = Function(
            mangled_name, tuple((n, t, d) for n, t, d in node.args),
            node.return_type, node.body, []
        )
        self._codegen_Function(func_node)

    def _codegen_Function(self, node):
        # Start with globals in scope (they can be shadowed by locals)
        self.var_symtab = (
            self.global_var_ptrs.copy() if hasattr(self, "global_var_ptrs") else {}
        )
        self.current_func_ret_type_name = node.return_type

        name = node.name
        is_main_with_args = False
        is_worker = getattr(node, "is_worker", False)

        if is_worker:
            # Worker functions have void*(void*) signature
            void_ptr = ir.IntType(8).as_pointer()
            func_type = ir.FunctionType(void_ptr, [void_ptr])
            ret_type = void_ptr
            arg_types = [void_ptr]
        else:
            # Determine return type
            ret_type = self._get_llvm_type(node.return_type, is_return=True)

            # Determine argument types
            arg_types = []
            for arg_name, typ, _ in node.args:
                arg_types.append(self._get_llvm_type(typ, is_return=False))

            func_type = ir.FunctionType(ret_type, arg_types)

        # Main function usually needs to be i32 main() in standard C compilation
        if name == "main":
            if len(node.args) == 1 and node.args[0][1] == "string[]":
                is_main_with_args = True
                arg_types = [ir.IntType(32), ir.IntType(8).as_pointer().as_pointer()]

            if node.return_type == "void":
                ret_type = ir.IntType(32)

            func_type = ir.FunctionType(ret_type, arg_types)

        func = self.func_symtab.get(name)
        if func is None:
            func = ir.Function(self.module, func_type, name=name)
            self.func_symtab[name] = func

        self._codegen_Function_body(node, func, is_main_with_args=is_main_with_args)

        return func

    def _codegen_Function_body(self, node, func, is_main_with_args=False):
        """Fill in the body of an already-declared function."""
        block = func.append_basic_block(name="entry")
        self.builder = ir.IRBuilder(block)

        name = node.name
        is_worker = getattr(node, "is_worker", False)

        old_func_name = self.current_func_name
        self.current_func_name = name

        old_unsafe = self.in_unsafe_func
        self.in_unsafe_func = getattr(node, "is_unsafe", False)

        if name == "main":
            self.builder.call(self.gc_init, [])
            # Set stdout to unbuffered so that interactive prompts and prints are flushed in real time
            stdout_ptr = self.builder.call(self.get_stdout_fn, [])
            null_ptr = ir.Constant(ir.IntType(8).as_pointer(), None)
            self.builder.call(self.setbuf_fn, [stdout_ptr, null_ptr])

            # Call global initializer if any globals need runtime init
            if self.init_func:
                self.builder.call(self.init_func, [])
            # Initialize start_time for timepass()
            self.builder.call(
                self.clock_gettime, [self.CLOCK_MONOTONIC, self.start_time_gv]
            )
            # Auto-seed random number generator if seed() was not explicitly called
            if not self.seed_called:
                time_val = self.builder.call(
                    self.time, [ir.Constant(ir.IntType(64).as_pointer(), None)]
                )
                seed_val = self.builder.trunc(time_val, ir.IntType(32))
                self.builder.call(self.srand, [seed_val])

            # Set up interrupt handler for worker threads
            self.builder.call(self.leash_setup_interrupt_fn, [])

        # Allocate args
        struct_type_name = getattr(node, 'struct_type', None)
        arg_offset = 0
        if struct_type_name:
            func.args[0].name = "this_ptr"
            this_ptr = self.builder.alloca(func.args[0].type)
            self.builder.store(func.args[0], this_ptr)
            self.var_symtab["this"] = (this_ptr, struct_type_name)
            arg_offset = 1

        if is_worker:
            # Worker functions get void*(void*) - the arg contains params
            func.args[0].name = "_arg"
            if node.args:
                # Cast void* arg to struct pointer and load each parameter
                arg_types = [self._get_llvm_type(t) for _, t, _ in node.args]
                arg_struct_ty = ir.LiteralStructType(arg_types)
                arg_struct_ptr = self.builder.bitcast(
                    func.args[0], arg_struct_ty.as_pointer()
                )
                for i, (arg_name, arg_type_name, _) in enumerate(node.args):
                    llvm_arg_type = self._get_llvm_type(arg_type_name)
                    ptr = self.builder.alloca(llvm_arg_type)
                    gep = self.builder.gep(
                        arg_struct_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), i),
                        ],
                    )
                    val = self.builder.load(gep)
                    self.builder.store(val, ptr)
                    self.var_symtab[arg_name] = (ptr, arg_type_name)
                # Unregister the arg from GC roots (registered in leash_spawn_worker)
                self.builder.call(self.gc_unregister_root, [func.args[0]])
            else:
                for arg_name, arg_type_name, _ in node.args:
                    llvm_arg_type = self._get_llvm_type(arg_type_name)
                    ptr = self.builder.alloca(llvm_arg_type)
                    self.builder.store(
                        ir.Constant(llvm_arg_type, 0), ptr
                    )
                    self.var_symtab[arg_name] = (ptr, arg_type_name)
        elif is_main_with_args and len(node.args) == 1 and node.args[0][1] == "string[]":
            argc_val = func.args[arg_offset]
            argv_val = func.args[arg_offset + 1]
            argc_val.name = "argc"
            argv_val.name = "argv"

            leash_arg_name = node.args[0][0]
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer().as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(
                slice_val, self.builder.zext(argc_val, ir.IntType(64)), 0
            )
            slice_val = self.builder.insert_value(slice_val, argv_val, 1)

            ptr = self.builder.alloca(slice_type)
            self.builder.store(slice_val, ptr)
            self.var_symtab[leash_arg_name] = (ptr, "string[]")
        else:
            for i, (arg_name, arg_type_name, _) in enumerate(node.args):
                func.args[i + arg_offset].name = arg_name
                ptr = self.builder.alloca(func.args[i + arg_offset].type)
                self.builder.store(func.args[i + arg_offset], ptr)
                self.var_symtab[arg_name] = (ptr, arg_type_name)

        self.defer_stack.append([])  # Push new defer stack frame for this function

        # Pre-declare nested functions so they are available when called from the body
        nested_funcs = [s for s in node.body if isinstance(s, Function)]
        for nf in nested_funcs:
            self._codegen_predeclare_function(nf)

        for stmt in node.body:
            if isinstance(stmt, Function):
                continue  # Handled after the outer function body
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        while self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)

        self.defer_stack.pop()  # Pop defer stack frame

        if not self.builder.block.is_terminated:
            self._emit_cleanup()
            if name == "main":
                self.builder.call(self.showb_flush_fn, [])
                # Wait for all worker threads before exiting
                self.builder.call(self.leash_wait_workers_fn, [])
                self.builder.ret(ir.Constant(ir.IntType(32), 0))
            elif is_worker:
                void_ptr = ir.IntType(8).as_pointer()
                self.builder.ret(ir.Constant(void_ptr, None))
            elif node.return_type == "void":
                self.builder.ret_void()
            elif node.return_type and node.return_type.startswith("("):
                # Multi-return type: return a zero-initialized struct
                default_val = self._emit_default_value(node.return_type)
                self.builder.ret(default_val)
            else:
                self.builder.unreachable()

        # Generate nested function bodies
        for nf in nested_funcs:
            saved_vars = self.var_symtab.copy()
            self._codegen_Function(nf)
            self.var_symtab = saved_vars

        self.builder = None
        self.in_unsafe_func = old_unsafe
        self.current_func_name = old_func_name

    def _codegen_ReturnStatement(self, node):
        old_target = self.current_target_type
        self.current_target_type = getattr(self, "current_func_ret_type_name", None)
        val = self._codegen(node.value)
        self.current_target_type = old_target

        # Cast to return type
        ret_type = self.builder.function.type.pointee.return_type
        if not isinstance(ret_type, ir.VoidType):
            val = self._emit_cast(val, ret_type)

        # Execute deferred calls before returning
        while self.defer_stack and self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)

        self._emit_cleanup(ret_val=val)  # Auto-free everything EXCEPT the return value
        if isinstance(ret_type, ir.VoidType):
            self.builder.ret_void()
        else:
            self.builder.ret(val)

    def _codegen_MultiReturnStatement(self, node):
        """Generate code for multi-return: return expr1, expr2, ..."""
        ret_type = self.builder.function.type.pointee.return_type

        # Build a struct value with all the return values
        if isinstance(ret_type, ir.LiteralStructType):
            # Multi-return: create a struct and insert each value
            val = ir.Constant(ret_type, ir.Undefined)
            for i, expr in enumerate(node.values):
                elem_val = self._codegen(expr)
                # Cast to the expected element type if needed
                expected_elem_type = ret_type.elements[i]
                elem_val = self._emit_cast(elem_val, expected_elem_type)
                val = self.builder.insert_value(val, elem_val, i)
        else:
            # Single return value (shouldn't happen, but handle gracefully)
            val = self._codegen(node.values[0])
            val = self._emit_cast(val, ret_type)

        # Execute deferred calls before returning
        while self.defer_stack and self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)

        self._emit_cleanup(ret_val=val)
        if isinstance(ret_type, ir.VoidType):
            self.builder.ret_void()
        else:
            self.builder.ret(val)

    def _codegen_MultiVariableDecl(self, node):
        """Generate code for multi-variable declaration: a, b : int, int = call()"""
        # Generate the call expression which returns a struct
        call_val = self._codegen(node.value)

        # Extract each element from the struct and create variables
        for i, (name, var_type) in enumerate(zip(node.names, node.var_types)):
            elem_val = self.builder.extract_value(call_val, i)
            target_llvm = self._get_llvm_type(var_type)
            elem_val = self._emit_cast(elem_val, target_llvm)
            ptr = self.builder.alloca(elem_val.type)
            self.builder.store(elem_val, ptr)
            self.var_symtab[name] = (ptr, var_type)

    def _codegen_MultiAssign(self, node):
        """Generate code for multi-assignment: a, b = call()"""
        # Generate the call expression which returns a struct
        call_val = self._codegen(node.value)

        # Extract each element and assign to the target variable
        for i, target in enumerate(node.targets):
            elem_val = self.builder.extract_value(call_val, i)
            # Get the target lvalue
            if isinstance(target, Identifier):
                var_info = self.var_symtab.get(target.name)
                if var_info:
                    ptr = var_info[0]
                    target_type = var_info[1]
                    target_llvm = self._get_llvm_type(target_type)
                    elem_val = self._emit_cast(elem_val, target_llvm)
                    self.builder.store(elem_val, ptr)
            else:
                # Handle member access, index access, etc.
                lvalue_result = self._codegen_lvalue(target)
                if lvalue_result:
                    if len(lvalue_result) == 3:
                        ptr, type_name, _ = lvalue_result
                    else:
                        ptr, type_name = lvalue_result
                    target_llvm = self._get_llvm_type(type_name)
                    elem_val = self._emit_cast(elem_val, target_llvm)
                    self.builder.store(elem_val, ptr)

    def _codegen_StopStatement(self, node):
        # stop (break) - jump to the loop's merge block
        if not self.loop_stack:
            raise LeashError(
                "`stop` can only be used inside a loop",
                node.line,
                node.col,
                tip="`stop` (break) is used to exit a loop early. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
            )
        while self.defer_stack and self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)
        break_bb, _ = self.loop_stack[-1]
        self.builder.branch(break_bb)

    def _codegen_ContinueStatement(self, node):
        # continue - jump to the loop's continue block (usually condition or update)
        if not self.loop_stack:
            raise LeashError(
                "`continue` can only be used inside a loop",
                node.line,
                node.col,
                tip="`continue` skips to the next iteration of a loop. It can only be used within `while`, `for`, `do-while`, or `foreach` loops.",
            )
        while self.defer_stack and self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)
        _, continue_bb = self.loop_stack[-1]
        self.builder.branch(continue_bb)

    def _codegen_EmptyStatement(self, node):
        pass

    def _codegen_IgnoreStatement(self, node):
        ret_type = self.builder.function.type.pointee.return_type
        while self.defer_stack and self.defer_stack[-1]:
            deferred_call = self.defer_stack[-1].pop()
            self._codegen(deferred_call)
        self._emit_cleanup()
        if isinstance(ret_type, ir.VoidType):
            self.builder.ret_void()
        else:
            default_val = self._get_default_value_for_type(ret_type)
            self.builder.ret(default_val)

    def _get_default_value_for_type(self, ty):
        if isinstance(ty, ir.IntType):
            return ir.Constant(ty, 0)
        elif isinstance(ty, (ir.HalfType, ir.FloatType, ir.DoubleType)):
            return ir.Constant(ty, 0.0)
        elif isinstance(ty, ir.PointerType):
            return ir.Constant(ty, None)
        elif isinstance(ty, ir.ArrayType):
            return ir.Constant(ty, 0)
        elif isinstance(ty, ir.StructType):
            return ir.Constant(ty, None)
        else:
            return ir.Constant(ty, 0)

    def _codegen_WorksOtherwiseStatement(self, node):
        works_bb = self.builder.function.append_basic_block("works_body")
        otherwise_bb = self.builder.function.append_basic_block("otherwise_body")
        merge_bb = self.builder.function.append_basic_block("works_merge")

        self.builder.branch(works_bb)
        self.builder.position_at_end(works_bb)

        old_in_works = self.in_works_block
        self.in_works_block = True

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
            if self.works_error_occured:
                self.builder.branch(otherwise_bb)
                break

        self.in_works_block = old_in_works

        if not self.builder.block.is_terminated and not self.works_error_occured:
            self.builder.branch(merge_bb)

        if self.works_error_occured:
            err_msg = self.works_error_info or "Runtime error in works block"
        else:
            err_msg = getattr(node, "err_msg", "Runtime error in works block")

        self.builder.position_at_end(otherwise_bb)
        err_ptr = self.builder.alloca(ir.IntType(8).as_pointer())
        err_str = self._emit_const_str(err_msg)
        self.builder.store(err_str, err_ptr)
        self.var_symtab[node.err_var] = (err_ptr, "string")

        self.works_error_occured = False
        self.works_error_info = None

        for stmt in node.otherwise_block:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        if node.err_var in self.var_symtab:
            del self.var_symtab[node.err_var]

    def _codegen_VariableDecl(self, node):
        resolved_type = self._resolve_type_name(node.var_type)

        # Check if the variable type is a union
        if resolved_type in self.union_symtab:
            union_info = self.union_symtab[resolved_type]
            union_type = union_info["type"]
            ptr = self.builder.alloca(union_type)
            self.var_symtab[node.name] = (ptr, resolved_type, None)
            # Auto-assign the value into the union if provided
            if node.value is not None:
                val = self._codegen(node.value)
                self._union_auto_store(ptr, val, union_info)
            else:
                # Default init to zeros
                self.builder.store(ir.Constant(union_type, None), ptr)
            return

        # Class type without initializer: auto-initialize with default constructor
        if node.value is None and resolved_type in self.class_symtab:
            val = self._codegen_CreateExpr(CreateExpr(resolved_type, []))
            target_llvm = self._get_llvm_type(node.var_type)
            val = self._emit_cast(val, target_llvm)
            ptr = self.builder.alloca(val.type)
            self.builder.store(val, ptr)
            self.var_symtab[node.name] = (ptr, node.var_type, None)
            return

        # Check if we should pass the target type to the expression (useful for ArrayInit)
        old_target = self.current_target_type
        self.current_target_type = node.var_type
        if node.value is None:
            val = self._emit_default_value(node.var_type)
        else:
            val = self._codegen(node.value)
        self.current_target_type = old_target

        # If the declared type maps to a different LLVM type, cast
        target_llvm = self._get_llvm_type(node.var_type)
        val = self._emit_cast(val, target_llvm)
        ptr = self.builder.alloca(val.type)
        self.builder.store(val, ptr)
        
        extra_data = None
        if hasattr(val, 'hash_key_ptrs') and hasattr(val, 'hash_value_ptrs'):
            extra_data = (val.hash_key_ptrs, val.hash_value_ptrs)
        
        self.var_symtab[node.name] = (ptr, node.var_type, extra_data)

    def _union_auto_store(self, union_ptr, val, union_info):
        """Store a value into a union, auto-detecting the matching variant by LLVM type."""
        matched_idx = None
        for vname, vdata in union_info["variants"].items():
            if vdata["llvm_type"] == val.type:
                matched_idx = vdata["index"]
                break
        # Fallback: try to match int types by checking if both are IntType
        if matched_idx is None:
            for vname, vdata in union_info["variants"].items():
                if isinstance(vdata["llvm_type"], ir.IntType) and isinstance(
                    val.type, ir.IntType
                ):
                    val = self._emit_cast(val, vdata["llvm_type"])
                    matched_idx = vdata["index"]
                    break
        # Fallback: try to match float types
        if matched_idx is None:
            for vname, vdata in union_info["variants"].items():
                if isinstance(
                    vdata["llvm_type"], (ir.HalfType, ir.FloatType, ir.DoubleType)
                ) and isinstance(val.type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                    val = self._emit_cast(val, vdata["llvm_type"])
                    matched_idx = vdata["index"]
                    break
        if matched_idx is None:
            raise LeashError(f"Cannot store value of this type into the union")

        # Store tag
        tag_ptr = self.builder.gep(
            union_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
            inbounds=True,
        )
        self.builder.store(ir.Constant(ir.IntType(64), matched_idx), tag_ptr)
        # Store value into data region
        data_ptr = self.builder.gep(
            union_ptr,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
            inbounds=True,
        )
        typed_ptr = self.builder.bitcast(data_ptr, val.type.as_pointer())
        self.builder.store(val, typed_ptr)

    def _codegen_Assignment(self, node):
        from .ast_nodes import MemberAccess, Identifier

        # 1. Specialized logic: Assigning to a specific union variant (e.g., f.i = 10, s.y.b = 3.14)
        if isinstance(node.target, MemberAccess):
            try:
                # Get the base (the union itself)
                lvalue_result = self._codegen_lvalue(node.target.expr)
                if len(lvalue_result) == 3:
                    base_ptr, type_name, _ = lvalue_result
                else:
                    base_ptr, type_name = lvalue_result
                resolved = self._resolve_type_name(type_name)

                if resolved in self.union_symtab:
                    union_info = self.union_symtab[resolved]
                    member = node.target.member

                    if member in union_info["variants"]:
                        vdata = union_info["variants"][member]
                        val = self._codegen(node.value)
                        val = self._emit_cast(val, vdata["llvm_type"])

                        # Store tag
                        tag_ptr = self.builder.gep(
                            base_ptr,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), 0),
                            ],
                            inbounds=True,
                        )
                        self.builder.store(
                            ir.Constant(ir.IntType(64), vdata["index"]), tag_ptr
                        )

                        # Store data
                        data_ptr = self.builder.gep(
                            base_ptr,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), 1),
                            ],
                            inbounds=True,
                        )
                        typed_ptr = self.builder.bitcast(
                            data_ptr, vdata["llvm_type"].as_pointer()
                        )
                        self.builder.store(val, typed_ptr)
                        return
                    elif member == "cur":
                        raise LeashError(
                            "Cannot assign to '.cur'. Assign to a specific union variant or directly to the union.",
                            node=node,
                        )
            except LeashError:
                pass  # Fallback to standard assignment

        # 2. General Assignment (handles Identifiers, IndexAccess, and Struct Members)
        lvalue_result = self._codegen_lvalue(node.target)
        if len(lvalue_result) == 3:
            ptr, target_type_name, _ = lvalue_result
        else:
            ptr, target_type_name = lvalue_result
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
        from .ast_nodes import (
            Identifier,
            MemberAccess,
            IndexAccess,
            ThisExpr,
            UnaryOp,
            PointerMemberAccess,
            MethodCall,
        )

        if isinstance(node, Identifier):
            if node.name not in self.var_symtab:
                if node.name in self.class_symtab:
                    return None, node.name, None  # Static access
                # Special handling for File class (built-in)
                if node.name == "File":
                    return None, "File", None  # Static access to File class
                # Check if this is a generic class name that needs instantiation
                from .typechecker import TypeChecker

                # Find a concrete instantiation (prefer one without placeholder types)
                for inst_name in TypeChecker.instantiated_class_nodes:
                    if inst_name.startswith(node.name + "_") and "_T" not in inst_name:
                        return None, inst_name, None
                # Fallback to any instantiation
                for inst_name in TypeChecker.instantiated_class_nodes:
                    if inst_name.startswith(node.name + "_"):
                        return None, inst_name, None

                if self.in_works_block:
                    self.works_error_occured = True
                    self.works_error_info = f"Undefined variable: '{node.name}'"
                    err_ptr = self.builder.alloca(ir.IntType(8).as_pointer())
                    err_str = self._emit_const_str(f"Undefined variable: '{node.name}'")
                    self.builder.store(err_str, err_ptr)
                    return err_ptr, "string", None
                else:
                    raise LeashError(f"Undefined variable: '{node.name}'", node=node)
            var_info = self.var_symtab[node.name]
            ptr = var_info[0]
            type_name = var_info[1]
            extra_data = var_info[2] if len(var_info) > 2 else None
            
            resolved = self._resolve_type_name(type_name)
            while resolved.startswith("&"):
                ptr = self.builder.load(ptr)
                resolved = resolved[1:]
            
            return ptr, resolved, extra_data
        elif isinstance(node, MemberAccess):
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                base_ptr, type_name, _ = lvalue_result
            else:
                base_ptr, type_name = lvalue_result
            resolved = self._resolve_type_name(type_name)

            instance_ptr = base_ptr

            if resolved in self.struct_symtab:
                struct_info = self.struct_symtab[resolved]
                idx = struct_info["fields"].get(node.member)
                if idx is None:
                    raise LeashError(
                        f"Struct '{resolved}' has no member named '{node.member}'",
                        node=node
                    )
                field_type_name = struct_info["field_types"][node.member]
                return self.builder.gep(
                    instance_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), field_type_name, None
            elif resolved in self.class_symtab:
                if instance_ptr is None:
                    # Static field access (e.g., ClassName.field or this.field in static method)
                    cls_info = self.class_symtab[resolved]
                    if "static_fields" in cls_info and node.member in cls_info["static_fields"]:
                        static_info = cls_info["static_fields"][node.member]
                        return static_info["global"], static_info["type"], None
                    raise LeashError(f"Class '{resolved}' has no static field named '{node.member}'", node=node)

                # Classes are reference types (pointers). LOAD the pointer first.
                instance_ptr = self.builder.load(instance_ptr)
                cls_info = self.class_symtab[resolved]
                idx = cls_info["fields"].get(node.member)
                if idx is None:
                    raise LeashError(
                        f"Class '{resolved}' has no field named '{node.member}'",
                        node=node
                    )

                field_type_name = cls_info["field_types"][node.member]
                return self.builder.gep(
                    instance_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), field_type_name, None
            elif resolved in self.union_symtab:
                union_info = self.union_symtab[resolved]
                if node.member in union_info["variants"]:
                    vdata = union_info["variants"][node.member]
                    data_ptr = self.builder.gep(
                        instance_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), 1),
                        ],
                        inbounds=True,
                    )
                    typed_ptr = self.builder.bitcast(
                        data_ptr, vdata["llvm_type"].as_pointer()
                    )
                    return typed_ptr, vdata["type_name"], None
                elif node.member == "cur":
                    raise LeashError(
                        "Cannot use '.cur' as an l-value. Assign directly to the union or a specific variant member.",
                        node=node
                    )
                else:
                    raise LeashError(
                        f"Union '{resolved}' has no variant named '{node.member}'",
                        node=node
                    )
            else:
                raise LeashError(
                    f"Cannot access member '{node.member}': '{type_name}' is not a struct or union",
                    node=node
                )
        elif isinstance(node, PointerMemberAccess):
            ptr = self._codegen(node.expr)
            type_name = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(type_name)
            underlying = self._resolve_type_name(resolved[1:])

            if underlying in self.struct_symtab:
                struct_info = self.struct_symtab[underlying]
                idx = struct_info["fields"][node.member]
                return self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), struct_info["field_types"][node.member], None
            elif underlying in self.class_symtab:
                cls_info = self.class_symtab[underlying]
                idx = cls_info["fields"][node.member]
                return self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                ), cls_info["field_types"][node.member], None
            raise LeashError(f"Type '{underlying}' is not a struct or class", node=node)
        elif isinstance(node, UnaryOp) and node.op == "*":
            ptr = self._codegen(node.expr)
            type_name = self._get_leash_type_name(node.expr)
            resolved = self._resolve_type_name(type_name)
            return ptr, resolved[1:], None
        elif isinstance(node, IndexAccess):
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                slice_ptr, slice_type_name, extra_data = lvalue_result
            else:
                slice_ptr, slice_type_name = lvalue_result
                extra_data = None
            
            if slice_type_name == "string":
                str_ptr = self.builder.load(slice_ptr)
                if not self.in_unsafe_func:
                    self._emit_null_pointer_check(
                        str_ptr, "Runtime error: String index on null string.\n"
                    )
                idx_val = self._codegen(node.index)
                if not self.in_unsafe_func:
                    str_len = self.builder.call(self.strlen, [str_ptr])
                    idx32 = self._emit_cast(idx_val, ir.IntType(32))
                    idx64 = self.builder.sext(idx32, ir.IntType(64))
                    # Normalize negative index: idx = idx < 0 ? idx + len : idx
                    is_negative = self.builder.icmp_signed("<", idx64, ir.Constant(ir.IntType(64), 0))
                    wrapped = self.builder.add(idx64, str_len)
                    idx64 = self.builder.select(is_negative, wrapped, idx64)
                    idx_nonneg = self.builder.icmp_signed(">=", idx64, ir.Constant(ir.IntType(64), 0))
                    idx_in_bounds = self.builder.icmp_unsigned("<", idx64, str_len)
                    in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
                    self._emit_runtime_check(
                        in_bounds, "Runtime error: String index out of bounds.\n"
                    )
                    ptr = self.builder.gep(str_ptr, [idx64], inbounds=True)
                else:
                    ptr = self.builder.gep(str_ptr, [idx_val], inbounds=True)
                return (ptr, "char", None)
            
            resolved = self._resolve_type_name(slice_type_name)
            
            # Handle hash table index access
            if resolved.startswith("hash<") and resolved.endswith(">"):
                inner = resolved[5:-1]
                parts = inner.split(", ")
                if len(parts) == 2:
                    key_type, value_type = parts
                else:
                    key_type, value_type = "string", "void"
                
                key_val = self._codegen(node.index)
                
                hash_val = self.builder.load(slice_ptr)
                
                value_llvm = self._get_llvm_type(value_type)
                key_llvm = self._get_llvm_type(key_type)
                
                result_ptr = self.builder.alloca(value_llvm, name="hash_lookup_result")
                default_val = self._emit_default_value(value_type)
                self.builder.store(default_val, result_ptr)
                
                key_ptrs = []
                value_ptrs = []
                if extra_data:
                    key_ptrs, value_ptrs = extra_data
                elif hasattr(hash_val, 'hash_key_ptrs'):
                    key_ptrs = getattr(hash_val, 'hash_key_ptrs', [])
                    value_ptrs = getattr(hash_val, 'hash_value_ptrs', [])
                
                found_ptr = self.builder.alloca(ir.IntType(1), name="hash_found")
                self.builder.store(ir.Constant(ir.IntType(1), 0), found_ptr)
                
                for k_ptr, v_ptr in zip(key_ptrs, value_ptrs):
                    stored_key = self.builder.load(k_ptr)
                    if key_type == "string":
                        cmp_result = self.builder.call(self.strcmp, [key_val, stored_key])
                        key_match = self.builder.icmp_signed("==", cmp_result, ir.Constant(ir.IntType(32), 0))
                    else:
                        key_match = self.builder.icmp_signed("==", key_val, stored_key)
                    
                    with self.builder.if_then(key_match):
                        stored_value = self.builder.load(v_ptr)
                        self.builder.store(stored_value, result_ptr)
                        self.builder.store(ir.Constant(ir.IntType(1), 1), found_ptr)
                
                if not self.in_unsafe_func:
                    was_found = self.builder.load(found_ptr)
                    self._emit_runtime_check(
                        was_found, "Runtime error: Hash key not found in index access.\n"
                    )
                
                return (result_ptr, value_type, None)
            
            # Array/slice index access
            slice_val = self.builder.load(slice_ptr)
            slice_size = self.builder.extract_value(slice_val, 0)
            data_ptr = self.builder.extract_value(slice_val, 1)
            idx_val = self._codegen(node.index)
            idx32 = self._emit_cast(idx_val, ir.IntType(32))
            idx64 = self.builder.sext(idx32, ir.IntType(64))
            # Normalize negative index: idx = idx < 0 ? idx + size : idx
            is_negative = self.builder.icmp_signed("<", idx64, ir.Constant(ir.IntType(64), 0))
            wrapped = self.builder.add(idx64, slice_size)
            idx64 = self.builder.select(is_negative, wrapped, idx64)
            if not self.in_unsafe_func:
                idx_nonneg = self.builder.icmp_signed(">=", idx64, ir.Constant(ir.IntType(64), 0))
                idx_in_bounds = self.builder.icmp_unsigned("<", idx64, slice_size)
                in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
                self._emit_runtime_check(
                    in_bounds, "Runtime error: Array index out of bounds.\n"
                )
            ptr = self.builder.gep(data_ptr, [idx64], inbounds=True)
            elem_type_name = (
                slice_type_name.split("[")[0] if "[" in slice_type_name else "int"
            )
            return (ptr, elem_type_name, None)
        elif isinstance(node, ThisExpr):
            if "this" not in self.var_symtab:
                if self.current_class_name:
                    # In a static method, 'this' refers to the class itself (no instance pointer)
                    return None, self.current_class_name, None
                raise LeashError("'this' is not available in the current context", node=node)
            var_info = self.var_symtab["this"]
            ptr = var_info[0]
            type_name = var_info[1]
            extra_data = var_info[2] if len(var_info) > 2 else None
            # For struct types (in struct functions), the this pointer is stored in an alloca.
            # Load the actual struct pointer from the alloca so GEP works correctly.
            resolved = self._resolve_type_name(type_name)
            if resolved in self.struct_symtab:
                loaded = self.builder.load(ptr)
                return loaded, type_name, extra_data
            return ptr, type_name, extra_data
        elif isinstance(node, MethodCall):
            # Handle method call as l-value (e.g., for h.get("Joe").age)
            # Call the method and store result in a temporary, then return pointer to it
            val = self._codegen_MethodCall(node)
            type_name = self._get_leash_type_name(node)
            resolved = self._resolve_type_name(type_name)
            # Allocate temporary to hold the method result
            ptr = self.builder.alloca(val.type)
            self.builder.store(val, ptr)
            return ptr, resolved, None
        else:
            line = getattr(node, "line", None)
            col = getattr(node, "col", None)
            raise LeashError(
                f"Invalid l-value: {type(node).__name__}", line=line, col=col
            )

    def _codegen_PointerMemberAccess(self, node):
        ptr = self._codegen(node.expr)
        type_name = self._get_leash_type_name(node.expr)
        resolved = self._resolve_type_name(type_name)

        if resolved.startswith("*") or resolved.startswith("&"):
            underlying = self._resolve_type_name(resolved[1:])
        else:
            raise LeashError(
                "Cannot use '->' on non-pointer type",
                line=getattr(node, "line", None),
                col=getattr(node, "col", None),
            )

        if underlying in self.struct_symtab:
            struct_info = self.struct_symtab[underlying]
            idx = struct_info["fields"].get(node.member)
            if idx is None:
                raise LeashError(
                    f"Struct '{underlying}' has no member named '{node.member}'",
                    node=node
                )
            res_ptr = self.builder.gep(
                ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
            )
            return self.builder.load(res_ptr)
        elif underlying in self.class_symtab:
            cls_info = self.class_symtab[underlying]
            idx = cls_info["fields"].get(node.member)
            if idx is None:
                raise LeashError(
                    f"Class '{underlying}' has no field named '{node.member}'",
                    node=node
                )
            res_ptr = self.builder.gep(
                ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
            )
            return self.builder.load(res_ptr)

        raise LeashError(
            f"Type '{underlying}' is not a struct or class",
            line=getattr(node, "line", None),
            col=getattr(node, "col", None),
        )

    def _codegen_ExpressionStatement(self, node):
        self._codegen(node.expr)

    def _codegen_SpawnStatement(self, node):
        from .ast_nodes import Call
        call = node.call
        if isinstance(call, Call):
            func_name = call.name
            if func_name in self.func_symtab:
                func = self.func_symtab[func_name]
                void_ptr = ir.IntType(8).as_pointer()
                if call.args:
                    # Evaluate all arguments
                    arg_vals = [self._codegen(a) for a in call.args]
                    # Build a struct type matching the argument types
                    arg_types = [v.type for v in arg_vals]
                    arg_struct_ty = ir.LiteralStructType(arg_types)
                    # Allocate heap memory for the struct
                    struct_size = self._get_type_size(arg_struct_ty)
                    size_val = ir.Constant(ir.IntType(64), struct_size)
                    arg_mem = self.builder.call(self.malloc, [size_val])
                    arg_ptr = self.builder.bitcast(
                        arg_mem, arg_struct_ty.as_pointer()
                    )
                    # Store each argument into the struct
                    for i, v in enumerate(arg_vals):
                        gep = self.builder.gep(
                            arg_ptr,
                            [
                                ir.Constant(ir.IntType(32), 0),
                                ir.Constant(ir.IntType(32), i),
                            ],
                        )
                        self.builder.store(v, gep)
                    self.builder.call(
                        self.leash_spawn_worker_fn, [func, arg_mem]
                    )
                else:
                    null_arg = ir.Constant(void_ptr, None)
                    self.builder.call(
                        self.leash_spawn_worker_fn, [func, null_arg]
                    )
            else:
                raise LeashError(
                    f"Cannot spawn unknown function '{func_name}'",
                    line=node.line, col=node.col
                )
        return None

    def _codegen_ThisWorkerExpr(self, node):
        is_interrupted = self.builder.call(self.leash_is_interrupted_fn, [])
        return self.builder.icmp_signed("!=", is_interrupted, ir.Constant(ir.IntType(32), 0))

    def _codegen_ShowStatement(self, node):
        from .ast_nodes import Identifier, MemberAccess

        is_buffer = getattr(node, "is_buffer", False)

        # showb: print vector elements in buffer format
        if is_buffer:
            self._show_buffer(node.args)
            return

        # Check if any arg is a union variable or union .cur — those need special per-variant printing
        # First, collect which args are "union-cur" args
        union_arg_indices = set()
        for i, arg_node in enumerate(node.args):
            union_name = self._get_union_type_for_node(arg_node)
            if union_name:
                union_arg_indices.add(i)

        end = getattr(node, "end", "\n")

        if not union_arg_indices:
            # No union args: standard show path
            self._show_standard(node.args, end=end)
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
            self._show_standard(prefix_args, end="")

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
                ptr = self.var_symtab[arg_node.name][0]
            elif (
                isinstance(arg_node, MemberAccess)
                and arg_node.member == "cur"
                and isinstance(arg_node.expr, Identifier)
            ):
                ptr = self.var_symtab[arg_node.expr.name][0]
            else:
                # fallback
                self._show_standard([arg_node], end="")
                continue

            tag_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                inbounds=True,
            )
            tag_val = self.builder.load(tag_ptr)
            data_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                inbounds=True,
            )

            self._union_show_branched(tag_val, data_ptr, union_info)

        # suffix non-union args
        suffix_args = []
        if union_arg_indices:
            last_union = max(union_arg_indices)
            for i in range(last_union + 1, len(node.args)):
                suffix_args.append(node.args[i])

        if suffix_args:
            self._show_standard(suffix_args, end=end)
        elif end:
            self._print_raw(end)

    def _get_union_type_for_node(self, arg_node):
        """Check if an AST node refers to a union variable or union.cur."""
        from .ast_nodes import Identifier, MemberAccess

        if isinstance(arg_node, Identifier):
            if arg_node.name in self.var_symtab:
                var_info = self.var_symtab[arg_node.name]
                type_name = var_info[1]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        elif isinstance(arg_node, MemberAccess) and arg_node.member == "cur":
            if (
                isinstance(arg_node.expr, Identifier)
                and arg_node.expr.name in self.var_symtab
            ):
                var_info = self.var_symtab[arg_node.expr.name]
                type_name = var_info[1]
                resolved = self._resolve_type_name(type_name)
                if resolved in self.union_symtab:
                    return resolved
        return None

    def _print_raw(self, text):
        """Print a raw string via printf."""
        s = bytearray(text.encode("utf8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        g = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("raw")
        )
        g.linkage = "internal"
        g.global_constant = True
        g.initializer = c_str
        ptr = self.builder.bitcast(g, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [ptr])

    def _union_show_branched(self, tag_val, data_ptr, union_info):
        """Print the current union value by branching on the tag and calling printf per variant."""
        variants = list(union_info["variants"].items())
        merge_bb = self.builder.function.append_basic_block("union_show_merge")

        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f"union_show_{vname}")
            var_bbs.append(bb)

        # Build if-else chain
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed(
                "==", tag_val, ir.Constant(ir.IntType(64), vdata["index"])
            )
            next_check = self.builder.function.append_basic_block(
                f"union_show_check_{i + 1}"
            )
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)
        self.builder.branch(var_bbs[-1])

        # In each variant BB, printf the loaded value with appropriate format
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
            loaded = self.builder.load(typed_ptr)

            # Determine format
            fmt, val = self._format_value(loaded)
            fmt_bytes = bytearray(fmt.encode("utf8") + b"\0")
            c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
            g_fmt = ir.GlobalVariable(
                self.module, c_fmt.type, name=self.module.get_unique_name("ufmt")
            )
            g_fmt.linkage = "internal"
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
            if width == 8:
                return ("%c", val)
            if width <= 32:
                casted = self.builder.zext(val, ir.IntType(32)) if width < 32 else val
                return ("%d", casted)
            elif width == 64:
                return ("%lld", val)
            else:
                casted = self.builder.trunc(val, ir.IntType(64))
                return ("%lld", casted)
        elif isinstance(val.type, (ir.HalfType, ir.FloatType)):
            val = self.builder.fpext(val, ir.DoubleType())
            return ("%f", val)
        elif isinstance(val.type, ir.DoubleType):
            return ("%f", val)
        elif isinstance(val.type, ir.PointerType):
            return ("%s", val)
        return ("%s", val)

    def _show_standard(self, arg_nodes, end="\n"):
        """Standard show() implementation for non-union args."""
        format_str = ""
        args = []
        for arg_node in arg_nodes:
            val = self._codegen(arg_node)

            is_char_slice = False
            if (
                isinstance(val.type, ir.LiteralStructType)
                and len(val.type.elements) == 2
            ):
                elt = val.type.elements[1]
                if (
                    hasattr(ir, "PointerType")
                    and getattr(ir, "PointerType") is not None
                    and isinstance(elt, ir.PointerType)
                    and getattr(elt, "pointee", None) == ir.IntType(8)
                ) or (
                    getattr(elt, "is_pointer", False)
                    and getattr(elt, "pointee", None) == ir.IntType(8)
                ):
                    is_char_slice = True

            if is_char_slice:
                format_str += "%.*s"
                length = self.builder.extract_value(val, 0)
                args.append(self.builder.trunc(length, ir.IntType(32)))
                args.append(self.builder.extract_value(val, 1))
                continue

            if isinstance(val.type, ir.IntType):
                width = val.type.width
                arg_type = self._get_leash_type_name(arg_node)
                is_unsigned = (arg_type.startswith("uint") or arg_type == "uint")
                if width == 8:
                    format_str += "%c"
                elif width <= 32:
                    if width < 32:
                        # Use zext for unsigned and bool types, sext for signed
                        if is_unsigned or arg_type == "bool":
                            val = self.builder.zext(val, ir.IntType(32))
                        else:
                            val = self.builder.sext(val, ir.IntType(32))
                    format_str += "%d"
                elif width == 64:
                    format_str += "%lld"
                else:
                    val = self.builder.trunc(val, ir.IntType(64))
                    format_str += "%lld"
            elif isinstance(val.type, (ir.HalfType, ir.FloatType)):
                val = self.builder.fpext(val, ir.DoubleType())
                format_str += "%f"
            elif isinstance(val.type, ir.DoubleType):
                format_str += "%f"
            elif isinstance(val.type, ir.PointerType):
                if val.type.pointee == ir.IntType(8):
                    format_str += "%s"
                else:
                    format_str += "%p"
            else:
                format_str += "%s"  # fallback
            args.append(val)

        if end:
            format_str += end

        # Create global string for format
        fmt_bytes = bytearray(format_str.encode("utf8") + b"\0")
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(
            self.module, c_fmt.type, name=self.module.get_unique_name("fmt")
        )
        global_fmt.linkage = "internal"
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt

        fmt_ptr = self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [fmt_ptr] + args)

    def _show_buffer(self, arg_nodes):
        """showb: print elements of vectors and nested vectors in buffer format."""
        for arg_node in arg_nodes:
            val = self._codegen(arg_node)
            type_name = self._get_leash_type_name(arg_node)
            resolved = self._resolve_type_name(type_name)

            if resolved.startswith("vec<") and resolved.endswith(">"):
                inner_type = resolved[4:-1]
                self._print_vec_buffer(val, inner_type)
            elif resolved == "string":
                self.builder.call(self.showb_append_str_fn, [val])
            else:
                self._print_value_buffered(val)

    def _print_vec_buffer(self, vec_val, inner_type):
        """Print a vector in buffer format (elements without separators)."""
        data_ptr = self.builder.extract_value(vec_val, 0)
        size_val = self.builder.extract_value(vec_val, 1)

        size_i32 = self.builder.trunc(size_val, ir.IntType(32))

        entry_bb = self.builder.block

        # Pre-header: initialize idx = 0
        idx_ptr = self.builder.alloca(ir.IntType(32), name="buf_idx")
        self.builder.store(ir.Constant(ir.IntType(32), 0), idx_ptr)

        # Loop header block
        loop_cond_bb = self.builder.function.append_basic_block("vec_buffer_cond")
        self.builder.branch(loop_cond_bb)
        self.builder.position_at_end(loop_cond_bb)

        idx_val = self.builder.load(idx_ptr)
        done = self.builder.icmp_unsigned("==", idx_val, size_i32)

        loop_body_bb = self.builder.function.append_basic_block("vec_buffer_body")
        merge_bb = self.builder.function.append_basic_block("vec_buffer_merge")

        self.builder.cbranch(done, merge_bb, loop_body_bb)

        # Loop body
        self.builder.position_at_end(loop_body_bb)

        elem_ptr = self.builder.gep(data_ptr, [idx_val], inbounds=True)
        elem_val = self.builder.load(elem_ptr)
        self._print_buffer_element(elem_val, inner_type)

        if not self.builder.block.is_terminated:
            next_idx = self.builder.add(idx_val, ir.Constant(ir.IntType(32), 1))
            self.builder.store(next_idx, idx_ptr)
            self.builder.branch(loop_cond_bb)

        # Merge block
        self.builder.position_at_end(merge_bb)

    def _print_buffer_element(self, elem_val, inner_type):
        """Print a single element in buffer format."""
        resolved = self._resolve_type_name(inner_type)

        if resolved.startswith("vec<") and resolved.endswith(">"):
            inner_inner = resolved[4:-1]
            self._print_vec_buffer(elem_val, inner_inner)
        elif resolved == "string":
            self.builder.call(self.showb_append_str_fn, [elem_val])
        else:
            self._print_value_buffered(elem_val)

    def _print_value_buffered(self, val):
        """Print a value to the internal showb buffer."""
        # For buffered output, we first convert everything to string then append
        # This is simpler than implementing full buffered printf logic
        str_val = self._emit_tostring(val, val.type)
        self.builder.call(self.showb_append_str_fn, [str_val])

    def _print_value(self, val):
        """Print a value using standard show formatting."""
        if isinstance(val.type, ir.IntType):
            width = val.type.width
            if width == 8:
                fmt = "%c"
            elif width <= 32:
                if width < 32:
                    val = self.builder.zext(val, ir.IntType(32))
                fmt = "%d"
            elif width == 64:
                fmt = "%lld"
            else:
                val = self.builder.trunc(val, ir.IntType(64))
                fmt = "%lld"
            self._print_formatted(fmt, [val])
        elif isinstance(val.type, (ir.HalfType, ir.FloatType)):
            val = self.builder.fpext(val, ir.DoubleType())
            self._print_formatted("%f", [val])
        elif isinstance(val.type, ir.DoubleType):
            self._print_formatted("%f", [val])
        elif isinstance(val.type, ir.PointerType):
            if val.type.pointee == ir.IntType(8):
                self._print_formatted("%s", [val])
            else:
                self._print_formatted("%p", [val])
        else:
            self._print_formatted("%s", [val])

    def _print_formatted(self, fmt_str, args):
        """Print using printf with format string."""
        fmt_bytes = bytearray(fmt_str.encode("utf8") + b"\0")
        c_fmt = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_bytes)), fmt_bytes)
        global_fmt = ir.GlobalVariable(
            self.module, c_fmt.type, name=self.module.get_unique_name("fmt")
        )
        global_fmt.linkage = "internal"
        global_fmt.global_constant = True
        global_fmt.initializer = c_fmt
        fmt_ptr = self.builder.bitcast(global_fmt, ir.IntType(8).as_pointer())
        self.builder.call(self.printf, [fmt_ptr] + args)

    def _create_showb_helpers(self):
        """Create internal functions for showb buffer management."""
        # _leash_showb_ensure_capacity(size_t needed)
        ensure_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(64)])
        ensure_fn = ir.Function(
            self.module, ensure_ty, name="_leash_showb_ensure_capacity"
        )
        block = ensure_fn.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        needed = ensure_fn.args[0]
        curr_cap = builder.load(self.showb_cap_gv)
        curr_size = builder.load(self.showb_size_gv)

        new_size = builder.add(curr_size, needed)
        is_enough = builder.icmp_unsigned(">=", curr_cap, new_size)

        with builder.if_then(builder.not_(is_enough)):
            # new_cap = max(curr_cap * 2, new_size, 1024)
            double_cap = builder.mul(curr_cap, ir.Constant(ir.IntType(64), 2))

            # Simple max logic
            cond1 = builder.icmp_unsigned(">", double_cap, new_size)
            max1 = builder.select(cond1, double_cap, new_size)

            cond2 = builder.icmp_unsigned(">", max1, ir.Constant(ir.IntType(64), 1024))
            new_cap = builder.select(cond2, max1, ir.Constant(ir.IntType(64), 1024))

            curr_buf = builder.load(self.showb_buffer_gv)
            is_null = builder.icmp_unsigned(
                "==",
                builder.ptrtoint(curr_buf, ir.IntType(64)),
                ir.Constant(ir.IntType(64), 0),
            )

            new_buf = builder.select(
                is_null,
                builder.call(self.malloc, [new_cap]),
                builder.call(self.realloc, [curr_buf, new_cap]),
            )

            builder.store(new_buf, self.showb_buffer_gv)
            builder.store(new_cap, self.showb_cap_gv)

        builder.ret_void()
        self.showb_ensure_fn = ensure_fn

        # _leash_showb_append_char(i8 char)
        append_char_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8)])
        append_char_fn = ir.Function(
            self.module, append_char_ty, name="_leash_showb_append_char"
        )
        block = append_char_fn.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        char_val = append_char_fn.args[0]
        builder.call(ensure_fn, [ir.Constant(ir.IntType(64), 1)])

        buf = builder.load(self.showb_buffer_gv)
        size = builder.load(self.showb_size_gv)

        pos_ptr = builder.gep(buf, [size])
        builder.store(char_val, pos_ptr)

        new_size = builder.add(size, ir.Constant(ir.IntType(64), 1))
        builder.store(new_size, self.showb_size_gv)
        builder.ret_void()
        self.showb_append_char_fn = append_char_fn

        # _leash_showb_append_str(i8* str)
        append_str_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        append_str_fn = ir.Function(
            self.module, append_str_ty, name="_leash_showb_append_str"
        )
        block = append_str_fn.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        str_val = append_str_fn.args[0]
        str_len = builder.call(self.strlen, [str_val])
        builder.call(ensure_fn, [str_len])

        buf = builder.load(self.showb_buffer_gv)
        size = builder.load(self.showb_size_gv)

        dest_ptr = builder.gep(buf, [size])
        # Use memmove or strcpy? strcpy is for null-terminated.
        # Since Leash strings are null-terminated, strcpy is fine.
        builder.call(self.strcpy, [dest_ptr, str_val])

        new_size = builder.add(size, str_len)
        builder.store(new_size, self.showb_size_gv)
        builder.ret_void()
        self.showb_append_str_fn = append_str_fn

        # _leash_showb_flush()
        flush_ty = ir.FunctionType(ir.VoidType(), [])
        flush_fn = ir.Function(self.module, flush_ty, name="_leash_showb_flush")
        block = flush_fn.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        size = builder.load(self.showb_size_gv)
        is_empty = builder.icmp_unsigned("==", size, ir.Constant(ir.IntType(64), 0))

        with builder.if_then(builder.not_(is_empty)):
            buf = builder.load(self.showb_buffer_gv)
            # Call portable helper to get stdout
            stdout = builder.call(self.get_stdout_fn, [])
            # fwrite(buf, 1, size, stdout)
            builder.call(
                self.fwrite, [buf, ir.Constant(ir.IntType(64), 1), size, stdout]
            )
            builder.store(ir.Constant(ir.IntType(64), 0), self.showb_size_gv)

        builder.ret_void()
        self.showb_flush_fn = flush_fn

    def _cast_bool(self, cond_val):
        if not isinstance(cond_val.type, ir.IntType) or cond_val.type.width != 1:
            # Handle pointer types (compare with null)
            if isinstance(cond_val.type, ir.PointerType):
                null_ptr = ir.Constant(cond_val.type, None)
                return self.builder.icmp_unsigned("!=", cond_val, null_ptr)
            zero = ir.Constant(cond_val.type, 0)
            if isinstance(cond_val.type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                return self.builder.fcmp_ordered("!=", cond_val, zero)
            else:
                return self.builder.icmp_signed("!=", cond_val, zero)
        return cond_val

    def _codegen_IfStatement(self, node):
        cond_val = self._cast_bool(self._codegen(node.condition))
        if node.invert:
            cond_val = self.builder.not_(cond_val)
        then_bb = self.builder.function.append_basic_block("then")

        also_bbs = []
        for _ in node.also_blocks:
            also_cond_bb = self.builder.function.append_basic_block("also_cond")
            also_body_bb = self.builder.function.append_basic_block("also_body")
            also_bbs.append((also_cond_bb, also_body_bb))

        else_bb = (
            self.builder.function.append_basic_block("else")
            if node.else_block
            else None
        )
        merge_bb = self.builder.function.append_basic_block("if_merge")

        next_bb = also_bbs[0][0] if also_bbs else (else_bb if else_bb else merge_bb)
        self.builder.cbranch(cond_val, then_bb, next_bb)

        self.builder.position_at_end(then_bb)
        for stmt in node.then_block:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        for i, (also_cond, also_body, also_invert) in enumerate(node.also_blocks):
            cond_bb, body_bb = also_bbs[i]
            self.builder.position_at_end(cond_bb)
            a_cond_val = self._cast_bool(self._codegen(also_cond))
            if also_invert:
                a_cond_val = self.builder.not_(a_cond_val)
            next_also = (
                also_bbs[i + 1][0]
                if i + 1 < len(also_bbs)
                else (else_bb if else_bb else merge_bb)
            )
            self.builder.cbranch(a_cond_val, body_bb, next_also)

            self.builder.position_at_end(body_bb)
            for stmt in also_body:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        if else_bb:
            self.builder.position_at_end(else_bb)
            for stmt in node.else_block:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _codegen_SwitchStatement(self, node):
        switch_val = self._codegen(node.expression)
        switch_type = switch_val.type

        case_bbs = []
        for i, (case_expr, case_body) in enumerate(node.cases):
            case_bbs.append(
                self.builder.function.append_basic_block(f"switch_case_{i}")
            )

        default_bb = None
        if node.default_block is not None:
            default_bb = self.builder.function.append_basic_block("switch_default")

        merge_bb = self.builder.function.append_basic_block("switch_merge")
        is_string_switch = isinstance(
            switch_type, ir.PointerType
        ) and switch_type.pointee == ir.IntType(8)

        if node.cases:
            # Build check blocks for the comparison chain
            check_bbs = []
            for i in range(len(node.cases) - 1):
                check_bbs.append(
                    self.builder.function.append_basic_block(f"switch_check_{i}")
                )

            # Emit comparison chain from the entry block
            for i, (case_expr, case_body) in enumerate(node.cases):
                case_val = self._codegen(case_expr)
                case_val = self._emit_cast(case_val, switch_type)

                if is_string_switch:
                    cmp_res = self.builder.call(self.strcmp, [switch_val, case_val])
                    cmp = self.builder.icmp_signed(
                        "==", cmp_res, ir.Constant(ir.IntType(32), 0)
                    )
                else:
                    cmp = self.builder.icmp_signed("==", switch_val, case_val)

                if i + 1 < len(node.cases):
                    next_check = check_bbs[i]
                else:
                    next_check = default_bb if default_bb else merge_bb
                self.builder.cbranch(cmp, case_bbs[i], next_check)
                # Move to next check block to continue the chain
                if i + 1 < len(node.cases):
                    self.builder.position_at_end(check_bbs[i])

            # Emit case bodies
            for i, (case_expr, case_body) in enumerate(node.cases):
                self.builder.position_at_end(case_bbs[i])
                for stmt in case_body:
                    self._codegen(stmt)
                    if self.builder.block.is_terminated:
                        break
                if not self.builder.block.is_terminated:
                    self.builder.branch(merge_bb)

            # Emit default body
            if default_bb:
                self.builder.position_at_end(default_bb)
                for stmt in node.default_block:
                    self._codegen(stmt)
                    if self.builder.block.is_terminated:
                        break
                if not self.builder.block.is_terminated:
                    self.builder.branch(merge_bb)
        elif default_bb:
            self.builder.branch(default_bb)
            self.builder.position_at_end(default_bb)
            for stmt in node.default_block:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)
        else:
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _codegen_WhileStatement(self, node):
        cond_bb = self.builder.function.append_basic_block("while_cond")
        body_bb = self.builder.function.append_basic_block("while_body")
        merge_bb = self.builder.function.append_basic_block("while_merge")
        continue_bb = cond_bb  # continue jumps to condition check
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_LoopStatement(self, node):
        body_bb = self.builder.function.append_basic_block("loop_body")
        merge_bb = self.builder.function.append_basic_block("loop_merge")
        continue_bb = body_bb  # continue jumps to body (infinite loop)
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(body_bb)
        self.builder.position_at_end(body_bb)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(body_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForStatement(self, node):
        self._codegen(node.init)
        cond_bb = self.builder.function.append_basic_block("for_cond")
        body_bb = self.builder.function.append_basic_block("for_body")
        step_bb = self.builder.function.append_basic_block("for_step")
        merge_bb = self.builder.function.append_basic_block("for_merge")
        continue_bb = step_bb  # continue jumps to step
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(step_bb)

        self.builder.position_at_end(step_bb)
        self._codegen(node.step)
        self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_DoWhileStatement(self, node):
        body_bb = self.builder.function.append_basic_block("do_body")
        cond_bb = self.builder.function.append_basic_block("do_cond")
        merge_bb = self.builder.function.append_basic_block("do_merge")
        continue_bb = cond_bb  # continue jumps to condition check
        break_bb = merge_bb  # break jumps to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(body_bb)
        self.builder.position_at_end(body_bb)
        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(cond_bb)
        cond_val = self._cast_bool(self._codegen(node.condition))
        self.builder.cbranch(cond_val, body_bb, merge_bb)
        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachStructStatement(self, node):
        lvalue_result = self._codegen_lvalue(node.struct_expr)
        if len(lvalue_result) == 3:
            struct_ptr, struct_type_name, _ = lvalue_result
        else:
            struct_ptr, struct_type_name = lvalue_result
        struct_ir_type = struct_ptr.type.pointee

        struct_meta = None
        for sname, sdata in self.struct_symtab.items():
            if sdata["type"] == struct_ir_type:
                struct_meta = sdata
                break

        if not struct_meta:
            raise LeashError(
                "Cannot use 'foreach' with 'in<struct>' on a non-struct type",
                node=node
            )

        from .ast_nodes import StringLiteral

        for i, (field_name, field_idx) in enumerate(struct_meta["fields"].items()):
            name_val = self._codegen(StringLiteral(field_name))
            name_ptr = self.builder.alloca(name_val.type, name=node.name_var)
            self.builder.store(name_val, name_ptr)
            self.var_symtab[node.name_var] = (name_ptr, "string")

            field_ptr = self.builder.gep(
                struct_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
            )
            field_val = self.builder.load(field_ptr)
            val_ptr = self.builder.alloca(field_val.type, name=node.value_var)
            self.builder.store(field_val, val_ptr)
            self.var_symtab[node.value_var] = (
                val_ptr,
                struct_meta["field_types"][field_name],
            )

            for stmt in node.body:
                self._codegen(stmt)
                if self.builder.block.is_terminated:
                    break

    def _codegen_ForeachArrayStatement(self, node):
        elem_type_name = "int"
        try:
            lvalue_result = self._codegen_lvalue(node.array_expr)
            if len(lvalue_result) == 3:
                _, full_type_name, _ = lvalue_result
            else:
                _, full_type_name = lvalue_result
            elem_type_name = (
                full_type_name.split("[")[0] if "[" in full_type_name else "int"
            )
        except:
            pass

        slice_val = self._codegen(node.array_expr)

        length_val = self.builder.extract_value(slice_val, 0)
        data_ptr = self.builder.extract_value(slice_val, 1)

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int<64>", None)

        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)
        self.var_symtab[node.value_var] = (val_ptr, elem_type_name, None)

        cond_bb = self.builder.function.append_basic_block("foreach_cond")
        body_bb = self.builder.function.append_basic_block("foreach_body")
        inc_bb = self.builder.function.append_basic_block("foreach_inc")
        merge_bb = self.builder.function.append_basic_block("foreach_merge")
        continue_bb = inc_bb  # continue goes back to increment
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, length_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)

        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self.builder.branch(inc_bb)

        self.builder.position_at_end(inc_bb)
        next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
        self.builder.store(next_idx, idx_ptr)
        self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachStringStatement(self, node):
        str_val = self._codegen(node.string_expr)
        length_val = self.builder.call(self.strlen, [str_val])

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int<64>")

        val_ptr = self.builder.alloca(ir.IntType(8), name=node.char_var)
        self.var_symtab[node.char_var] = (val_ptr, "char")

        cond_bb = self.builder.function.append_basic_block("foreach_str_cond")
        body_bb = self.builder.function.append_basic_block("foreach_str_body")
        inc_bb = self.builder.function.append_basic_block("foreach_str_inc")
        merge_bb = self.builder.function.append_basic_block("foreach_str_merge")
        continue_bb = inc_bb  # continue goes back to increment
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, length_val)
        self.builder.cbranch(cmp_res, body_bb, merge_bb)

        self.builder.position_at_end(body_bb)

        curr_char_ptr = self.builder.gep(str_val, [curr_idx], inbounds=True)
        curr_char_val = self.builder.load(curr_char_ptr)
        self.builder.store(curr_char_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self.builder.branch(inc_bb)

        self.builder.position_at_end(inc_bb)
        next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
        self.builder.store(next_idx, idx_ptr)
        self.builder.branch(cond_bb)

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachVectorStatement(self, node):
        vec_val = self._codegen(node.vector_expr)

        data_ptr = self.builder.extract_value(vec_val, 0)
        size_val = self.builder.extract_value(vec_val, 1)

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int<64>")

        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)

        # Get element type name for symtab
        try:
            lvalue_result = self._codegen_lvalue(node.vector_expr)
            if len(lvalue_result) == 3:
                _, full_type_name, _ = lvalue_result
            else:
                _, full_type_name = lvalue_result
            elem_type_name = (
                full_type_name[4:-1] if full_type_name.startswith("vec<") else "int"
            )
        except:
            elem_type_name = "int"

        self.var_symtab[node.value_var] = (val_ptr, elem_type_name)

        cond_bb = self.builder.function.append_basic_block("foreach_vec_cond")
        body_bb = self.builder.function.append_basic_block("foreach_vec_body")
        inc_bb = self.builder.function.append_basic_block("foreach_vec_inc")
        merge_bb = self.builder.function.append_basic_block("foreach_vec_merge")
        continue_bb = inc_bb  # continue goes back to increment
        break_bb = merge_bb  # break exits to merge

        # Push loop context for nested stop/continue
        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, size_val)
        # Optimization: Add branch weight metadata (likely: body taken, exit unlikely)
        cb = self.builder.cbranch(cmp_res, body_bb, merge_bb)
        try:
            cb.set_weights(99, 1)  # hot path: body, cold path: exit
        except Exception:
            pass

        self.builder.position_at_end(body_bb)

        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self.builder.branch(inc_bb)

        self.builder.position_at_end(inc_bb)
        next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
        next_idx.flags = ['nuw', 'nsw']  # Optimization: nsw+nuw on index increment
        self.builder.store(next_idx, idx_ptr)
        back_branch = self.builder.branch(cond_bb)
        try:
            back_branch.set_weights(99, 1)  # likely: continue loop
        except Exception:
            pass

        # Pop loop context
        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_ForeachMatrixStatement(self, node):
        mat_val = self._codegen(node.matrix_expr)

        data_ptr = self.builder.extract_value(mat_val, 0)
        size_val = self.builder.extract_value(mat_val, 1)

        idx_ptr = self.builder.alloca(ir.IntType(64), name=node.index_var)
        self.builder.store(ir.Constant(ir.IntType(64), 0), idx_ptr)
        self.var_symtab[node.index_var] = (idx_ptr, "int<64>")

        elem_type = data_ptr.type.pointee
        val_ptr = self.builder.alloca(elem_type, name=node.value_var)

        try:
            lvalue_result = self._codegen_lvalue(node.matrix_expr)
            if len(lvalue_result) == 3:
                _, full_type_name, _ = lvalue_result
            else:
                _, full_type_name = lvalue_result
            elem_type_name = (
                full_type_name[7:-1] if full_type_name.startswith("matrix<") else "int"
            )
        except:
            elem_type_name = "int"

        self.var_symtab[node.value_var] = (val_ptr, elem_type_name)

        cond_bb = self.builder.function.append_basic_block("foreach_mat_cond")
        body_bb = self.builder.function.append_basic_block("foreach_mat_body")
        inc_bb = self.builder.function.append_basic_block("foreach_mat_inc")
        merge_bb = self.builder.function.append_basic_block("foreach_mat_merge")
        continue_bb = inc_bb
        break_bb = merge_bb

        self.loop_stack.append((break_bb, continue_bb))

        self.builder.branch(cond_bb)
        self.builder.position_at_end(cond_bb)

        curr_idx = self.builder.load(idx_ptr)
        cmp_res = self.builder.icmp_signed("<", curr_idx, size_val)
        cb = self.builder.cbranch(cmp_res, body_bb, merge_bb)
        try:
            cb.set_weights(99, 1)
        except Exception:
            pass

        self.builder.position_at_end(body_bb)

        curr_elem_ptr = self.builder.gep(data_ptr, [curr_idx], inbounds=True)
        curr_elem_val = self.builder.load(curr_elem_ptr)
        self.builder.store(curr_elem_val, val_ptr)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            self.builder.branch(inc_bb)

        self.builder.position_at_end(inc_bb)
        next_idx = self.builder.add(curr_idx, ir.Constant(ir.IntType(64), 1))
        next_idx.flags = ['nuw', 'nsw']
        self.builder.store(next_idx, idx_ptr)
        back = self.builder.branch(cond_bb)
        try:
            back.set_weights(99, 1)
        except Exception:
            pass

        self.loop_stack.pop()

        self.builder.position_at_end(merge_bb)

    def _codegen_TernaryOp(self, node):
        cond_val = self._cast_bool(self._codegen(node.condition))
        true_bb = self.builder.function.append_basic_block("ternary_true")
        false_bb = self.builder.function.append_basic_block("ternary_false")
        merge_bb = self.builder.function.append_basic_block("ternary_merge")

        self.builder.cbranch(cond_val, true_bb, false_bb)

        self.builder.position_at_end(true_bb)
        true_val = self._codegen(node.true_expr)
        true_bb_terminated = self.builder.block.is_terminated
        if not true_bb_terminated:
            self.builder.branch(merge_bb)

        self.builder.position_at_end(false_bb)
        false_val = self._codegen(node.false_expr)
        false_bb_terminated = self.builder.block.is_terminated
        if not false_bb_terminated:
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

        result_type = true_val.type
        if false_val.type != result_type:
            false_val = self._emit_cast(false_val, result_type)

        phi = self.builder.phi(result_type)
        if not true_bb_terminated:
            phi.add_incoming(true_val, true_bb)
        if not false_bb_terminated:
            phi.add_incoming(false_val, false_bb)
        return phi

    def _codegen_BinaryOp(self, node):
        left = self._codegen(node.left)

        # OpDef operator overload
        opdef_func_name = getattr(node, "opdef_func_name", None)
        if opdef_func_name:
            right = self._codegen(node.right)
            func = self.func_symtab.get(opdef_func_name)
            if not func:
                raise LeashError(f"Call to undefined opdef function: '{opdef_func_name}'", node=node)
            args = []
            for i, arg_val in enumerate([left, right]):
                if i < len(func.args):
                    target_llvm = func.args[i].type
                    arg_val = self._emit_cast(arg_val, target_llvm)
                args.append(arg_val)
            return self.builder.call(func, args)

        # Compute right operand (eager for matrix/arithmetic, lazy handled separately for &&/||)
        right = self._codegen(node.right)

        # Matrix element-wise binary operations (before standard arithmetic)
        left_leash = self._get_leash_type_name(node.left)
        if left_leash.startswith("matrix<") and left_leash.endswith(">") and node.op not in ("&&", "||"):
            inner_type_name = left_leash[7:-1]
            inner_llvm = self._get_llvm_type(inner_type_name)
            matrix_llvm = ir.LiteralStructType([inner_llvm.as_pointer(), ir.IntType(64), ir.IntType(64)])

            left_data = self.builder.extract_value(left, 0)
            left_size = self.builder.extract_value(left, 1)
            right_data = self.builder.extract_value(right, 0)
            right_size = self.builder.extract_value(right, 1)

            sizes_match = self.builder.icmp_signed("==", left_size, right_size)
            self._emit_runtime_check(
                sizes_match, "Runtime error: Matrix size mismatch in binary operation.\n"
            )

            # Use aligned allocation for cache-friendly / SIMD-friendly memory layout
            i8ptr = ir.IntType(8).as_pointer()
            i32 = ir.IntType(32)
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size_val = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(i32, 1)]), ir.IntType(64)
            )
            total_bytes = self.builder.mul(left_size, elem_size_val)
            total_bytes.flags = ['nuw']
            result_data_bytes = self.builder.call(
                self.aligned_alloc, [total_bytes, ir.Constant(ir.IntType(64), 64)]
            )
            self._track_alloc(result_data_bytes)
            result_data = self.builder.bitcast(result_data_bytes, inner_llvm.as_pointer())

            op_code = {
                "+": 0, "-": 1, "*": 2, "/": 3
            }.get(node.op, -1)

            # Dispatch to optimized C runtime for common types (parallel + compiler auto-vec)
            use_c_runtime = False
            if isinstance(inner_llvm, ir.FloatType):
                runtime_fn = self.mat_parop_float; use_c_runtime = True
            elif isinstance(inner_llvm, ir.DoubleType):
                runtime_fn = self.mat_parop_double; use_c_runtime = True
            elif isinstance(inner_llvm, ir.IntType) and inner_llvm.width == 32:
                runtime_fn = self.mat_parop_int32; use_c_runtime = True
            elif isinstance(inner_llvm, ir.IntType) and inner_llvm.width == 64:
                runtime_fn = self.mat_parop_int64; use_c_runtime = True

            if use_c_runtime and op_code >= 0:
                left_bytes = self.builder.bitcast(left_data, i8ptr)
                right_bytes = self.builder.bitcast(right_data, i8ptr)
                res_bytes = self.builder.bitcast(result_data, i8ptr)
                op_val = ir.Constant(i32, op_code)
                self.builder.call(runtime_fn, [res_bytes, left_bytes, right_bytes, left_size, op_val])
            else:
                # Fallback inline loop for uncommon types (half, int8, int16, etc.)
                if op_code < 0:
                    raise LeashError(f"Operator '{node.op}' not supported for matrix types", node=node)
                is_float_elem = isinstance(inner_llvm, (ir.HalfType, ir.FloatType, ir.DoubleType))
                loop_cond_bb = self.builder.function.append_basic_block("mat_binop_cond")
                loop_body_bb = self.builder.function.append_basic_block("mat_binop_body")
                loop_inc_bb = self.builder.function.append_basic_block("mat_binop_inc")
                loop_done_bb = self.builder.function.append_basic_block("mat_binop_done")
                i_ptr = self.builder.alloca(ir.IntType(64), name="mat_binop_i")
                self.builder.store(ir.Constant(ir.IntType(64), 0), i_ptr)
                self.builder.branch(loop_cond_bb)
                self.builder.position_at_end(loop_cond_bb)
                i_val = self.builder.load(i_ptr)
                i_in_bounds = self.builder.icmp_unsigned("<", i_val, left_size)
                self.builder.cbranch(i_in_bounds, loop_body_bb, loop_done_bb)
                self.builder.position_at_end(loop_body_bb)
                lep = self.builder.gep(left_data, [i_val], inbounds=True)
                rep = self.builder.gep(right_data, [i_val], inbounds=True)
                lv = self.builder.load(lep); rv = self.builder.load(rep)
                # Optimization: Add fast math flags to float ops, nuw/nsw to int ops
                if node.op == "+":
                    if is_float_elem:
                        res_e = self.builder.fadd(lv, rv)
                        res_e.flags = ['fast']
                    else:
                        res_e = self.builder.add(lv, rv)
                        res_e.flags = ['nuw', 'nsw']
                elif node.op == "-":
                    if is_float_elem:
                        res_e = self.builder.fsub(lv, rv)
                        res_e.flags = ['fast']
                    else:
                        res_e = self.builder.sub(lv, rv)
                        res_e.flags = ['nuw', 'nsw']
                elif node.op == "*":
                    if is_float_elem:
                        res_e = self.builder.fmul(lv, rv)
                        res_e.flags = ['fast']
                    else:
                        res_e = self.builder.mul(lv, rv)
                        res_e.flags = ['nuw', 'nsw']
                elif node.op == "/":
                    if is_float_elem:
                        res_e = self.builder.fdiv(lv, rv)
                        res_e.flags = ['fast']
                    else:
                        res_e = (self._emit_division_by_zero_check(rv) or self.builder.sdiv(lv, rv))
                sep = self.builder.gep(result_data, [i_val], inbounds=True)
                self.builder.store(res_e, sep)
                self.builder.branch(loop_inc_bb)
                self.builder.position_at_end(loop_inc_bb)
                next_i = self.builder.add(i_val, ir.Constant(ir.IntType(64), 1))
                next_i.flags = ['nuw', 'nsw']
                self.builder.store(next_i, i_ptr)
                self.builder.branch(loop_cond_bb)
                self.builder.position_at_end(loop_done_bb)

            result_val = ir.Constant(matrix_llvm, ir.Undefined)
            result_val = self.builder.insert_value(result_val, result_data, 0)
            result_val = self.builder.insert_value(result_val, left_size, 1)
            result_val = self.builder.insert_value(result_val, left_size, 2)
            return result_val

        # Logical operations (short-circuiting)
        if node.op == "&&":
            res_ptr = self.builder.alloca(ir.IntType(1))
            self.builder.store(ir.Constant(ir.IntType(1), 0), res_ptr)
            left_bool = self._cast_bool(left)
            with self.builder.if_then(left_bool):
                right_bool = self._cast_bool(self._codegen(node.right))
                self.builder.store(right_bool, res_ptr)
            return self.builder.load(res_ptr)

        if node.op == "||":
            res_ptr = self.builder.alloca(ir.IntType(1))
            self.builder.store(ir.Constant(ir.IntType(1), 1), res_ptr)
            left_bool = self._cast_bool(left)
            with self.builder.if_else(left_bool) as (then, otherwise):
                with then:
                    pass
                with otherwise:
                    right_bool = self._cast_bool(self._codegen(node.right))
                    self.builder.store(right_bool, res_ptr)
            return self.builder.load(res_ptr)

        # Standard binary ops

        def is_ptr(typ):
            if not (
                isinstance(typ, ir.PointerType) or getattr(typ, "is_pointer", False)
            ):
                return False
            pointee = getattr(typ, "pointee", None)
            # String is i8*, so we check for pointers NOT to i8
            return not (isinstance(pointee, ir.IntType) and pointee.width == 8)

        # Check if left operand is a char pointer (should use pointer arithmetic, not string concat)
        def is_char_ptr_leash(node):
            leash_type = self._get_leash_type_name(node)
            resolved = self._resolve_type_name(leash_type)
            return resolved == "*char"

        left_is_char_ptr = is_char_ptr_leash(node.left)
        right_is_char_ptr = (
            is_char_ptr_leash(node.right) if hasattr(node, "right") else False
        )

        if (
            node.op == "+"
            and (is_ptr(left.type) or left_is_char_ptr)
            and isinstance(right.type, ir.IntType)
        ):
            return self.builder.gep(
                left,
                [
                    self.builder.zext(right, ir.IntType(64))
                    if right.type.width < 64
                    else right
                ],
            )
        if (
            node.op == "+"
            and isinstance(left.type, ir.IntType)
            and (is_ptr(right.type) or right_is_char_ptr)
        ):
            return self.builder.gep(
                right,
                [
                    self.builder.zext(left, ir.IntType(64))
                    if left.type.width < 64
                    else left
                ],
            )
        if (
            node.op == "-"
            and (is_ptr(left.type) or left_is_char_ptr)
            and isinstance(right.type, ir.IntType)
        ):
            idx = (
                self.builder.zext(right, ir.IntType(64))
                if right.type.width < 64
                else right
            )
            neg_idx = self.builder.neg(idx)
            return self.builder.gep(left, [neg_idx])

        def is_i8_ptr(typ):
            if not (
                isinstance(typ, ir.PointerType) or getattr(typ, "is_pointer", False)
            ):
                return False
            pointee = getattr(typ, "pointee", None)
            return isinstance(pointee, ir.IntType) and pointee.width == 8

        is_string_l = is_i8_ptr(left.type)
        is_string_r = is_i8_ptr(right.type)

        is_slice_l = (
            isinstance(left.type, ir.LiteralStructType)
            and len(left.type.elements) == 2
            and is_i8_ptr(left.type.elements[1])
        )
        is_slice_r = (
            isinstance(right.type, ir.LiteralStructType)
            and len(right.type.elements) == 2
            and is_i8_ptr(right.type.elements[1])
        )

        is_char_l = isinstance(left.type, ir.IntType) and left.type.width == 8
        is_char_r = isinstance(right.type, ir.IntType) and right.type.width == 8

        # Mixed string concatenation
        is_numeric_l = isinstance(left.type, (ir.IntType, ir.HalfType, ir.FloatType, ir.DoubleType))
        is_numeric_r = isinstance(right.type, (ir.IntType, ir.HalfType, ir.FloatType, ir.DoubleType))

        if (
            node.op == "+"
            and (
                (is_string_l or is_slice_l)
                and (is_string_r or is_slice_r or is_numeric_r or is_char_r)
            )
            or ((is_string_r or is_slice_r) and (is_numeric_l or is_char_l))
        ):
            # Convert non-strings to strings
            if not (is_string_l or is_slice_l):
                left = self._emit_tostring(left, left.type)
                is_string_l = True
            if not (is_string_r or is_slice_r):
                right = self._emit_tostring(right, right.type)
                is_string_r = True

            len_l = None
            if is_string_l:
                # Handle null pointers safely - treat as empty string using explicit blocks
                null_left = self.builder.icmp_unsigned(
                    "==", left, ir.Constant(left.type, None)
                )
                len_l_null_bb = self.builder.function.append_basic_block("len_l_null")
                len_l_str_bb = self.builder.function.append_basic_block("len_l_str")
                len_l_merge_bb = self.builder.function.append_basic_block("len_l_merge")
                self.builder.cbranch(null_left, len_l_null_bb, len_l_str_bb)

                self.builder.position_at_end(len_l_null_bb)
                len_l_null = ir.Constant(ir.IntType(64), 0)
                self.builder.branch(len_l_merge_bb)

                self.builder.position_at_end(len_l_str_bb)
                len_l_str = self.builder.call(self.strlen, [left])
                self.builder.branch(len_l_merge_bb)

                self.builder.position_at_end(len_l_merge_bb)
                phi_l = self.builder.phi(ir.IntType(64))
                phi_l.add_incoming(len_l_null, len_l_null_bb)
                phi_l.add_incoming(len_l_str, len_l_str_bb)
                len_l = phi_l
            elif is_slice_l:
                len_l = self.builder.zext(
                    self.builder.extract_value(left, 0), ir.IntType(64)
                )
            elif is_char_l:
                len_l = ir.Constant(ir.IntType(64), 1)

            len_r = None
            if is_string_r:
                # Handle null pointers safely - treat as empty string using explicit blocks
                null_right = self.builder.icmp_unsigned(
                    "==", right, ir.Constant(right.type, None)
                )
                len_r_null_bb = self.builder.function.append_basic_block("len_r_null")
                len_r_str_bb = self.builder.function.append_basic_block("len_r_str")
                len_r_merge_bb = self.builder.function.append_basic_block("len_r_merge")
                self.builder.cbranch(null_right, len_r_null_bb, len_r_str_bb)

                self.builder.position_at_end(len_r_null_bb)
                len_r_null = ir.Constant(ir.IntType(64), 0)
                self.builder.branch(len_r_merge_bb)

                self.builder.position_at_end(len_r_str_bb)
                len_r_str = self.builder.call(self.strlen, [right])
                self.builder.branch(len_r_merge_bb)

                self.builder.position_at_end(len_r_merge_bb)
                phi_r = self.builder.phi(ir.IntType(64))
                phi_r.add_incoming(len_r_null, len_r_null_bb)
                phi_r.add_incoming(len_r_str, len_r_str_bb)
                len_r = phi_r
            elif is_slice_r:
                len_r = self.builder.zext(
                    self.builder.extract_value(right, 0), ir.IntType(64)
                )
            elif is_char_r:
                len_r = ir.Constant(ir.IntType(64), 1)

            total_len = self.builder.add(len_l, len_r)
            total_len_plus_1 = self.builder.add(
                total_len, ir.Constant(ir.IntType(64), 1)
            )
            new_str = self.builder.call(self.malloc, [total_len_plus_1])
            self._track_alloc(new_str)  # TRACK THIS ALLOCATION

            # Copy left
            if is_string_l:
                # Handle null pointers safely using explicit blocks
                null_left = self.builder.icmp_unsigned(
                    "==", left, ir.Constant(left.type, None)
                )
                copy_left_null_bb = self.builder.function.append_basic_block(
                    "copy_left_null"
                )
                copy_left_str_bb = self.builder.function.append_basic_block(
                    "copy_left_str"
                )
                copy_left_merge_bb = self.builder.function.append_basic_block(
                    "copy_left_merge"
                )
                self.builder.cbranch(null_left, copy_left_null_bb, copy_left_str_bb)

                self.builder.position_at_end(copy_left_null_bb)
                # Left is null, start with empty string
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(
                        new_str, [ir.Constant(ir.IntType(64), 0)], inbounds=True
                    ),
                )
                self.builder.branch(copy_left_merge_bb)

                self.builder.position_at_end(copy_left_str_bb)
                self.builder.call(self.strcpy, [new_str, left])
                self.builder.branch(copy_left_merge_bb)

                self.builder.position_at_end(copy_left_merge_bb)
            elif is_slice_l:
                ptr_l = self.builder.extract_value(left, 1)
                self.builder.call(self.strncpy, [new_str, ptr_l, len_l])
                null_pos = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.store(ir.Constant(ir.IntType(8), 0), null_pos)
            elif is_char_l:
                self.builder.store(
                    left,
                    self.builder.gep(
                        new_str, [ir.Constant(ir.IntType(64), 0)], inbounds=True
                    ),
                )
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(
                        new_str, [ir.Constant(ir.IntType(64), 1)], inbounds=True
                    ),
                )

            # Concatenate right
            if is_string_r:
                # Handle null pointers safely using explicit blocks
                null_right = self.builder.icmp_unsigned(
                    "==", right, ir.Constant(right.type, None)
                )
                concat_right_null_bb = self.builder.function.append_basic_block(
                    "concat_right_null"
                )
                concat_right_str_bb = self.builder.function.append_basic_block(
                    "concat_right_str"
                )
                concat_right_merge_bb = self.builder.function.append_basic_block(
                    "concat_right_merge"
                )
                self.builder.cbranch(
                    null_right, concat_right_null_bb, concat_right_str_bb
                )

                self.builder.position_at_end(concat_right_null_bb)
                # Right is null, nothing to concatenate
                self.builder.branch(concat_right_merge_bb)

                self.builder.position_at_end(concat_right_str_bb)
                self.builder.call(self.strcat, [new_str, right])
                self.builder.branch(concat_right_merge_bb)

                self.builder.position_at_end(concat_right_merge_bb)
            elif is_slice_r:
                ptr_r = self.builder.extract_value(right, 1)
                dest = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.call(self.strncpy, [dest, ptr_r, len_r])
                end_pos = self.builder.add(len_l, len_r)
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(new_str, [end_pos], inbounds=True),
                )
            elif is_char_r:
                dest = self.builder.gep(new_str, [len_l], inbounds=True)
                self.builder.store(right, dest)
                end_pos = self.builder.add(len_l, ir.Constant(ir.IntType(64), 1))
                self.builder.store(
                    ir.Constant(ir.IntType(8), 0),
                    self.builder.gep(new_str, [end_pos], inbounds=True),
                )

            return new_str

        is_string = is_string_l and is_string_r
        if is_string:
            if node.op == "-":
                p = self.builder.call(self.strstr, [left, right])
                null_ptr = (
                    getattr(ir.IntType(8), "as_pointer")()(None)
                    if hasattr(ir.IntType(8), "as_pointer")
                    else ir.Constant(left.type, None)
                )
                null_ptr = ir.Constant(left.type, None) if not null_ptr else null_ptr
                is_not_null = self.builder.icmp_unsigned("!=", p, null_ptr)

                found_bb = self.builder.function.append_basic_block("strstr_found")
                not_found_bb = self.builder.function.append_basic_block(
                    "strstr_not_found"
                )
                merge_bb = self.builder.function.append_basic_block("strstr_merge")

                self.builder.cbranch(is_not_null, found_bb, not_found_bb)

                self.builder.position_at_end(found_bb)
                len_l = self.builder.call(self.strlen, [left])
                len_r = self.builder.call(self.strlen, [right])
                p_int = self.builder.ptrtoint(p, ir.IntType(64))
                l_int = self.builder.ptrtoint(left, ir.IntType(64))
                prefix_len = self.builder.sub(p_int, l_int)

                new_len = self.builder.sub(len_l, len_r)
                new_len_plus_1 = self.builder.add(
                    new_len, ir.Constant(ir.IntType(64), 1)
                )
                res_found = self.builder.call(self.malloc, [new_len_plus_1])
                self._track_alloc(res_found)  # TRACK

                self.builder.call(self.strncpy, [res_found, left, prefix_len])
                res_null_pos = self.builder.gep(res_found, [prefix_len], inbounds=True)
                self.builder.store(ir.Constant(ir.IntType(8), 0), res_null_pos)

                suffix_ptr = self.builder.gep(p, [len_r], inbounds=True)
                self.builder.call(self.strcat, [res_found, suffix_ptr])
                self.builder.branch(merge_bb)

                self.builder.position_at_end(not_found_bb)
                len_l2 = self.builder.call(self.strlen, [left])
                len_l2_plus_1 = self.builder.add(len_l2, ir.Constant(ir.IntType(64), 1))
                res_not_found = self.builder.call(self.malloc, [len_l2_plus_1])
                self._track_alloc(res_not_found)  # TRACK
                self.builder.call(self.strcpy, [res_not_found, left])
                self.builder.branch(merge_bb)

                self.builder.position_at_end(merge_bb)
                phi = self.builder.phi(left.type, name="str_sub_res")
                phi.add_incoming(res_found, found_bb)
                phi.add_incoming(res_not_found, not_found_bb)
                return phi
            elif node.op == "==":
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed(
                    "==", cmp, ir.Constant(ir.IntType(32), 0)
                )
            elif node.op == "!=":
                cmp = self.builder.call(self.strcmp, [left, right])
                return self.builder.icmp_signed(
                    "!=", cmp, ir.Constant(ir.IntType(32), 0)
                )
            else:
                raise Exception(f"Unknown string binary op {node.op}")

        # Determine signedness for int type promotion
        def _is_uint_expr(node):
            t = self._get_leash_type_name(node)
            return t.startswith("uint") or t == "uint"

        # Type promotion
        if left.type != right.type:
            if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
                signed = not (_is_uint_expr(node.left) or _is_uint_expr(node.right))
                if left.type.width < right.type.width:
                    left = self._emit_cast(left, right.type, is_signed=signed)
                else:
                    right = self._emit_cast(right, left.type, is_signed=signed)
            elif isinstance(left.type, (ir.HalfType, ir.FloatType, ir.DoubleType)) and isinstance(
                right.type, (ir.HalfType, ir.FloatType, ir.DoubleType)
            ):
                src_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(left.type), 64)
                dst_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(right.type), 64)
                target = left.type if src_size >= dst_size else right.type
                left = self._emit_cast(left, target)
                right = self._emit_cast(right, target)
            elif isinstance(left.type, (ir.HalfType, ir.FloatType, ir.DoubleType)) and isinstance(
                right.type, ir.IntType
            ):
                right = self._emit_cast(right, left.type)
            elif isinstance(left.type, ir.IntType) and isinstance(
                right.type, (ir.HalfType, ir.FloatType, ir.DoubleType)
            ):
                left = self._emit_cast(left, right.type)

        # Determine if float or int based on types (assume matching types for now)
        is_float = isinstance(left.type, (ir.HalfType, ir.FloatType, ir.DoubleType))

        # Determine if unsigned operation (check Leash type names of operands)
        if not is_float and isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
            left_type = self._get_leash_type_name(node.left)
            right_type = self._get_leash_type_name(node.right)
            is_unsigned = (left_type.startswith("uint") or left_type == "uint") or \
                          (right_type.startswith("uint") or right_type == "uint")
        else:
            is_unsigned = False

        if node.op == "+":
            return (
                self.builder.fadd(left, right)
                if is_float
                else self.builder.add(left, right)
            )
        elif node.op == "-":
            return (
                self.builder.fsub(left, right)
                if is_float
                else self.builder.sub(left, right)
            )
        elif node.op == "*":
            return (
                self.builder.fmul(left, right)
                if is_float
                else self.builder.mul(left, right)
            )
        elif node.op == "/":
            if not is_float:
                self._emit_division_by_zero_check(right)
            if is_float:
                return self.builder.fdiv(left, right)
            elif is_unsigned:
                return self.builder.udiv(left, right)
            else:
                return self.builder.sdiv(left, right)
        elif node.op == "%":
            if not is_float:
                self._emit_division_by_zero_check(right)
            if is_float:
                return self.builder.frem(left, right)
            elif is_unsigned:
                return self.builder.urem(left, right)
            else:
                return self.builder.srem(left, right)
        elif node.op == "&":
            return self.builder.and_(left, right)
        elif node.op == "|":
            return self.builder.or_(left, right)
        elif node.op == "^":
            return self.builder.xor(left, right)
        elif node.op == "<<":
            return self.builder.shl(left, right)
        elif node.op == ">>":
            return self.builder.lshr(left, right) if is_unsigned else self.builder.ashr(left, right)
        elif node.op == "==":
            return (
                self.builder.fcmp_ordered("==", left, right)
                if is_float
                else self.builder.icmp_signed("==", left, right)
            )
        elif node.op == "!=":
            return (
                self.builder.fcmp_ordered("!=", left, right)
                if is_float
                else self.builder.icmp_signed("!=", left, right)
            )
        elif node.op == "<":
            return (
                self.builder.fcmp_ordered("<", left, right)
                if is_float
                else (self.builder.icmp_unsigned("<", left, right) if is_unsigned
                      else self.builder.icmp_signed("<", left, right))
            )
        elif node.op == "<=":
            return (
                self.builder.fcmp_ordered("<=", left, right)
                if is_float
                else (self.builder.icmp_unsigned("<=", left, right) if is_unsigned
                      else self.builder.icmp_signed("<=", left, right))
            )
        elif node.op == ">":
            return (
                self.builder.fcmp_ordered(">", left, right)
                if is_float
                else (self.builder.icmp_unsigned(">", left, right) if is_unsigned
                      else self.builder.icmp_signed(">", left, right))
            )
        elif node.op == ">=":
            return (
                self.builder.fcmp_ordered(">=", left, right)
                if is_float
                else (self.builder.icmp_unsigned(">=", left, right) if is_unsigned
                      else self.builder.icmp_signed(">=", left, right))
            )
        elif node.op == "<>":
            return self._codegen_isin_operator(left, right, node)

        raise LeashError(f"Unknown binary operator: '{node.op}'", node=node)

    def _codegen_UnaryOp(self, node):
        from .ast_nodes import Identifier

        if node.op == "*":  # Dereference
            val = self._codegen(node.expr)
            self._emit_null_pointer_check(val, "Runtime error: Null pointer dereference.\n")
            return self.builder.load(val)
        if node.op == "&":  # Address-of
            # Check if we're taking address of a function
            if isinstance(node.expr, Identifier) and node.expr.name in self.func_symtab:
                # Return the function as a pointer
                func = self.func_symtab[node.expr.name]
                return func
            # For variables, return pointer to variable
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result
            return ptr

        val = self._codegen(node.expr)
        if node.op == "-":
            if isinstance(val.type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                return self.builder.fneg(val)
            else:
                return self.builder.neg(val)
        elif node.op == "!":
            bool_val = self._cast_bool(val)
            return self.builder.not_(bool_val)
        elif node.op == "~":
            return self.builder.not_(val)
        elif node.op == "++p":
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result
            current = self.builder.load(ptr)
            one = ir.Constant(current.type, 1)
            incremented = self.builder.add(current, one)
            self.builder.store(incremented, ptr)
            return incremented
        elif node.op == "--p":
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result
            current = self.builder.load(ptr)
            one = ir.Constant(current.type, 1)
            decremented = self.builder.sub(current, one)
            self.builder.store(decremented, ptr)
            return decremented
        elif node.op == "++":
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result
            current = self.builder.load(ptr)
            one = ir.Constant(current.type, 1)
            incremented = self.builder.add(current, one)
            self.builder.store(incremented, ptr)
            return current
        elif node.op == "--":
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result
            current = self.builder.load(ptr)
            one = ir.Constant(current.type, 1)
            decremented = self.builder.sub(current, one)
            self.builder.store(decremented, ptr)
            return current

        raise LeashError(f"Unknown unary operator: '{node.op}'", node=node)

    def _emit_get_input(self, node):
        """Implement the 'get' builtin for interactive input."""
        # 1. Print prompt if exists
        if node.args:
            prompt_val = self._codegen(node.args[0])
            # Use printf-like show_standard logic for consistency
            fmt = self._emit_const_str("%s")
            self.builder.call(self.printf, [fmt, prompt_val])

        # 2. Buffer for reading
        # capacity = 256; size = 0; buffer = malloc(capacity)
        capacity_ptr = self.builder.alloca(ir.IntType(64), name="input_cap")
        size_ptr = self.builder.alloca(ir.IntType(64), name="input_size")
        self.builder.store(ir.Constant(ir.IntType(64), 256), capacity_ptr)
        self.builder.store(ir.Constant(ir.IntType(64), 0), size_ptr)

        buffer_ptr_ptr = self.builder.alloca(
            ir.IntType(8).as_pointer(), name="input_buf_ptr"
        )
        initial_buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 256)])
        self.builder.store(initial_buf, buffer_ptr_ptr)

        # 3. Read loop
        loop_bb = self.builder.function.append_basic_block("get_loop")
        loop_end_bb = self.builder.function.append_basic_block("get_loop_end")

        self.builder.branch(loop_bb)
        self.builder.position_at_end(loop_bb)

        c = self.builder.call(self.getchar, [])

        # Check for newline (10), carriage return (13), or EOF (-1)
        is_eof = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), -1))
        is_nl = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), 10))
        is_cr = self.builder.icmp_signed("==", c, ir.Constant(ir.IntType(32), 13))
        should_stop = self.builder.or_(self.builder.or_(is_eof, is_nl), is_cr)

        next_char_bb = self.builder.function.append_basic_block("get_next_char")
        self.builder.cbranch(should_stop, loop_end_bb, next_char_bb)

        # Body of next char
        self.builder.position_at_end(next_char_bb)

        # Append char: buffer[size] = (i8)c; size++
        curr_size = self.builder.load(size_ptr)
        curr_cap = self.builder.load(capacity_ptr)

        # Check realloc: if size + 2 >= capacity (reserve space for null)
        is_full = self.builder.icmp_signed(
            ">=", self.builder.add(curr_size, ir.Constant(ir.IntType(64), 2)), curr_cap
        )

        realloc_bb = self.builder.function.append_basic_block("get_realloc")
        store_char_bb = self.builder.function.append_basic_block("get_store_char")
        self.builder.cbranch(is_full, realloc_bb, store_char_bb)

        # Realloc
        self.builder.position_at_end(realloc_bb)
        new_cap = self.builder.mul(curr_cap, ir.Constant(ir.IntType(64), 2))
        new_cap.flags = ['nuw']
        self.builder.store(new_cap, capacity_ptr)
        old_buf = self.builder.load(buffer_ptr_ptr)
        new_buf = self.builder.call(self.realloc, [old_buf, new_cap])
        self.builder.store(new_buf, buffer_ptr_ptr)
        self.builder.branch(store_char_bb)

        # Store char
        self.builder.position_at_end(store_char_bb)
        buf = self.builder.load(buffer_ptr_ptr)
        idx_ptr = self.builder.gep(buf, [curr_size])
        self.builder.store(self.builder.trunc(c, ir.IntType(8)), idx_ptr)

        self.builder.store(
            self.builder.add(curr_size, ir.Constant(ir.IntType(64), 1)), size_ptr
        )
        self.builder.branch(loop_bb)

        self.builder.position_at_end(loop_end_bb)

        # 4. Null terminator
        final_size = self.builder.load(size_ptr)
        final_buf = self.builder.load(buffer_ptr_ptr)
        term_ptr = self.builder.gep(final_buf, [final_size])
        self.builder.store(ir.Constant(ir.IntType(8), 0), term_ptr)

        # 5. Track for SAMM and return
        return self._track_alloc(final_buf)

    def _emit_exec(self, node):
        """Implement the 'exec' builtin for running shell commands.

        Modes:
        - nil or no mode: execute with input support (interactive), return output
        - "wait": wait for command to finish, return output
        - "silent": execute and return result after finish, no output
        - "code": return exit code as string
        """
        if len(node.args) < 1:
            raise LeashError("exec requires at least 1 argument (command)", node=node)

        command_val = self._codegen(node.args[0])
        original_command_val = command_val  # Keep original for "code" mode

        mode = None
        if len(node.args) >= 2:
            mode_arg = node.args[1]
            from .ast_nodes import NullLiteral, StringLiteral

            if isinstance(mode_arg, NullLiteral):
                mode = None
            elif isinstance(mode_arg, StringLiteral):
                mode = mode_arg.value
            else:
                mode_val = self._codegen(mode_arg)
                mode = None

        if mode == "code":
            # Use platform-appropriate shell command
            if self.target_platform == "win64":
                # Windows: use cmd /v:on for delayed expansion of errorlevel
                redirect_cmd = self._emit_const_str("cmd /v:on /c \"%s >nul 2>&1 & echo !errorlevel!\"")
            else:
                # Unix: use sh -c
                redirect_cmd = self._emit_const_str("sh -c '(%s) >/dev/null 2>&1; echo $?'")
            cmd_buf = self.builder.call(
                self.malloc_fn, [ir.Constant(ir.IntType(64), 256)]
            )
            self.builder.call(self.sprintf_fn, [cmd_buf, redirect_cmd, command_val])
            pipe = self.builder.call(
                self.popen_fn, [cmd_buf, self._emit_const_str("r")]
            )

            read_buffer = self.builder.call(
                self.malloc_fn, [ir.Constant(ir.IntType(64), 64)]
            )
            line = self.builder.call(
                self.fgets_fn, [read_buffer, ir.Constant(ir.IntType(32), 64), pipe]
            )
            self.builder.call(self.pclose_fn, [pipe])

            return line
        elif mode == "silent" or mode == "wait":
            # On Windows, use cmd /c prefix for popen
            if self.target_platform == "win64":
                cmd_val = original_command_val
                cmd_prefix_fmt = self._emit_const_str("cmd /c %s")
                new_cmd_buf = self.builder.call(
                    self.malloc_fn, [ir.Constant(ir.IntType(64), 1024)]
                )
                self.builder.call(self.sprintf_fn, [new_cmd_buf, cmd_prefix_fmt, cmd_val])
                popen_cmd = new_cmd_buf
            else:
                popen_cmd = command_val
            popen_mode = self._emit_const_str("r")
            pipe = self.builder.call(self.popen_fn, [popen_cmd, popen_mode])

            read_buffer = self.builder.call(
                self.malloc_fn, [ir.Constant(ir.IntType(64), 4096)]
            )

            alloc_size = ir.Constant(ir.IntType(64), 4096)
            result_ptr_ptr = self.builder.alloca(
                ir.IntType(8).as_pointer(), name="exec_result_ptr"
            )
            result_size_ptr = self.builder.alloca(
                ir.IntType(64), name="exec_result_size"
            )
            capacity_ptr = self.builder.alloca(ir.IntType(64), name="exec_capacity")

            self.builder.store(read_buffer, result_ptr_ptr)
            self.builder.store(ir.Constant(ir.IntType(64), 0), result_size_ptr)
            self.builder.store(alloc_size, capacity_ptr)

            loop_bb = self.builder.function.append_basic_block("exec_read_loop")
            loop_end_bb = self.builder.function.append_basic_block("exec_read_end")

            self.builder.branch(loop_bb)
            self.builder.position_at_end(loop_bb)

            line = self.builder.call(
                self.fgets_fn, [read_buffer, ir.Constant(ir.IntType(32), 4096), pipe]
            )
            is_null = self.builder.icmp_signed(
                "==", line, ir.Constant(ir.IntType(8).as_pointer(), None)
            )

            next_char_bb = self.builder.function.append_basic_block("exec_next_line")
            self.builder.cbranch(is_null, loop_end_bb, next_char_bb)

            self.builder.position_at_end(next_char_bb)

            line_len = self.builder.call(self.strlen_fn, [line])

            curr_result_ptr = self.builder.load(result_ptr_ptr)
            curr_result_size = self.builder.load(result_size_ptr)
            curr_capacity = self.builder.load(capacity_ptr)

            new_size = self.builder.add(curr_result_size, line_len)

            need_realloc = self.builder.icmp_signed(">=", new_size, curr_capacity)

            realloc_bb = self.builder.function.append_basic_block("exec_realloc")
            copy_bb = self.builder.function.append_basic_block("exec_copy")
            self.builder.cbranch(need_realloc, realloc_bb, copy_bb)

            self.builder.position_at_end(realloc_bb)
            new_alloc_size = self.builder.mul(
                curr_capacity, ir.Constant(ir.IntType(64), 2)
            )
            new_alloc_size.flags = ['nuw']
            new_buffer = self.builder.call(
                self.realloc_fn, [curr_result_ptr, new_alloc_size]
            )
            self.builder.store(new_buffer, result_ptr_ptr)
            self.builder.store(new_alloc_size, capacity_ptr)
            self.builder.branch(copy_bb)

            self.builder.position_at_end(copy_bb)

            copy_result_ptr = self.builder.load(result_ptr_ptr)
            self.builder.call(self.strcpy_fn, [copy_result_ptr, line])

            self.builder.store(
                self.builder.add(self.builder.load(result_size_ptr), line_len),
                result_size_ptr,
            )
            self.builder.branch(loop_bb)

            self.builder.position_at_end(loop_end_bb)

            self.builder.call(self.pclose_fn, [pipe])

            final_result_ptr = self.builder.load(result_ptr_ptr)
            final_result_size = self.builder.load(result_size_ptr)

            term_ptr = self.builder.gep(final_result_ptr, [final_result_size])
            self.builder.store(ir.Constant(ir.IntType(8), 0), term_ptr)

            return final_result_ptr
        else:
            # On Windows, system() works with cmd, but let's use prefixed command if needed
            if self.target_platform == "win64":
                # Use original command for system() - it goes through cmd.exe on Windows
                self.builder.call(self.system_fn, [original_command_val])
            else:
                self.builder.call(self.system_fn, [command_val])
            return self._emit_const_str("")

    def _codegen_MethodCall(self, node):
        from .ast_nodes import Identifier

        # Handle static method calls on class names (e.g., pMath.exp(x))
        from .ast_nodes import ThisExpr, GenericTypeExpr
        is_static_call = False
        target_cls = None
        if isinstance(node.expr, Identifier) and node.expr.name in self.class_symtab:
            is_static_call = True
            target_cls = node.expr.name
        elif isinstance(node.expr, GenericTypeExpr):
            is_static_call = True
            # Compute the mangled class name (same logic as typechecker)
            type_args_str = "_".join(
                t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
                for t in node.expr.type_args
            )
            target_cls = f"{node.expr.name}_{type_args_str}"
        elif isinstance(node.expr, ThisExpr) and "this" not in self.var_symtab and self.current_class_name:
            is_static_call = True
            target_cls = self.current_class_name

        if is_static_call:
            cls_info = self.class_symtab[target_cls]
            func = cls_info["methods"].get(node.method)
            if not func:
                raise LeashError(
                    f"Class '{target_cls}' has no method named '{node.method}'",
                    node=node
                )

            is_m_static = cls_info["method_static"].get(node.method, False)
            if not is_m_static:
                raise LeashError(
                    f"Cannot call instance method '{node.method}' on class name '{target_cls}'",
                    node=node
                )

            # Prepare arguments for static method
            args = []
            for i, arg_node in enumerate(node.args):
                args.append(self._codegen(arg_node))

            casted_args = []
            for arg_val, expected_type in zip(args, func.args):
                casted_args.append(self._emit_cast(arg_val, expected_type.type))

            return self.builder.call(func, casted_args)

        from .ast_nodes import Identifier, StringLiteral

        if isinstance(node.expr, StringLiteral):
            str_val = self._codegen(node.expr)
            if node.method == "replace":
                return self._codegen_string_replace_from_value(str_val, node.args)
            raise LeashError(f"String has no method named '{node.method}'", node=node)

        lvalue_result = self._codegen_lvalue(node.expr)
        if len(lvalue_result) == 3:
            base_ptr, type_name, extra_data = lvalue_result
        else:
            base_ptr, type_name = lvalue_result
            extra_data = None
        
        resolved = self._resolve_type_name(type_name)

        if resolved.startswith("vec<"):
            return self._codegen_vector_method(
                base_ptr, resolved, node.method, node.args
            )

        if resolved.startswith("matrix<"):
            return self._codegen_matrix_method(
                base_ptr, resolved, node.method, node.args
            )

        if resolved.startswith("hash<") and resolved.endswith(">"):
            return self._codegen_hash_method(
                base_ptr, resolved, node.method, node.args, extra_data
            )

        if resolved == "string" and node.method == "size":
            val = self.builder.load(base_ptr)
            length = self.builder.call(self.strlen, [val])
            return self.builder.trunc(length, ir.IntType(32))
        if resolved == "string" and node.method == "replace":
            str_val = self.builder.load(base_ptr)
            return self._codegen_string_replace_from_value(str_val, node.args)

        if resolved.endswith("]") and node.method == "size":
            val = self.builder.load(base_ptr)
            length = self.builder.extract_value(val, 0)
            return self.builder.trunc(length, ir.IntType(32))

        # Handle struct method calls (e.g., p.getName())
        if resolved in self.struct_symtab:
            struct_info = self.struct_symtab[resolved]
            func = struct_info["methods"].get(node.method)
            if not func:
                raise LeashError(
                    f"Struct '{resolved}' has no method named '{node.method}'",
                    node=node
                )

            # Prepare arguments: instance pointer + method arguments
            args = []
            # Add the instance pointer as first argument (this)
            args.append(base_ptr)
            for i, arg_node in enumerate(node.args):
                args.append(self._codegen(arg_node))

            casted_args = []
            for arg_val, expected_type in zip(args, func.args):
                casted_args.append(self._emit_cast(arg_val, expected_type.type))

            return self.builder.call(func, casted_args)

        # Handle static File methods (File.open, File.rename, File.delete)
        from .ast_nodes import Identifier

        if isinstance(node.expr, Identifier) and node.expr.name == "File":
            return self._codegen_file_static_method(node.method, node.args)

        # Handle File instance methods
        if resolved == "File":
            return self._codegen_file_method(base_ptr, node.method, node.args)

        if resolved in self.class_symtab:
            cls_info = self.class_symtab[resolved]
            func = cls_info["methods"].get(node.method)
            if not func:
                raise LeashError(
                    f"Class '{resolved}' has no method named '{node.method}'",
                    node=node
                )
                print(
                    f"DEBUG CODEGEN: class_symtab keys={list(self.class_symtab.keys())}",
                    file=sys.stderr,
                )
                if resolved in self.class_symtab:
                    print(
                        f"DEBUG CODEGEN: cls_info methods={list(cls_info['methods'].keys())}",
                        file=sys.stderr,
                    )
                raise LeashError(
                    f"Class '{resolved}' has no method named '{node.method}'",
                    node=node
                )

            # Prepare arguments
            args = []
            for i, arg_node in enumerate(node.args):
                args.append(self._codegen(arg_node))

            is_m_static = cls_info["method_static"].get(node.method, False)

            if not is_m_static:
                if base_ptr is None:
                    raise LeashError(
                        f"Method '{node.method}' of class '{resolved}' is an instance method and requires an instance.",
                        node=node
                    )
                # Get the 'this' pointer
                if not resolved.startswith("&"):
                    this_ptr = self.builder.load(base_ptr)
                else:
                    this_ptr = base_ptr

                # For dynamic dispatch, use vtable lookup
                # Find the method index in the vtable
                vtable_idx = cls_info.get("vtable_indices", {}).get(node.method)

                if vtable_idx is not None and cls_info.get("vtable_global"):
                    # Load vtable pointer from instance (index 0)
                    vtable_ptr_ptr = self.builder.gep(
                        this_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), 0),
                        ],
                    )
                    vtable_ptr_i8 = self.builder.load(vtable_ptr_ptr)

                    # Cast i8* vtable pointer to the actual vtable struct pointer type
                    vtable_struct_ptr_type = cls_info["vtable_type"].as_pointer()
                    vtable_ptr = self.builder.bitcast(
                        vtable_ptr_i8, vtable_struct_ptr_type
                    )

                    # Load function pointer from vtable at the method index
                    func_ptr_ptr = self.builder.gep(
                        vtable_ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), vtable_idx),
                        ],
                    )
                    func_ptr = self.builder.load(func_ptr_ptr)

                    # Cast function pointer to the correct type
                    func_type = func.function_type
                    typed_func_ptr = self.builder.bitcast(
                        func_ptr, func_type.as_pointer()
                    )

                    # Call through vtable with this as first argument
                    args = [this_ptr] + args
                    casted_args = []
                    for arg_val, expected_type in zip(args, func.args):
                        casted_args.append(self._emit_cast(arg_val, expected_type.type))

                    return self.builder.call(typed_func_ptr, casted_args)
                else:
                    # No vtable (shouldn't happen for instance methods), direct call
                    args = [this_ptr] + args
                    casted_args = []
                    for arg_val, expected_type in zip(args, func.args):
                        casted_args.append(self._emit_cast(arg_val, expected_type.type))
                    return self.builder.call(func, casted_args)
            else:
                # Static method - direct call
                casted_args = []
                for arg_val, expected_type in zip(args, func.args):
                    casted_args.append(self._emit_cast(arg_val, expected_type.type))
                return self.builder.call(func, casted_args)

        raise LeashError(
            f"Method '{node.method}' is not implemented for type '{resolved}'",
            line=getattr(node, "line", None),
            col=getattr(node, "col", None),
        )

    def _update_vec_struct(self, vec_ptr, data, size, cap):
        """Update the fields of a vector struct through its pointer."""
        struct_val = self.builder.load(vec_ptr)
        struct_val = self.builder.insert_value(struct_val, data, 0)
        struct_val = self.builder.insert_value(struct_val, size, 1)
        struct_val = self.builder.insert_value(struct_val, cap, 2)
        self.builder.store(struct_val, vec_ptr)

    def _vector_ensure_capacity(self, vec_ptr, data, size, cap, needed_size, elem_llvm):
        """Ensure vector has space for needed_size elements. Safe for SAMM (uses malloc)."""
        needed = self.builder.icmp_unsigned(">", needed_size, cap)

        merge_bb = self.builder.function.append_basic_block("vec_cap_merge")
        needed_bb = self.builder.function.append_basic_block("vec_cap_needed")

        self.builder.cbranch(needed, needed_bb, merge_bb)

        self.builder.position_at_end(needed_bb)
        # new_cap = max(needed_size, cap * 2, 8)
        is_zero = self.builder.icmp_unsigned("==", cap, ir.Constant(ir.IntType(64), 0))
        cap2 = self.builder.mul(cap, ir.Constant(ir.IntType(64), 2))
        cap2.flags = ['nuw']
        cap_candidate = self.builder.select(
            is_zero, ir.Constant(ir.IntType(64), 8), cap2
        )

        is_needed_larger = self.builder.icmp_unsigned(
            ">", needed_size, cap_candidate
        )
        new_cap = self.builder.select(is_needed_larger, needed_size, cap_candidate)

        # total_bytes = new_cap * sizeof(elem)
        # sizeof(elem) trick via gep
        dummy_ptr = ir.Constant(elem_llvm.as_pointer(), None)
        elem_size_ptr = self.builder.gep(
            dummy_ptr, [ir.Constant(ir.IntType(32), 1)], inbounds=True
        )
        elem_size = self.builder.ptrtoint(elem_size_ptr, ir.IntType(64))
        total_bytes = self.builder.mul(new_cap, elem_size)
        total_bytes.flags = ['nuw']

        new_data_bytes = self.builder.call(self.malloc, [total_bytes])
        self._track_alloc(new_data_bytes)

        # Copy old data
        is_not_null = self.builder.icmp_unsigned(
            "!=",
            self.builder.ptrtoint(data, ir.IntType(64)),
            ir.Constant(ir.IntType(64), 0),
        )
        with self.builder.if_then(is_not_null):
            old_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            copy_bytes = self.builder.mul(size, elem_size)
            copy_bytes.flags = ['nuw']
            self.builder.call(self.memmove, [new_data_bytes, old_bytes, copy_bytes])

        new_data = self.builder.bitcast(new_data_bytes, elem_llvm.as_pointer())
        self._update_vec_struct(vec_ptr, new_data, size, new_cap)
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

        # Reload current state
        final_struct = self.builder.load(vec_ptr)
        final_data = self.builder.extract_value(final_struct, 0)
        final_cap = self.builder.extract_value(final_struct, 2)
        return final_data, final_cap

    def _vector_check_capacity(self, vec_ptr, data, size, cap, elem_llvm):
        """Check if capacity is enough, otherwise allocate larger buffer."""
        needed_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
        needed_size.flags = ['nuw']
        return self._vector_ensure_capacity(
            vec_ptr, data, size, cap, needed_size, elem_llvm
        )

    def _codegen_vector_method(self, vec_ptr, vec_type_name, method, args):
        inner_type_name = vec_type_name[4:-1]
        inner_llvm = self._get_llvm_type(inner_type_name)

        struct_val = self.builder.load(vec_ptr)
        data = self.builder.extract_value(struct_val, 0)
        size = self.builder.extract_value(struct_val, 1)
        cap = self.builder.extract_value(struct_val, 2)

        if method == "pushb":
            val = self._codegen(args[0])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # Store at data[size]
            store_ptr = self.builder.gep(new_data, [size], inbounds=True)
            self.builder.store(val, store_ptr)

            # Update size (with nuw flag: size+1 never wraps)
            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popb":
            # Check vector is not empty
            is_nonempty = self.builder.icmp_unsigned(
                ">", size, ir.Constant(ir.IntType(64), 0)
            )
            self._emit_runtime_check(
                is_nonempty, "Runtime error: popb called on empty vector.\n"
            )
            # return data[size-1], size--
            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']
            res_ptr = self.builder.gep(data, [new_size], inbounds=True)
            res_val = self.builder.load(res_ptr)

            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return res_val

        elif method == "size":
            return self.builder.trunc(size, ir.IntType(32))

        elif method == "get":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx64 = self.builder.sext(idx, ir.IntType(64))
            # Normalize negative index: idx = idx < 0 ? idx + size : idx
            is_negative = self.builder.icmp_signed(
                "<", idx64, ir.Constant(ir.IntType(64), 0)
            )
            wrapped = self.builder.add(idx64, size)
            idx64 = self.builder.select(is_negative, wrapped, idx64)
            # Bounds check: idx >= 0 && idx < size
            idx_nonneg = self.builder.icmp_signed(
                ">=", idx64, ir.Constant(ir.IntType(64), 0)
            )
            idx_in_bounds = self.builder.icmp_unsigned("<", idx64, size)
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Vector index out of bounds in get().\n"
            )
            ptr = self.builder.gep(data, [idx64], inbounds=True)
            return self.builder.load(ptr)

        elif method == "set":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx64 = self.builder.sext(idx, ir.IntType(64))
            # Normalize negative index: idx = idx < 0 ? idx + size : idx
            is_negative = self.builder.icmp_signed(
                "<", idx64, ir.Constant(ir.IntType(64), 0)
            )
            wrapped = self.builder.add(idx64, size)
            idx64 = self.builder.select(is_negative, wrapped, idx64)
            # Bounds check
            idx_nonneg = self.builder.icmp_signed(
                ">=", idx64, ir.Constant(ir.IntType(64), 0)
            )
            idx_in_bounds = self.builder.icmp_unsigned("<", idx64, size)
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Vector index out of bounds in set().\n"
            )
            val = self._codegen(args[1])
            val = self._emit_cast(val, inner_llvm)
            ptr = self.builder.gep(data, [idx64], inbounds=True)
            self.builder.store(val, ptr)
            return None

        elif method == "clear":
            self._update_vec_struct(vec_ptr, data, ir.Constant(ir.IntType(32), 0), cap)
            return None

        elif method == "pushf":
            val = self._codegen(args[0])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # memmove data[1..size] = data[0..size-1]
            old_data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            elem_size_val = self._type_byte_size(inner_llvm)
            new_dst_bytes = self.builder.gep(
                old_data_bytes,
                [ir.Constant(ir.IntType(64), elem_size_val)],
                inbounds=True,
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(size, ir.IntType(64)), elem_size
            )
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [new_dst_bytes, old_data_bytes, copy_bytes])

            # Store at data[0]
            self.builder.store(val, new_data)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popf":
            # Check vector is not empty
            is_nonempty = self.builder.icmp_unsigned(
                ">", size, ir.Constant(ir.IntType(64), 0)
            )
            self._emit_runtime_check(
                is_nonempty, "Runtime error: popf called on empty vector.\n"
            )
            # res = data[0], memmove data[0..size-2] = data[1..size-1], size--
            res_val = self.builder.load(data)

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']

            dst_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            elem_size_val = self._type_byte_size(inner_llvm)
            src_bytes = self.builder.gep(
                dst_bytes, [ir.Constant(ir.IntType(64), elem_size_val)], inbounds=True
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(new_size, ir.IntType(64)), elem_size
            )
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return res_val

        elif method == "insert":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx64 = self.builder.zext(idx, ir.IntType(64))
            # Bounds check: 0 <= idx <= size (inserting at size == append)
            idx_nonneg = self.builder.icmp_signed(">=", idx64, ir.Constant(ir.IntType(64), 0))
            idx_in_bounds = self.builder.icmp_unsigned("<=", idx64, size)
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Vector insert index out of bounds.\n"
            )
            val = self._codegen(args[1])
            val = self._emit_cast(val, inner_llvm)

            new_data, new_cap = self._vector_check_capacity(
                vec_ptr, data, size, cap, inner_llvm
            )

            # memmove data[idx+1..size] = data[idx..size-1]
            elem_size_val = self._type_byte_size(inner_llvm)

            data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            idx_64 = self.builder.zext(idx, ir.IntType(64))
            idx_offset = self.builder.mul(idx_64, ir.Constant(ir.IntType(64), elem_size_val))
            idx_offset.flags = ['nuw']
            src_bytes = self.builder.gep(
                data_bytes,
                [idx_offset],
                inbounds=True,
            )
            dst_bytes = self.builder.gep(
                src_bytes, [ir.Constant(ir.IntType(64), elem_size_val)], inbounds=True
            )

            copy_count = self.builder.sub(size, idx)
            copy_count.flags = ['nsw']
            copy_bytes = self.builder.mul(
                self.builder.zext(copy_count, ir.IntType(64)),
                ir.Constant(ir.IntType(64), elem_size_val),
            )
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            # Store at data[idx]
            store_ptr = self.builder.gep(new_data, [idx], inbounds=True)
            self.builder.store(val, store_ptr)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "isin":
            val = self._codegen(args[0])
            val = self._emit_cast(val, inner_llvm)

            loop_head = self.builder.function.append_basic_block("vec_isin_loop")
            found_bb = self.builder.function.append_basic_block("vec_isin_found")
            not_found_bb = self.builder.function.append_basic_block(
                "vec_isin_not_found"
            )
            merge_bb = self.builder.function.append_basic_block("vec_isin_merge")

            i = self.builder.alloca(ir.IntType(64))
            self.builder.store(ir.Constant(ir.IntType(64), 0), i)

            self.builder.branch(loop_head)

            self.builder.position_at_end(loop_head)
            cur_i = self.builder.load(i)
            in_bounds = self.builder.icmp_unsigned("<", cur_i, size)
            self.builder.cbranch(in_bounds, found_bb, not_found_bb)

            self.builder.position_at_end(found_bb)
            elem_ptr = self.builder.gep(data, [cur_i], inbounds=True)
            elem_val = self.builder.load(elem_ptr)
            if isinstance(inner_llvm, ir.IntType):
                eq = self.builder.icmp_signed("==", elem_val, val)
            elif isinstance(inner_llvm, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                eq = self.builder.fcmp_ordered("==", elem_val, val)
                eq.flags = ['fast']
            elif isinstance(inner_llvm, ir.LiteralStructType):
                # For structs, compare by pointer address
                elem_ptr_int = self.builder.ptrtoint(elem_ptr, ir.IntType(64))
                val_ptr = self.builder.alloca(inner_llvm)
                self.builder.store(val, val_ptr)
                val_ptr_int = self.builder.ptrtoint(val_ptr, ir.IntType(64))
                eq = self.builder.icmp_signed("==", elem_ptr_int, val_ptr_int)
            else:
                # For pointers and other types, compare by pointer address
                elem_ptr_int = self.builder.ptrtoint(elem_ptr, ir.IntType(64))
                val_int = self.builder.ptrtoint(val, ir.IntType(64))
                eq = self.builder.icmp_signed("==", elem_ptr_int, val_int)

            next_i = self.builder.add(cur_i, ir.Constant(ir.IntType(64), 1))
            next_i.flags = ['nuw', 'nsw']
            self.builder.store(next_i, i)

            self.builder.cbranch(eq, merge_bb, loop_head)

            self.builder.position_at_end(not_found_bb)
            self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)
            phi = self.builder.phi(ir.IntType(1), name="isin_result")
            phi.add_incoming(ir.Constant(ir.IntType(1), 1), found_bb)
            phi.add_incoming(ir.Constant(ir.IntType(1), 0), not_found_bb)
            return phi

        elif method == "remove":
            # vec.remove(index) - remove element at index
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx_64 = self.builder.zext(idx, ir.IntType(64))

            # Bounds check: 0 <= idx < size
            idx_nonneg = self.builder.icmp_signed(">=", idx_64, ir.Constant(ir.IntType(64), 0))
            idx_in_bounds = self.builder.icmp_unsigned("<", idx_64, size)
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Vector remove index out of bounds.\n"
            )

            # Compute element size at runtime (same as pushf/popf)
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            # memmove data[idx..size-2] = data[idx+1..size-1]
            data_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            dst_offset = self.builder.mul(idx_64, elem_size)
            dst_offset.flags = ['nuw']
            dst_bytes = self.builder.gep(data_bytes, [dst_offset], inbounds=True)
            src_bytes = self.builder.gep(dst_bytes, [elem_size], inbounds=True)

            copy_count = self.builder.sub(size, idx_64)
            copy_count.flags = ['nsw']
            copy_count = self.builder.sub(copy_count, ir.Constant(ir.IntType(64), 1))
            copy_bytes = self.builder.mul(copy_count, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']
            self._update_vec_struct(vec_ptr, data, new_size, cap)
            return None

        elif method == "extend":
            # Expect T[] (slice) {i64 len, T* ptr}
            old_target = self.current_target_type
            self.current_target_type = f"{inner_type_name}[]"
            arr_val = self._codegen(args[0])
            self.current_target_type = old_target

            if not (
                isinstance(arr_val.type, ir.LiteralStructType)
                and len(arr_val.type.elements) == 2
            ):
                # If it's a pointer *T, we can't know the length.
                # However, if it's a fixed-size array pointer, it might be different.
                # For now, require a slice.
                raise LeashError(
                    "Vector.extend requires a slice or array (length + pointer)",
                    node=args[0],
                )

            arr_len = self.builder.extract_value(arr_val, 0)
            arr_ptr = self.builder.extract_value(arr_val, 1)

            needed_size = self.builder.add(size, arr_len)
            new_data, new_cap = self._vector_ensure_capacity(
                vec_ptr, data, size, cap, needed_size, inner_llvm
            )

            dst_ptr = self.builder.gep(new_data, [size], inbounds=True)
            dst_bytes = self.builder.bitcast(dst_ptr, ir.IntType(8).as_pointer())
            src_bytes = self.builder.bitcast(arr_ptr, ir.IntType(8).as_pointer())

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(arr_len, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])
            self._update_vec_struct(vec_ptr, new_data, needed_size, new_cap)
            return None

        elif method == "extendv":
            # Expect vec<T> {T* data, i64 size, i64 cap}
            old_target = self.current_target_type
            self.current_target_type = f"vec<{inner_type_name}>"
            other_vec_val = self._codegen(args[0])
            self.current_target_type = old_target

            other_data = self.builder.extract_value(other_vec_val, 0)
            other_size = self.builder.extract_value(other_vec_val, 1)

            needed_size = self.builder.add(size, other_size)
            new_data, new_cap = self._vector_ensure_capacity(
                vec_ptr, data, size, cap, needed_size, inner_llvm
            )

            dst_ptr = self.builder.gep(new_data, [size], inbounds=True)
            dst_bytes = self.builder.bitcast(dst_ptr, ir.IntType(8).as_pointer())
            src_bytes = self.builder.bitcast(other_data, ir.IntType(8).as_pointer())

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            copy_bytes = self.builder.mul(other_size, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])
            self._update_vec_struct(vec_ptr, new_data, needed_size, new_cap)
            return None

        elif method == "insertv":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx_64 = self.builder.zext(idx, ir.IntType(64))

            old_target = self.current_target_type
            self.current_target_type = f"vec<{inner_type_name}>"
            other_vec_val = self._codegen(args[1])
            self.current_target_type = old_target

            other_data = self.builder.extract_value(other_vec_val, 0)
            other_size = self.builder.extract_value(other_vec_val, 1)

            needed_size = self.builder.add(size, other_size)
            new_data, new_cap = self._vector_ensure_capacity(
                vec_ptr, data, size, cap, needed_size, inner_llvm
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            # memmove data[idx+other_size..size+other_size-1] = data[idx..size-1]
            data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            idx_offset = self.builder.mul(idx_64, elem_size)
            idx_offset.flags = ['nuw']
            src_b = self.builder.gep(data_bytes, [idx_offset], inbounds=True)
            other_bytes = self.builder.mul(other_size, elem_size)
            other_bytes.flags = ['nuw']
            dst_offset = self.builder.add(
                idx_offset, other_bytes
            )
            dst_b = self.builder.gep(data_bytes, [dst_offset], inbounds=True)

            copy_count = self.builder.sub(size, idx_64)
            copy_count.flags = ['nsw']
            copy_bytes = self.builder.mul(copy_count, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            # memmove data[idx..idx+other_size-1] = other_data[0..other_size-1]
            dst_b = src_b
            src_b = self.builder.bitcast(other_data, ir.IntType(8).as_pointer())
            copy_bytes = self.builder.mul(other_size, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            new_size = self.builder.add(size, other_size)
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        elif method == "inserta":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx_64 = self.builder.zext(idx, ir.IntType(64))

            old_target = self.current_target_type
            self.current_target_type = f"{inner_type_name}[]"
            arr_val = self._codegen(args[1])
            self.current_target_type = old_target

            if not (
                isinstance(arr_val.type, ir.LiteralStructType)
                and len(arr_val.type.elements) == 2
            ):
                raise LeashError(
                    "Vector.inserta requires a slice or array (length + pointer)",
                    node=args[1],
                )

            arr_len = self.builder.extract_value(arr_val, 0)
            arr_ptr = self.builder.extract_value(arr_val, 1)

            needed_size = self.builder.add(size, arr_len)
            new_data, new_cap = self._vector_ensure_capacity(
                vec_ptr, data, size, cap, needed_size, inner_llvm
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            # memmove data[idx+arr_len..size+arr_len-1] = data[idx..size-1]
            data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            idx_offset = self.builder.mul(idx_64, elem_size)
            idx_offset.flags = ['nuw']
            src_b = self.builder.gep(data_bytes, [idx_offset], inbounds=True)
            arr_bytes = self.builder.mul(arr_len, elem_size)
            arr_bytes.flags = ['nuw']
            dst_offset = self.builder.add(
                idx_offset, arr_bytes
            )
            dst_b = self.builder.gep(data_bytes, [dst_offset], inbounds=True)

            copy_count = self.builder.sub(size, idx_64)
            copy_count.flags = ['nsw']
            copy_bytes = self.builder.mul(copy_count, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            # memmove data[idx..idx+arr_len-1] = arr_ptr[0..arr_len-1]
            dst_b = src_b
            src_b = self.builder.bitcast(arr_ptr, ir.IntType(8).as_pointer())
            copy_bytes = self.builder.mul(arr_len, elem_size)
            copy_bytes.flags = ['nuw']

            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            new_size = self.builder.add(size, arr_len)
            self._update_vec_struct(vec_ptr, new_data, new_size, new_cap)
            return None

        raise LeashError(
            f"Vector method '{method}' not fully implemented yet", node=vec_ptr
        )

    def _update_matrix_struct(self, mat_ptr, data, size, cap):
        struct_val = self.builder.load(mat_ptr)
        struct_val = self.builder.insert_value(struct_val, data, 0)
        struct_val = self.builder.insert_value(struct_val, size, 1)
        struct_val = self.builder.insert_value(struct_val, cap, 2)
        self.builder.store(struct_val, mat_ptr)

    def _codegen_matrix_method(self, mat_ptr, mat_type_name, method, args):
        inner_type_name = mat_type_name[7:-1]
        inner_llvm = self._get_llvm_type(inner_type_name)

        struct_val = self.builder.load(mat_ptr)
        data = self.builder.extract_value(struct_val, 0)
        size = self.builder.extract_value(struct_val, 1)
        cap = self.builder.extract_value(struct_val, 2)

        if method == "pushb":
            if args:
                val = self._codegen(args[0])
                val = self._emit_cast(val, inner_llvm)
            else:
                val = self._emit_default_value(inner_type_name)

            new_data, new_cap = self._vector_check_capacity(
                mat_ptr, data, size, cap, inner_llvm
            )

            store_ptr = self.builder.gep(new_data, [size], inbounds=True)
            self.builder.store(val, store_ptr)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_matrix_struct(mat_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popb":
            is_nonempty = self.builder.icmp_unsigned(
                ">", size, ir.Constant(ir.IntType(64), 0)
            )
            self._emit_runtime_check(
                is_nonempty, "Runtime error: popb called on empty matrix.\n"
            )
            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']
            res_ptr = self.builder.gep(data, [new_size], inbounds=True)
            res_val = self.builder.load(res_ptr)
            self._update_matrix_struct(mat_ptr, data, new_size, cap)
            return res_val

        elif method == "size":
            return self.builder.trunc(size, ir.IntType(32))

        elif method == "shape":
            vec_type = self._get_llvm_type("vec<int>")
            result_vec = ir.Constant(vec_type, ir.Undefined)
            shape_data_ptr = self.builder.alloca(ir.IntType(64).as_pointer(), name="shape_data")
            self.builder.store(ir.Constant(ir.IntType(64).as_pointer(), None), shape_data_ptr)
            result_vec = self.builder.insert_value(result_vec, self.builder.load(shape_data_ptr), 0)
            result_vec = self.builder.insert_value(result_vec, ir.Constant(ir.IntType(64), 1), 1)
            result_vec = self.builder.insert_value(result_vec, ir.Constant(ir.IntType(64), 1), 2)
            return result_vec

        elif method == "get":
            if len(args) == 1:
                idx = self._codegen(args[0])
                idx = self._emit_cast(idx, ir.IntType(32))
                idx64 = self.builder.sext(idx, ir.IntType(64))
                is_negative = self.builder.icmp_signed(
                    "<", idx64, ir.Constant(ir.IntType(64), 0)
                )
                wrapped = self.builder.add(idx64, size)
                idx64 = self.builder.select(is_negative, wrapped, idx64)
                idx_nonneg = self.builder.icmp_signed(
                    ">=", idx64, ir.Constant(ir.IntType(64), 0)
                )
                idx_in_bounds = self.builder.icmp_unsigned("<", idx64, size)
                in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
                self._emit_runtime_check(
                    in_bounds, "Runtime error: Matrix index out of bounds in get().\n"
                )
                ptr = self.builder.gep(data, [idx64], inbounds=True)
                return self.builder.load(ptr)
            else:
                raise LeashError(
                    f"Matrix method 'get' with multiple indices not yet supported",
                    node=mat_ptr
                )

        elif method == "set":
            if len(args) >= 2:
                idx = self._codegen(args[0])
                idx = self._emit_cast(idx, ir.IntType(32))
                idx64 = self.builder.sext(idx, ir.IntType(64))
                is_negative = self.builder.icmp_signed(
                    "<", idx64, ir.Constant(ir.IntType(64), 0)
                )
                wrapped = self.builder.add(idx64, size)
                idx64 = self.builder.select(is_negative, wrapped, idx64)
                idx_nonneg = self.builder.icmp_signed(
                    ">=", idx64, ir.Constant(ir.IntType(64), 0)
                )
                idx_in_bounds = self.builder.icmp_unsigned("<", idx64, size)
                in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
                self._emit_runtime_check(
                    in_bounds, "Runtime error: Matrix index out of bounds in set().\n"
                )
                val = self._codegen(args[-1])
                val = self._emit_cast(val, inner_llvm)
                ptr = self.builder.gep(data, [idx64], inbounds=True)
                self.builder.store(val, ptr)
                return None
            else:
                raise LeashError(
                    f"Matrix method 'set' with multiple indices not yet supported",
                    node=mat_ptr
                )

        elif method == "clear":
            self._update_matrix_struct(
                mat_ptr, data, ir.Constant(ir.IntType(64), 0), cap
            )
            return None

        elif method == "pushf":
            if args:
                val = self._codegen(args[0])
                val = self._emit_cast(val, inner_llvm)
            else:
                val = self._emit_default_value(inner_type_name)

            new_data, new_cap = self._vector_check_capacity(
                mat_ptr, data, size, cap, inner_llvm
            )

            old_data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            new_dst_bytes = self.builder.gep(
                old_data_bytes,
                [ir.Constant(ir.IntType(64), elem_size)],
                inbounds=True,
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(size, ir.IntType(64)), elem_size
            )
            self.builder.call(self.memmove, [new_dst_bytes, old_data_bytes, copy_bytes])
            self.builder.store(val, new_data)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_matrix_struct(mat_ptr, new_data, new_size, new_cap)
            return None

        elif method == "popf":
            is_nonempty = self.builder.icmp_unsigned(
                ">", size, ir.Constant(ir.IntType(64), 0)
            )
            self._emit_runtime_check(
                is_nonempty, "Runtime error: popf called on empty matrix.\n"
            )
            res_val = self.builder.load(data)

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']
            dst_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )
            src_bytes = self.builder.gep(
                dst_bytes, [ir.Constant(ir.IntType(64), elem_size)], inbounds=True
            )
            copy_bytes = self.builder.mul(
                self.builder.zext(new_size, ir.IntType(64)), elem_size
            )
            self.builder.call(self.memmove, [dst_bytes, src_bytes, copy_bytes])

            self._update_matrix_struct(mat_ptr, data, new_size, cap)
            return res_val

        elif method == "insert":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx64 = self.builder.zext(idx, ir.IntType(64))
            idx_nonneg = self.builder.icmp_signed(
                ">=", idx64, ir.Constant(ir.IntType(64), 0)
            )
            idx_in_bounds = self.builder.icmp_unsigned(
                "<=", idx64, size
            )
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Matrix insert index out of bounds.\n"
            )

            if len(args) >= 2:
                val = self._codegen(args[1])
                val = self._emit_cast(val, inner_llvm)
            else:
                val = self._emit_default_value(inner_type_name)

            new_data, new_cap = self._vector_check_capacity(
                mat_ptr, data, size, cap, inner_llvm
            )

            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            data_bytes = self.builder.bitcast(new_data, ir.IntType(8).as_pointer())
            idx_offset = self.builder.mul(idx64, elem_size)
            idx_offset.flags = ['nuw']
            src_b = self.builder.gep(
                data_bytes, [idx_offset], inbounds=True
            )
            dst_b = self.builder.gep(
                src_bytes, [ir.Constant(ir.IntType(64), elem_size)], inbounds=True
            )

            copy_count = self.builder.sub(size, idx64)
            copy_count.flags = ['nsw']
            copy_bytes = self.builder.mul(copy_count, elem_size)
            copy_bytes.flags = ['nuw']
            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            self.builder.store(val, src_b)

            new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw']
            self._update_matrix_struct(mat_ptr, new_data, new_size, new_cap)
            return None

        elif method == "remove":
            idx = self._codegen(args[0])
            idx = self._emit_cast(idx, ir.IntType(32))
            idx64 = self.builder.sext(idx, ir.IntType(64))
            is_negative = self.builder.icmp_signed(
                "<", idx64, ir.Constant(ir.IntType(64), 0)
            )
            wrapped = self.builder.add(idx64, size)
            idx64 = self.builder.select(is_negative, wrapped, idx64)
            idx_nonneg = self.builder.icmp_signed(
                ">=", idx64, ir.Constant(ir.IntType(64), 0)
            )
            idx_in_bounds = self.builder.icmp_unsigned("<", idx64, size)
            in_bounds = self.builder.and_(idx_nonneg, idx_in_bounds)
            self._emit_runtime_check(
                in_bounds, "Runtime error: Matrix remove index out of bounds.\n"
            )

            new_size = self.builder.sub(size, ir.Constant(ir.IntType(64), 1))
            new_size.flags = ['nuw', 'nsw']
            dummy_ptr = ir.Constant(inner_llvm.as_pointer(), None)
            elem_size = self.builder.ptrtoint(
                self.builder.gep(dummy_ptr, [ir.Constant(ir.IntType(32), 1)]),
                ir.IntType(64),
            )

            data_bytes = self.builder.bitcast(data, ir.IntType(8).as_pointer())
            remove_offset = self.builder.mul(
                self.builder.add(idx64, ir.Constant(ir.IntType(64), 1)), elem_size
            )
            remove_offset.flags = ['nuw']
            src_b = self.builder.gep(
                data_bytes, [remove_offset], inbounds=True
            )
            idx_bytes = self.builder.mul(idx64, elem_size)
            idx_bytes.flags = ['nuw']
            dst_b = self.builder.gep(
                data_bytes,
                [idx_bytes],
                inbounds=True,
            )
            copy_count = self.builder.sub(new_size, idx64)
            copy_bytes = self.builder.mul(copy_count, elem_size)
            copy_bytes.flags = ['nuw']
            self.builder.call(self.memmove, [dst_b, src_b, copy_bytes])

            self._update_matrix_struct(mat_ptr, data, new_size, cap)
            return None

        elif method == "isin":
            search_val = self._codegen(args[0])
            search_val = self._emit_cast(search_val, inner_llvm)
            found_ptr = self.builder.alloca(ir.IntType(1), name="mat_isin_found")
            self.builder.store(ir.Constant(ir.IntType(1), 0), found_ptr)

            loop_cond_bb = self.builder.function.append_basic_block("mat_isin_cond")
            loop_body_bb = self.builder.function.append_basic_block("mat_isin_body")
            loop_done_bb = self.builder.function.append_basic_block("mat_isin_done")

            i_ptr = self.builder.alloca(ir.IntType(64), name="mat_isin_i")
            self.builder.store(ir.Constant(ir.IntType(64), 0), i_ptr)
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(loop_cond_bb)
            i_val = self.builder.load(i_ptr)
            i_in_bounds = self.builder.icmp_unsigned("<", i_val, size)
            found_flag = self.builder.load(found_ptr)
            not_found_yet = self.builder.icmp_unsigned("==", found_flag, ir.Constant(ir.IntType(1), 0))
            continue_search = self.builder.and_(i_in_bounds, not_found_yet)
            self.builder.cbranch(continue_search, loop_body_bb, loop_done_bb)

            self.builder.position_at_end(loop_body_bb)
            elem_ptr = self.builder.gep(data, [i_val], inbounds=True)
            elem_val = self.builder.load(elem_ptr)
            is_float_elem = isinstance(inner_llvm, (ir.HalfType, ir.FloatType, ir.DoubleType))
            if is_float_elem:
                match = self.builder.fcmp_ordered("==", elem_val, search_val)
                match.flags = ['fast']
            else:
                match = self.builder.icmp_signed("==", elem_val, search_val)

            with self.builder.if_then(match):
                self.builder.store(ir.Constant(ir.IntType(1), 1), found_ptr)
            next_i = self.builder.add(i_val, ir.Constant(ir.IntType(64), 1))
            self.builder.store(next_i, i_ptr)
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(loop_done_bb)
            return self.builder.load(found_ptr)

        raise LeashError(
            f"Matrix method '{method}' not fully implemented yet", node=mat_ptr
        )

    def _codegen_hash_method(self, hash_ptr, hash_type_name, method, args, extra_data=None):
        """Handle hash table methods."""
        inner = hash_type_name[5:-1]
        parts = inner.split(", ")
        if len(parts) == 2:
            key_type, value_type = parts
        else:
            key_type, value_type = "string", "void"

        value_llvm = self._get_llvm_type(value_type)
        key_llvm = self._get_llvm_type(key_type)

        hash_val = self.builder.load(hash_ptr)
        size = self.builder.extract_value(hash_val, 0)
        cap = self.builder.extract_value(hash_val, 1)
        
        key_ptrs = []
        value_ptrs = []
        if extra_data:
            key_ptrs, value_ptrs = extra_data
        elif hasattr(hash_val, 'hash_key_ptrs'):
            key_ptrs = getattr(hash_val, 'hash_key_ptrs', [])
            value_ptrs = getattr(hash_val, 'hash_value_ptrs', [])

        if method == "size":
            return self.builder.trunc(size, ir.IntType(32))

        elif method == "keys":
            vec_type = self._get_llvm_type(f"vec<{key_type}>")
            result_vec = ir.Constant(vec_type, ir.Undefined)
            data_ptr = self.builder.alloca(key_llvm.as_pointer(), name="keys_data")
            self.builder.store(ir.Constant(key_llvm.as_pointer(), None), data_ptr)
            result_vec = self.builder.insert_value(result_vec, self.builder.load(data_ptr), 0)
            result_vec = self.builder.insert_value(result_vec, size, 1)
            result_vec = self.builder.insert_value(result_vec, cap, 2)
            return result_vec

        elif method == "values":
            vec_type = self._get_llvm_type(f"vec<{value_type}>")
            result_vec = ir.Constant(vec_type, ir.Undefined)
            data_ptr = self.builder.alloca(value_llvm.as_pointer(), name="values_data")
            self.builder.store(ir.Constant(value_llvm.as_pointer(), None), data_ptr)
            result_vec = self.builder.insert_value(result_vec, self.builder.load(data_ptr), 0)
            result_vec = self.builder.insert_value(result_vec, size, 1)
            result_vec = self.builder.insert_value(result_vec, cap, 2)
            return result_vec

        elif method == "getKey":
            search_val = self._codegen(args[0])
            
            result_ptr = self.builder.alloca(key_llvm, name="getKey_result")
            self.builder.store(ir.Constant(key_llvm, None), result_ptr)
            
            found_ptr = self.builder.alloca(ir.IntType(1), name="getKey_found")
            self.builder.store(ir.Constant(ir.IntType(1), 0), found_ptr)
            
            if key_ptrs and value_ptrs:
                for k_ptr, v_ptr in zip(key_ptrs, value_ptrs):
                    stored_value = self.builder.load(v_ptr)
                    if value_type == "string":
                        cmp_result = self.builder.call(self.strcmp, [search_val, stored_value])
                        value_match = self.builder.icmp_signed("==", cmp_result, ir.Constant(ir.IntType(32), 0))
                    else:
                        value_match = self.builder.icmp_signed("==", search_val, stored_value)
                    
                    with self.builder.if_then(value_match):
                        stored_key = self.builder.load(k_ptr)
                        self.builder.store(stored_key, result_ptr)
                        self.builder.store(ir.Constant(ir.IntType(1), 1), found_ptr)
            
            if not self.in_unsafe_func:
                was_found = self.builder.load(found_ptr)
                self._emit_runtime_check(
                    was_found, "Runtime error: Hash value not found in getKey().\n"
                )
            
            return self.builder.load(result_ptr)

        elif method == "isin":
            if args:
                search_key = self._codegen(args[0])
                
                result_val = ir.Constant(ir.IntType(1), 0)
                
                if key_ptrs:
                    for k_ptr in key_ptrs:
                        stored_key = self.builder.load(k_ptr)
                        if key_type == "string":
                            cmp_result = self.builder.call(self.strcmp, [search_key, stored_key])
                            key_match = self.builder.icmp_signed("==", cmp_result, ir.Constant(ir.IntType(32), 0))
                        else:
                            key_match = self.builder.icmp_signed("==", search_key, stored_key)
                        
                        with self.builder.if_then(key_match):
                            result_val = ir.Constant(ir.IntType(1), 1)
                
                return result_val
            return ir.Constant(ir.IntType(1), 0)

        elif method == "delete":
            return None

        elif method == "push":
            if len(args) == 2:
                key_val = self._codegen(args[0])
                value_val = self._codegen(args[1])
                key_val = self._emit_cast(key_val, key_llvm)
                value_val = self._emit_cast(value_val, value_llvm)

                new_key_ptr = self.builder.alloca(key_llvm, name="hash_push_key")
                self.builder.store(key_val, new_key_ptr)
                new_value_ptr = self.builder.alloca(value_llvm, name="hash_push_value")
                self.builder.store(value_val, new_value_ptr)

                key_ptrs.append(new_key_ptr)
                value_ptrs.append(new_value_ptr)

                new_size = self.builder.add(size, ir.Constant(ir.IntType(64), 1))
                new_size.flags = ['nuw']
                hash_val = self.builder.insert_value(hash_val, new_size, 0)
                self.builder.store(hash_val, hash_ptr)
            return None

        raise LeashError(
            f"Hash method '{method}' not implemented yet", node=hash_ptr
        )

    def _codegen_isin_operator(self, left, right, node):
        """Handle the <> operator: value <> array_or_pointer
        Returns true if value is found in the array/pointer range.
        """
        left_leash = self._get_leash_type_name(node.left)
        right_leash = self._get_leash_type_name(node.right)
        resolved_right = self._resolve_type_name(right_leash)

        # Handle array/slice types (e.g., int[10], int[])
        if resolved_right.endswith("]") and "[" in resolved_right:
            base_type = resolved_right.split("[")[0]
            inner_llvm = self._get_llvm_type(base_type)

            # Load array struct: { i64 size, ptr data }
            size = self.builder.extract_value(right, 0)
            data = self.builder.extract_value(right, 1)

            loop_head = self.builder.function.append_basic_block("isin_arr_loop")
            found_bb = self.builder.function.append_basic_block("isin_arr_found")
            not_found_bb = self.builder.function.append_basic_block(
                "isin_arr_not_found"
            )
            merge_bb = self.builder.function.append_basic_block("isin_arr_merge")

            i = self.builder.alloca(ir.IntType(64))
            self.builder.store(ir.Constant(ir.IntType(64), 0), i)

            self.builder.branch(loop_head)

            self.builder.position_at_end(loop_head)
            cur_i = self.builder.load(i)
            in_bounds = self.builder.icmp_unsigned("<", cur_i, size)
            self.builder.cbranch(in_bounds, found_bb, not_found_bb)

            self.builder.position_at_end(found_bb)
            elem_ptr = self.builder.gep(data, [cur_i], inbounds=True)
            elem_val = self.builder.load(elem_ptr)
            if isinstance(inner_llvm, ir.IntType):
                eq = self.builder.icmp_signed("==", elem_val, left)
            elif isinstance(inner_llvm, (ir.FloatType, ir.DoubleType)):
                eq = self.builder.fcmp_ordered("==", elem_val, left)
                eq.flags = ['fast']
            elif isinstance(
                inner_llvm, ir.PointerType
            ) and inner_llvm.pointee == ir.IntType(8):
                cmp = self.builder.call(self.strcmp, [elem_val, left])
                eq = self.builder.icmp_signed("==", cmp, ir.Constant(ir.IntType(32), 0))
            else:
                eq = self.builder.icmp_signed(
                    "==",
                    self.builder.ptrtoint(elem_val, ir.IntType(64)),
                    self.builder.ptrtoint(left, ir.IntType(64)),
                )

            next_i = self.builder.add(cur_i, ir.Constant(ir.IntType(64), 1))
            next_i.flags = ['nuw', 'nsw']
            self.builder.store(next_i, i)

            self.builder.cbranch(eq, merge_bb, loop_head)

            self.builder.position_at_end(not_found_bb)
            self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)
            phi = self.builder.phi(ir.IntType(1), name="isin_result")
            phi.add_incoming(ir.Constant(ir.IntType(1), 1), found_bb)
            phi.add_incoming(ir.Constant(ir.IntType(1), 0), not_found_bb)
            return phi

        # Handle pointer types (e.g., *int)
        if resolved_right.startswith("*"):
            inner_type = resolved_right[1:]
            inner_llvm = self._get_llvm_type(inner_type)

            # For pointers, we need a size - this is a low-level operation
            # We'll need to pass the size as context or use a sentinel
            # For now, this requires the pointer to be part of an array struct
            raise LeashError(
                "The '<>' operator on raw pointers requires a size context. "
                "Use it with array types instead.",
                line=getattr(node, "line", None),
                col=getattr(node, "col", None),
            )

        raise LeashError(
            f"The '<>' operator is not supported for type '{resolved_right}'",
            line=getattr(node, "line", None),
            col=getattr(node, "col", None),
        )

    def _codegen_file_method(self, file_ptr, method, args):
        """Handle File instance methods."""
        # Load the FILE* pointer
        file_handle = self.builder.load(file_ptr)

        # Null check: if file_handle is null, the file was never opened or already closed
        self._emit_null_pointer_check(
            file_handle,
            "Runtime error: File operation on a null file handle. Make sure File.open() succeeded and the file is not already closed.\n",
        )

        if method == "read":
            # read() -> string: Read entire file content
            # First, seek to end to get file size
            self.builder.call(
                self.fseek,
                [
                    file_handle,
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(32), 2),
                ],
            )  # SEEK_END = 2
            file_size = self.builder.call(self.ftell, [file_handle])
            # Rewind to beginning
            self.builder.call(self.frewind, [file_handle])

            # Allocate buffer for file content + null terminator
            buffer_size = self.builder.add(file_size, ir.Constant(ir.IntType(64), 1))
            buffer = self.builder.call(self.malloc, [buffer_size])
            self._track_alloc(buffer)

            # Read the file
            bytes_read = self.builder.call(
                self.fread,
                [buffer, ir.Constant(ir.IntType(64), 1), file_size, file_handle],
            )
            # Null terminate
            null_pos = self.builder.gep(buffer, [bytes_read], inbounds=True)
            self.builder.store(ir.Constant(ir.IntType(8), 0), null_pos)

            return buffer

        elif method == "write":
            # write(text string) -> int: Write string to file
            text_val = self._codegen(args[0])
            # Get string length
            str_len = self.builder.call(self.strlen, [text_val])
            # Write to file
            bytes_written = self.builder.call(
                self.fwrite,
                [text_val, ir.Constant(ir.IntType(64), 1), str_len, file_handle],
            )
            # Return 0 on success (compare bytes_written == str_len)
            success = self.builder.icmp_unsigned("==", bytes_written, str_len)
            return self.builder.select(
                success, ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), -1)
            )

        elif method == "close":
            # close() -> int: Close the file
            result = self.builder.call(self.fclose, [file_handle])
            # Set FILE* to null after closing
            self.builder.store(ir.Constant(ir.IntType(8).as_pointer(), None), file_ptr)
            return self.builder.trunc(result, ir.IntType(32))

        elif method == "writeb":
            # writeb(btext char[]) -> int: Write bytes to file
            # char[] is a slice: {i64 len, i8* ptr}
            bytes_val = self._codegen(args[0])
            slice_len = self.builder.extract_value(bytes_val, 0)
            slice_ptr = self.builder.extract_value(bytes_val, 1)
            bytes_written = self.builder.call(
                self.fwrite,
                [slice_ptr, ir.Constant(ir.IntType(64), 1), slice_len, file_handle],
            )
            success = self.builder.icmp_unsigned("==", bytes_written, slice_len)
            return self.builder.select(
                success, ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), -1)
            )

        elif method == "readb":
            # readb() -> char[]: Read bytes as char array
            # First, seek to end to get file size
            self.builder.call(
                self.fseek,
                [
                    file_handle,
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(32), 2),
                ],
            )
            file_size = self.builder.call(self.ftell, [file_handle])
            self.builder.call(self.frewind, [file_handle])

            # Allocate buffer
            buffer = self.builder.call(self.malloc, [file_size])
            self._track_alloc(buffer)

            # Read the file
            bytes_read = self.builder.call(
                self.fread,
                [buffer, ir.Constant(ir.IntType(64), 1), file_size, file_handle],
            )

            # Return as char[] slice
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(slice_val, bytes_read, 0)
            slice_val = self.builder.insert_value(slice_val, buffer, 1)
            return slice_val

        elif method == "readln":
            # readln() -> string: Read a line from file (strips trailing newline)
            # Allocate a buffer for reading
            buffer_size = 4096
            buffer = self.builder.call(
                self.malloc, [ir.Constant(ir.IntType(64), buffer_size)]
            )
            self._track_alloc(buffer)

            # Read a line using fgets
            result = self.builder.call(
                self.fgets,
                [buffer, ir.Constant(ir.IntType(32), buffer_size), file_handle],
            )

            # Check if we got a line (result != null)
            is_null = self.builder.icmp_unsigned(
                "==", result, ir.Constant(ir.IntType(8).as_pointer(), None)
            )

            # If null, return empty string
            empty_str = self._emit_const_str("")

            # Strip trailing newline (\n) and carriage return (\r) if present
            # Find the newline character
            newline_ptr = self.builder.call(
                self.strstr, [buffer, self._emit_const_str("\n")]
            )
            has_newline = self.builder.icmp_unsigned(
                "!=", newline_ptr, ir.Constant(ir.IntType(8).as_pointer(), None)
            )
            # If newline found, replace with null terminator
            with self.builder.if_then(has_newline):
                self.builder.store(ir.Constant(ir.IntType(8), 0), newline_ptr)

            # Also check for carriage return (for Windows line endings)
            cr_ptr = self.builder.call(
                self.strstr, [buffer, self._emit_const_str("\r")]
            )
            has_cr = self.builder.icmp_unsigned(
                "!=", cr_ptr, ir.Constant(ir.IntType(8).as_pointer(), None)
            )
            with self.builder.if_then(has_cr):
                self.builder.store(ir.Constant(ir.IntType(8), 0), cr_ptr)

            return self.builder.select(is_null, empty_str, buffer)

        elif method == "readlnb":
            # readlnb() -> char[]: Read a line as bytes (strips trailing newline)
            buffer_size = 4096
            buffer = self.builder.call(
                self.malloc, [ir.Constant(ir.IntType(64), buffer_size)]
            )
            self._track_alloc(buffer)

            result = self.builder.call(
                self.fgets,
                [buffer, ir.Constant(ir.IntType(32), buffer_size), file_handle],
            )

            is_null = self.builder.icmp_unsigned(
                "==", result, ir.Constant(ir.IntType(8).as_pointer(), None)
            )

            # Strip trailing newline (\n) if present
            newline_ptr = self.builder.call(
                self.strstr, [buffer, self._emit_const_str("\n")]
            )
            has_newline = self.builder.icmp_unsigned(
                "!=", newline_ptr, ir.Constant(ir.IntType(8).as_pointer(), None)
            )
            with self.builder.if_then(has_newline):
                self.builder.store(ir.Constant(ir.IntType(8), 0), newline_ptr)

            # Also strip carriage return (\r) for Windows line endings
            cr_ptr = self.builder.call(
                self.strstr, [buffer, self._emit_const_str("\r")]
            )
            has_cr = self.builder.icmp_unsigned(
                "!=", cr_ptr, ir.Constant(ir.IntType(8).as_pointer(), None)
            )
            with self.builder.if_then(has_cr):
                self.builder.store(ir.Constant(ir.IntType(8), 0), cr_ptr)

            # Calculate string length after stripping
            str_len = self.builder.call(self.strlen, [buffer])

            # Return as char[] slice
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            # If null, return empty slice
            zero_len = ir.Constant(ir.IntType(64), 0)
            final_len = self.builder.select(is_null, zero_len, str_len)
            empty_buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 1)])
            self.builder.store(ir.Constant(ir.IntType(8), 0), empty_buf)
            final_buf = self.builder.select(is_null, empty_buf, buffer)
            slice_val = self.builder.insert_value(slice_val, final_len, 0)
            slice_val = self.builder.insert_value(slice_val, final_buf, 1)
            return slice_val

        elif method == "replace":
            # replace(oldstr, newstr) -> int: Replace first occurrence
            # This is complex - we need to read the file, do the replacement, and write it back
            old_str = self._codegen(args[0])
            new_str = self._codegen(args[1])
            return self._file_replace(
                file_ptr, file_handle, old_str, new_str, replace_all=False
            )

        elif method == "replaceall":
            # replaceall(oldstr, newstr) -> int: Replace all occurrences
            old_str = self._codegen(args[0])
            new_str = self._codegen(args[1])
            return self._file_replace(
                file_ptr, file_handle, old_str, new_str, replace_all=True
            )

        elif method == "rewind":
            # rewind() -> void: Reset file position to beginning
            # Use fseek(file, 0, SEEK_SET) instead of rewind() for safety
            self.builder.call(
                self.fseek,
                [
                    file_handle,
                    ir.Constant(ir.IntType(64), 0),
                    ir.Constant(ir.IntType(32), 0),  # SEEK_SET = 0
                ],
            )
            return None

        raise LeashError(f"File method '{method}' not implemented", line=getattr(node, "line", None), col=getattr(node, "col", None))

    def _codegen_file_static_method(self, method, args):
        """Handle File static methods: open, rename, delete."""
        if method == "open":
            # open(filename string, mode string) -> File
            filename = self._codegen(args[0])
            mode = self._codegen(args[1])

            # Call fopen
            file_handle = self.builder.call(self.fopen, [filename, mode])

            # Check if file was opened successfully
            is_null = self.builder.icmp_unsigned(
                "==", file_handle, ir.Constant(ir.IntType(8).as_pointer(), None)
            )

            # Return the FILE* pointer (or null if failed)
            # The File type is just an i8* (FILE*)
            return file_handle

        elif method == "rename":
            # rename(oldname string, newname string) -> int
            oldname = self._codegen(args[0])
            newname = self._codegen(args[1])
            result = self.builder.call(self.rename_fn, [oldname, newname])
            return self.builder.trunc(result, ir.IntType(32))

        elif method == "delete":
            # delete(filename string) -> int
            filename = self._codegen(args[0])
            result = self.builder.call(self.remove_fn, [filename])
            return self.builder.trunc(result, ir.IntType(32))

        raise LeashError(f"File static method '{method}' not implemented", line=getattr(node, "line", None), col=getattr(node, "col", None))

    def _file_replace(self, file_ptr, file_handle, old_str, new_str, replace_all):
        """Implement file replace/replaceall functionality."""
        # Read entire file
        self.builder.call(
            self.fseek,
            [
                file_handle,
                ir.Constant(ir.IntType(64), 0),
                ir.Constant(ir.IntType(32), 2),
            ],
        )
        file_size = self.builder.call(self.ftell, [file_handle])
        self.builder.call(self.frewind, [file_handle])

        # Allocate buffer for file content + null terminator
        buffer_size = self.builder.add(file_size, ir.Constant(ir.IntType(64), 1))
        buffer = self.builder.call(self.malloc, [buffer_size])
        self._track_alloc(buffer)

        # Read file into buffer
        self.builder.call(
            self.fread, [buffer, ir.Constant(ir.IntType(64), 1), file_size, file_handle]
        )
        null_pos = self.builder.gep(buffer, [file_size], inbounds=True)
        self.builder.store(ir.Constant(ir.IntType(8), 0), null_pos)

        # Get lengths
        old_len = self.builder.call(self.strlen, [old_str])
        new_len = self.builder.call(self.strlen, [new_str])

        # Allocate result buffer (worst case: all characters replaced could be larger)
        # We need enough space for the worst case where every character is replaced
        # Estimate: original size * (new_len / old_len) + extra buffer
        result_size = self.builder.add(
            self.builder.mul(file_size, ir.Constant(ir.IntType(64), 4)),
            ir.Constant(ir.IntType(64), 4096),
        )
        result = self.builder.call(self.malloc, [result_size])
        self._track_alloc(result)

        if replace_all:
            # Replace all occurrences
            count_ptr = self.builder.alloca(ir.IntType(32), name="replace_count")
            self.builder.store(ir.Constant(ir.IntType(32), 0), count_ptr)

            # Current positions
            src_ptr = self.builder.alloca(ir.IntType(8).as_pointer(), name="src_ptr")
            dst_ptr = self.builder.alloca(ir.IntType(8).as_pointer(), name="dst_ptr")
            self.builder.store(buffer, src_ptr)
            self.builder.store(result, dst_ptr)

            loop_cond_bb = self.builder.function.append_basic_block("replaceall_cond")
            loop_check_bb = self.builder.function.append_basic_block("replaceall_check")
            loop_copy_bb = self.builder.function.append_basic_block("replaceall_copy")
            loop_no_match_bb = self.builder.function.append_basic_block(
                "replaceall_no_match"
            )
            loop_merge_bb = self.builder.function.append_basic_block("replaceall_merge")

            self.builder.branch(loop_cond_bb)

            # Main loop condition: check if we're at end of string
            self.builder.position_at_end(loop_cond_bb)
            current_src = self.builder.load(src_ptr)
            current_char = self.builder.load(current_src)
            is_end = self.builder.icmp_unsigned(
                "==", current_char, ir.Constant(ir.IntType(8), 0)
            )
            self.builder.cbranch(is_end, loop_merge_bb, loop_check_bb)

            # Check if old_str matches at current position
            self.builder.position_at_end(loop_check_bb)
            current_src = self.builder.load(src_ptr)
            found_ptr = self.builder.call(self.strstr, [current_src, old_str])
            is_at_start = self.builder.icmp_unsigned("==", found_ptr, current_src)
            self.builder.cbranch(is_at_start, loop_copy_bb, loop_no_match_bb)

            # Match found: copy new_str instead of old_str
            self.builder.position_at_end(loop_copy_bb)
            curr_count = self.builder.load(count_ptr)
            self.builder.store(
                self.builder.add(curr_count, ir.Constant(ir.IntType(32), 1)), count_ptr
            )
            current_dst = self.builder.load(dst_ptr)
            self.builder.call(self.strcpy, [current_dst, new_str])
            # Advance src past old_str and dst past new_str
            current_src = self.builder.load(src_ptr)
            new_src = self.builder.gep(current_src, [old_len], inbounds=True)
            new_dst = self.builder.gep(current_dst, [new_len], inbounds=True)
            self.builder.store(new_src, src_ptr)
            self.builder.store(new_dst, dst_ptr)
            self.builder.branch(loop_cond_bb)

            # No match at current position: copy one character and advance by 1
            self.builder.position_at_end(loop_no_match_bb)
            current_src = self.builder.load(src_ptr)
            current_dst = self.builder.load(dst_ptr)
            current_char = self.builder.load(current_src)
            self.builder.store(current_char, current_dst)
            one = ir.Constant(ir.IntType(64), 1)
            self.builder.store(
                self.builder.gep(current_src, [one], inbounds=True), src_ptr
            )
            self.builder.store(
                self.builder.gep(current_dst, [one], inbounds=True), dst_ptr
            )
            self.builder.branch(loop_cond_bb)

            # End of string: copy null terminator
            self.builder.position_at_end(loop_merge_bb)
            final_dst = self.builder.load(dst_ptr)
            final_src = self.builder.load(src_ptr)
            self.builder.call(self.strcpy, [final_dst, final_src])

            count = self.builder.load(count_ptr)

            # Write result back to file
            self.builder.call(self.frewind, [file_handle])
            result_len = self.builder.call(self.strlen, [result])
            self.builder.call(
                self.fwrite,
                [result, ir.Constant(ir.IntType(64), 1), result_len, file_handle],
            )
            # Truncate file
            self._truncate_file(file_handle, result_len)
            self.builder.call(self.frewind, [file_handle])

            return count
        else:
            # Replace first occurrence
            found_ptr = self.builder.call(self.strstr, [buffer, old_str])
            is_found = self.builder.icmp_unsigned(
                "!=", found_ptr, ir.Constant(ir.IntType(8).as_pointer(), None)
            )

            found_bb = self.builder.function.append_basic_block("replace_found")
            not_found_bb = self.builder.function.append_basic_block("replace_not_found")
            merge_bb = self.builder.function.append_basic_block("replace_merge")

            self.builder.cbranch(is_found, found_bb, not_found_bb)

            self.builder.position_at_end(found_bb)
            # Calculate prefix length
            prefix_len = self.builder.sub(
                self.builder.ptrtoint(found_ptr, ir.IntType(64)),
                self.builder.ptrtoint(buffer, ir.IntType(64)),
            )
            # Copy prefix to result
            self.builder.call(self.strncpy, [result, buffer, prefix_len])
            # Null terminate after prefix
            prefix_end = self.builder.gep(result, [prefix_len], inbounds=True)
            self.builder.store(ir.Constant(ir.IntType(8), 0), prefix_end)
            # Append new_str
            dest_ptr = self.builder.gep(result, [prefix_len], inbounds=True)
            self.builder.call(self.strcpy, [dest_ptr, new_str])
            # Append suffix
            suffix_src = self.builder.gep(found_ptr, [old_len], inbounds=True)
            suffix_dest = self.builder.gep(
                result, [self.builder.add(prefix_len, new_len)], inbounds=True
            )
            self.builder.call(self.strcpy, [suffix_dest, suffix_src])
            self.builder.branch(merge_bb)

            self.builder.position_at_end(not_found_bb)
            # No replacement needed, copy original
            self.builder.call(self.strcpy, [result, buffer])
            self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)
            # Write result back to file
            self.builder.call(self.frewind, [file_handle])
            result_len = self.builder.call(self.strlen, [result])
            self.builder.call(
                self.fwrite,
                [result, ir.Constant(ir.IntType(64), 1), result_len, file_handle],
            )
            # Truncate file using fileno + ftruncate
            self._truncate_file(file_handle, result_len)
            self.builder.call(self.frewind, [file_handle])

            return self.builder.select(
                is_found, ir.Constant(ir.IntType(32), 1), ir.Constant(ir.IntType(32), 0)
            )

    def _truncate_file(self, file_handle, size):
        """Truncate a file to the given size using fileno and ftruncate."""
        # Get file descriptor from FILE*
        fd = self.builder.call(self.fileno_fn, [file_handle])
        # Truncate to size
        self.builder.call(self.ftruncate_fn, [fd, size])

    def _codegen_GenericCall(self, node):
        """Handle generic function calls like add<int>(10, 20)."""
        # Generate the mangled function name
        type_args_str = "_".join(
            t.replace("<", "_").replace(">", "_").replace(",", "_").replace(" ", "")
            for t in node.type_args
        )
        mangled_name = f"{node.name}_{type_args_str}"

        # Check if the function exists in the symbol table
        func = self.func_symtab.get(mangled_name)
        if not func:
            raise LeashError(
                f"Call to undefined generic function: '{node.name}' with types {node.type_args}",
                node=node
            )

        # Generate arguments
        args = []
        for i, arg_expr in enumerate(node.args):
            if i < len(func.args):
                target_llvm = func.args[i].type
                v = self._codegen(arg_expr)
                v = self._emit_cast(v, target_llvm)
                args.append(v)
            else:
                args.append(self._codegen(arg_expr))

        return self.builder.call(func, args)

    def _codegen_Call(self, node):
        if node.name == "get":
            return self._emit_get_input(node)

        if node.name == "cstr":
            arg_val = self._codegen(node.args[0])
            # arg_val is i8*
            length = self.builder.call(self.strlen, [arg_val])
            slice_type = ir.LiteralStructType(
                [ir.IntType(64), ir.IntType(8).as_pointer()]
            )
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(slice_val, length, 0)
            slice_val = self.builder.insert_value(slice_val, arg_val, 1)
            return slice_val

        if node.name == "lstr":
            arg_val = self._codegen(node.args[0])
            # arg_val is {i32, i8*} slice
            length = self.builder.zext(
                self.builder.extract_value(arg_val, 0), ir.IntType(64)
            )
            ptr = self.builder.extract_value(arg_val, 1)
            length_plus_1 = self.builder.add(length, ir.Constant(ir.IntType(64), 1))
            new_str = self.builder.call(self.malloc, [length_plus_1])
            self._track_alloc(new_str)
            self.builder.call(self.strncpy, [new_str, ptr, length])
            null_ptr = self.builder.gep(new_str, [length], inbounds=True)
            self.builder.store(ir.Constant(ir.IntType(8), 0), null_ptr)
            return new_str

        if node.name == "tostring":
            arg_val = self._codegen(node.args[0])
            # format based on type
            # allocate 64 bytes for the result string
            buf = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 64)])
            self._track_alloc(buf)

            if isinstance(arg_val.type, ir.IntType):
                fmt = self._emit_const_str("%lld")
                # cast if needed
                if arg_val.type.width < 64:
                    arg_val = self.builder.sext(arg_val, ir.IntType(64))
                self.builder.call(self.sprintf, [buf, fmt, arg_val])
            elif isinstance(arg_val.type, (ir.FloatType, ir.DoubleType, ir.HalfType)):
                fmt = self._emit_const_str("%f")
                if not isinstance(arg_val.type, ir.DoubleType):
                    arg_val = self.builder.fpext(arg_val, ir.DoubleType())
                self.builder.call(self.sprintf, [buf, fmt, arg_val])
            else:
                return arg_val
            return buf

        if node.name == "rand":
            # rand(min, max) - returns random int in [min, max]
            # Uses rand() % (max - min + 1) + min
            min_val = self._codegen(node.args[0])
            max_val = self._codegen(node.args[1])
            # Cast to i32 for calculation (rand returns i32)
            if isinstance(min_val.type, ir.IntType) and min_val.type.width != 32:
                min_val = self.builder.trunc(min_val, ir.IntType(32))
            if isinstance(max_val.type, ir.IntType) and max_val.type.width != 32:
                max_val = self.builder.trunc(max_val, ir.IntType(32))
            # range = max - min + 1
            range_val = self.builder.sub(max_val, min_val)
            range_val = self.builder.add(range_val, ir.Constant(ir.IntType(32), 1))
            # rand() % range + min
            rand_val = self.builder.call(self.rand, [])
            rand_val = self.builder.srem(rand_val, range_val)
            result = self.builder.add(rand_val, min_val)
            return result

        if node.name == "randf":
            # randf(min, max) - returns random float in [min, max]
            # Uses (double)rand() / RAND_MAX * (max - min) + min
            min_val = self._codegen(node.args[0])
            max_val = self._codegen(node.args[1])
            # Cast to double
            if isinstance(min_val.type, ir.IntType):
                min_val = self.builder.sitofp(min_val, ir.DoubleType())
            elif isinstance(min_val.type, (ir.HalfType, ir.FloatType)):
                min_val = self.builder.fpext(min_val, ir.DoubleType())
            if isinstance(max_val.type, ir.IntType):
                max_val = self.builder.sitofp(max_val, ir.DoubleType())
            elif isinstance(max_val.type, (ir.HalfType, ir.FloatType)):
                max_val = self.builder.fpext(max_val, ir.DoubleType())
            # (double)rand() / RAND_MAX
            rand_val = self.builder.call(self.rand, [])
            rand_dbl = self.builder.sitofp(rand_val, ir.DoubleType())
            rand_max = ir.Constant(ir.DoubleType(), 2147483647.0)  # RAND_MAX
            fraction = self.builder.fdiv(rand_dbl, rand_max)
            # fraction * (max - min) + min
            range_val = self.builder.fsub(max_val, min_val)
            result = self.builder.fmul(fraction, range_val)
            result = self.builder.fadd(result, min_val)
            return result

        if node.name == "seed":
            # seed(value) - sets the random seed
            seed_val = self._codegen(node.args[0])
            if isinstance(seed_val.type, ir.IntType) and seed_val.type.width != 32:
                seed_val = self.builder.trunc(seed_val, ir.IntType(32))
            self.builder.call(self.srand, [seed_val])
            self.seed_called = True
            return None  # void

        if node.name == "choose":
            # choose(str1, str2, ...) - randomly selects one of the string arguments
            # Use a chain of if-else to select the right string
            num_args = len(node.args)

            # Generate random index
            rand_val = self.builder.call(self.rand, [])
            idx = self.builder.srem(rand_val, ir.Constant(ir.IntType(32), num_args))

            # Create blocks
            entry_bb = self.builder.block
            merge_bb = self.builder.function.append_basic_block("choose_merge")

            # Build if-else chain, collecting incoming values for phi
            incoming = []
            current_bb = entry_bb
            for i in range(num_args):
                if i < num_args - 1:
                    then_bb = self.builder.function.append_basic_block(
                        f"choose_then_{i}"
                    )
                    else_bb = self.builder.function.append_basic_block(
                        f"choose_else_{i}"
                    )

                    self.builder.position_at_end(current_bb)
                    cond = self.builder.icmp_signed(
                        "==", idx, ir.Constant(ir.IntType(32), i)
                    )
                    self.builder.cbranch(cond, then_bb, else_bb)

                    self.builder.position_at_end(then_bb)
                    str_val = self._codegen(node.args[i])
                    incoming.append((str_val, then_bb))
                    self.builder.branch(merge_bb)

                    current_bb = else_bb
                else:
                    # Last case - position at current block (else_bb from previous iteration)
                    self.builder.position_at_end(current_bb)
                    str_val = self._codegen(node.args[i])
                    incoming.append((str_val, current_bb))
                    self.builder.branch(merge_bb)

            # Now position at merge_bb and create phi
            self.builder.position_at_end(merge_bb)
            phi = self.builder.phi(ir.IntType(8).as_pointer(), name="choose_result")
            for val, bb in incoming:
                phi.add_incoming(val, bb)
            return phi

        if node.name == "wait":
            # wait(seconds) - pause execution for given number of seconds (float or int)
            if len(node.args) != 1:
                self._error(
                    f"Function 'wait' expects 1 argument (seconds), but got {len(node.args)}",
                    node=node,
                )
            wait_val = self._codegen(node.args[0])
            # Convert to double for microseconds calculation
            if isinstance(wait_val.type, ir.IntType):
                wait_dbl = self.builder.sitofp(wait_val, ir.DoubleType())
            elif isinstance(wait_val.type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                wait_dbl = (
                    wait_val
                    if isinstance(wait_val.type, ir.DoubleType)
                    else self.builder.fpext(wait_val, ir.DoubleType())
                )
            else:
                wait_dbl = wait_val  # shouldn't happen due to typecheck
            # microseconds = seconds * 1_000_000
            one_million = ir.Constant(ir.DoubleType(), 1000000.0)
            usec_dbl = self.builder.fmul(wait_dbl, one_million)
            usec_i64 = self.builder.fptoui(usec_dbl, ir.IntType(64))
            # Call usleep
            return self.builder.call(self.usleep, [usec_i64])

        if node.name == "timepass":
            # timepass() - returns elapsed time in seconds since program start as float
            if len(node.args) != 0:
                self._error(
                    f"Function 'timepass' expects 0 arguments, but got {len(node.args)}",
                    node=node,
                )
            # Allocate timespec for current time
            timespec_ty = ir.LiteralStructType([ir.IntType(64), ir.IntType(64)])
            cur_ts = self.builder.alloca(timespec_ty, name="cur_ts")
            self.builder.call(self.clock_gettime, [self.CLOCK_MONOTONIC, cur_ts])
            # Load start_time global (stored at program start)
            start_time_val = self.builder.load(self.start_time_gv)
            start_sec = self.builder.extract_value(start_time_val, 0)
            start_nsec = self.builder.extract_value(start_time_val, 1)
            # Load current time fields
            cur_sec = self.builder.load(
                self.builder.gep(
                    cur_ts,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                )
            )
            cur_nsec = self.builder.load(
                self.builder.gep(
                    cur_ts,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                )
            )
            # Compute differences
            sec_diff = self.builder.sub(cur_sec, start_sec)
            nsec_diff = self.builder.sub(cur_nsec, start_nsec)
            # If nsec_diff < 0, borrow 1 second
            nsec_is_neg = self.builder.icmp_signed(
                "<", nsec_diff, ir.Constant(ir.IntType(64), 0)
            )
            entry_bb = self.builder.block
            then_bb = self.builder.function.append_basic_block("timepass_adj")
            else_bb = self.builder.function.append_basic_block("timepass_no_adj")
            merge_bb = self.builder.function.append_basic_block("timepass_merge")
            self.builder.cbranch(nsec_is_neg, then_bb, else_bb)
            # Then: nsec += 1e9, sec -= 1
            self.builder.position_at_end(then_bb)
            one_billion = ir.Constant(ir.IntType(64), 1000000000)
            adj_nsec = self.builder.add(nsec_diff, one_billion)
            adj_sec = self.builder.sub(sec_diff, ir.Constant(ir.IntType(64), 1))
            self.builder.branch(merge_bb)
            # Else: no adjustment
            self.builder.position_at_end(else_bb)
            self.builder.branch(merge_bb)
            # Merge
            self.builder.position_at_end(merge_bb)
            phi_sec = self.builder.phi(ir.IntType(64), name="elapsed_sec")
            phi_nsec = self.builder.phi(ir.IntType(64), name="elapsed_nsec")
            phi_sec.add_incoming(adj_sec, then_bb)
            phi_sec.add_incoming(sec_diff, else_bb)
            phi_nsec.add_incoming(adj_nsec, then_bb)
            phi_nsec.add_incoming(nsec_diff, else_bb)
            # Convert to double: sec + nsec / 1e9
            sec_dbl = self.builder.sitofp(phi_sec, ir.DoubleType())
            nsec_dbl = self.builder.sitofp(phi_nsec, ir.DoubleType())
            one_billion_dbl = ir.Constant(ir.DoubleType(), 1000000000.0)
            nsec_sec = self.builder.fdiv(nsec_dbl, one_billion_dbl)
            result = self.builder.fadd(sec_dbl, nsec_sec)
            return result

        if node.name == "normescape":
            if len(node.args) != 1:
                self._error(
                    f"Function 'normescape' expects 1 argument, but got {len(node.args)}",
                    node=node,
                )
            input_str = self._codegen(node.args[0])

            i64 = ir.IntType(64)
            i8 = ir.IntType(8)
            i8_ptr = ir.PointerType(i8)

            null_ptr = ir.Constant(i8_ptr, None)
            is_null = self.builder.icmp_unsigned("==", input_str, null_ptr)

            null_bb = self.builder.function.append_basic_block("nescape_null")
            nonnull_bb = self.builder.function.append_basic_block("nescape_nonnull")
            merge_bb = self.builder.function.append_basic_block("nescape_merge")

            self.builder.cbranch(is_null, null_bb, nonnull_bb)

            self.builder.position_at_end(null_bb)
            null_buf = self.builder.call(self.malloc, [ir.Constant(i64, 1)])
            self.builder.store(ir.Constant(i8, 0), null_buf)
            self.builder.branch(merge_bb)

            self.builder.position_at_end(nonnull_bb)
            input_len = self.builder.call(self.strlen, [input_str])
            alloc_size = self.builder.add(input_len, ir.Constant(i64, 1))
            output_buf = self.builder.call(self.malloc, [alloc_size])

            i_ptr = self.builder.alloca(i64)
            j_ptr = self.builder.alloca(i64)
            self.builder.store(ir.Constant(i64, 0), i_ptr)
            self.builder.store(ir.Constant(i64, 0), j_ptr)

            loop_cond_bb = self.builder.function.append_basic_block("nescape_cond")
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(loop_cond_bb)
            i = self.builder.load(i_ptr)
            cond = self.builder.icmp_unsigned("<", i, input_len)
            loop_body_bb = self.builder.function.append_basic_block("nescape_body")
            loop_end_bb = self.builder.function.append_basic_block("nescape_loop_end")
            self.builder.cbranch(cond, loop_body_bb, loop_end_bb)

            self.builder.position_at_end(loop_body_bb)
            i = self.builder.load(i_ptr)
            ch = self.builder.load(self.builder.gep(input_str, [i], inbounds=True))
            is_escape = self.builder.icmp_unsigned("==", ch, ir.Constant(i8, ord("\\")))

            copy_char_bb = self.builder.function.append_basic_block("nescape_copy")
            handle_escape_bb = self.builder.function.append_basic_block("nescape_handle")
            self.builder.cbranch(is_escape, handle_escape_bb, copy_char_bb)

            self.builder.position_at_end(copy_char_bb)
            i = self.builder.load(i_ptr)
            j = self.builder.load(j_ptr)
            ch = self.builder.load(self.builder.gep(input_str, [i], inbounds=True))
            self.builder.store(ch, self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(self.builder.add(i, ir.Constant(i64, 1)), i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(handle_escape_bb)
            i = self.builder.load(i_ptr)
            j = self.builder.load(j_ptr)
            next_i = self.builder.add(i, ir.Constant(i64, 1))
            has_next = self.builder.icmp_unsigned("<", next_i, input_len)

            escape_copy_bb = self.builder.function.append_basic_block("nescape_esc_copy")
            escape_conv_bb = self.builder.function.append_basic_block("nescape_esc_conv")
            self.builder.cbranch(has_next, escape_conv_bb, escape_copy_bb)

            self.builder.position_at_end(escape_copy_bb)
            self.builder.store(ir.Constant(i8, ord("\\")), self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(self.builder.add(i, ir.Constant(i64, 1)), i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(escape_conv_bb)
            next_ch = self.builder.load(self.builder.gep(input_str, [next_i], inbounds=True))

            # Check for hex escape \x
            is_hex = self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("x")))
            hex_esc_bb = self.builder.function.append_basic_block("nescape_hex_esc")
            norm_esc_bb = self.builder.function.append_basic_block("nescape_norm_esc")
            self.builder.cbranch(is_hex, hex_esc_bb, norm_esc_bb)

            # Normal escape mapping (single-char escapes)
            self.builder.position_at_end(norm_esc_bb)
            conv_char = next_ch  # default: copy the escape char itself
            # \n -> newline
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("n"))),
                ir.Constant(i8, 0x0A), conv_char,
            )
            # \t -> tab
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("t"))),
                ir.Constant(i8, 0x09), conv_char,
            )
            # \r -> carriage return
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("r"))),
                ir.Constant(i8, 0x0D), conv_char,
            )
            # \0 -> null
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("0"))),
                ir.Constant(i8, 0x00), conv_char,
            )
            # \a -> bell/alert
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("a"))),
                ir.Constant(i8, 0x07), conv_char,
            )
            # \b -> backspace
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("b"))),
                ir.Constant(i8, 0x08), conv_char,
            )
            # \f -> form feed
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("f"))),
                ir.Constant(i8, 0x0C), conv_char,
            )
            # \v -> vertical tab
            conv_char = self.builder.select(
                self.builder.icmp_unsigned("==", next_ch, ir.Constant(i8, ord("v"))),
                ir.Constant(i8, 0x0B), conv_char,
            )
            i_plus_2 = self.builder.add(i, ir.Constant(i64, 2))
            self.builder.store(conv_char, self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(i_plus_2, i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            # Hex escape \xNN
            self.builder.position_at_end(hex_esc_bb)
            i_plus_2 = self.builder.add(i, ir.Constant(i64, 2))
            has_d1 = self.builder.icmp_unsigned("<", i_plus_2, input_len)

            hex_try_d1_bb = self.builder.function.append_basic_block("nescape_hex_try_d1")
            hex_inv_bb = self.builder.function.append_basic_block("nescape_hex_inv")
            self.builder.cbranch(has_d1, hex_try_d1_bb, hex_inv_bb)

            # No hex digit after \x: output 'x', skip \x
            self.builder.position_at_end(hex_inv_bb)
            self.builder.store(ir.Constant(i8, ord("x")), self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(i_plus_2, i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            # Try first hex digit
            self.builder.position_at_end(hex_try_d1_bb)
            ch1 = self.builder.load(self.builder.gep(input_str, [i_plus_2], inbounds=True))

            ge_0 = self.builder.icmp_unsigned(">=", ch1, ir.Constant(i8, ord("0")))
            le_9 = self.builder.icmp_unsigned("<=", ch1, ir.Constant(i8, ord("9")))
            is_dec = self.builder.and_(ge_0, le_9)
            d1_dec = self.builder.sub(ch1, ir.Constant(i8, ord("0")))
            d1 = self.builder.select(is_dec, d1_dec, ir.Constant(i8, 255))

            ge_a = self.builder.icmp_unsigned(">=", ch1, ir.Constant(i8, ord("a")))
            le_f = self.builder.icmp_unsigned("<=", ch1, ir.Constant(i8, ord("f")))
            is_lower = self.builder.and_(ge_a, le_f)
            d1_lower = self.builder.sub(ch1, ir.Constant(i8, ord("a") - 10))
            d1 = self.builder.select(is_lower, d1_lower, d1)

            ge_A = self.builder.icmp_unsigned(">=", ch1, ir.Constant(i8, ord("A")))
            le_F = self.builder.icmp_unsigned("<=", ch1, ir.Constant(i8, ord("F")))
            is_upper = self.builder.and_(ge_A, le_F)
            d1_upper = self.builder.sub(ch1, ir.Constant(i8, ord("A") - 10))
            d1 = self.builder.select(is_upper, d1_upper, d1)

            d1_valid = self.builder.icmp_unsigned("<", d1, ir.Constant(i8, 16))

            hex_try_d2_bb = self.builder.function.append_basic_block("nescape_hex_try_d2")
            self.builder.cbranch(d1_valid, hex_try_d2_bb, hex_inv_bb)

            # Try second hex digit
            self.builder.position_at_end(hex_try_d2_bb)
            i_plus_3 = self.builder.add(i, ir.Constant(i64, 3))
            has_d2 = self.builder.icmp_unsigned("<", i_plus_3, input_len)

            hex_1d_bb = self.builder.function.append_basic_block("nescape_hex_1d")
            hex_2d_bb = self.builder.function.append_basic_block("nescape_hex_2d")
            self.builder.cbranch(has_d2, hex_2d_bb, hex_1d_bb)

            # Only one hex digit: skip \x + 1 digit (total +3)
            self.builder.position_at_end(hex_1d_bb)
            self.builder.store(d1, self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(i_plus_3, i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            # Two hex digits
            self.builder.position_at_end(hex_2d_bb)
            ch2 = self.builder.load(self.builder.gep(input_str, [i_plus_3], inbounds=True))

            ge_0 = self.builder.icmp_unsigned(">=", ch2, ir.Constant(i8, ord("0")))
            le_9 = self.builder.icmp_unsigned("<=", ch2, ir.Constant(i8, ord("9")))
            is_dec = self.builder.and_(ge_0, le_9)
            d2 = self.builder.select(is_dec, self.builder.sub(ch2, ir.Constant(i8, ord("0"))), ir.Constant(i8, 255))

            ge_a = self.builder.icmp_unsigned(">=", ch2, ir.Constant(i8, ord("a")))
            le_f = self.builder.icmp_unsigned("<=", ch2, ir.Constant(i8, ord("f")))
            is_lower = self.builder.and_(ge_a, le_f)
            d2 = self.builder.select(is_lower, self.builder.sub(ch2, ir.Constant(i8, ord("a") - 10)), d2)

            ge_A = self.builder.icmp_unsigned(">=", ch2, ir.Constant(i8, ord("A")))
            le_F = self.builder.icmp_unsigned("<=", ch2, ir.Constant(i8, ord("F")))
            is_upper = self.builder.and_(ge_A, le_F)
            d2 = self.builder.select(is_upper, self.builder.sub(ch2, ir.Constant(i8, ord("A") - 10)), d2)

            d2_valid = self.builder.icmp_unsigned("<", d2, ir.Constant(i8, 16))

            hex_store_bb = self.builder.function.append_basic_block("nescape_hex_store")
            self.builder.cbranch(d2_valid, hex_store_bb, hex_1d_bb)

            self.builder.position_at_end(hex_store_bb)
            d1_16 = self.builder.mul(d1, ir.Constant(i8, 16))
            val = self.builder.add(d1_16, d2)
            self.builder.store(val, self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.store(self.builder.add(i, ir.Constant(i64, 4)), i_ptr)
            self.builder.store(self.builder.add(j, ir.Constant(i64, 1)), j_ptr)
            self.builder.branch(loop_cond_bb)

            self.builder.position_at_end(loop_end_bb)
            j = self.builder.load(j_ptr)
            self.builder.store(ir.Constant(i8, 0), self.builder.gep(output_buf, [j], inbounds=True))
            self.builder.branch(merge_bb)

            self.builder.position_at_end(merge_bb)
            phi = self.builder.phi(i8_ptr, "nescape_result")
            phi.add_incoming(null_buf, null_bb)
            phi.add_incoming(output_buf, loop_end_bb)
            return phi

        if node.name == "keyget":
            # keyget() - reads a single key without waiting for Enter
            char_val = self.builder.call(self.keyget, [])
            result = self.builder.trunc(char_val, ir.IntType(8))
            return result

        if node.name == "exit":
            # exit(exit_code) - terminates the program with given exit code
            if len(node.args) != 1:
                self._error(
                    f"Function 'exit' expects 1 argument (exit code), but got {len(node.args)}",
                    node=node,
                )
            exit_val = self._codegen(node.args[0])
            # Convert to i32 if needed
            if isinstance(exit_val.type, ir.IntType) and exit_val.type.width != 32:
                exit_val = self.builder.trunc(exit_val, ir.IntType(32))
            elif isinstance(exit_val.type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                exit_val = self.builder.fptoui(exit_val, ir.IntType(32))
            self.builder.call(self.exit_fn, [exit_val])
            # exit doesn't return - mark as unreachable
            self.builder.unreachable()
            return None

        if node.name == "exec":
            return self._emit_exec(node)

        func = self.func_symtab.get(node.name)
        if not func:
            # Check if it's a function pointer variable being called
            var_info = self.var_symtab.get(node.name)
            if var_info:
                ptr = var_info[0]
                var_type = var_info[1]
                # Resolve type aliases
                resolved_type = self._resolve_type_name(var_type)
                if self._is_function_pointer_type(resolved_type):
                    # It's a function pointer - load it and call through the pointer
                    param_types, return_type = self._get_function_pointer_signature(
                        resolved_type
                    )
                    fn_ptr = self.builder.load(ptr)
                    # Generate arguments
                    args = []
                    for arg_expr in node.args:
                        v = self._codegen(arg_expr)
                        args.append(v)
                    # Call through the function pointer
                    return self.builder.call(fn_ptr, args)

            raise LeashError(f"Call to undefined function: '{node.name}'", node=node)

        # Get function signature to determine arg names and defaults
        func_name = node.name
        func_node = None
        for item in self.program.items:
            if hasattr(item, "name") and item.name == func_name:
                func_node = item
                break

        # Build final args list: positional + kwargs + defaults
        final_args = []
        num_provided = len(node.args)
        provided_kwarg_names = set(node.kwargs.keys())

        # Determine the mapping: position -> arg_name
        arg_mapping = []  # list of (position, arg_name, default_expr)
        if func_node and hasattr(func_node, "args"):
            for pos, (arg_name, arg_type, default_expr) in enumerate(func_node.args):
                arg_mapping.append((pos, arg_name, default_expr))

        # First, fill in all positional args
        for i, arg_expr in enumerate(node.args):
            final_args.append((i, arg_expr))

        # Then, fill in kwargs by finding their positions
        for kw_name, kw_expr in node.kwargs.items():
            found = False
            for pos, arg_name, _ in arg_mapping:
                if arg_name == kw_name:
                    # Replace or add at this position
                    while len(final_args) <= pos:
                        final_args.append(None)
                    final_args[pos] = (pos, kw_expr)
                    found = True
                    break
            if not found:
                raise LeashError(
                    f"Unexpected keyword argument '{kw_name}' for function '{func_name}'",
                    node=node
                )

        # Finally, fill in defaults for missing args
        num_positional = len(node.args)
        for pos, arg_name, default_expr in arg_mapping:
            while len(final_args) <= pos:
                final_args.append(None)
            if final_args[pos] is None:
                if default_expr is not None:
                    final_args[pos] = (pos, default_expr)
                elif pos < num_positional:
                    # This shouldn't happen normally, but just in case
                    raise LeashError(
                        f"Missing argument at position {pos} for function '{func_name}'",
                        node=node
                    )
                else:
                    raise LeashError(
                        f"Missing required argument '{arg_name}' for function '{func_name}'",
                        node=node
                    )

        # Generate LLVM values
        args = []
        for i, arg_entry in enumerate(final_args):
            if arg_entry is None:
                continue
            pos, arg_expr = arg_entry
            if pos < len(func.args):
                target_llvm = func.args[pos].type
                src_type = self._get_leash_type_name(arg_expr)
                resolved_src = self._resolve_type_name(src_type)

                # If target is pointer and source is not a pointer (safe pointer conversion)
                # BUT: skip this for i8* (string) pointers - strings are passed by value
                target_pointee = getattr(target_llvm, "pointee", None)
                is_string_ptr = (
                    isinstance(target_pointee, ir.IntType) and target_pointee.width == 8
                )
                if (
                    isinstance(target_llvm, ir.PointerType)
                    and not is_string_ptr
                    and not (
                        resolved_src.startswith("*") or resolved_src.startswith("&")
                    )
                ):
                    try:
                        v, _, _ = self._codegen_lvalue(arg_expr)
                    except:
                        v = self._codegen(arg_expr)
                else:
                    v = self._codegen(arg_expr)
                v = self._emit_cast(v, target_llvm)
                args.append(v)
            else:
                v = self._codegen(arg_expr)
                args.append(v)

        return self.builder.call(func, args)

    def _codegen_StructInit(self, node):
        is_class = False
        struct_info = self.struct_symtab.get(node.name)
        if not struct_info:
            struct_info = self.class_symtab.get(node.name)
            is_class = True
        if not struct_info:
            raise LeashError(f"Undefined struct or class: '{node.name}'", node=node)
        struct_type = struct_info["type"]

        if is_class:
            # Allocate class instances on the heap (GC_malloc)
            # To get the size: gep(null, 1) and ptrtoint
            ptr_type = struct_type.as_pointer()
            dummy_ptr = ir.Constant(ptr_type, None)
            size_ptr = self.builder.gep(
                dummy_ptr, [ir.Constant(ir.IntType(32), 1)], inbounds=True
            )
            size = self.builder.ptrtoint(size_ptr, ir.IntType(64))

            ptr_void = self.builder.call(self.malloc, [size])
            ptr = self.builder.bitcast(ptr_void, ptr_type)

            # Initialize vtable pointer at index 0
            vtable_global = struct_info.get("vtable_global")
            if vtable_global:
                vtable_ptr = self.builder.bitcast(
                    vtable_global, ir.IntType(8).as_pointer()
                )
                vtable_field_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                )
                self.builder.store(vtable_ptr, vtable_field_ptr)

            # Initialize fields with default values
            if "field_defaults" in struct_info:
                for fname, default_expr in struct_info["field_defaults"].items():
                    idx = struct_info["fields"][fname]
                    field_val = self._codegen(default_expr)
                    # Ensure type match
                    expected_type_str = struct_info["field_types"][fname]
                    expected_llvm_type = self._get_llvm_type(expected_type_str)
                    if field_val.type != expected_llvm_type:
                        field_val = self._emit_cast(field_val, expected_llvm_type)

                    field_ptr = self.builder.gep(
                        ptr,
                        [
                            ir.Constant(ir.IntType(32), 0),
                            ir.Constant(ir.IntType(32), idx),
                        ],
                    )
                    self.builder.store(field_val, field_ptr)

            # Initialize missing fields from kwargs with type-defaults (if not already handled)
            for fname, idx in struct_info["fields"].items():
                # Skip if it has an explicit default value (handled above)
                # or if it's provided in kwargs (handled below)
                if (
                    "field_defaults" in struct_info and fname in struct_info["field_defaults"]
                ) or any(k == fname for k, _ in node.kwargs):
                    continue

                # Skip vtable pointer (handled above)
                if fname == "_vtable" or idx == 0:
                    continue

                ftype = struct_info["field_types"][fname]
                default_val = self._emit_default_value(ftype)
                field_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                )
                self.builder.store(default_val, field_ptr)

            # Initialize other fields
            for key, expr in node.kwargs:
                idx = struct_info["fields"].get(key)
                if idx is None:
                    raise LeashError(f"Class '{node.name}' has no field named '{key}'", node=node)
                field_val = self._codegen(expr)
                # Convert field_val to the expected field type if needed
                expected_type_str = struct_info["field_types"].get(key)
                if expected_type_str:
                    expected_llvm_type = self._get_llvm_type(expected_type_str)
                    if field_val.type != expected_llvm_type:
                        field_val = self._emit_cast(field_val, expected_llvm_type)
                # gep on the instance pointer
                field_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)],
                )
                self.builder.store(field_val, field_ptr)
            return ptr
        else:
            val = ir.Constant(struct_type, None)  # Zero-initialize the struct constant

            # Initialize fields with default values
            if "field_defaults" in struct_info:
                for fname, default_expr in struct_info["field_defaults"].items():
                    # Skip if it's provided in kwargs
                    if any(k == fname for k, _ in node.kwargs):
                        continue
                    idx = struct_info["fields"][fname]
                    field_val = self._codegen(default_expr)
                    # Ensure type match
                    expected_type_str = struct_info["field_types"][fname]
                    expected_llvm_type = self._get_llvm_type(expected_type_str)
                    if field_val.type != expected_llvm_type:
                        field_val = self._emit_cast(field_val, expected_llvm_type)
                    val = self.builder.insert_value(val, field_val, idx)

            # Initialize other fields from kwargs
            for key, expr in node.kwargs:
                idx = struct_info["fields"].get(key)
                if idx is None:
                    raise LeashError(f"Struct '{node.name}' has no member named '{key}'", node=node)
                field_val = self._codegen(expr)
                # Convert field_val to the expected field type if needed
                expected_type_str = struct_info["field_types"].get(key)
                if expected_type_str:
                    expected_llvm_type = self._get_llvm_type(expected_type_str)
                    if field_val.type != expected_llvm_type:
                        field_val = self._emit_cast(field_val, expected_llvm_type)
                val = self.builder.insert_value(val, field_val, idx)
            return val

    def _codegen_Identifier(self, node):
        var_info = self.var_symtab.get(node.name)
        if var_info:
            ptr = var_info[0]
            type_name = var_info[1]
        else:
            ptr, type_name = None, None
        
        if not ptr:
            # Check global variables (including those from @from)
            gv_info = self.global_var_ptrs.get(node.name)
            if gv_info:
                ptr, type_name = gv_info
        if not ptr:
            # Check if it's a function with no arguments
            func = self.func_symtab.get(node.name)
            if func and not func.args:
                return self.builder.call(func, [])

            if self.in_works_block:
                self.works_error_occured = True
                self.works_error_info = f"Undefined variable: '{node.name}'"
                err_ptr = self.builder.alloca(ir.IntType(8).as_pointer())
                err_str = self._emit_const_str(f"Undefined variable: '{node.name}'")
                self.builder.store(err_str, err_ptr)
                return err_ptr
            else:
                raise LeashError(f"Undefined variable: '{node.name}'", node=node)
        val = self.builder.load(ptr)
        resolved = self._resolve_type_name(type_name)
        while resolved.startswith("&"):
            val = self.builder.load(val)
            resolved = resolved[1:]
        return val

    def _codegen_MemberAccess(self, node):
        from .ast_nodes import Identifier

        # Handle thisworker.interrupted
        from .ast_nodes import ThisWorkerExpr
        if isinstance(node.expr, ThisWorkerExpr) and node.member == "interrupted":
            return self._codegen_ThisWorkerExpr(node.expr)

        # Handle static class field access (e.g., idkMath.PI or this.PI in static method)
        from .ast_nodes import ThisExpr
        is_static_base = False
        target_cls = None
        if isinstance(node.expr, Identifier) and node.expr.name in self.class_symtab:
            is_static_base = True
            target_cls = node.expr.name
        elif isinstance(node.expr, ThisExpr) and "this" not in self.var_symtab and self.current_class_name:
            is_static_base = True
            target_cls = self.current_class_name

        if is_static_base:
            cls_info = self.class_symtab[target_cls]
            # Check static fields first
            if "static_fields" in cls_info and node.member in cls_info["static_fields"]:
                static_info = cls_info["static_fields"][node.member]
                static_gv = static_info["global"]
                return self.builder.load(static_gv)
            # Fall back to instance fields (error case)
            if node.member in cls_info["fields"]:
                # Static field access on instance field is an error
                raise LeashError(
                    f"Instance field '{node.member}' cannot be accessed without an instance",
                    node=node
                )
            raise LeashError(
                f"Class '{node.expr.name}' has no field named '{node.member}'",
                node=node
            )

        # Get the leash type name of the base expression
        type_name = self._get_leash_type_name(node.expr)
        resolved = self._resolve_type_name(type_name)

        # 1. Handle Enum .name
        if resolved in self.enum_symtab and node.member == "name":
            enum_info = self.enum_symtab[resolved]
            enum_val = self._codegen(node.expr)
            idx = self._emit_cast(enum_val, ir.IntType(32))
            names_ptr = enum_info["names_arr"]
            member_ptr = self.builder.gep(
                names_ptr, [ir.Constant(ir.IntType(32), 0), idx], inbounds=True
            )
            return self.builder.load(member_ptr)

        # 2. Handle String .size
        val = self._codegen(node.expr)

        is_string = getattr(val.type, "pointee", None) == ir.IntType(8)
        if hasattr(ir, "PointerType") and getattr(ir, "PointerType") is not None:
            is_string = is_string and isinstance(val.type, ir.PointerType)
        elif getattr(val.type, "is_pointer", False):
            is_string = is_string and val.type.is_pointer

        if is_string and node.member == "size":
            length = self.builder.call(self.strlen, [val])
            return self.builder.trunc(length, ir.IntType(32))

        # Handle Array .size
        if (
            resolved
            and resolved.endswith("]")
            and "[" in resolved
            and node.member == "size"
        ):
            length = self.builder.extract_value(val, 0)
            return self.builder.trunc(length, ir.IntType(32))

        # 2b. Handle Vector .size (property access, not method call)
        if (
            resolved
            and resolved.startswith("vec<")
            and resolved.endswith(">")
            and node.member == "size"
        ):
            # Vector layout: { data_ptr, size (i64), capacity (i64) }
            # Extract the size field (index 1)
            size_val = self.builder.extract_value(val, 1)
            return self.builder.trunc(size_val, ir.IntType(32))

        # 2c. Handle Hash .size (property access, not method call)
        if (
            resolved
            and resolved.startswith("hash<")
            and resolved.endswith(">")
            and node.member == "size"
        ):
            # Hash layout: { size (i64), capacity (i64), entries_ptr }
            # Extract the size field (index 0)
            size_val = self.builder.extract_value(val, 0)
            return self.builder.trunc(size_val, ir.IntType(32))

        # 3. Handle Union variants and .cur
        if resolved in self.union_symtab:
            lvalue_result = self._codegen_lvalue(node.expr)
            if len(lvalue_result) == 3:
                ptr, _, _ = lvalue_result
            else:
                ptr, _ = lvalue_result

            union_info = self.union_symtab[resolved]
            data_ptr = self.builder.gep(
                ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
                inbounds=True,
            )

            if node.member == "cur":
                tag_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                )
                tag_val = self.builder.load(tag_ptr)
                return self._union_cur_load(tag_val, data_ptr, union_info)
            elif node.member in union_info["variants"]:
                vdata = union_info["variants"][node.member]
                tag_ptr = self.builder.gep(
                    ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                )
                tag_val = self.builder.load(tag_ptr)
                self._emit_union_tag_check(
                    tag_val, vdata["index"], node.member, resolved
                )
                typed_ptr = self.builder.bitcast(
                    data_ptr, vdata["llvm_type"].as_pointer()
                )
                return self.builder.load(typed_ptr)

        # 4. Standard Struct access or other l-values
        lvalue_result = self._codegen_lvalue(node)
        if len(lvalue_result) == 3:
            ptr, _, _ = lvalue_result
        else:
            ptr, _ = lvalue_result
        return self.builder.load(ptr)

    def _emit_union_tag_check(self, tag_val, expected_idx, member_name, union_name):
        """Emit runtime check: if tag != expected_idx, print error to stderr and exit(1). Skipped in unsafe functions."""
        if self.in_unsafe_func:
            return
        ok_bb = self.builder.function.append_basic_block("union_check_ok")
        fail_bb = self.builder.function.append_basic_block("union_check_fail")

        cmp = self.builder.icmp_signed(
            "==", tag_val, ir.Constant(ir.IntType(64), expected_idx)
        )
        self.builder.cbranch(cmp, ok_bb, fail_bb)

        # Fail path: print error and exit
        self.builder.position_at_end(fail_bb)

        # Build a descriptive error message
        # Find the name of the actual active variant based on the tag
        # We can't do that statically, so we'll print the tag index
        err_msg = f"Runtime error: Attempted to access '.{member_name}' on union '{union_name}', but the active variant is different.\n"
        err_bytes = bytearray(err_msg.encode("utf8") + b"\0")
        c_err = ir.Constant(ir.ArrayType(ir.IntType(8), len(err_bytes)), err_bytes)
        g_err = ir.GlobalVariable(
            self.module, c_err.type, name=self.module.get_unique_name("union_err")
        )
        g_err.linkage = "internal"
        g_err.global_constant = True
        g_err.initializer = c_err
        err_ptr = self.builder.bitcast(g_err, ir.IntType(8).as_pointer())

        # Print error message to stdout using printf
        self.builder.call(self.printf, [err_ptr])
        self.builder.call(self.exit_fn, [ir.Constant(ir.IntType(32), 1)])
        self.builder.unreachable()

        # Continue on the ok path
        self.builder.position_at_end(ok_bb)

    def _emit_runtime_check(self, condition, message):
        """Emit a generic runtime check: if condition is false, print message to stderr and exit(1). Skipped in unsafe functions."""
        if self.in_unsafe_func:
            return
        ok_bb = self.builder.function.append_basic_block("safety_check_ok")
        fail_bb = self.builder.function.append_basic_block("safety_check_fail")

        self.builder.cbranch(condition, ok_bb, fail_bb)

        self.builder.position_at_end(fail_bb)

        err_bytes = bytearray(message.encode("utf8") + b"\0")
        c_err = ir.Constant(ir.ArrayType(ir.IntType(8), len(err_bytes)), err_bytes)
        g_err = ir.GlobalVariable(
            self.module, c_err.type, name=self.module.get_unique_name("safety_err")
        )
        g_err.linkage = "internal"
        g_err.global_constant = True
        g_err.initializer = c_err
        err_ptr = self.builder.bitcast(g_err, ir.IntType(8).as_pointer())

        # Print error message to stdout using printf
        self.builder.call(self.printf, [err_ptr])
        self.builder.call(self.exit_fn, [ir.Constant(ir.IntType(32), 1)])
        self.builder.unreachable()

        self.builder.position_at_end(ok_bb)

    def _emit_division_by_zero_check(self, divisor):
        """Emit a runtime check that divisor is not zero. Skipped in unsafe functions."""
        if self.in_unsafe_func:
            return
        zero = ir.Constant(divisor.type, 0)
        is_nonzero = self.builder.icmp_signed("!=", divisor, zero)
        self._emit_runtime_check(is_nonzero, "Runtime error: Division by zero.\n")

    def _emit_null_pointer_check(self, ptr, message=None):
        """Emit a runtime check that a pointer is not null. Skipped in unsafe functions."""
        if self.in_unsafe_func:
            return
        null = ir.Constant(ptr.type, None)
        is_nonnull = self.builder.icmp_unsigned("!=", ptr, null)
        msg = message or "Runtime error: Null pointer dereference.\n"
        self._emit_runtime_check(is_nonnull, msg)

    def _union_cur_load(self, tag_val, data_ptr, union_info):
        """Load the current value from a union based on the runtime tag."""
        variants = list(union_info["variants"].items())

        if len(variants) == 1:
            vname, vdata = variants[0]
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
            return self.builder.load(typed_ptr)

        # Determine the common type to promote all variants into
        has_float = any(
            isinstance(vd["llvm_type"], (ir.HalfType, ir.FloatType, ir.DoubleType))
            for _, vd in variants
        )
        has_ptr = any(isinstance(vd["llvm_type"], ir.PointerType) for _, vd in variants)

        if has_ptr:
            common_type = ir.IntType(8).as_pointer()
        elif has_float:
            common_type = ir.DoubleType()
        else:
            common_type = ir.IntType(64)

        merge_bb = self.builder.function.append_basic_block("union_cur_merge")

        var_bbs = []
        for vname, vdata in variants:
            bb = self.builder.function.append_basic_block(f"union_cur_{vname}")
            var_bbs.append(bb)

        # Build the if-else chain from current position
        for i, (vname, vdata) in enumerate(variants[:-1]):
            cmp = self.builder.icmp_signed(
                "==", tag_val, ir.Constant(ir.IntType(64), vdata["index"])
            )
            next_check = self.builder.function.append_basic_block(
                f"union_cur_check_{i + 1}"
            )
            self.builder.cbranch(cmp, var_bbs[i], next_check)
            self.builder.position_at_end(next_check)

        # Default: last variant
        self.builder.branch(var_bbs[-1])

        # In each variant BB: load, convert to common type, branch to merge
        incoming = []
        for i, (vname, vdata) in enumerate(variants):
            self.builder.position_at_end(var_bbs[i])
            typed_ptr = self.builder.bitcast(data_ptr, vdata["llvm_type"].as_pointer())
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
        src_is_float = isinstance(src, (ir.HalfType, ir.FloatType, ir.DoubleType))
        src_is_ptr = isinstance(src, ir.PointerType)
        dst_is_int = isinstance(dst, ir.IntType)
        dst_is_float = isinstance(dst, (ir.HalfType, ir.FloatType, ir.DoubleType))
        dst_is_ptr = isinstance(dst, ir.PointerType)

        if src_is_int and dst_is_int:
            if src.width < dst.width:
                return self.builder.sext(val, dst)
            elif src.width > dst.width:
                return self.builder.trunc(val, dst)
        elif src_is_int and dst_is_float:
            return self.builder.sitofp(val, dst)
        elif src_is_float and dst_is_float:
            src_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(src), 64)
            dst_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(type(dst), 64)
            if src_size < dst_size:
                return self.builder.fpext(val, dst)
            elif src_size > dst_size:
                return self.builder.fptrunc(val, dst)
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
        if self.current_target_type:
            target_llvm = self._get_llvm_type(self.current_target_type)
            if isinstance(target_llvm, ir.IntType):
                return ir.Constant(target_llvm, node.value)
        # Use i64 if the value doesn't fit in i32
        if isinstance(node.value, int) and (
            node.value > 2147483647 or node.value < -2147483648
        ):
            return ir.Constant(ir.IntType(64), node.value)
        return ir.Constant(ir.IntType(32), node.value)

    def _codegen_FloatLiteral(self, node):
        if self.current_target_type:
            target_llvm = self._get_llvm_type(self.current_target_type)
            if isinstance(target_llvm, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                return ir.Constant(target_llvm, node.value)
        return ir.Constant(ir.DoubleType(), node.value)

    def _codegen_CharLiteral(self, node):
        return ir.Constant(ir.IntType(8), ord(node.value))

    def _codegen_BoolLiteral(self, node):
        return ir.Constant(ir.IntType(1), 1 if node.value else 0)

    def _codegen_NullLiteral(self, node):
        return ir.Constant(ir.IntType(8).as_pointer(), None)

    def _codegen_FilePathLiteral(self, node):
        if node.name == "_FILEPATH":
            path = node.source_file or ""
        elif node.name == "_FILENAME":
            import os

            path = os.path.basename(node.source_file) if node.source_file else ""
        else:
            path = ""

        s = bytearray(path.encode("utf-8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        global_str = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("filepath")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = c_str
        return self.builder.bitcast(global_str, ir.IntType(8).as_pointer())

    def _codegen_BuiltinVarLiteral(self, node):
        if node.name == "_PLATFORM":
            from .targets import get_native_target

            platform_name = get_native_target().name
        else:
            platform_name = ""

        s = bytearray(platform_name.encode("utf-8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        global_str = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("builtinvar")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = c_str
        return self.builder.bitcast(global_str, ir.IntType(8).as_pointer())

    def _codegen_StringLiteral(self, node):
        s = bytearray(node.value.encode("utf-8") + b"\0")
        c_str = ir.Constant(ir.ArrayType(ir.IntType(8), len(s)), s)
        global_str = ir.GlobalVariable(
            self.module, c_str.type, name=self.module.get_unique_name("str")
        )
        global_str.linkage = "internal"
        global_str.global_constant = True
        global_str.initializer = c_str
        return self.builder.bitcast(global_str, ir.IntType(8).as_pointer())

    def _codegen_ArrayInit(self, node):
        target = self.current_target_type

        # Matrix type initialization: create a matrix struct with heap-allocated data
        if target and target.startswith("matrix<") and target.endswith(">"):
            elem_type_name = target[7:-1]
            elem_type = self._get_llvm_type(elem_type_name)
            old_target = self.current_target_type
            self.current_target_type = elem_type_name
            vals = []
            for e in node.elements:
                v = self._codegen(e)
                v = self._emit_cast(v, elem_type)
                vals.append(v)
            self.current_target_type = old_target

            length = len(vals)
            size_val = ir.Constant(ir.IntType(64), length)
            cap_val = ir.Constant(ir.IntType(64), length)

            elem_size_ptr = self.builder.gep(
                ir.Constant(elem_type.as_pointer(), None),
                [ir.Constant(ir.IntType(32), 1)], inbounds=True
            )
            elem_size = self.builder.ptrtoint(elem_size_ptr, ir.IntType(64))
            total_bytes = self.builder.mul(size_val, elem_size)
            total_bytes.flags = ['nuw']
            data_bytes = self.builder.call(self.aligned_alloc, [total_bytes, ir.Constant(ir.IntType(64), 64)])
            self._track_alloc(data_bytes)
            data_ptr = self.builder.bitcast(data_bytes, elem_type.as_pointer())

            # Optimization: Store using batch memcpy for non-trivial types
            if len(vals) >= 4 and isinstance(elem_type, (ir.IntType, ir.FloatType, ir.HalfType, ir.DoubleType)):
                # For power-of-2 count, use a run of stores that LLVM can auto-vectorize
                pass
            for i, v in enumerate(vals):
                ptr = self.builder.gep(
                    data_ptr,
                    [ir.Constant(ir.IntType(64), i)], inbounds=True
                )
                self.builder.store(v, ptr)

            matrix_type = ir.LiteralStructType([elem_type.as_pointer(), ir.IntType(64), ir.IntType(64)])
            matrix_val = ir.Constant(matrix_type, ir.Undefined)
            matrix_val = self.builder.insert_value(matrix_val, data_ptr, 0)
            matrix_val = self.builder.insert_value(matrix_val, size_val, 1)
            matrix_val = self.builder.insert_value(matrix_val, cap_val, 2)
            return matrix_val

        elem_type = None
        elem_type_name = None
        inferred_from_elements = False

        if target and "[" in target:
            elem_type_name = target.split("[")[0]
            elem_type = self._get_llvm_type(elem_type_name)
            # If elem_type is i32 (default fallback), it means the type wasn't resolved (e.g., generic T)
            # In this case, we'll need to infer from elements
            if elem_type == ir.IntType(32) and elem_type_name not in ("int", "uint"):
                elem_type = None
                inferred_from_elements = True

        old_target = self.current_target_type
        if target and "[" in target:
            base_type = target.split("[")[0]
            # Only set target to base type if it was successfully resolved
            if not inferred_from_elements:
                self.current_target_type = base_type
            else:
                self.current_target_type = None
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
            # Update elem_type_name for slice type creation
            if inferred_from_elements:
                elem_type_name = None

        arr_type = ir.ArrayType(elem_type, length)

        is_const = all(isinstance(v, ir.Constant) for v in vals)
        if is_const:
            c_arr = ir.Constant(arr_type, vals)
            global_arr = ir.GlobalVariable(
                self.module, arr_type, name=self.module.get_unique_name("const_arr")
            )
            global_arr.linkage = "internal"
            global_arr.global_constant = True
            global_arr.initializer = c_arr
            arr_ptr = global_arr
        else:
            arr_ptr = self.builder.alloca(arr_type)
            for i, v in enumerate(vals):
                ptr = self.builder.gep(
                    arr_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)],
                    inbounds=True,
                )
                self.builder.store(v, ptr)

        # Build slice type from the actual element type
        slice_type = ir.LiteralStructType([ir.IntType(64), elem_type.as_pointer()])
        slice_val = ir.Constant(slice_type, ir.Undefined)

        slice_val = self.builder.insert_value(
            slice_val, ir.Constant(ir.IntType(64), length), 0
        )

        elem_ptr = self.builder.bitcast(arr_ptr, elem_type.as_pointer())
        slice_val = self.builder.insert_value(slice_val, elem_ptr, 1)

        return slice_val

    def _codegen_HashInit(self, node):
        target = self.current_target_type
        if target and target.startswith("hash<") and target.endswith(">"):
            inner = target[5:-1]
            parts = inner.split(", ")
            if len(parts) == 2:
                key_type, value_type = parts
            else:
                key_type, value_type = "string", "void"
        else:
            key_type, value_type = "string", "void"

        key_llvm = self._get_llvm_type(key_type)
        value_llvm = self._get_llvm_type(value_type)
        
        num_entries = len(node.entries)
        
        key_ptrs = []
        value_ptrs = []
        
        for i, (key_expr, value_expr) in enumerate(node.entries):
            key_val = self._codegen(key_expr)
            value_val = self._codegen(value_expr)
            
            key_ptr = self.builder.alloca(key_llvm, name=f"hash_key_{i}")
            self.builder.store(key_val, key_ptr)
            key_ptrs.append(key_ptr)
            
            value_ptr = self.builder.alloca(value_llvm, name=f"hash_value_{i}")
            self.builder.store(value_val, value_ptr)
            value_ptrs.append(value_ptr)
        
        entries_ptr = ir.Constant(ir.IntType(8).as_pointer(), None)
        
        size_val = ir.Constant(ir.IntType(64), num_entries)
        cap_val = ir.Constant(ir.IntType(64), num_entries * 2 if num_entries > 0 else 0)
        
        hash_type = self._get_llvm_type(f"hash<{key_type}, {value_type}>")
        hash_val = ir.Constant(hash_type, ir.Undefined)
        hash_val = self.builder.insert_value(hash_val, size_val, 0)
        hash_val = self.builder.insert_value(hash_val, cap_val, 1)
        hash_val = self.builder.insert_value(hash_val, entries_ptr, 2)
        
        hash_val.hash_key_ptrs = key_ptrs
        hash_val.hash_value_ptrs = value_ptrs
        
        return hash_val
        hash_val = self.builder.insert_value(hash_val, size_val, 0)
        hash_val = self.builder.insert_value(hash_val, cap_val, 1)
        hash_val = self.builder.insert_value(hash_val, entries_ptr, 2)
        
        return hash_val

    def _codegen_EnumMemberAccess(self, node):
        enum_info = self.enum_symtab.get(node.enum_name)
        if not enum_info:
            raise LeashError(f"Undefined enum: '{node.enum_name}'", node=node)

        # Check for custom value in custom_values dict
        custom_values = enum_info.get("custom_values", {})
        if node.member_name in custom_values:
            _, llvm_val = custom_values[node.member_name]
            return llvm_val
        
        # Look up the member in member_dict for string values
        member_dict = enum_info.get("member_dict", {})
        if node.member_name in member_dict:
            mtype, value_ptr = member_dict[node.member_name]
            # If custom value exists (string), return it
            if value_ptr is not None:
                return value_ptr
        
        # Otherwise, find the index (for traditional enum access)
        members = enum_info["members"]
        idx = None
        for i, (mname, _, _) in enumerate(members):
            if mname == node.member_name:
                idx = i
                break
        
        if idx is None:
            raise LeashError(
                f"Enum '{node.enum_name}' has no member named '{node.member_name}'",
                node=node
            )

        return ir.Constant(ir.IntType(32), idx)

    def _codegen_IndexAccess(self, node):
        lvalue_result = self._codegen_lvalue(node)
        if len(lvalue_result) == 3:
            ptr, _, _ = lvalue_result
        else:
            ptr, _ = lvalue_result
        return self.builder.load(ptr)

    def _codegen_TypeConvExpr(self, node):
        val = self._codegen(node.expr)
        dst_type = self._get_llvm_type(node.target_type)

        # Check if src is string (i8*)
        src_is_str = (
            isinstance(val.type, ir.PointerType)
            and isinstance(val.type.pointee, ir.IntType)
            and val.type.pointee.width == 8
        )

        if node.name == "toint":
            if src_is_str:
                res64 = self.builder.call(self.atoll, [val])
                return self._emit_cast(res64, dst_type)
            else:
                return self._emit_cast(val, dst_type, is_signed=False)
        elif node.name == "tofloat":
            if src_is_str:
                res64 = self.builder.call(self.atof, [val])
                return self._emit_cast(res64, dst_type)
            else:
                return self._emit_cast(val, dst_type)
        return val

    def _codegen_ByteConvExpr(self, node):
        size_val = self._codegen(node.size_expr)

        if isinstance(size_val, ir.Constant):
            size = size_val.constant
        else:
            size = self.builder.zext(size_val, ir.IntType(64))
            size = self.builder.trunc(size, ir.IntType(32))

        if isinstance(size, int):
            size_int = size
            size_ir = ir.Constant(ir.IntType(64), size)
        else:
            size_int = 4
            size_ir = self.builder.zext(size, ir.IntType(64))

        value_val = self._codegen(node.value_expr)

        if node.name == "inttobytes":
            mem = self.builder.call(self.malloc, [size_ir])
            val_ptr = self.builder.alloca(value_val.type)
            self.builder.store(value_val, val_ptr)
            self.builder.call(
                self.memmove,
                [mem, self.builder.bitcast(val_ptr, ir.IntType(8).as_pointer()), size_ir],
            )
            slice_type = ir.LiteralStructType([ir.IntType(64), ir.IntType(8).as_pointer()])
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(slice_val, ir.Constant(ir.IntType(64), size_int), 0)
            slice_val = self.builder.insert_value(slice_val, mem, 1)
            return slice_val
        elif node.name == "bytestoint":
            ptr = self.builder.extract_value(value_val, 1)
            int_type = ir.IntType(size_int * 8)
            result_ptr = self.builder.alloca(int_type)
            self.builder.call(
                self.memmove,
                [self.builder.bitcast(result_ptr, ir.IntType(8).as_pointer()), ptr, size_ir],
            )
            return self.builder.load(result_ptr)
        elif node.name == "floattobytes":
            mem = self.builder.call(self.malloc, [size_ir])
            val_ptr = self.builder.alloca(value_val.type)
            self.builder.store(value_val, val_ptr)
            self.builder.call(
                self.memmove,
                [mem, self.builder.bitcast(val_ptr, ir.IntType(8).as_pointer()), size_ir],
            )
            slice_type = ir.LiteralStructType([ir.IntType(64), ir.IntType(8).as_pointer()])
            slice_val = ir.Constant(slice_type, ir.Undefined)
            slice_val = self.builder.insert_value(slice_val, ir.Constant(ir.IntType(64), size_int), 0)
            slice_val = self.builder.insert_value(slice_val, mem, 1)
            return slice_val
        elif node.name == "bytestofloat":
            slice_len = self.builder.extract_value(value_val, 0)
            ptr = self.builder.extract_value(value_val, 1)
            result_type = ir.DoubleType()
            result_ptr = self.builder.alloca(result_type)
            self.builder.call(
                self.memmove,
                [self.builder.bitcast(result_ptr, ir.IntType(8).as_pointer()), ptr, size_ir],
            )
            return self.builder.load(result_ptr)
        return value_val

    def _codegen_CastExpr(self, node):
        from .ast_nodes import ThisExpr

        dst_type_name = self._resolve_type_name(node.target_type)
        old_target = self.current_target_type

        # Provide target type context for the inner expression (especially for ArrayInit)
        if dst_type_name.startswith("vec<") and dst_type_name.endswith(">"):
            inner = dst_type_name[4:-1]
            self.current_target_type = f"{inner}[]"
        else:
            self.current_target_type = node.target_type

        val = self._codegen(node.expr)
        self.current_target_type = old_target

        src_type_name = self._get_leash_type_name(node.expr)
        dst_type_name = self._resolve_type_name(node.target_type)

        # Handle class casting (inheritance)
        src_resolved = self._resolve_type_name(src_type_name)
        dst_resolved = self._resolve_type_name(dst_type_name)

        if src_resolved in self.class_symtab and dst_resolved in self.class_symtab:
            # Both are classes - use bitcast for inheritance
            target_type = self._get_llvm_type(node.target_type)
            return self.builder.bitcast(val, target_type)

        # Handle casting from slice (e.g., {i64, ptr}) to vec (e.g., {ptr, i64, i64})
        target_type = self._get_llvm_type(node.target_type)
        src = val.type

        if (
            isinstance(src, ir.LiteralStructType)
            and len(src.elements) == 2
            and isinstance(src.elements[0], ir.IntType)
            and isinstance(src.elements[1], ir.PointerType)
            and dst_resolved.startswith("vec<")
            and dst_resolved.endswith(">")
        ):
            # Convert slice {length, ptr} to vec {ptr, size, cap}
            length = self.builder.extract_value(val, 0)
            data_ptr = self.builder.extract_value(val, 1)

            # Bitcast the data pointer to the expected element pointer type
            expected_ptr_type = target_type.elements[0]
            data_ptr = self.builder.bitcast(data_ptr, expected_ptr_type)

            vec_type = target_type
            vec_val = ir.Constant(vec_type, ir.Undefined)
            vec_val = self.builder.insert_value(vec_val, data_ptr, 0)
            vec_val = self.builder.insert_value(vec_val, length, 1)
            vec_val = self.builder.insert_value(vec_val, length, 2)
            return vec_val

        return self._emit_cast(val, target_type)

    def _codegen_AsExpr(self, node):
        val = self._codegen(node.expr)
        target_type = self._get_llvm_type(node.target_type)
        return self._emit_cast(val, target_type)

    def _codegen_IsExpr(self, node):
        """Generate code for 'is' and 'isnt' expressions."""
        if node.is_type_check:
            # Type check - compare the actual type of the left expression with the right type
            left_type = self._get_leash_type_name(node.left)
            right_type = node.right
            
            # Resolve type aliases for comparison
            left_resolved = self._resolve_type_name(left_type) if left_type else None
            right_resolved = self._resolve_type_name(right_type) if right_type else None
            
            # Compare types (allow loose matching, e.g., "int" matches "int<32>")
            def _types_match(lt, rt):
                if lt == rt:
                    return True
                if lt and rt:
                    # Handle int special cases
                    if lt == "int" and rt.startswith("int"):
                        return True
                    if lt.startswith("int") and rt == "int":
                        return True
                    # Handle uint special cases
                    if lt == "uint" and rt.startswith("uint"):
                        return True
                    if lt.startswith("uint") and rt == "uint":
                        return True
                    # Handle float special cases
                    if lt == "float" and rt.startswith("float"):
                        return True
                    if lt.startswith("float") and rt == "float":
                        return True
                return False
            
            types_match = _types_match(left_resolved, right_resolved)
            result = types_match if node.op == "is" else not types_match
            return ir.Constant(ir.IntType(1), 1 if result else 0)
        else:
            # Value comparison
            left_val = self._codegen(node.left)
            right_val = self._codegen(node.right)

            left_llvm_type = left_val.type
            right_llvm_type = right_val.type

            # Handle integer comparisons
            if isinstance(left_llvm_type, ir.IntType) and isinstance(right_llvm_type, ir.IntType):
                if left_llvm_type.width == 1:  # bool
                    result = self.builder.icmp_unsigned("==", left_val, right_val)
                else:
                    result = self.builder.icmp_signed("==", left_val, right_val)
            # Handle float comparisons
            elif isinstance(left_llvm_type, (ir.HalfType, ir.FloatType, ir.DoubleType)) or \
                 isinstance(right_llvm_type, (ir.HalfType, ir.FloatType, ir.DoubleType)):
                result = self.builder.fcmp_ordered("==", left_val, right_val)
            # Handle pointer comparisons (strings)
            elif isinstance(left_llvm_type, ir.PointerType) and isinstance(right_llvm_type, ir.PointerType):
                left_type_name = self._get_leash_type_name(node.left)
                right_type_name = self._get_leash_type_name(node.right)

                if left_type_name == "string" or right_type_name == "string":
                    # Compare strings using strcmp
                    strcmp_result = self.builder.call(self.strcmp, [left_val, right_val])
                    result = self.builder.icmp_signed("==", strcmp_result, ir.Constant(ir.IntType(32), 0))
                else:
                    result = self.builder.icmp_unsigned("==", left_val, right_val)
            # Handle slice/array/vector comparisons - use memcmp for deep comparison
            elif isinstance(left_llvm_type, ir.LiteralStructType) and \
                 isinstance(right_llvm_type, ir.LiteralStructType) and \
                 len(left_llvm_type.elements) in (2, 3) and \
                 len(right_llvm_type.elements) in (2, 3):
                # Determine if it's a slice {length, pointer} or vector {pointer, capacity, size}
                is_vector = len(left_llvm_type.elements) == 3

                if is_vector:
                    # Vector: {pointer, capacity, size}
                    left_size = self.builder.extract_value(left_val, 2)  # size element
                    right_size = self.builder.extract_value(right_val, 2)
                    left_ptr = self.builder.extract_value(left_val, 0)  # data pointer
                    right_ptr = self.builder.extract_value(right_val, 0)
                    # Get element type from pointer (elements[0] is pointer)
                    elem_type = left_llvm_type.elements[0].pointee
                else:
                    # Slice: {length, pointer}
                    left_size = self.builder.extract_value(left_val, 0)
                    right_size = self.builder.extract_value(right_val, 0)
                    left_ptr = self.builder.extract_value(left_val, 1)
                    right_ptr = self.builder.extract_value(right_val, 1)
                    # Get element type from pointer (elements[1] is pointer)
                    elem_type = left_llvm_type.elements[1].pointee

                # Compare sizes first
                size_eq = self.builder.icmp_signed("==", left_size, right_size)

                # Compare pointers (quick path - same object)
                ptr_eq = self.builder.icmp_unsigned("==", left_ptr, right_ptr)

                # Calculate element size from elem_type
                elem_size = self._get_type_size(elem_type)

                # Calculate total size = size * elem_size
                total_size = self.builder.mul(left_size, ir.Constant(ir.IntType(64), elem_size))
                total_size.flags = ['nuw']

                # Cast pointers to i8* for memcmp
                left_i8ptr = self.builder.bitcast(left_ptr, ir.IntType(8).as_pointer())
                right_i8ptr = self.builder.bitcast(right_ptr, ir.IntType(8).as_pointer())

                # Call memcmp
                memcmp_result = self.builder.call(
                    self.memcmp, [left_i8ptr, right_i8ptr, total_size]
                )
                content_eq = self.builder.icmp_signed("==", memcmp_result, ir.Constant(ir.IntType(32), 0))

                # Result is true if sizes equal and (pointers equal OR contents equal)
                result = self.builder.and_(size_eq, self.builder.or_(ptr_eq, content_eq))
            else:
                # For other types, return false
                result = ir.Constant(ir.IntType(1), 0)

            # Handle 'isnt' (negation)
            if node.op == "isnt":
                result = self.builder.not_(result)

            return result

    def _emit_cast(self, val, target_type, is_signed=True):
        """Cast a value to the target LLVM type. is_signed controls sext vs zext for int widening."""
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

        # Handle null pointer to non-pointer destination (e.g., returning nil for struct type)
        # This can happen when a generic function returns nil for a value type
        if (
            src_is_ptr
            and isinstance(src.pointee, ir.IntType)
            and src.pointee.width == 8
            and not dst_is_ptr
        ):
            # Return undef for the destination type since we can't convert null to a value type
            return ir.Constant(dst, ir.Undefined)
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
                return self.builder.sext(val, dst) if is_signed else self.builder.zext(val, dst)
            return val
        # float -> float (fpext / fptrunc)
        elif src_is_float and dst_is_float:
            src_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(
                type(src), 64
            )
            dst_size = {ir.HalfType: 16, ir.FloatType: 32, ir.DoubleType: 64}.get(
                type(dst), 64
            )
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
            # char (i8) -> string (i8*): allocate 2 bytes, store char + null terminator
            if src.width == 8 and isinstance(dst.pointee, ir.IntType) and dst.pointee.width == 8:
                ptr = self.builder.call(self.malloc, [ir.Constant(ir.IntType(64), 2)])
                self._track_alloc(ptr)
                self.builder.store(val, ptr)
                null_ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(64), 1)])
                self.builder.store(ir.Constant(ir.IntType(8), 0), null_ptr)
                return ptr
            return self.builder.inttoptr(val, dst)

        # Slice to pointer (e.g., char[] to *char)
        if (
            isinstance(src, ir.LiteralStructType)
            and len(src.elements) == 2
            and dst_is_ptr
        ):
            return self.builder.extract_value(val, 1)

        # Pointer to slice (e.g., *char to char[]) - treat as slice of length 1 or unknown
        # Only apply if the destination struct's second element is actually a pointer (slice type)
        if (
            src_is_ptr
            and isinstance(dst, ir.LiteralStructType)
            and len(dst.elements) == 2
            and isinstance(dst.elements[1], ir.PointerType)
        ):
            new_slice = ir.Constant(dst, ir.Undefined)
            new_slice = self.builder.insert_value(
                new_slice, ir.Constant(ir.IntType(32), 1), 0
            )
            new_slice = self.builder.insert_value(new_slice, val, 1)
            return new_slice

        # Struct-to-struct (slices)
        if isinstance(src, ir.LiteralStructType) and isinstance(
            dst, ir.LiteralStructType
        ):
            if len(src.elements) == 2 and len(dst.elements) == 2:
                # If it's a slice {i32, T*} to {i32, U*}
                if isinstance(src.elements[1], ir.PointerType) and isinstance(
                    dst.elements[1], ir.PointerType
                ):
                    length = self.builder.extract_value(val, 0)
                    ptr = self.builder.extract_value(val, 1)
                    # Bitcast the pointer part
                    new_ptr = self.builder.bitcast(ptr, dst.elements[1])
                    new_slice = ir.Constant(dst, ir.Undefined)
                    new_slice = self.builder.insert_value(new_slice, length, 0)
                    new_slice = self.builder.insert_value(new_slice, new_ptr, 1)
                    return new_slice

            # Vector struct types: {ptr, i64, i64} - handle casting between different vec types
            if len(src.elements) == 3 and len(dst.elements) == 3:
                # Check if both are vec-like: {ptr, i64, i64}
                if (
                    isinstance(src.elements[0], ir.PointerType)
                    and isinstance(src.elements[1], ir.IntType)
                    and isinstance(src.elements[2], ir.IntType)
                    and isinstance(dst.elements[0], ir.PointerType)
                    and isinstance(dst.elements[1], ir.IntType)
                    and isinstance(dst.elements[2], ir.IntType)
                ):
                    # Extract the data pointer and do bitcast
                    data_ptr = self.builder.extract_value(val, 0)
                    size_val = self.builder.extract_value(val, 1)
                    cap_val = self.builder.extract_value(val, 2)
                    new_data_ptr = self.builder.bitcast(data_ptr, dst.elements[0])
                    new_vec = ir.Constant(dst, ir.Undefined)
                    new_vec = self.builder.insert_value(new_vec, new_data_ptr, 0)
                    new_vec = self.builder.insert_value(new_vec, size_val, 1)
                    new_vec = self.builder.insert_value(new_vec, cap_val, 2)
                    return new_vec

        # Handle struct to integer conversion (manual bitcast)
        if isinstance(src, ir.LiteralStructType) and dst_is_int:
            # Calculate total size of struct in bits
            src_bits = sum(field.width for field in src.elements)
            if src_bits != dst.width:
                raise LeashError(
                    f"Cannot convert struct of size {src_bits} bits to integer of {dst.width} bits",
                    node=node
                )
            # Ensure all fields are integers
            for field in src.elements:
                if not isinstance(field, ir.IntType):
                    raise LeashError(
                        f"Cannot convert struct with non-integer field to integer",
                        node=node
                    )
            # Build integer from fields (little-endian: first field is least significant bits)
            result = ir.Constant(dst, 0)
            for idx, field_type in enumerate(src.elements):
                field_val = self.builder.extract_value(val, idx)
                # Truncate or extend field_val to its native width
                if field_type.width != field_val.type.width:
                    if field_type.width < field_val.type.width:
                        field_val = self.builder.trunc(field_val, field_type)
                    else:
                        field_val = self.builder.zext(field_val, field_type)
                # Zero-extend to dst width for shifting
                if field_type.width < dst.width:
                    field_val = self.builder.zext(field_val, dst)
                # Shift and combine
                shift_amount = sum(f.width for f in src.elements[:idx])
                if shift_amount > 0:
                    field_val = self.builder.shl(
                        field_val, ir.Constant(dst, shift_amount)
                    )
                result = self.builder.or_(result, field_val)
            return result
        self.builder.call(self.strcat, [result_after_new, suffix_start])

        self.builder.branch(merge_bb)

        self.builder.position_at_end(not_found_bb)
        str_len_plus_1 = self.builder.add(str_len, ir.Constant(ir.IntType(64), 1))
        result_copy = self.builder.call(self.malloc, [str_len_plus_1])
        self._track_alloc(result_copy)
        self.builder.call(self.strcpy, [result_copy, str_val])
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(str_val.type, name="str_replace_res")
        phi.add_incoming(result, found_bb)
        phi.add_incoming(result_copy, not_found_bb)
        return phi

    def _codegen_string_replace_from_value(self, str_val, args):
        """Implement string.replace(old, new) for StringLiteral - replace first occurrence."""
        old_str = self._codegen(args[0])
        new_str = self._codegen(args[1])

        old_len = self.builder.call(self.strlen, [old_str])
        new_len = self.builder.call(self.strlen, [new_str])
        str_len = self.builder.call(self.strlen, [str_val])

        found_bb = self.builder.function.append_basic_block("str_replace_found")
        not_found_bb = self.builder.function.append_basic_block("str_replace_not_found")
        merge_bb = self.builder.function.append_basic_block("str_replace_merge")

        p = self.builder.call(self.strstr, [str_val, old_str])
        is_not_null = self.builder.icmp_signed("!=", p, ir.Constant(old_str.type, None))
        self.builder.cbranch(is_not_null, found_bb, not_found_bb)

        self.builder.position_at_end(found_bb)
        p_int = self.builder.ptrtoint(p, ir.IntType(64))
        l_int = self.builder.ptrtoint(str_val, ir.IntType(64))
        prefix_len = self.builder.sub(p_int, l_int)

        suffix_start = self.builder.gep(p, [old_len], inbounds=True)
        suffix_len = self.builder.sub(str_len, prefix_len)
        suffix_len = self.builder.sub(suffix_len, old_len)

        result_len = self.builder.add(prefix_len, new_len)
        result_len = self.builder.add(result_len, suffix_len)
        result_len_plus_1 = self.builder.add(result_len, ir.Constant(ir.IntType(64), 1))
        result = self.builder.call(self.malloc, [result_len_plus_1])
        self._track_alloc(result)

        # Copy prefix
        self.builder.call(self.strncpy, [result, str_val, prefix_len])

        # Null-terminate after prefix so strcat works
        terminator_ptr = self.builder.gep(result, [prefix_len], inbounds=True)
        self.builder.store(ir.Constant(ir.IntType(8), 0), terminator_ptr)

        # Append new string
        self.builder.call(self.strcat, [result, new_str])

        # Append suffix
        self.builder.call(self.strcat, [result, suffix_start])

        self.builder.branch(merge_bb)

        self.builder.position_at_end(not_found_bb)
        str_len_plus_1 = self.builder.add(str_len, ir.Constant(ir.IntType(64), 1))
        result_copy = self.builder.call(self.malloc, [str_len_plus_1])
        self._track_alloc(result_copy)
        self.builder.call(self.strcpy, [result_copy, str_val])
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        phi = self.builder.phi(str_val.type, name="str_replace_res")
        phi.add_incoming(result, found_bb)
        phi.add_incoming(result_copy, not_found_bb)
        return phi

    def _codegen_SizeofExpr(self, node):
        llvm_type = None
        if isinstance(node.target, str):
            resolved = self._resolve_type_name(node.target)
            llvm_type = self._get_llvm_type(resolved)
        else:
            # It's an expression
            if isinstance(node.target, Identifier):
                name = node.target.name
                if name in self.var_symtab:
                    var_info = self.var_symtab[name]
                    leash_type = var_info[1]
                    llvm_type = self._get_llvm_type(leash_type)
                elif name in self.global_var_ptrs:
                    _, leash_type = self.global_var_ptrs[name]
                    llvm_type = self._get_llvm_type(leash_type)
                elif name in self.func_symtab:
                    llvm_type = self.func_symtab[name].function_type.as_pointer()
                elif f"_error_{name}" in self.func_symtab:
                    llvm_type = self.func_symtab[f"_error_{name}"].function_type.as_pointer()
                elif name in self.class_symtab:
                    llvm_type = self.class_symtab[name]["type"].as_pointer()
                elif name in self.struct_symtab:
                    llvm_type = self.struct_symtab[name]["type"]
                elif name in self.union_symtab:
                    llvm_type = self.union_symtab[name]["type"]
                elif name in self.enum_symtab:
                    llvm_type = ir.IntType(32)
                elif name in self.type_aliases:
                    resolved = self._resolve_type_name(name)
                    llvm_type = self._get_llvm_type(resolved)
                else:
                    leash_type = self._get_leash_type_name(node.target)
                    llvm_type = self._get_llvm_type(leash_type)
            else:
                leash_type = self._get_leash_type_name(node.target)
                llvm_type = self._get_llvm_type(leash_type)

        if llvm_type is None or isinstance(llvm_type, ir.VoidType):
            return ir.Constant(ir.IntType(32), 0)

        size = self._get_type_size(llvm_type)
        return ir.Constant(ir.IntType(32), size)

    def get_ir(self):
        return str(self.module)

    def _codegen_DeferStatement(self, node):
        self.defer_stack[-1].append(node.call)

    def _codegen_Lambda(self, node):
        lambda_name = self.module.get_unique_name("lambda")

        ret_type = self._get_llvm_type(node.return_type, is_return=True)

        arg_types = []
        for _, arg_type, _ in node.args:
            arg_types.append(self._get_llvm_type(arg_type, is_return=False))

        func_type = ir.FunctionType(ret_type, arg_types)
        func = ir.Function(self.module, func_type, name=lambda_name)
        func.linkage = "internal"

        block = func.append_basic_block(name="entry")
        old_builder = self.builder
        self.builder = ir.IRBuilder(block)

        old_vars = self.var_symtab.copy()
        old_func_name = self.current_func_name
        self.current_func_name = "<lambda>"

        for i, (arg_name, arg_type_name, _) in enumerate(node.args):
            func.args[i].name = arg_name
            ptr = self.builder.alloca(func.args[i].type)
            self.builder.store(func.args[i], ptr)
            self.var_symtab[arg_name] = (ptr, arg_type_name)

        for stmt in node.body:
            self._codegen(stmt)
            if self.builder.block.is_terminated:
                break

        if not self.builder.block.is_terminated:
            if node.return_type == "void":
                self.builder.ret_void()
            else:
                self.builder.unreachable()

        self.builder = old_builder
        self.var_symtab = old_vars
        self.current_func_name = old_func_name

        return func

    def _codegen_CreateExpr(self, node):
        """Generate code for 'create ClassName(args)' expression."""
        class_name = node.class_name

        # Get the class info
        if class_name not in self.class_symtab:
            raise LeashError(f"Unknown class '{class_name}' in create expression", node=node)

        cls_info = self.class_symtab[class_name]
        class_type = cls_info["type"]

        # Allocate memory for the class instance using GC_malloc
        # The type is a pointer to the struct
        instance_ptr_type = class_type.as_pointer()
        size = self._get_type_size(class_type)
        size_val = ir.Constant(ir.IntType(64), size)
        malloc_ptr = self.builder.call(self.malloc, [size_val])
        instance_ptr = self.builder.bitcast(malloc_ptr, instance_ptr_type)

        # Set up vtable pointer if the class has virtual methods
        if cls_info.get("vtable_global") is not None:
            # Store vtable pointer as the first field (index 0)
            vtable_ptr = self.builder.gep(
                instance_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]
            )
            # Bitcast vtable_global to i8* for storage
            vtable_value = self.builder.bitcast(cls_info["vtable_global"], ir.IntType(8).as_pointer())
            self.builder.store(vtable_value, vtable_ptr)

        # Call the constructor if it exists
        constructor_name = f"{class_name}_{class_name}"
        if constructor_name in self.func_symtab:
            constructor = self.func_symtab[constructor_name]
            # Prepare arguments: 'this' (instance pointer) + user arguments with defaults
            args = [instance_ptr]
            constr_args = cls_info.get("constructor_args")
            if constr_args:
                # Skip 'this' (first arg)
                non_this = constr_args[1:] if constr_args and constr_args[0][0] == 'this' else constr_args
                for i, (_, _, default) in enumerate(non_this):
                    if i < len(node.args):
                        arg_val = self._codegen(node.args[i])
                    elif default is not None:
                        arg_val = self._codegen(default)
                    else:
                        raise LeashError(
                            f"Constructor '{class_name}' missing required argument at position {i}",
                            node=node,
                        )
                    args.append(arg_val)
            else:
                # Fallback: just use provided args
                for arg_expr in node.args:
                    arg_val = self._codegen(arg_expr)
                    args.append(arg_val)
            self.builder.call(constructor, args)

        return instance_ptr

    def _codegen_DelStatement(self, node):
        """Generate code for 'del target;' statement."""
        # Get the target type
        target_type = self._get_leash_type_name(node.target)
        resolved = self._resolve_type_name(target_type)

        if resolved not in self.class_symtab:
            raise LeashError(f"Cannot delete non-class type '{target_type}'", node=node)

        cls_info = self.class_symtab[resolved]

        # Get the pointer to the instance
        target_ptr = self._codegen(node.target)

        # Call the destructor if it exists (DEL_ClassName)
        destructor_name = f"{resolved}_DEL_{resolved}"
        # Check common destructor naming patterns
        destructor = None
        for func_name, func in self.func_symtab.items():
            if func_name.startswith(f"{resolved}_DEL_"):
                destructor = func
                break

        if destructor is None:
            # Try another naming pattern
            destructor_name = f"DEL_{resolved}"
            if destructor_name in self.func_symtab:
                destructor = self.func_symtab[destructor_name]

        if destructor:
            # Call destructor with 'this' (instance pointer)
            self.builder.call(destructor, [target_ptr])

        # Note: Memory is managed by Boehm GC, so no explicit free is needed
        # The GC will collect the object when no more references exist
