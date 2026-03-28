import sys
import os
import subprocess
from .lexer import Lexer
from .parser_l import Parser
from .codegen import CodeGen
from .typechecker import TypeChecker
from .errors import LeashError
import llvmlite.binding as llvm

def _print_error(e, input_file, code):
    RED = "\033[1;31m"
    BLUE = "\033[1;34m"
    GREEN = "\033[1;32m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"{RED}error{RESET}: {BOLD}{e.msg}{RESET}")
    
    if e.line is not None:
        lines = code.splitlines()
        line_idx = e.line - 1
        
        # File location
        print(f"  {BLUE}-->{RESET} {input_file}:{e.line}:{e.col if e.col is not None else 0}")
        
        # Line snippet
        if 0 <= line_idx < len(lines):
            gutter_width = len(str(e.line)) + 1
            padding = " " * gutter_width
            
            error_line = lines[line_idx]
            print(f"{padding}{BLUE}|{RESET}")
            print(f"{BLUE}{e.line} |{RESET} {error_line}")
            
            if e.col is not None:
                # Pointer
                pointer = " " * e.col + "^"
                print(f"{padding}{BLUE}|{RESET} {RED}{pointer}{RESET}")
            
            print(f"{padding}{BLUE}|{RESET}")

    if e.tip:
        print(f"{GREEN}tip{RESET}: {e.tip}")

def compile_file(input_file, output_name=None, is_run_mode=False):
    with open(input_file, 'r') as f:
        code = f.read()

    try:
        # 1. Lexical Analysis
        lexer = Lexer(code)
        tokens = lexer.tokenize()

        # 2. Parsing
        parser = Parser(tokens)
        ast = parser.parse()

        # 3. Static Type Checking
        checker = TypeChecker()
        warnings = checker.check(ast)
        for w in warnings:
            print(w)

        # 4. LLVM Initialization
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        
        # 5. Code Generation
        codegen = CodeGen()
        codegen.generate_code(ast)
        
        llvm_ir = codegen.get_ir()
        
        # Parse IR
        mod = llvm.parse_assembly(llvm_ir)
        mod.verify()
    except LeashError as e:
        _print_error(e, input_file, code)
        sys.exit(1)
    except Exception as e:
        print(f"\033[1;31merror\033[0m: Internal compiler error: {e}")
        # import traceback; traceback.print_exc()
        sys.exit(1)
    
    # Create target machine
    target = llvm.Target.from_default_triple()
    target_machine = target.create_target_machine()
    
    # Optional Output name
    if output_name is None:
        if input_file.endswith('.lsh'):
            output_name = input_file[:-4]
        else:
            output_name = "out"
            
    if os.name == 'nt':
        output_name += '.exe'
        
    obj_name = output_name + '.o'

    # Compile to object file
    with open(obj_name, "wb") as f:
        f.write(target_machine.emit_object(mod))
        
    # Link to executable using system compiler
    cc = os.environ.get('CC', 'gcc')
    
    if os.name == 'nt':
        # On Windows, try calling clang or gcc. If ms linker is preferred, would be `link`.
        # MSYS2/MinGW gcc works too.
        link_cmd = [cc, obj_name, '-o', output_name]
    else:
        # Linux / Mac
        link_cmd = [cc, obj_name, '-o', output_name, '-no-pie']

    try:
        subprocess.run(link_cmd, check=True)
    except FileNotFoundError:
        print(f"Error: A C compiler ('{cc}') is required to link the executable.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Linking failed: {e}")
        sys.exit(1)
        
    # Finally, cleanup object file
    if os.path.exists(obj_name):
        os.remove(obj_name)
        
    if not is_run_mode:
        print(f"Successfully compiled '{input_file}' to '{output_name}'")
    return output_name

def run_file(input_file):
    output_name = compile_file(input_file, output_name=".__temp_run_leash_exe", is_run_mode=True)
    executable_path = f"./{output_name}" if os.name != 'nt' else output_name

    try:
        result = subprocess.run([executable_path])
        if result.returncode != 0:
            sys.exit(result.returncode)
    except Exception as e:
        print(f"Execution failed: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(output_name):
            try:
                os.remove(output_name)
            except:
                pass

def main():
    if len(sys.argv) < 3:
        print("Usage: leash <compile|run> <file.lsh> [to <outname>]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd not in ("compile", "run"):
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    input_file = sys.argv[2]

    if cmd == "run":
        run_file(input_file)
    else:
        output_name = None
        if len(sys.argv) == 5 and sys.argv[3] == "to":
            output_name = sys.argv[4]

        compile_file(input_file, output_name)

if __name__ == '__main__':
    main()
