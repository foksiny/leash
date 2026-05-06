# Leash VS Code Extension

This extension provides syntax highlighting and LSP support for the Leash programming language.

## Features

- **Syntax Highlighting**: Comprehensive highlighting for Leash keywords, types, built-ins, and more.
- **Diagnostics**: Real-time error and warning reporting by running the Leash compiler in the background.
- **Hover Support**: Shows documentation and signatures for functions, types, and variables.
- **Go to Definition**: Quickly navigate to the declaration of symbols.
- **Auto-Completion**: Context-aware suggestions for keywords and built-ins.
- **Document Symbols**: Outline view for easy navigation.
- **Auto-indentation**: Basic support for automatic indentation and bracket matching.

## Prerequisites

- **Node.js** (v16 or newer)
- **Leash** compiler installed and available in your system's PATH.

## Installation

### Method 1: Pre-built Extension
1. Install the `leash-0.1.0.vsix` file in VS Code (Extensions view -> `...` -> `Install from VSIX...`).

### Method 2: Manual Development Setup
1. Copy this directory to your VS Code extensions folder.
2. Run `npm install`.
3. Run `npm run compile`.
4. Restart VS Code.

## Troubleshooting

If the extension is not working:
1. Check the **Leash LSP** output channel in VS Code:
   * Open the **Output** panel (`View` -> `Output`).
   * Select **Leash LSP** from the dropdown menu.
2. Ensure you can run `leash --version` in your terminal.
3. Check for any Node.js errors in the extension host logs.

## Architecture

This extension uses a **Node.js Language Server** implemented in TypeScript. It provides instant, crash-proof language features (like Hover and Definition) using fast regex-based scanning, while leveraging the official Leash compiler for deep static analysis and diagnostics. This hybrid approach ensures the best balance of performance, reliability, and accuracy.
