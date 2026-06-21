import {
    createConnection,
    TextDocuments,
    Diagnostic,
    DiagnosticSeverity,
    ProposedFeatures,
    InitializeParams,
    DidChangeConfigurationNotification,
    CompletionItem,
    CompletionItemKind,
    TextDocumentPositionParams,
    TextDocumentSyncKind,
    InitializeResult,
    Hover,
    Location,
    Range,
    SymbolKind,
    DocumentSymbolParams,
    DocumentSymbol,
    CompletionList,
    MarkupContent,
    MarkupKind,
    Position
} from 'vscode-languageserver/node';

import {
    TextDocument
} from 'vscode-languageserver-textdocument';

import { exec } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { URI } from 'vscode-uri';

const connection = createConnection(ProposedFeatures.all);
const documents: TextDocuments<TextDocument> = new TextDocuments(TextDocument);

let hasConfigurationCapability = false;
let hasWorkspaceFolderCapability = false;

connection.onInitialize((params: InitializeParams) => {
    const capabilities = params.capabilities;
    hasConfigurationCapability = !!(capabilities.workspace && !!capabilities.workspace.configuration);
    hasWorkspaceFolderCapability = !!(capabilities.workspace && !!capabilities.workspace.workspaceFolders);

    const result: InitializeResult = {
        capabilities: {
            textDocumentSync: TextDocumentSyncKind.Full,
            completionProvider: {
                resolveProvider: true
            },
            hoverProvider: true,
            definitionProvider: true,
            documentSymbolProvider: true
        }
    };
    if (hasWorkspaceFolderCapability) {
        result.capabilities.workspace = {
            workspaceFolders: { supported: true }
        };
    }
    return result;
});

connection.onInitialized(() => {
    if (hasConfigurationCapability) {
        connection.client.register(DidChangeConfigurationNotification.type, undefined);
    }
});

documents.onDidChangeContent(change => {
    validateTextDocument(change.document);
});

// Cache for diagnostics to prevent flickering and redundant runs
const diagnosticCache = new Map<string, Diagnostic[]>();

function findProjectRoot(filePath: string): string | null {
    let dir = path.dirname(filePath);
    while (true) {
        if (fs.existsSync(path.join(dir, 'config.lshc'))) {
            return dir;
        }
        const uidePath = path.join(dir, '.uide', 'project.json');
        if (fs.existsSync(uidePath)) {
            try {
                const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
                if (data.type === 'Leash Project') {
                    return dir;
                }
            } catch (e) {}
        }
        const parent = path.dirname(dir);
        if (parent === dir) break;
        dir = parent;
    }
    return null;
}

function readProjectConfig(projectRoot: string): { imports?: string } | null {
    const configPath = path.join(projectRoot, 'config.lshc');
    if (!fs.existsSync(configPath)) return null;
    try {
        const content = fs.readFileSync(configPath, 'utf-8');
        const config: { imports?: string } = {};
        for (const line of content.split('\n')) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith('#')) continue;
            const commentIdx = trimmed.indexOf(' #');
            const clean = commentIdx >= 0 ? trimmed.slice(0, commentIdx).trim() : trimmed;
            const colonIdx = clean.indexOf(':');
            if (colonIdx === -1) continue;
            const key = clean.slice(0, colonIdx).trim();
            let val = clean.slice(colonIdx + 1).trim();
            if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
            if (key === 'imports') {
                config.imports = val;
            }
        }
        return Object.keys(config).length > 0 ? config : null;
    } catch (e) {
        return null;
    }
}

async function validateTextDocument(textDocument: TextDocument): Promise<void> {
    const text = textDocument.getText();
    const uri = textDocument.uri;
    
    // Create a temporary file for the compiler to check
    const tempDir = path.join(os.tmpdir(), 'leash-lsp');
    if (!fs.existsSync(tempDir)) fs.mkdirSync(tempDir, { recursive: true });
    
    // We use a specific filename to help the compiler resolve relative imports if possible
    const filePath = URI.parse(uri).fsPath;
    const fileName = path.basename(filePath);
    const tempFile = path.join(tempDir, fileName);
    fs.writeFileSync(tempFile, text);

    // Detect project context for --other-imports resolution
    const projectRoot = findProjectRoot(filePath);
    let checkCmd = `leash check "${tempFile}"`;
    let cwd = path.dirname(filePath);
    
    if (projectRoot) {
        const projectConfig = readProjectConfig(projectRoot);
        if (projectConfig && projectConfig.imports) {
            const importsPath = path.resolve(projectRoot, projectConfig.imports);
            if (fs.existsSync(importsPath)) {
                checkCmd = `leash check --other-imports "${importsPath}" "${tempFile}"`;
            }
        }
        cwd = projectRoot;
    }
    
    exec(checkCmd, { cwd }, (error, stdout, stderr) => {
        const diagnostics: Diagnostic[] = [];
        const output = stdout + stderr;
        
        // Advanced Parser: Handles errors, warnings, error codes, and tips
        const blocks = output.split(/(?=error:|warning:)/g);
        
        for (const block of blocks) {
            const isError = block.startsWith('error:');
            const isWarning = block.startsWith('warning:');
            if (!isError && !isWarning) continue;

            const severity = isError ? DiagnosticSeverity.Error : DiagnosticSeverity.Warning;
            
            // Extract core message (first line)
            const lines = block.split('\n');
            const message = lines[0].replace(/^(error:|warning:)\s*/, '').trim();
            
            // Find the location line: --> file:line:col
            const posMatch = /-->\s*.*:(\d+):(\d+)(?:\s*\[(.*?)\])?/.exec(block);
            if (!posMatch) continue;
            
            const line = Math.max(0, parseInt(posMatch[1]) - 1);
            const col = Math.max(0, parseInt(posMatch[2]));
            const code = posMatch[3]; // Error code like LEASH-E001
            
            // Collect supplementary info (Tips)
            let fullMessage = message;
            const tipMatch = /tip:\s*(.*)/.exec(block);
            if (tipMatch) {
                fullMessage += `\n\n💡 Tip: ${tipMatch[1]}`;
            }

            // Create diagnostic with a small range (1 char or word if we can detect it)
            diagnostics.push({
                severity,
                range: {
                    start: { line, character: col },
                    end: { line, character: col + 1 } 
                },
                message: fullMessage,
                code: code,
                source: 'leash'
            });
        }

        connection.sendDiagnostics({ uri, diagnostics });
    });
}

const BUILTIN_DOCS: Record<string, { sig: string, desc: string, detail?: string }> = {
    'show': { sig: 'show(...args)', desc: 'Prints arguments to console with spaces and a newline.' },
    'showb': { sig: 'showb(...args)', desc: 'Prints arguments to console buffer without spaces/newlines.' },
    'get': { sig: 'get(prompt?: string) : string', desc: 'Reads a line of user input from the console.' },
    'keyget': { sig: 'keyget() : char', desc: 'Reads a single key press immediately.' },
    'size': { sig: 'size(collection) : int', desc: 'Returns the number of elements in an array, vector, or string.' },
    'pushb': { sig: 'pushb(val: T)', desc: 'Appends an element to the back of a vector.' },
    'popb': { sig: 'popb() : T', desc: 'Removes and returns the last element of a vector.' },
    'pushf': { sig: 'pushf(val: T)', desc: 'Appends an element to the front of a vector.' },
    'popf': { sig: 'popf() : T', desc: 'Removes and returns the first element of a vector.' },
    'insert': { sig: 'insert(idx: int, val: T)', desc: 'Inserts an element at a specific index, shifting subsequent elements.' },
    'insertv': { sig: 'insertv(pos: int, other: vec<T>)', desc: 'Inserts all elements from another vector at position `pos`, shifting existing elements right.' },
    'inserta': { sig: 'inserta(pos: int, arr: T[])', desc: 'Inserts all elements from an array or slice at position `pos`, shifting existing elements right.' },
    'clear': { sig: 'clear()', desc: 'Removes all elements from a vector or hash table.' },
    'remove': { sig: 'remove(idx: int)', desc: 'Removes an element at a specific index, shifting subsequent elements.' },
    'isin': { sig: 'isin(val: T) : bool', desc: 'Checks if a value exists in a vector or key in a hash table.' },
    'rand': { sig: 'rand(min: int, max: int) : int', desc: 'Returns a random integer between min and max (inclusive).' },
    'randf': { sig: 'randf(min: float, max: float) : float', desc: 'Returns a random float between min and max.' },
    'seed': { sig: 'seed(val: int)', desc: 'Sets the random number generator seed.' },
    'choose': { sig: 'choose(...args: string) : string', desc: 'Randomly selects one of the provided strings.' },
    'wait': { sig: 'wait(seconds: float)', desc: 'Pauses execution for the specified time in seconds.' },
    'timepass': { sig: 'timepass() : float', desc: 'Returns elapsed time in seconds since program start.' },
    'exit': { sig: 'exit(code: int)', desc: 'Terminates the program immediately with an exit code.' },
    'exec': { sig: 'exec(cmd: string, mode?: string) : string', desc: 'Executes a shell command and returns output.', detail: 'Modes: nil (output), "wait", "silent", "code" (exit code).' },
    'toint': { sig: 'toint(val) : int', desc: 'Converts a value to an integer.' },
    'tofloat': { sig: 'tofloat(val) : float', desc: 'Converts a value to a float.' },
    'tostring': { sig: 'tostring(val) : string', desc: 'Converts a numeric value to a string.' },
    'cstr': { sig: 'cstr(s: string) : *char', desc: 'Converts a Leash string to a C-style null-terminated char pointer.' },
    'lstr': { sig: 'lstr(c: *char) : string', desc: 'Converts a C-style char pointer to a Leash string.' },
    'sizeof': { sig: 'sizeof(type_or_expr) : int', desc: 'Returns the size in bytes of a type or expression result.' },
    'set': { sig: 'set(idx: int, val: T)', desc: 'Sets an element at a specific index in a vector.' },
    'extend': { sig: 'extend(arr: T[])', desc: 'Appends all elements from an array/slice to a vector.' },
    'extendv': { sig: 'extendv(other: vec<T>)', desc: 'Appends all elements from another vector.' },
    'normescape': { sig: 'normescape(s: string) : string', desc: 'Converts escape sequences in a string to actual characters.' },
    'inttobytes': { sig: 'inttobytes(size: int, value: int) : char[]', desc: 'Converts an integer to a byte array.' },
    'bytestoint': { sig: 'bytestoint(size: int, bytes: char[]) : int', desc: 'Converts a byte array back to an integer.' },
    'floattobytes': { sig: 'floattobytes(size: int, value: float) : char[]', desc: 'Converts a float to a byte array.' },
    'bytestofloat': { sig: 'bytestofloat(size: int, bytes: char[]) : float', desc: 'Converts a byte array back to a float.' },
    'getKey': { sig: 'getKey(value: V) : K', desc: 'Returns the key for a given value in a hash table.' },
    'keys': { sig: 'keys() : vec<K>', desc: 'Returns all keys in a hash table.' },
    'values': { sig: 'values() : vec<V>', desc: 'Returns all values in a hash table.' },
    'delete': { sig: 'delete(key: K)', desc: 'Removes a key-value pair from a hash table.' },
    'push': { sig: 'push(key: K, value: V)', desc: 'Adds or updates a key-value pair in a hash table.' },
    'int': { sig: 'int | int<width>', desc: 'Standard signed integer type (1-512 bit widths).' },
    'uint': { sig: 'uint | uint<width>', desc: 'Unsigned integer type (1-512 bit widths).' },
    'float': { sig: 'float | float<width>', desc: 'Floating point type (16-512 bit widths).' },
    'bool': { sig: 'bool', desc: 'Boolean type (true or false).' },
    'char': { sig: 'char', desc: 'Character type (single byte).' },
    'string': { sig: 'string', desc: 'Immutable, managed string type.' },
    'void': { sig: 'void', desc: 'Represents the absence of a value.' },
    'hash': { sig: 'hash<K, V>', desc: 'Key-value hash table type with string keys.' },
    'vec': { sig: 'vec<T>', desc: 'Dynamic array (vector) type.' },
    'array': { sig: 'T[N]', desc: 'Fixed-size array type.' }
};

interface LeashSymbol {
    name: string;
    type: string;
    signature: string;
    line: number;
    col: number;
    endCol: number;
}

function findSymbol(text: string, name: string): LeashSymbol | null {
    const patterns = [
        // Function definitions
        { regex: new RegExp(`(?:fnc|def)\\s+(${name})\\s*<.*?>?(?:\\s*\\((.*?)\\))?\\s*(?::\\s*(.*?))?\\s*[{|>]`, 'g'), type: 'Function' },
        // Struct/Class/Enum/Union definitions
        { regex: new RegExp(`def\\s+(${name})\\s*<.*?>?\\s*:\\s*(struct|union|enum|class|type)\\b`, 'g'), type: 'Type Definition' },
        // Class fields
        { regex: new RegExp(`\\b(pub|priv|static)?\\s*(${name})\\s*:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\\s*(=|;)`, 'g'), type: 'Field' },
        // Struct fields (with optional default value)
        { regex: new RegExp(`\\b(${name})\\s*:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\\s*(?:=\\s*[^;]+)?\\s*;`, 'g'), type: 'Struct Field' },
        // Native imports (@from)
        { regex: new RegExp(`@from\\s*\\(.*?\\)\\s*\\{[^}]*?(?:fnc|def)?\\s+(${name})\\s*\\((.*?)\\)\\s*:\\s*(.*?)\\s*;`, 'gs'), type: 'Native Function' },
        { regex: new RegExp(`@from\\s*\\(.*?\\)\\s*\\{[^}]*?(${name})\\s*:\\s*(.*?)\\s*;`, 'gs'), type: 'Native Variable' },
        // Global variables
        { regex: new RegExp(`^(${name})\\s*:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\\s*=`, 'gm'), type: 'Global Variable' },
        // Local Variables (matches 'name : type =' or 'name :=')
        { regex: new RegExp(`\\b(${name})\\s*:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\\s*=`, 'g'), type: 'Variable' },
        { regex: new RegExp(`\\b(${name})\\s*:=`, 'g'), type: 'Inferred Variable' },
        // Enum members
        { regex: new RegExp(`\\b(${name})\\s*(?::\\s*\\w+)?\\s*(?:=.*?)?\\s*(?:,|\\n|\\})`, 'g'), type: 'Enum Member' }
    ];

    for (const p of patterns) {
        p.regex.lastIndex = 0;
        let match;
        while ((match = p.regex.exec(text)) !== null) {
            const index = match.index;
            const lines = text.substring(0, index).split('\n');
            const line = lines.length - 1;
            const col = lines[lines.length - 1].length;
            
            let signature = match[0].trim().split('\n')[0];
            if (p.type === 'Function') {
                signature = `fnc ${name}(${match[2]})` + (match[3] ? ` : ${match[3]}` : '');
            } else if (p.type === 'Variable' || p.type === 'Field') {
                signature = `${name} : ${match[2] ? match[2].trim() : 'unknown'}`;
            } else if (p.type === 'Native Function') {
                signature = `[Native] fnc ${name}(${match[2]}) : ${match[3]}`;
            } else if (p.type === 'Type Definition') {
                signature = `def ${name} : ${match[2]}`;
            }

            return { name, type: p.type, signature, line, col, endCol: col + name.length };
        }
    }
    return null;
}

connection.onHover((params: TextDocumentPositionParams): Hover | null => {
    const document = documents.get(params.textDocument.uri);
    if (!document) return null;
    const text = document.getText();
    const offset = document.offsetAt(params.position);
    const word = getWordAt(text, offset);
    if (!word) return null;

    // 1. Built-in Intelligence
    if (BUILTIN_DOCS[word]) {
        const b = BUILTIN_DOCS[word];
        let content = `**Built-in**: \`${b.sig}\`  \n___  \n${b.desc}`;
        if (b.detail) content += `  \n\n${b.detail}`;
        return { contents: { kind: 'markdown', value: content } };
    }

    // 2. Scope-Aware Local Intelligence (Scanning Current Function)
    const funcScope = findEnclosingFunction(text, offset);
    if (funcScope) {
        // Check Parameters
        const paramRegex = new RegExp(`\\b(${word})\\s+([a-zA-Z0-9_<>\\[\\]&\\*]+)`, 'g');
        let pMatch;
        while ((pMatch = paramRegex.exec(funcScope.params)) !== null) {
            return {
                contents: {
                    kind: 'markdown',
                    value: `**Parameter**: \`${word} : ${pMatch[2]}\`  \n(in function \`${funcScope.name}\`)`
                }
            };
        }

        // Check Local Variables within this function's body
        const localRegex = new RegExp(`\\b(${word})\\s*(?::\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+))?\\s*(:=|=)`, 'g');
        let lMatch;
        while ((lMatch = localRegex.exec(funcScope.body)) !== null) {
            const typeInfo = lMatch[2] ? lMatch[2].trim() : (lMatch[3] === ':=' ? 'inferred' : 'unknown');
            return {
                contents: {
                    kind: 'markdown',
                    value: `**Local Variable**: \`${word} : ${typeInfo}\`  \n(defined in \`${funcScope.name}\`)`
                }
            };
        }
    }

    // 3. Enhanced Global Index Intelligence with more details
    const symbol = findSymbol(text, word);
    if (symbol) {
        let detail = `**${symbol.type}**: \`${symbol.signature}\``;
        
        // Add more context for certain symbol types
        if (symbol.type === 'Function' || symbol.type === 'Native Function') {
            // Try to get more context about the function
            const funcDetails = getFunctionDetails(text, symbol.name);
            if (funcDetails) {
                detail += `\n\n${funcDetails}`;
            }
        } else if (symbol.type === 'Type Definition' || symbol.type === 'Class' || symbol.type === 'Struct') {
            // Try to get class/struct members
            const typeDetails = getTypeDetails(text, symbol.name);
            if (typeDetails) {
                detail += `\n\n${typeDetails}`;
            }
        }
        
        return {
            contents: {
                kind: 'markdown',
                value: detail
            }
        };
    }

    // 4. Keyword Documentation
    const KEYWORD_DOCS: Record<string, string> = {
        'fnc': 'Starts a function definition.',
        'def': 'Declares a custom type (struct, class, union, enum) or an alias.',
        'return': 'Exits current function and optionally returns a value.',
        'if': 'Conditional branching. Executes block when condition is true.',
        'else': 'Alternative branch executed when condition is false.',
        'also': 'Else-if conditional. Equivalent to `else if` in other languages.',
        'unless': 'Inverted if. Executes block when condition is FALSE.',
        'alsou': 'Else-unless. Combines `else` with a false-condition check.',
        'while': 'While loop. Repeats block while condition is true.',
        'for': 'For loop. Repeats block with an index variable.',
        'do': 'Do-while loop. Executes block at least once then repeats while condition is true.',
        'foreach': 'Iterates over elements of an array, vector, string, or struct.',
        'loop': 'Infinite loop. Repeats block until `stop` or `return`.',
        'in': 'Used in foreach syntax (`in<array>`, `in<struct>`) and hash membership check.',
        'stop': 'Exits the current loop immediately (like `break`).',
        'continue': 'Skips to the next iteration of the current loop.',
        'switch': 'Multi-way branch based on an expression value.',
        'case': 'A branch case within a switch block.',
        'default': 'Default case within a switch or default value for variables.',
        'empty': 'No-op statement. Does nothing.',
        'ignore': 'Returns the default value for the current function early.',
        'defer': 'Defers execution until the function returns (stacked LIFO order).',
        'throw': 'Throws a custom error caught by `works`/`otherwise`.',
        'works': 'Error handling block. If code fails, control jumps to `otherwise`.',
        'otherwise': 'Executes if the preceding `works` block encountered an error.',
        'struct': 'Defines a struct type (value type with named fields).',
        'union': 'Defines a union type (value type holding one of several variants).',
        'enum': 'Defines an enum type (named constants with optional types/values).',
        'class': 'Defines a class type (reference type with methods and inheritance).',
        'type': 'Creates a type alias: `def MyType : type ExistingType;`.',
        'template': 'Declares a template type parameter: `def T : template;`.',
        'macro': 'Defines a compile-time code transformation macro.',
        'opdef': 'Defines an operator overload or extension method for a type.',
        'error': 'Defines a custom error type for `throw`/`works`/`otherwise`.',
        'self': 'Contextual name string. Evaluates to function/method/class name.',
        'this': 'Reference to the current class/struct instance.',
        'thisop': 'References the inner type in `opdef` definitions (`thisop.typ`).',
        'pub': 'Public visibility. Item is accessible from other modules.',
        'priv': 'Private visibility. Item is only accessible within the current module.',
        'static': 'Static member. Belongs to the class itself rather than instances.',
        'pubif': 'Conditional compilation: `pubif(condition)` includes item only on matching platforms.',
        'extern': 'Declares an external function for FFI with native libraries.',
        'use': 'Imports an item from another module: `use module::Item;`.',
        'unsafe': 'Disables runtime safety checks (bounds checking, etc.) for performance.',
        'as': 'Type conversion or renaming keyword.',
        'inline': 'Compiler hint to insert function body at call sites.',
        'imut': 'Immutable qualifier. Variables marked `imut` cannot be reassigned.',
        'create': 'Allocates a class instance on the heap and calls its constructor.',
        'del': 'Destroys a class instance and calls its destructor.',
        'is': 'Type/value check or equality operator.',
        'isnt': 'Negated type/value check or inequality operator.',
        'true': 'Boolean true literal.',
        'false': 'Boolean false literal.',
        'null': 'Null/nil literal representing absence of a value.',
        'nil': 'Null/nil literal (alias for `null`).'
    };
    if (KEYWORD_DOCS[word]) {
        return {
            contents: {
                kind: 'markdown',
                value: `**Keyword**: \`${word}\`  \n___  \n${KEYWORD_DOCS[word]}`
            }
        };
    }

    return null;
});

// Helper function to get more detailed function information
function getFunctionDetails(text: string, funcName: string): string | null {
    // Look for the function definition to extract more details
    const funcPattern = new RegExp(`(?:fnc|def)\\s+${funcName}\\s*<.*?>?(?:\\s*\\((.*?)\\))?\\s*(?::\\s*(.*?))?\\s*[{|>]`, 'g');
    let match;
    while ((match = funcPattern.exec(text)) !== null) {
        const params = match[2];
        const returnType = match[3];
        let details = `**Parameters**: ${params || 'none'}\n**Returns**: ${returnType || 'void'}`;
        
        // Try to find the function body to extract local variables
        const funcStartMatch = new RegExp(`(?:fnc|def)\\s+${funcName}\\s*<.*?>?(?:\\s*\\(.*?\\))?\\s*(?::\\s*[^{|>]*)?\\s*[{|>]`, 'g');
        funcStartMatch.lastIndex = match.index;
        const startMatch = funcStartMatch.exec(text);
        if (startMatch) {
            const startPos = startMatch.index + startMatch[0].length;
            // Find matching closing brace
            let braceCount = 1;
            let i = startPos;
            while (i < text.length && braceCount > 0) {
                if (text[i] === '{') braceCount++;
                else if (text[i] === '}') braceCount--;
                i++;
            }
            if (braceCount === 0) {
                const funcBody = text.substring(startPos, i - 1);
                // Extract local variable declarations
                const varMatches = funcBody.matchAll(/([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)\s*=/g);
                const locals = Array.from(varMatches, m => `${m[1]} : ${m[2].trim()}`);
                if (locals.length > 0) {
                    details += `\n**Local Variables**: ${locals.join(', ')}`;
                }
            }
        }
        return details;
    }
    return null;
}

// Helper function to get more detailed type information
function getTypeDetails(text: string, typeName: string): string | null {
    // Look for the type definition to extract more details
    // For structs/classes, look for field definitions
    const typePattern = new RegExp(`def\\s+${typeName}\\s*<.*?>?\\s*:\\s*(struct|class|union|enum|type)\\b(?:\\s*[^{]*)?[{]([^}]*)[}]`, 'g');
    let match;
    while ((match = typePattern.exec(text)) !== null) {
        const typeKind = match[1];
        const typeBody = match[2];
        
        let details = '';
        if (typeKind === 'struct' || typeKind === 'class') {
            // Extract field definitions
            const fieldMatches = typeBody.matchAll(/([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)(?:\s*=\s*[^;]+)?\s*;/g);
            const fields = Array.from(fieldMatches, m => `${m[1]} : ${m[2].trim()}`);
            if (fields.length > 0) {
                details += `**Fields**: ${fields.join(', ')}`;
            }
            
             // For classes, also look for methods
             if (typeKind === 'class') {
                 const methodMatches = typeBody.matchAll(/(?:fnc|def)\s+([a-zA-Z_]\w*)\s*<.*?>?\s*\((.*?)\)\s*(?::\s*(.*?))?\s*[{|>]/g);
                 const methods = Array.from(methodMatches, m => {
                     const params = m[2] || '';
                     const returnType = m[3] || 'void';
                     return `${m[1]}(${params}) : ${returnType}`;
                 });
                 if (methods.length > 0) {
                     if (details) details += '\n';
                     details += `**Methods**: ${methods.join(', ')}`;
                 }
             }
        } else if (typeKind === 'enum') {
            // Extract enum members
            const enumMatches = typeBody.matchAll(/([a-zA-Z_]\w*)\s*(?::\s*\w+)?\s*(?:=.*?)?\s*(?:,|\n|})/g);
            const members = Array.from(enumMatches, m => m[1]);
            if (members.length > 0) {
                details += `**Members**: ${members.join(', ')}`;
            }
        }
        
        return details || null;
    }
    return null;
}

function findEnclosingFunction(text: string, offset: number): { name: string, params: string, body: string } | null {
    // Basic scanner to find the containing fnc block
    const fncIndex = text.substring(0, offset).lastIndexOf('fnc ');
    if (fncIndex === -1) return null;

    const remaining = text.substring(fncIndex);
    const headMatch = new RegExp(`^fnc\\s+([a-zA-Z_]\\w*)\\s*<.*?>?(?:\\s*\\((.*?)\\))?\\s*(?::\\s*[^{|>]*)?`).exec(remaining);
    if (!headMatch) return null;

    const name = headMatch[1];
    const params = headMatch[2];
    
    // Find the end of the function (rough brace matching)
    let body = "";
    const openBrace = remaining.indexOf('{');
    if (openBrace !== -1) {
        let depth = 1;
        let i = openBrace + 1;
        while (i < remaining.length && depth > 0) {
            if (remaining[i] === '{') depth++;
            else if (remaining[i] === '}') depth--;
            i++;
        }
        body = remaining.substring(openBrace + 1, i - 1);
    }

    return { name, params, body };
}

connection.onDefinition((params: TextDocumentPositionParams): Location | null => {
    const document = documents.get(params.textDocument.uri);
    if (!document) return null;
    const text = document.getText();
    const word = getWordAt(text, document.offsetAt(params.position));
    if (!word) return null;

    const symbol = findSymbol(text, word);
    if (symbol) {
        return {
            uri: params.textDocument.uri,
            range: {
                start: { line: symbol.line, character: symbol.col },
                end: { line: symbol.line, character: symbol.endCol }
            }
        };
    }
    return null;
});

connection.onDocumentSymbol((params: DocumentSymbolParams): DocumentSymbol[] => {
    const document = documents.get(params.textDocument.uri);
    if (!document) return [];
    const text = document.getText();
    const symbols: DocumentSymbol[] = [];

    const patterns = [
        { regex: /fnc\s+([a-zA-Z_]\w*)/g, kind: SymbolKind.Function },
        { regex: /def\s+([a-zA-Z_]\w*)\s*:\s*(struct|union|enum|class)/g, kind: SymbolKind.Class },
        { regex: /([a-zA-Z_]\w*)\s*:\s*(?:imut\s+)?([a-zA-Z0-9_<>\\[\\]&\\*]+)\s*=/g, kind: SymbolKind.Variable },
        // Struct fields (with optional default value)
        { regex: /^\s*([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*]+)(?:\s*=\s*[^;]+)?\s*;/g, kind: SymbolKind.Field }
    ];

    for (const p of patterns) {
        p.regex.lastIndex = 0;
        let match;
        while ((match = p.regex.exec(text)) !== null) {
            const name = match[1];
            const lines = text.substring(0, match.index).split('\n');
            const line = lines.length - 1;
            const col = lines[lines.length - 1].length;
            const range = Range.create(line, col, line, col + name.length);
            symbols.push({
                name,
                kind: p.kind,
                range,
                selectionRange: range
            });
        }
    }
    return symbols;
});

function getWordAt(text: string, offset: number): string {
    const left = text.substring(0, offset).match(/[a-zA-Z_]\w*$/);
    const right = text.substring(offset).match(/^[a-zA-Z_]\w*/);
    if (!left && !right) return "";
    return (left ? left[0] : "") + (right ? right[0] : "");
}

connection.onCompletion((params: TextDocumentPositionParams): CompletionList => {
    const document = documents.get(params.textDocument.uri);
    if (!document) {
        return {
            isIncomplete: false,
            items: []
        };
    }
    
    const text = document.getText();
    const position = params.position;
    const offset = document.offsetAt(position);
    
    // Get current word for filtering
    const currentWord = getWordAt(text, offset);
    
    // Collect all symbols from the document
    const symbols: CompletionItem[] = [];
    
    // Add keywords
    const keywords = ["fnc", "return", "int", "void", "def", "struct", "true", "false", "null", "nil", "string", "char", "bool", "float", "uint", "if", "also", "else", "unless", "while", "for", "do", "foreach", "loop", "in", "class", "this", "thisop", "pub", "priv", "static", "stop", "continue", "empty", "ignore", "use", "switch", "case", "default", "pubif", "unsafe", "as", "inline", "defer", "error", "throw", "self", "macro", "create", "del", "is", "isnt", "union", "enum", "type", "template", "opdef", "extern", "works", "otherwise", "imut", "show", "showb", "get", "keyget", "toint", "tofloat", "tostring", "sizeof", "size", "push", "popb", "popf", "pushb", "pushf", "insert", "clear", "remove", "extend", "extendv", "insertv", "inserta", "isin", "rand", "randf", "seed", "choose", "wait", "timepass", "exit", "exec", "cstr", "lstr", "normescape", "inttobytes", "bytestoint", "floattobytes", "bytestofloat", "open", "close", "read", "write", "readln", "readb", "writeb", "replaceall", "rewind", "rename", "delete", "replace", "keys", "values"];
    
    keywords.forEach(kw => {
        if (!currentWord || kw.startsWith(currentWord)) {
            symbols.push({
                label: kw,
                kind: CompletionItemKind.Keyword,
                data: kw
            });
        }
    });
    
    // Find all symbols in document for completion
    const findAllSymbols = (text: string): LeashSymbol[] => {
        const foundSymbols: LeashSymbol[] = [];
        const patterns = [
            // Function definitions
            { regex: /(?:fnc|def)\s+([a-zA-Z_]\w*)\s*<.*?>?(?:\s*\((.*?)\))?\s*(?::\s*(.*?))?\s*[{|>]/g, type: 'Function' },
            // Struct/Class/Enum/Union definitions
            { regex: /def\s+([a-zA-Z_]\w*)\s*<.*?>?\s*:\s*(struct|union|enum|class|type)\b/g, type: 'Type Definition' },
            // Class fields
            { regex: /\b(pub|priv|static)?\s*([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\s*(=|;)/g, type: 'Field' },
            // Struct fields (with optional default value)
            { regex: /\b([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\s*(?:=\s*[^;]+)?\s*;/g, type: 'Struct Field' },
            // Native imports (@from)
            { regex: /@from\s*\(.*?\)\s*\{[^}]*?(?:fnc|def)?\s+([a-zA-Z_]\w*)\s*\((.*?)\)\s*:\s*(.*?)\s*;/g, type: 'Native Function' },
            { regex: /@from\s*\(.*?\)\s*\{[^}]*?([a-zA-Z_]\w*)\s*:\s*(.*?)\s*;/g, type: 'Native Variable' },
            // Global variables
            { regex: /^([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\s*=/gm, type: 'Global Variable' },
            // Local Variables
            { regex: /\b([a-zA-Z_]\w*)\s*:\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\s*=/g, type: 'Variable' },
            { regex: /\b([a-zA-Z_]\w*)\s*:=/g, type: 'Inferred Variable' },
            // Enum members
            { regex: /\b([a-zA-Z_]\w*)\s*(?::\s*\w+)?\s*(?:=.*?)?\s*(?:,|\n|\})/g, type: 'Enum Member' }
        ];
        
        for (const p of patterns) {
            p.regex.lastIndex = 0;
            let match;
            while ((match = p.regex.exec(text)) !== null) {
                const name = match[1];
                if (!name) continue;
                
                // Skip if it's the current word we're completing against (to avoid self-suggestions in some cases)
                // Actually, we want to include it for case like completing a function name within itself
                
                const index = match.index;
                const lines = text.substring(0, index).split('\n');
                const line = lines.length - 1;
                const col = lines[lines.length - 1].length;
                
                let signature = match[0].trim().split('\n')[0];
                 let kind: CompletionItemKind = CompletionItemKind.Text;
                
                // Map symbol types to completion item kinds
                switch (p.type) {
                    case 'Function':
                    case 'Native Function':
                        kind = CompletionItemKind.Function;
                        if (p.type === 'Function') {
                            signature = `fnc ${name}(${match[2]})` + (match[3] ? ` : ${match[3]}` : '');
                        } else {
                            signature = `[Native] fnc ${name}(${match[2]}) : ${match[3]}`;
                        }
                        break;
                    case 'Type Definition':
                        kind = CompletionItemKind.Class;
                        signature = `def ${name} : ${match[2]}`;
                        break;
                    case 'Field':
                    case 'Struct Field':
                        kind = CompletionItemKind.Field;
                        signature = `${name} : ${match[2] ? match[2].trim() : 'unknown'}`;
                        break;
                    case 'Native Variable':
                        kind = CompletionItemKind.Variable;
                        signature = `[Native] ${name} : ${match[2]}`;
                        break;
                    case 'Global Variable':
                    case 'Variable':
                    case 'Inferred Variable':
                        kind = CompletionItemKind.Variable;
                        if (p.type === 'Variable' || p.type === 'Global Variable') {
                            signature = `${name} : ${match[2] ? match[2].trim() : 'unknown'}`;
                        } else {
                            signature = `${name} := inferred`;
                        }
                        break;
                    case 'Enum Member':
                        kind = CompletionItemKind.EnumMember;
                        signature = `${name}`;
                        break;
                }
                
                // Only add if matches current word filter or no filter
                if (!currentWord || name.startsWith(currentWord)) {
                    // Avoid duplicates
                    if (!foundSymbols.some(s => s.name === name && s.line === line && s.col === col)) {
                        foundSymbols.push({ name, type: p.type, signature, line, col, endCol: col + name.length });
                    }
                }
            }
        }
        
        return foundSymbols;
    };
    
    const documentSymbols = findAllSymbols(text);
    
    documentSymbols.forEach(symbol => {
         let kind: CompletionItemKind = CompletionItemKind.Text;
        switch (symbol.type) {
            case 'Function':
            case 'Native Function':
                kind = CompletionItemKind.Function;
                break;
            case 'Type Definition':
                kind = CompletionItemKind.Class;
                break;
            case 'Field':
            case 'Struct Field':
                kind = CompletionItemKind.Field;
                break;
            case 'Native Variable':
            case 'Global Variable':
            case 'Variable':
            case 'Inferred Variable':
                kind = CompletionItemKind.Variable;
                break;
            case 'Enum Member':
                kind = CompletionItemKind.EnumMember;
                break;
        }
        
        symbols.push({
            label: symbol.name,
            kind: kind,
            detail: symbol.signature,
            documentation: {
                kind: MarkupKind.Markdown,
                value: `**${symbol.type}**\n\n\`${symbol.signature}\``
            }
        });
    });
    
    return {
        isIncomplete: false,
        items: symbols
    };
});

connection.onCompletionResolve((item: CompletionItem): CompletionItem => {
    const keyword = item.data as string;
    if (BUILTIN_DOCS[keyword]) {
        const doc = BUILTIN_DOCS[keyword];
        item.documentation = {
            kind: MarkupKind.Markdown,
            value: `**${doc.sig}**\n\n${doc.desc}`
        };
        item.detail = doc.sig;
    }
    return item;
});

documents.listen(connection);
connection.listen();
