// Leash Language Support for Monaco Editor
// Implements syntax highlighting, completions, hovers, and background diagnostics (LSP)

function registerLeashLanguage() {
  if (typeof monaco === 'undefined') return;

  // 1. REGISTER THE LANGUAGE
  monaco.languages.register({ id: 'leash' });

  // 2. MONARCH TOKENIZER (SYNTAX HIGHLIGHTING)
  monaco.languages.setMonarchTokensProvider('leash', {
    defaultToken: '',
    tokenPostfix: '.lsh',

    keywords: [
      'return', 'if', 'else', 'unless', 'while', 'for', 'do', 'foreach', 'loop', 'in', 
      'stop', 'continue', 'empty', 'ignore', 'switch', 'case', 'default', 'defer', 
      'throw', 'try', 'catch', 'fnc', 'def', 'struct', 'union', 'enum', 'class', 
      'type', 'template', 'macro', 'opdef', 'use', 'pub', 'priv', 'static', 'pubif', 'unsafe', 
      'as', 'inline', 'imut', 'create', 'del', 'this', 'thisop', 'self', 'also', 'alsou', 
      'works', 'otherwise', 'is', 'isnt'
    ],

    builtins: [
      'show', 'showb', 'get', 'set', 'toint', 'tofloat', 'tostring', 'cstr', 'lstr', 
      'size', 'cur', 'name', 'pushb', 'popb', 'pushf', 'popf', 'insert', 'clear', 
      'remove', 'extend', 'extendv', 'isin', 'rand', 'randf', 'seed', 'choose', 
      'wait', 'timepass', 'exit', 'exec', 'inttobytes', 'bytestoint', 'floattobytes', 
      'bytestofloat', 'open', 'close', 'read', 'write', 'readln', 'readb', 'writeb', 
      'readlnb', 'replaceall', 'rewind', 'rename', 'delete', 'replace', 'getKey', 
      'keys', 'values', 'push'
    ],

    typeKeywords: [
      'int', 'uint', 'float', 'bool', 'string', 'char', 'void', 'array', 'vec', 'vector', 'hash'
    ],

    operators: [
      '=', '>', '<', '!', '~', '?', ':', '==', '<=', '>=', '!=',
      '&&', '||', '++', '--', '+', '-', '*', '/', '&', '|', '^', '%',
      '<<', '>>', '+=', '-=', '*=', '/=', '&=', '|=', '^=',
      '%=', '<<=', '>>=', ':=', '::', '->', '|>'
    ],

    // Common Regular Expressions
    symbols: /[=><!~?:&|+\-*\/\^%]+/,
    escapes: /\\(?:[abfnrtv\\"']|x[0-9A-Fa-f]{1,4}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})/,

    tokenizer: {
      root: [
        // Identifiers and keywords
        [/[a-zA-Z_][a-zA-Z0-9_]*/, {
          cases: {
            '@typeKeywords': 'type',
            '@keywords': 'keyword',
            '@builtins': 'predefined',
            '@default': 'identifier'
          }
        }],

        // thisop.typ (operator definition type placeholder)
        [/\bthisop\.typ\b/, 'keyword.other'],

        // Class type references (Capitalized names)
        [/[A-Z][a-zA-Z0-9_]*/, 'type.identifier'],

        // Whitespace and comments
        { include: '@whitespace' },

        // Delimiters and brackets
        [/[{}()\[\]]/, '@brackets'],

        // Annotations / Built-in variables
        [/_[A-Z0-9_]+\b/, 'variable.predefined'],

        // Operators
        [/@symbols/, {
          cases: {
            '@operators': 'operator',
            '@default': ''
          }
        }],

        // Custom operators like namespace and assignments
        [/:=/, 'operator'],
        [/::/, 'operator'],
        [/->/, 'operator'],

        // Numbers (Hex, Binary, Octal, Floats, Decimals)
        [/0[xX][0-9a-fA-F]+(\.[0-9a-fA-F]*)?([pP][+-]?\d+)?\b/, 'number.hex'],
        [/0[bB][01]+\b/, 'number.binary'],
        [/0[oO][0-7]+\b/, 'number.octal'],
        [/\d+(\.\d+)?([eE][+-]?\d+)?\b/, 'number.float'],
        [/\d+\b/, 'number'],

        // Strings
        [/"""/, { token: 'string', bracket: '@open', next: '@mstring_double' }],
        [/'''/, { token: 'string', bracket: '@open', next: '@mstring_single' }],
        [/"([^"\\]|\\.)*$/, 'string.invalid'],  // non-teminated double quote string
        [/'([^'\\]|\\.)*$/, 'string.invalid'],  // non-terminated single quote char
        [/"/, { token: 'string.quote', bracket: '@open', next: '@string_double' }],
        [/'/, { token: 'string.quote', bracket: '@open', next: '@string_single' }],
      ],

      // Whitespace tokenizer
      whitespace: [
        [/[ \t\r\n]+/, 'white'],
        [/\/\*/, 'comment', '@comment'],
        [/\/\/.*$/, 'comment'],
      ],

      comment: [
        [/[^\/*]+/, 'comment'],
        [/\/\*/, 'comment', '@push'],    // nested comments
        [/\*\//, 'comment', '@pop'],
        [/[\/*]/, 'comment']
      ],

      // Double-quoted strings
      string_double: [
        [/[^\\"]+/, 'string'],
        [/@escapes/, 'string.escape'],
        [/\\./, 'string.escape.invalid'],
        [/"/, { token: 'string.quote', bracket: '@close', next: '@pop' }]
      ],

      // Single-quoted character/strings
      string_single: [
        [/[^\\']+/, 'string'],
        [/@escapes/, 'string.escape'],
        [/\\./, 'string.escape.invalid'],
        [/'/, { token: 'string.quote', bracket: '@close', next: '@pop' }]
      ],

      // Triple double-quoted multiline strings
      mstring_double: [
        [/[^"]+/, 'string'],
        [/"""/, { token: 'string', bracket: '@close', next: '@pop' }],
        [/"/, 'string']
      ],

      // Triple single-quoted multiline strings
      mstring_single: [
        [/[^']+/, 'string'],
        [/'''/, { token: 'string', bracket: '@close', next: '@pop' }],
        [/'/, 'string']
      ]
    }
  });

  // 3. BRACKET PAIR COLORIZATION AND LANGUAGE CONFIG
  monaco.languages.setLanguageConfiguration('leash', {
    comments: {
      lineComment: '//',
      blockComment: ['/*', '*/'],
    },
    brackets: [
      ['{', '}'],
      ['[', ']'],
      ['(', ')'],
      ['<', '>']
    ],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"', notIn: ['string'] },
      { open: "'", close: "'", notIn: ['string', 'comment'] },
      { open: '`', close: '`', notIn: ['string', 'comment'] },
    ],
    surroundingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
    ]
  });

  // 4. AUTO-COMPLETE SUGGESTIONS PROVIDER (INTELLISENSE)
  monaco.languages.registerCompletionItemProvider('leash', {
    provideCompletionItems: (model, position) => {
      const textUntilPosition = model.getValueInRange({
        startLineNumber: 1,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column
      });

      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn
      };

      const suggestions = [];

      // Add Leash Keywords
      const keywords = [
        'return', 'if', 'else', 'unless', 'while', 'for', 'do', 'foreach', 'loop', 'in', 
        'stop', 'continue', 'empty', 'ignore', 'switch', 'case', 'default', 'defer', 
        'throw', 'try', 'catch', 'fnc', 'def', 'struct', 'union', 'enum', 'class', 
        'type', 'template', 'macro', 'opdef', 'use', 'pub', 'priv', 'static', 'pubif', 'unsafe', 
        'as', 'inline', 'imut', 'create', 'del', 'this', 'thisop', 'self', 'also', 'alsou', 
        'works', 'otherwise', 'is', 'isnt'
      ];
      keywords.forEach(kw => {
        suggestions.push({
          label: kw,
          kind: monaco.languages.CompletionItemKind.Keyword,
          insertText: kw,
          range: range
        });
      });

      // Add Leash Built-in Types
      const types = ['int', 'uint', 'float', 'bool', 'string', 'char', 'void', 'array', 'vec', 'vector', 'hash'];
      types.forEach(t => {
        suggestions.push({
          label: t,
          kind: monaco.languages.CompletionItemKind.TypeParameter,
          insertText: t,
          range: range
        });
      });

      // Add Leash Built-in Functions
      const builtins = [
        { name: 'show', sig: 'show(args...)' },
        { name: 'showb', sig: 'showb(args...)' },
        { name: 'get', sig: 'get(index: int)' },
        { name: 'set', sig: 'set(index: int, val: T)' },
        { name: 'toint', sig: 'toint(val: any) : int' },
        { name: 'tofloat', sig: 'tofloat(val: any) : float' },
        { name: 'tostring', sig: 'tostring(val: any) : string' },
        { name: 'size', sig: 'size() : int' },
        { name: 'pushb', sig: 'pushb(val: T)' },
        { name: 'popb', sig: 'popb() : T' },
        { name: 'pushf', sig: 'pushf(val: T)' },
        { name: 'popf', sig: 'popf() : T' },
        { name: 'insert', sig: 'insert(index: int, val: T)' },
        { name: 'clear', sig: 'clear()' },
        { name: 'remove', sig: 'remove(index: int)' },
        { name: 'isin', sig: 'isin(item: T) : bool' },
        { name: 'rand', sig: 'rand(min: int, max: int) : int' },
        { name: 'randf', sig: 'randf() : float' },
        { name: 'seed', sig: 'seed(val: int)' },
        { name: 'wait', sig: 'wait(ms: int)' },
        { name: 'exit', sig: 'exit(code: int)' },
        { name: 'exec', sig: 'exec(cmd: string) : string' }
      ];
      builtins.forEach(bi => {
        suggestions.push({
          label: bi.name,
          kind: monaco.languages.CompletionItemKind.Function,
          insertText: bi.name,
          detail: bi.sig,
          range: range
        });
      });

      // Dynamic in-file symbol parser for smart autocomplete!
      try {
        const fullText = model.getValue();
        
        // 1. Functions matching: fnc name(args)
        const fncRegex = /fnc\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)/g;
        let match;
        while ((match = fncRegex.exec(fullText)) !== null) {
          const fncName = match[1];
          const fncArgs = match[2];
          // Skip if already in list
          if (!suggestions.some(s => s.label === fncName)) {
            suggestions.push({
              label: fncName,
              kind: monaco.languages.CompletionItemKind.Method,
              insertText: fncName + '($1)',
              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
              detail: `fnc ${fncName}(${fncArgs})`,
              range: range
            });
          }
        }

        // 2. Structs/Classes: def Name : struct/class
        const defRegex = /def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(struct|class|union|enum)/g;
        while ((match = defRegex.exec(fullText)) !== null) {
          const defName = match[1];
          const defType = match[2];
          if (!suggestions.some(s => s.label === defName)) {
            suggestions.push({
              label: defName,
              kind: defType === 'class' ? monaco.languages.CompletionItemKind.Class : monaco.languages.CompletionItemKind.Struct,
              insertText: defName,
              detail: `custom ${defType} ${defName}`,
              range: range
            });
          }
        }

        // 3. Variables: name: type = val OR name := val
        const varRegex1 = /\b([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z0-9_<>\\[\\]&*]+\s*=/g;
        while ((match = varRegex1.exec(fullText)) !== null) {
          const varName = match[1];
          if (!keywords.includes(varName) && !types.includes(varName) && !suggestions.some(s => s.label === varName)) {
            suggestions.push({
              label: varName,
              kind: monaco.languages.CompletionItemKind.Variable,
              insertText: varName,
              range: range
            });
          }
        }
        
        const varRegex2 = /\b([a-zA-Z_][a-zA-Z0-9_]*)\s*:=\s*/g;
        while ((match = varRegex2.exec(fullText)) !== null) {
          const varName = match[1];
          if (!keywords.includes(varName) && !types.includes(varName) && !suggestions.some(s => s.label === varName)) {
            suggestions.push({
              label: varName,
              kind: monaco.languages.CompletionItemKind.Variable,
              insertText: varName,
              range: range
            });
          }
        }
      } catch (err) {
        console.error("Autocomplete parser error:", err);
      }

      return { suggestions: suggestions };
    }
  });

  // 5. HOVER DETAILS PROVIDER
  monaco.languages.registerHoverProvider('leash', {
    provideHover: (model, position) => {
      const word = model.getWordAtPosition(position);
      if (!word) return null;

      const hoveredWord = word.word;

      // Built-in hovers metadata
      const builtinsHovers = {
        show: {
          title: 'show(args...) : void',
          desc: 'Prints one or more values or variables directly to the standard output console. Automatically adds spacing between values and prints a trailing newline.'
        },
        showb: {
          title: 'showb(args...) : void',
          desc: 'Prints values to the output stream without appending a newline at the end. Useful for inline buffers.'
        },
        exit: {
          title: 'exit(exit_code: int) : void',
          desc: 'Terminates the program execution immediately with the specified exit code. An exit code of `0` denotes success; non-zero values indicate errors.'
        },
        exec: {
          title: 'exec(cmd: string) : string',
          desc: 'Spawns a shell environment, executes the given shell command, blocks until it completes, and returns its standard output text string.'
        },
        wait: {
          title: 'wait(ms: int) : void',
          desc: 'Suspends the execution of the active thread for the specified duration of milliseconds.'
        },
        timepass: {
          title: 'timepass() : float',
          desc: 'Returns the high-resolution elapsed time in seconds since the program started.'
        },
        size: {
          title: 'size() : int',
          desc: 'Returns the number of elements inside a vector, array, or hash table, or the character count in a string.'
        },
        push: {
          title: 'push(value: T) : void',
          desc: 'Appends an element to the end of a vector or array dynamic container.'
        },
        pushb: {
          title: 'pushb(value: T) : void',
          desc: 'Appends an element to the back of a vector.'
        },
        popb: {
          title: 'popb() : T',
          desc: 'Removes and returns the element at the back of a vector. Raises a bounds check safety runtime error if the vector is empty.'
        },
        pushf: {
          title: 'pushf(value: T) : void',
          desc: 'Inserts an element at the front of a vector, shifting all subsequent elements right.'
        },
        popf: {
          title: 'popf() : T',
          desc: 'Removes and returns the element at the front of a vector. Shifts remaining elements left.'
        },
        toint: {
          title: 'toint(val) : int',
          desc: 'Converts floats, strings, or booleans to their corresponding integer values.'
        },
        tostring: {
          title: 'tostring(val) : string',
          desc: 'Converts any data type (integers, floats, classes) to its string representation.'
        },
        open: {
          title: 'File.open(filename: string, mode: string) : File',
          desc: 'Attempts to open the specified file with the given access mode (`"r"`, `"w"`, `"a"`, `"r+"`, etc) and returns an active File instance. Raises error if failed.'
        }
      };

      if (builtinsHovers[hoveredWord]) {
        const hoverData = builtinsHovers[hoveredWord];
        return {
          range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
          contents: [
            { value: `**${hoverData.title}**` },
            { value: hoverData.desc }
          ]
        };
      }

      // Check in-file declarations to display hover signatures
      try {
        const fullText = model.getValue();
        
        // Check if function
        const fncRegex = new RegExp(`fnc\\s+(${hoveredWord})\\s*\\(([^)]*)\\)\\s*(:\\s*([a-zA-Z0-9_<>\\[\\]&\\*\\s()]+))?`, 'g');
        let match = fncRegex.exec(fullText);
        if (match) {
          const args = match[2].trim();
          const ret = match[4] ? match[4].trim() : 'void';
          return {
            range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
            contents: [
              { value: `**fnc ${hoveredWord}(${args}) : ${ret}**` },
              { value: '*User-defined function in this file*' }
            ]
          };
        }

        // Check if Struct or Class
        const typeRegex = new RegExp(`def\\s+(${hoveredWord})\\s*:\\s*(struct|class|union|enum)`, 'g');
        match = typeRegex.exec(fullText);
        if (match) {
          const typeCat = match[2];
          return {
            range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
            contents: [
              { value: `**def ${hoveredWord} : ${typeCat}**` },
              { value: `*User-defined ${typeCat} definition*` }
            ]
          };
        }
      } catch(e) {}

      return null;
    }
  });
}

// 5b. CODE ACTIONS PROVIDER (QUICK FIXES)
monaco.languages.registerCodeActionProvider('leash', {
  provideCodeActions: (model, position, context) => {
    const codeActions = [];
    const markers = context.markers || [];

    markers.forEach(marker => {
      if (marker.severity === monaco.MarkerSeverity.Warning || marker.severity === monaco.MarkerSeverity.Error) {
        if (marker.message && marker.message.includes('not imported')) {
          const importMatch = marker.message.match(/Try adding: 'use (.+?)'/);
          if (importMatch) {
            const importPath = importMatch[1];
            codeActions.push({
              title: `Add import: ${importPath}`,
              kind: monaco.languages.CodeActionKind.QuickFix,
              edit: {
                edits: [{
                  resource: model.uri,
                  textEdit: {
                    range: new monaco.Range(1, 1, 1, 1),
                    text: `use ${importPath};\n`
                  }
                }]
              }
            });
          }
        }
      }
    });

    return { actions: codeActions, dispose: () => {} };
  }
});

// 5c. MARKDOWN LANGUAGE SUPPORT
function registerMarkdownLanguage() {
  if (typeof monaco === 'undefined') return;

  monaco.languages.register({ id: 'markdown' });

  monaco.languages.setMonarchTokensProvider('markdown', {
    defaultToken: '',
    tokenPostfix: '.md',

    tokenizer: {
      root: [
        // Headings
        [/^#{6}\s+.*$/, 'keyword'],
        [/^#{5}\s+.*$/, 'keyword'],
        [/^#{4}\s+.*$/, 'keyword'],
        [/^#{3}\s+.*$/, 'keyword'],
        [/^#{2}\s+.*$/, 'keyword'],
        [/^#{1}\s+.*$/, 'keyword'],

        // Bold + Italic
        [/(\*\*|__)(.*?)\1/, 'keyword'],
        [/(\*|_)(.*?)\1/, 'string'],

        // Inline code
        [/`[^`]+`/, 'variable.source'],

        // Code blocks
        [/^```\w*$/, 'delimiter.html', '@codeBlock'],

        // Links
        [/\[[^\]]*\]\([^)]*\)/, 'string.link'],
        [/\[[^\]]*\]\[[^\]]*\]/, 'string.link'],

        // Images
        [/\!\[[^\]]*\]\([^)]*\)/, 'string.link'],

        // Blockquotes
        [/^>\s+.*$/, 'comment'],

        // Horizontal rules
        [/^(\s*[-*_]\s*){3,}\s*$/, 'delimiter.html'],

        // Lists
        [/^\s*[-*+]\s+/, 'keyword'],
        [/^\s*\d+\.\s+/, 'keyword'],

        // Strikethrough
        [/~~[^~]+~~/, 'invalid'],

        // HTML tags
        [/<\/?[a-zA-Z][^>]*>/, 'delimiter.html'],
      ],

      codeBlock: [
        [/^```\s*$/, 'delimiter.html', '@pop'],
        [/.*$/, 'variable.source'],
      ]
    }
  });

  monaco.languages.setLanguageConfiguration('markdown', {
    comments: {
      blockComment: ['<!--', '-->'],
    },
    brackets: [
      ['{', '}'],
      ['[', ']'],
      ['(', ')'],
    ],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '`', close: '`', notIn: ['string'] },
      { open: '*', close: '*', notIn: ['string'] },
      { open: '_', close: '_', notIn: ['string'] },
      { open: '~~', close: '~~', notIn: ['string'] },
    ],
    surroundingPairs: [
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '*', close: '*' },
      { open: '_', close: '_' },
      { open: '`', close: '`' },
    ]
  });
}

// 6. LSP BACKGROUND DIAGNOSTICS PARSER
function parseTypecheckerOutput(stdoutText, activeFilePath) {
  const markers = [];
  const lines = stdoutText.split('\n');
  
  let currentIssue = null;
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // Check for error header (format: "error" or "error [...]: ...")
    if (/^error(\s*\[[^\]]*\])?\s*:\s/.test(line)) {
      if (currentIssue) {
        markers.push(currentIssue);
      }
      // Extract optional code from brackets, then message after colon
      let code = '';
      let msg = line;
      const codeMatch = msg.match(/^error\s*\[([^\]]+)\]\s*:\s?(.*)/);
      if (codeMatch) {
        code = codeMatch[1];
        msg = codeMatch[2];
      } else {
        msg = msg.replace(/^error:\s?/, '');
      }
      currentIssue = {
        severity: monaco.MarkerSeverity.Error,
        message: msg.trim(),
        file: null,
        line: 1,
        col: 1,
        code: code,
        tip: ''
      };
      continue;
    }
    
    // Check for warning header
    if (line.startsWith('warning: ')) {
      if (currentIssue) {
        markers.push(currentIssue);
      }
      currentIssue = {
        severity: monaco.MarkerSeverity.Warning,
        message: line.substring(9).trim(),
        file: null,
        line: 1,
        col: 1,
        code: '',
        tip: ''
      };
      continue;
    }
    
    // Check for location arrow:   --> filePath:line:col [code]
    if (line.trim().startsWith('--> ')) {
      if (currentIssue) {
        const locPart = line.trim().substring(4).trim();
        // Parse: file:line:col [code] or file:line:col
        const spaceIdx = locPart.indexOf(' ');
        let pathLineCol = locPart;
        let codePart = '';
        
        if (spaceIdx !== -1) {
          pathLineCol = locPart.substring(0, spaceIdx).trim();
          codePart = locPart.substring(spaceIdx).trim();
          if (codePart.startsWith('[') && codePart.endsWith(']')) {
            codePart = codePart.substring(1, codePart.length - 1);
          }
        }
        
        const lastColon = pathLineCol.lastIndexOf(':');
        if (lastColon !== -1) {
          const secondLastColon = pathLineCol.lastIndexOf(':', lastColon - 1);
          if (secondLastColon !== -1) {
            const parsedFile = pathLineCol.substring(0, secondLastColon).trim();
            const parsedLine = parseInt(pathLineCol.substring(secondLastColon + 1, lastColon), 10);
            const parsedCol = parseInt(pathLineCol.substring(lastColon + 1), 10);
            
            // Fix compiler 'None' file path bug by mapping to current active file!
            let resolvedFile = parsedFile;
            if (parsedFile === 'None' || !parsedFile) {
              resolvedFile = activeFilePath;
            } else {
              try {
                const pathModule = window.nodeRequire('path');
                const workspaceDir = pathModule.dirname(activeFilePath);
                resolvedFile = pathModule.resolve(workspaceDir, parsedFile);
              } catch(e) {
                resolvedFile = parsedFile;
              }
            }
            currentIssue.file = resolvedFile;
            currentIssue.line = isNaN(parsedLine) ? 1 : parsedLine;
            currentIssue.col = isNaN(parsedCol) ? 1 : parsedCol;
          }
        }
        if (codePart) {
          currentIssue.code = codePart;
        }
      }
      continue;
    }
    
    // Check for tip
    if (line.trim().startsWith('tip: ')) {
      if (currentIssue) {
        currentIssue.tip = line.trim().substring(5).trim();
      }
      continue;
    }
  }
  
  if (currentIssue) {
    markers.push(currentIssue);
  }
  
  // Format the markers for Monaco's editor API
  return markers.map(marker => {
    let fullMsg = marker.message;
    if (marker.tip) {
      fullMsg += `\n\nTip: ${marker.tip}`;
    }
    
    return {
      severity: marker.severity,
      message: fullMsg,
      source: 'Leash Compiler',
      startLineNumber: marker.line,
      startColumn: marker.col,
      endLineNumber: marker.line,
      endColumn: marker.col + 5, // give it a reasonable width squiggly
      code: marker.code || undefined,
      // Store absolute path as a custom property to map in problems tree
      filePath: marker.file || activeFilePath
    };
  });
}
