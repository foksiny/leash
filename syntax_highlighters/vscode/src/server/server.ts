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

async function validateTextDocument(textDocument: TextDocument): Promise<void> {
    const text = textDocument.getText();
    const uri = textDocument.uri;
    
    // Create a temporary file for the compiler to check
    const tempDir = path.join(os.tmpdir(), 'leash-lsp');
    if (!fs.existsSync(tempDir)) fs.mkdirSync(tempDir, { recursive: true });
    
    // We use a specific filename to help the compiler resolve relative imports if possible
    const fileName = path.basename(URI.parse(uri).fsPath);
    const tempFile = path.join(tempDir, fileName);
    fs.writeFileSync(tempFile, text);

    // Run 'leash check' - this is the source of truth for all errors and warnings
    // (Type errors, syntax errors, unused variables, shadowed globals, etc.)
    const checkCmd = `leash check "${tempFile}"`;
    
    exec(checkCmd, { cwd: path.dirname(URI.parse(uri).fsPath) }, (error, stdout, stderr) => {
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
    'showb': { sig: 'showb(...args)', desc: 'Prints arguments to console buffer exactly as they are (no spaces/newlines).' },
    'get': { sig: 'get(prompt?: string) : string', desc: 'Reads interactive user input from the console.' },
    'size': { sig: 'size(collection) : int', desc: 'Returns the number of elements in an array, vector, or string.' },
    'pushb': { sig: 'pushb(val: T)', desc: 'Appends an element to the back of a vector.' },
    'popb': { sig: 'popb() : T', desc: 'Removes and returns the last element of a vector.' },
    'pushf': { sig: 'pushf(val: T)', desc: 'Appends an element to the front of a vector.' },
    'popf': { sig: 'popf() : T', desc: 'Removes and returns the first element of a vector.' },
    'insert': { sig: 'insert(idx: int, val: T)', desc: 'Inserts an element at a specific index in a vector.' },
    'clear': { sig: 'clear()', desc: 'Removes all elements from a vector.' },
    'remove': { sig: 'remove(idx: int)', desc: 'Removes an element at a specific index from a vector.' },
    'isin': { sig: 'isin(val: T) : bool', desc: 'Checks if a value exists in a vector.' },
    'rand': { sig: 'rand(min: int, max: int) : int', desc: 'Returns a random integer between min and max.' },
    'randf': { sig: 'randf(min: float, max: float) : float', desc: 'Returns a random float between min and max.' },
    'seed': { sig: 'seed(val: int)', desc: 'Sets the random number generator seed.' },
    'choose': { sig: 'choose(...args: string) : string', desc: 'Randomly selects one of the provided strings.' },
    'wait': { sig: 'wait(seconds: float)', desc: 'Pauses program execution for the specified time.' },
    'timepass': { sig: 'timepass() : float', desc: 'Returns elapsed time in seconds since program start.' },
    'exit': { sig: 'exit(code: int)', desc: 'Terminates the program immediately.' },
    'exec': { sig: 'exec(cmd: string, mode?: string) : string', desc: 'Executes a shell command and returns output.', detail: 'Modes: nil (output), "wait", "silent", "code" (exit code).' },
    'toint': { sig: 'toint(val) : int', desc: 'Converts a value to an integer.' },
    'tofloat': { sig: 'tofloat(val) : float', desc: 'Converts a value to a float.' },
    'tostring': { sig: 'tostring(val) : string', desc: 'Converts a numeric value to a string.' },
    'cstr': { sig: 'cstr(s: string) : *char', desc: 'Converts a Leash string to a C-style char pointer.' },
    'lstr': { sig: 'lstr(c: *char) : string', desc: 'Converts a C-style char pointer to a Leash string.' },
    'sizeof': { sig: 'sizeof(type_or_expr) : int', desc: 'Returns the size in bytes of a type or expression result.' },
    'int': { sig: 'int | int<width>', desc: 'Standard integer type. Supports bit-widths from 1 to 512.' },
    'float': { sig: 'float | float<width>', desc: 'Standard float type. Supports bit-widths from 16 to 512.' },
    'uint': { sig: 'uint | uint<width>', desc: 'Unsigned integer type.' },
    'bool': { sig: 'bool', desc: 'Boolean type (true or false).' },
    'char': { sig: 'char', desc: 'Character type (single byte).' },
    'string': { sig: 'string', desc: 'Immutable, managed string type.' },
    'void': { sig: 'void', desc: 'Represents the absence of a value.' }
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
        { regex: new RegExp(`(?:fnc|def)\\s+(${name})\\s*<.*?>?\\s*\\((.*?)\\)\\s*(?::\\s*(.*?))?\\s*[{|>]`, 'g'), type: 'Function' },
        // Struct/Class/Enum/Union definitions
        { regex: new RegExp(`def\\s+(${name})\\s*<.*?>?\\s*:\\s*(struct|union|enum|class|type)\\b`, 'g'), type: 'Type Definition' },
        // Class fields
        { regex: new RegExp(`\\b(pub|priv|static)?\\s*(${name})\\s*:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s]+)?\\s*(=|;)`, 'g'), type: 'Field' },
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

    // 3. Global Index Intelligence
    const symbol = findSymbol(text, word);
    if (symbol) {
        return {
            contents: {
                kind: 'markdown',
                value: `**${symbol.type}**: \`${symbol.signature}\``
            }
        };
    }

    // 4. Keyword Documentation
    const KEYWORD_DOCS: Record<string, string> = {
        'fnc': 'Starts a function definition.',
        'def': 'Declares a custom type (struct, class, union, enum) or an alias.',
        'imut': 'Immutable qualifier. Variables marked `imut` cannot be reassigned after initialization.',
        'pub': 'Public visibility. Item is accessible from other modules.',
        'priv': 'Private visibility. Item is only accessible within the current module.',
        'static': 'Belongs to the class itself rather than instances.',
        'self': 'Contextual name string. Evaluates to function, method, or class name.',
        'is': 'Type/value check operator. Checks if a value is of a specific type or equals another value.',
        'isnt': 'Negated type/value check operator. Checks if a value is NOT of a specific type or does NOT equal another value.',
        'this': 'Reference to the current class instance.',
        'works': 'Error handling block. If code inside fails, control jumps to `otherwise`.',
        'otherwise': 'Executes if the preceding `works` block encountered an error.',
        'unless': 'Inverted conditional. Executes block if condition is FALSE.',
        'alsou': 'Else-unless. Combines `else` with a false condition check.',
        'defer': 'Scope cleanup. Schedules a call to run when the current function/block returns.',
        'create': 'Heap allocation. Instantiates a class and calls its constructor.',
        'del': 'Manual deallocation. Explicitly calls a class destructor.',
        'unsafe': 'Disables runtime safety checks (like bounds checking) for performance.',
        'as': 'Explicit type casting or renaming.',
        'inline': 'Compiler hint to insert function body at call sites.',
        'return': 'Exits current function and optionally returns a value.'
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

function findEnclosingFunction(text: string, offset: number): { name: string, params: string, body: string } | null {
    // Basic scanner to find the containing fnc block
    const fncIndex = text.substring(0, offset).lastIndexOf('fnc ');
    if (fncIndex === -1) return null;

    const remaining = text.substring(fncIndex);
    const headMatch = /^fnc\s+([a-zA-Z_]\w*)\s*<.*?>?\s*\((.*?)\)\s*(?::\s*[^{|>]*)?/.exec(remaining);
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
        { regex: /([a-zA-Z_]\w*)\s*:\s*(?:imut\s+)?([a-zA-Z0-9_<>\\[\\]&\\*]+)\s*=/g, kind: SymbolKind.Variable }
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

connection.onCompletion((_textDocumentPosition: TextDocumentPositionParams): CompletionList => {
    const keywords = ["fnc", "return", "int", "void", "def", "struct", "true", "false", "null", "string", "char", "bool", "float", "uint", "if", "also", "else", "unless", "while", "for", "do", "foreach", "in", "class", "this", "pub", "priv", "static", "stop", "continue", "use", "switch", "case", "default", "pubif", "unsafe", "as", "inline", "defer", "error", "throw", "self", "macro", "create", "del", "is", "isnt"];
    return {
        isIncomplete: false,
        items: keywords.map(kw => ({
            label: kw,
            kind: CompletionItemKind.Keyword,
            data: kw
        }))
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
