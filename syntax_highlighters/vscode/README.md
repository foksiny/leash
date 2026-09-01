# Leash VS Code Extension

This extension provides syntax highlighting and a full Language Server for the Leash programming language (`.lsh` files).

## Features

- **Syntax Highlighting**: Comprehensive highlighting for Leash keywords, types, built-ins, and more (TextMate grammar).
- **Semantic Tokens**: Precise, tokenizer-driven coloring for types, functions, locals, globals, enum members, macro uses, native bindings and `use` imports, on top of the TextMate grammar.
- **Diagnostics**: Real-time errors and warnings from the official Leash compiler (`leash check`), with exact ranges, tips and notes. Configurable trigger mode (`onType` / `onSave` / `onOpen`), debounce and verbose output.
- **Quick Fixes**: Add missing `;`, remove `let`/`var`, and wrap expressions in a cast.
- **Hover**: Documentation and signatures for functions, methods, types, fields, enum members, parameters, locals, globals, imports, built-ins and keywords — including `@from` native bindings.
- **Go to Definition / Go to Type Definition**: Across files, `use` imports, `::` enum member access, `.` member access and call sites (including `Person.new(...)` style constructor calls).
- **Find All References / Document Highlight / Rename**: Kind-aware matching (function calls, member access, type usage, enum members, locals scoped to their enclosing function) with rename preview.
- **Auto-Completion**: Context-aware suggestions — member access after `.`, enum members after `::`, type names, locals, globals, imports, built-ins, keywords, and snippets (`fnc`, `def`, `class`, `if`, `foreach`, ...).
- **Signature Help**: Parameter lists for functions, methods and built-ins (triggered by `(` and `,`).
- **Document / Workspace Symbols**: Outline of types, functions, methods, fields, globals, macros, error types and natives.
- **Folding, Selection Ranges, Document Highlights, Code Lens, Inlay Hints**.
- **Call Hierarchy**: Incoming/outgoing callers via call-site analysis.
- **Document Links**: Clickable `use` imports that jump to the module file.
- **Formatting**: Whole-document indentation-based formatting.
- **Diagnostics for unsaved files**: Files opened from untitled/unsaved buffers are checked via a temporary file next to the real one (or in the system temp dir), so relative imports still resolve.

## Prerequisites

- **Node.js** (v16 or newer)
- **Leash** compiler installed and available on your system's PATH.

## Installation

### Method 1: Pre-built Extension
1. Install the `leash-0.23.4.vsix` file in VS Code (Extensions view -> `...` -> `Install from VSIX...`).

### Method 2: Manual Development Setup
1. Copy this directory to your VS Code extensions folder.
2. Run `npm install`.
3. Run `npm run compile`.
4. Restart VS Code.

## Settings

All settings live under `leash.lsp.*`:

| Setting | Default | Description |
| --- | --- | --- |
| `leash.lsp.enabled` | `true` | Master switch for the language server. |
| `leash.lsp.executablePath` | `leash` | Path to the Leash compiler binary. |
| `leash.lsp.compilerArgs` | `[]` | Extra arguments passed to the compiler (e.g. `["--verbose"]`). |
| `leash.lsp.diagnostics.enabled` | `true` | Enables compiler diagnostics. |
| `leash.lsp.diagnostics.mode` | `onType` | When to check: `onType`, `onSave` or `onOpen`. |
| `leash.lsp.diagnostics.debounceMs` | `500` | Debounce for `onType` checks. |
| `leash.lsp.diagnostics.verbose` | `false` | Passes `--verbose` to `leash check`. |
| `leash.lsp.index.workspace` | `true` | Index all `.lsh` files in the workspace (enables cross-file references). |
| `leash.lsp.index.followImports` | `true` | Also index files found through `use` imports and `imports` folders from `config.lshc`. |
| `leash.lsp.semanticTokens` | `true` | Enables semantic token coloring. |
| `leash.lsp.inlayHints` | `true` | Shows inferred types for `:=` declarations. |
| `leash.lsp.codeLens` | `true` | Shows reference counts above functions. |
| `leash.lsp.snippets` | `true` | Enables snippet completions. |
| `leash.lsp.formatting.indentSize` | `4` | Indentation width used by the formatter. |
| `leash.lsp.formatting.insertSpaces` | `true` | Use spaces instead of tabs when formatting. |

## Commands

- **Leash: Restart Language Server** (`leash.restartServer`) — restarts the server.
- **Leash: Check Current File** (`leash.checkFile`) — runs `leash check` on the active file and publishes diagnostics immediately.

## Project Discovery

The server finds your project root by looking for a `config.lshc` file (reading its `imports` key for extra import folders) or a `.uide/project.json` with `"type": "Leash Project"`. Module resolution for `use path::to::Item;` searches: the current file's directory, then the `imports` folders from `config.lshc`, then `~/.leash/libs`.

## Troubleshooting

If the extension is not working:
1. Check the **Leash LSP** output channel in VS Code:
   * Open the **Output** panel (`View` -> `Output`).
   * Select **Leash LSP** from the dropdown menu.
2. Ensure you can run `leash --version` in your terminal (or set `leash.lsp.executablePath`).
3. Check for any Node.js errors in the extension host logs.

## Architecture

The extension ships a full **Node.js Language Server** (TypeScript, LSP 3.17) in `src/server`. A real tokenizer + parser builds a document model (symbols, locals, call sites, imports) used by every feature, while the official Leash compiler is spawned for deep static analysis and diagnostics. The server is split into `parser.ts` (Leash grammar), `index.ts` (workspace-wide symbol index and module resolution), `builtins.ts` (built-in functions/types/member docs), and one module per feature under `src/server/features/`.