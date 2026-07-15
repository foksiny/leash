"""
LLVM Optimization Pipeline for the Leash compiler.

Provides an `optimize_module(mod_ref, opt_level, target_machine)` function
that runs a carefully-chosen pass pipeline on a parsed LLVM module before
code emission.  The pipeline is aligned with the standard LLVM `-O` levels
so that the user-visible `-O0…4` and `-Os` flags behave predictably.

Level  4 raises the LLVM inliner threshold from 225→600, then runs the
standard O3 pipeline **twice** with aggressive cleanup passes between
and after each iteration.  The higher threshold causes more functions
to be inlined, exposing more optimization opportunities to GVN, LICM,
and DSE on the second pass.
"""

import sys
import llvmlite.binding as llvm

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
    if arg == "4":
        return 4, False
    if arg == "3":
        return 3, False
    if arg == "2":
        return 2, False
    if arg == "1":
        return 1, False
    return 0, False


def _create_pass_builder(target_machine, speed_level=0, size_level=0, inlining_threshold=-1, slp=False):
    """Create a PassBuilder with PipelineTuningOptions."""
    pto = llvm.create_pipeline_tuning_options()
    pto.speed_level = speed_level
    pto.size_level = size_level
    if inlining_threshold > 0:
        pto.inlining_threshold = inlining_threshold
    if slp:
        pto.slp_vectorization = True
    return llvm.create_pass_builder(target_machine, pto)



def optimize_module(mod_ref, opt_level=0, size_opt=False, target_machine=None, opt_verbose=False):
    """
    Run the optimization pipeline on an already-parsed LLVM module.

    Parameters
    ----------
    mod_ref : llvmlite.binding.ModuleRef
        The parsed LLVM module to optimize.
    opt_level : int
        Optimization aggressiveness (0 = none, 1 = basic, 2 = default, 3 = aggressive).
    size_opt : bool or int
        If True/1, run additional passes that favor code size (like -Os).
    target_machine : llvmlite.binding.TargetMachine or None
        Used to create the PassBuilder for the new pass manager.
        If None, a default TargetMachine with opt=opt_level is created.
    opt_verbose : bool
        If True, print details about the LLVM optimization pipeline.
    """
    level_desc = f"O{opt_level}" + (f"s" if size_opt else "") + (f"z" if isinstance(size_opt, int) and size_opt > 1 else "")
    if opt_verbose:
        print(f"[LLVM Opt] Running optimization pipeline at {level_desc}", file=sys.stderr)

    if opt_level <= 0 and not size_opt:
        if target_machine is None:
            triple = llvm.get_default_triple()
            target = llvm.Target.from_triple(triple)
            target_machine = target.create_target_machine(opt=0)
        pb = _create_pass_builder(target_machine, speed_level=0, size_level=0)
        pm = llvm.create_new_module_pass_manager()
        pm.add_dead_code_elimination_pass()
        pm.add_strip_dead_prototype_pass()
        pm.add_instruction_combine_pass()
        pm.add_simplify_cfg_pass()
        pm.add_sroa_pass()
        try:
            pm.run(mod_ref, pb)
        except Exception:
            pass
        if opt_verbose:
            print(f"[LLVM Opt] Completed basic cleanup passes (DCE, instcombine, SROA)", file=sys.stderr)
        return

    if target_machine is None:
        triple = llvm.get_default_triple()
        target = llvm.Target.from_triple(triple)
        target_machine = target.create_target_machine(opt=opt_level)

    speed = min(opt_level, 3) if not size_opt else 2
    size = (size_opt if isinstance(size_opt, int) and size_opt > 1 else 1) if size_opt else 0

    pb = _create_pass_builder(target_machine, speed_level=speed, size_level=size)

    if opt_level >= 2:
        if opt_verbose:
            print(f"[LLVM Opt] Using LLVM standard pipeline (O2+) with inliner, GVN, loop opts", file=sys.stderr)
        # First iteration of the standard pipeline at the given speed level
        pm = pb.getModulePassManager()
        pm.add_verifier()
        try:
            pm.run(mod_ref, pb)
        except Exception as exc:
            if opt_verbose:
                print(f"[LLVM Opt] Standard pipeline failed, falling back to minimal passes", file=sys.stderr)
            try:
                pm2 = llvm.create_new_module_pass_manager()
                pm2.add_instruction_combine_pass()
                pm2.add_simplify_cfg_pass()
                pm2.run(mod_ref, pb)
            except Exception:
                raise RuntimeError(f"LLVM optimization failed: {exc}") from exc

        # Apply extra passes that the standard pipeline may not cover
        if opt_level >= 2:
            pm_extra = llvm.create_new_module_pass_manager()
            pm_extra.add_sinking_pass()
            pm_extra.add_instruction_combine_pass()
            pm_extra.add_simplify_cfg_pass()
            pm_extra.add_dead_code_elimination_pass()
            if opt_level >= 3:
                pm_extra.add_aggressive_instcombine_pass()
                pm_extra.add_new_gvn_pass()
                pm_extra.add_dead_store_elimination_pass()
                pm_extra.add_sccp_pass()
                pm_extra.add_global_dead_code_eliminate_pass()
            try:
                pm_extra.run(mod_ref, pb)
            except Exception:
                pass

    if opt_level >= 4:
        if opt_verbose:
            print(f"[LLVM Opt] Running aggressive O4 pipeline (aggressive inlining + dual pass)", file=sys.stderr)
        # Create a more aggressive PassBuilder for O4 with higher inlining threshold.
        # Default LLVM inliner threshold is 225; we raise it so more functions inline,
        # enabling downstream passes (GVN, LICM, DSE) to find more optimization opportunities.
        pb4 = _create_pass_builder(target_machine, speed_level=3, size_level=0, inlining_threshold=600, slp=True)
        # First pass with aggressive inlining
        pm4a = pb4.getModulePassManager()
        pm4a.add_verifier()
        try:
            pm4a.run(mod_ref, pb4)
        except Exception as exc:
            if opt_verbose:
                print(f"[LLVM Opt] O4 first pass failed, {exc}", file=sys.stderr)

        # Extra cleanup after first aggressive pass
        pm4b = llvm.create_new_module_pass_manager()
        pm4b.add_aggressive_dce_pass()
        pm4b.add_new_gvn_pass()
        pm4b.add_sinking_pass()
        pm4b.add_instruction_combine_pass()
        pm4b.add_simplify_cfg_pass()
        pm4b.add_dead_code_elimination_pass()
        pm4b.add_global_dead_code_eliminate_pass()
        pm4b.add_dead_store_elimination_pass()
        try:
            pm4b.run(mod_ref, pb4)
        except Exception:
            pass

        # Second full pass — catches cascading effects from newly-inlined code
        pm4c = pb4.getModulePassManager()
        pm4c.add_verifier()
        try:
            pm4c.run(mod_ref, pb4)
        except Exception as exc:
            if opt_verbose:
                print(f"[LLVM Opt] O4 second pass failed, {exc}", file=sys.stderr)

        # Final cleanup after second pass
        pm4d = llvm.create_new_module_pass_manager()
        pm4d.add_aggressive_dce_pass()
        pm4d.add_new_gvn_pass()
        pm4d.add_sinking_pass()
        pm4d.add_instruction_combine_pass()
        pm4d.add_simplify_cfg_pass()
        pm4d.add_dead_code_elimination_pass()
        pm4d.add_global_dead_code_eliminate_pass()
        pm4d.add_dead_store_elimination_pass()
        try:
            pm4d.run(mod_ref, pb4)
        except Exception:
            pass
    elif opt_level == 1:
        if opt_verbose:
            print(f"[LLVM Opt] Using O1 manual pass pipeline (instcombine, DCE, SROA, globalopt)", file=sys.stderr)
        pm = llvm.create_new_module_pass_manager()
        pm.add_instruction_combine_pass()
        pm.add_simplify_cfg_pass()
        pm.add_dead_code_elimination_pass()
        pm.add_strip_dead_prototype_pass()
        pm.add_sroa_pass()
        pm.add_global_opt_pass()
        pm.add_constant_merge_pass()
        try:
            pm.run(mod_ref, pb)
        except Exception:
            pass

    if opt_verbose:
        print(f"[LLVM Opt] Completed optimization at {level_desc}", file=sys.stderr)
