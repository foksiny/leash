"""
LLVM Optimization Pipeline for the Leash compiler.

Provides an `optimize_module(mod_ref, opt_level, target_machine)` function
that runs a carefully-chosen pass pipeline on a parsed LLVM module before
code emission.  The pipeline is aligned with the standard LLVM `-O` levels
so that the user-visible `-O0…3` and `-Os` flags behave predictably.
"""

import llvmlite.binding as llvm
from llvmlite.binding import newpassmanagers

__all__ = ["optimize_module", "parse_opt_level"]


def parse_opt_level(arg):
    """Convert a CLI string like '0', '1', '2', '3', 's', 'z' to an int (0‑3) and a boolean size‑flag.

    Returns (opt_level, size_opt) where size_opt can be 1 (-Os) or 2 (-Oz).
    """
    if arg is None:
        return 0, False
    arg = str(arg).strip()
    if arg.lower() in ("z", "min-size"):
        return 1, 2           # -Oz: cap speed at O1, size_level=2
    if arg.lower() in ("s", "size"):
        return 2, True        # -Os: O2 speed + size_level=1
    if arg == "3":
        return 3, False
    if arg == "2":
        return 2, False
    if arg == "1":
        return 1, False
    return 0, False


def _create_pass_builder(target_machine, speed_level=0, size_level=0):
    """Create a PassBuilder with PipelineTuningOptions."""
    pto = llvm.create_pipeline_tuning_options()
    pto.speed_level = speed_level
    pto.size_level = size_level
    return llvm.create_pass_builder(target_machine, pto)


def _add_early_module_passes(pm):
    """Add early module-level passes that clean up IR before function-level passes."""
    pm.add_lower_invoke_pass()
    pm.add_lower_switch_pass()


def _add_function_level_passes(pm, opt_level, size_opt=False):
    """Add function-level passes for a given optimization level."""
    if opt_level >= 1:
        pm.add_instruction_combine_pass()           # instcombine (peephole opts)
        pm.add_simplify_cfg_pass()                  # simplify CFG
        pm.add_dead_code_elimination_pass()         # DCE
        pm.add_strip_dead_prototype_pass()

    if opt_level >= 2:
        pm.add_sroa_pass()                          # scalar replacement of aggregates
        pm.add_mem_copy_opt_pass()                 # memcpy/memmove optimization
        pm.add_jump_threading_pass()
        pm.add_reassociate_pass()                    # reassociation for CSE
        pm.add_tail_call_elimination_pass()          # tail-call opt (TCO)
        pm.add_sinking_pass()                        # code sinking
        pm.add_instruction_combine_pass()            # second instcombine after sinking

    if opt_level >= 3:
        pm.add_aggressive_instcombine_pass()         # aggressive peephole
        pm.add_new_gvn_pass()                        # global value numbering
        pm.add_loop_simplify_pass()                  # canonicalize loops
        pm.add_loop_rotate_pass()                    # rotate for better unrolling
        pm.add_loop_unroll_pass()                    # unroll loops
        pm.add_loop_unroll_and_jam_pass()            # unroll and jam
        pm.add_loop_strength_reduce_pass()           # replace mult with add/shift
        pm.add_lcssa_pass()                          # loop-closed SSA
        pm.add_partial_inliner_pass()                # partial inlining
        pm.add_always_inliner_pass()                 # always inline

        if hasattr(pm, 'add_loop_vectorize_pass'):
            pm.add_loop_vectorize_pass()             # Optimization 11: Loop vectorization
        if hasattr(pm, 'add_slp_vectorize_pass'):
            pm.add_slp_vectorize_pass()              # Optimization 12: SLP vectorization (auto-SIMD)
        if hasattr(pm, 'add_loop_distribute_pass'):
            pm.add_loop_distribute_pass()            # Optimization 13: Loop distribution for better vectorization
        if hasattr(pm, 'add_loop_unswitch_pass'):
            pm.add_loop_unswitch_pass()              # Optimization 14: Loop unswitching
        if hasattr(pm, 'add_loop_versioning_pass'):
            pm.add_loop_versioning_pass()            # Optimization 15: Loop versioning for SIMD
        if hasattr(pm, 'add_loop_interchange_pass'):
            pm.add_loop_interchange_pass()           # Optimization 16: Loop interchange
        if hasattr(pm, 'add_loop_predication_pass'):
            pm.add_loop_predication_pass()           # Optimization 17: Loop predication
        if hasattr(pm, 'add_inject_tli_mappings_pass'):
            pm.add_inject_tli_mappings_pass()        # Optimization 18: TLI for better vectorization
        if hasattr(pm, 'add_coro_early_pass'):
            pm.add_coro_early_pass()                 # Optimization 19: Coroutine early cleanup



def _add_module_level_passes(pm, opt_level, size_opt=False):
    """Add module-level passes (cross-function, global opts)."""
    if opt_level >= 1:
        pm.add_global_opt_pass()                     # optimize global vars
        pm.add_constant_merge_pass()                 # deduplicate constants

    if opt_level >= 2:
        pm.add_ipsccp_pass()                         # inter-procedural SCCP
        pm.add_global_dead_code_eliminate_pass()       # global DCE
        pm.add_merge_functions_pass()                  # merge identical functions
        pm.add_argument_promotion_pass()             # promote args to registers
        pm.add_dead_arg_elimination_pass()           # remove dead arguments
        pm.add_post_order_function_attributes_pass() # infer fn attrs
        pm.add_sccp_pass()                           # sparse conditional const prop

    if opt_level >= 3:
        pm.add_dead_store_elimination_pass()         # remove dead stores
        pm.add_break_critical_edges_pass()             # break crit edges before more opts

        # Optimization 20: Additional IPO passes
        if hasattr(pm, 'add_function_attrs_pass'):
            pm.add_function_attrs_pass()
        if hasattr(pm, 'add_called_value_propagation_pass'):
            pm.add_called_value_propagation_pass()   # propagate callee values
        if hasattr(pm, 'add_coro_split_pass'):
            pm.add_coro_split_pass()
        if hasattr(pm, 'add_coro_elide_pass'):
            pm.add_coro_elide_pass()
        if hasattr(pm, 'add_coro_cleanup_pass'):
            pm.add_coro_cleanup_pass()

        # Optimization 21: Extra function-level cleanup at module scope
        if hasattr(pm, 'add_float2int_pass'):
            pm.add_float2int_pass()                  # float-to-int conversion

        # Optimization 22: Speculative execution optimization
        if hasattr(pm, 'add_speculative_execution_pass'):
            pm.add_speculative_execution_pass()


def _add_size_passes(pm):
    """Add size-specific passes (like -Os)."""
    pm.add_global_dead_code_eliminate_pass()
    pm.add_simplify_cfg_pass()
    pm.add_merge_functions_pass()


def optimize_module(mod_ref, opt_level=0, size_opt=False, target_machine=None):
    """
    Run the optimization pipeline on an already-parsed LLVM module.

    Parameters
    ----------
    mod_ref : llvmlite.binding.ModuleRef
        The parsed LLVM module to optimize.
    opt_level : int
        Optimization aggressiveness (0 = none, 1 = basic, 2 = default, 3 = aggressive).
    size_opt : bool
        If True, run additional passes that favor code size (like -Os).
    target_machine : llvmlite.binding.TargetMachine or None
        Used to create the PassBuilder for the new pass manager.
        If None, a default TargetMachine with opt=opt_level is created.
    """
    if opt_level <= 0 and not size_opt:
        # No optimization requested
        return

    if target_machine is None:
        # Create a default target machine at the requested opt level
        triple = llvm.get_default_triple()
        target = llvm.Target.from_triple(triple)
        target_machine = target.create_target_machine(opt=opt_level)

    speed = opt_level if not size_opt else 2  # LLVM requires speed_level==2 when size_level>0
    size = (size_opt if isinstance(size_opt, int) and size_opt > 1 else 1) if size_opt else 0

    pb = _create_pass_builder(target_machine, speed_level=speed, size_level=size)
    pm = llvm.create_new_module_pass_manager()

    # Early module passes (canonicalization)
    _add_early_module_passes(pm)

    # Function-level passes (vectorization, inlining, etc.)
    _add_function_level_passes(pm, opt_level, size_opt=size_opt)

    # Module-level passes (IPO, global opts)
    _add_module_level_passes(pm, opt_level, size_opt=size_opt)

    # Size-specific passes
    if size_opt:
        _add_size_passes(pm)

    # Run the pipeline
    try:
        pm.run(mod_ref, pb)
    except Exception as exc:
        # If the new pass manager fails, fall back to a minimal safe core
        try:
            pm_safe = llvm.create_new_module_pass_manager()
            pm_safe.add_instruction_combine_pass()
            pm_safe.add_simplify_cfg_pass()
            pm_safe.run(mod_ref, pb)
        except Exception:
            raise RuntimeError(f"LLVM optimization failed: {exc}") from exc
