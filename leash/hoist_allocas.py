import llvmlite.ir as ir

def hoist_allocas(mod):
    """Move all allocas to the entry block of each function.

    LLVM requires all static allocas to be in the entry block for proper
    code generation (especially at O0/O1 where mem2reg/SROA may not run).
    The Leash codegen places allocas at the current IR builder position,
    which can be inside loop bodies. This function hoists them all up.
    """
    for func in mod.functions:
        if func.is_declaration:
            continue
        blocks = list(func.blocks)
        if not blocks:
            continue
        entry_block = blocks[0]

        allocas = []
        for block in blocks[1:]:  # skip entry block
            for instr in list(block.instructions):
                if isinstance(instr, ir.AllocaInstr):
                    allocas.append(instr)
                    block.instructions.remove(instr)

        if not allocas:
            continue

        # Insert after the last alloca in the entry block, or at position 0
        insert_pos = 0
        for i, instr in enumerate(entry_block.instructions):
            if isinstance(instr, ir.AllocaInstr):
                insert_pos = i + 1
            else:
                break

        for alloca_instr in allocas:
            entry_block.instructions.insert(insert_pos, alloca_instr)
            insert_pos += 1
