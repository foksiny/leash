// Renderer Process - Leash Studio IDE
// Orchestrates Monaco Editor, File Explorer, Tab Management, IPC Actions, and Command Palette

const { ipcRenderer } = window.nodeRequire('electron');
const path = window.nodeRequire('path');
const fs = window.nodeRequire('fs');
const os = window.nodeRequire('os');
const { UnleashingAPI } = window.nodeRequire(path.join(__dirname, 'api.js'));
const { ExtensionLoader } = window.nodeRequire(path.join(__dirname, 'extensionLoader.js'));

let unleashingAPI;
let extensionLoader;


// --- THEME REGISTRY (12 CUSTOM ACCENT PALETTES) ---
const THEME_REGISTRY = {
  'leash-neon': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: '00f0ff', fontStyle: 'bold' },
      { token: 'predefined', foreground: '005eff' },
      { token: 'typeKeywords', foreground: '00d8ff' },
      { token: 'type.identifier', foreground: '00d8ff', fontStyle: 'italic' },
      { token: 'string', foreground: '8c9cb8' },
      { token: 'comment', foreground: '4e5e7a', fontStyle: 'italic' },
      { token: 'number', foreground: 'ff5c6c' }
    ],
    colors: {
      'editor.background': '#04050b',
      'editor.foreground': '#e6edf8',
      'editorCursor.foreground': '#00f0ff',
      'editor.lineHighlightBackground': '#0b0d17',
      'editor.selectionBackground': '#0f1a30',
      'editorLineNumber.foreground': '#4e5e7a',
      'editorLineNumber.activeForeground': '#00f0ff',
      'editorWidget.background': '#080c14',
      'editorWidget.border': '#00f0ff'
    },
    variables: {
      '--bg-darkest': '#04050b',
      '--bg-darker': '#080a12',
      '--bg-sidebar': '#05060a',
      '--bg-panel': 'rgba(10, 14, 26, 0.65)',
      '--border-cyan': '#00f0ff',
      '--border-cyan-dim': 'rgba(0, 240, 255, 0.25)',
      '--border-blue': '#005eff',
      '--border-dark': '#12182b',
      '--text-primary': '#e6edf8',
      '--text-secondary': '#8c9cb8',
      '--text-muted': '#4e5e7a',
      '--text-cyan': '#00f0ff',
      '--text-blue': '#3385ff',
      '--glow-cyan': '0 0 10px rgba(0, 240, 255, 0.4)',
      '--glow-blue': '0 0 10px rgba(0, 94, 255, 0.3)'
    }
  },
  'unleashing-light': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '008080', fontStyle: 'bold' },
      { token: 'predefined', foreground: '0056b3' },
      { token: 'typeKeywords', foreground: '097969' },
      { token: 'type.identifier', foreground: '097969', fontStyle: 'italic' },
      { token: 'string', foreground: '555555' },
      { token: 'comment', foreground: '8c92ac', fontStyle: 'italic' },
      { token: 'number', foreground: 'd9534f' }
    ],
    colors: {
      'editor.background': '#f7f9fc',
      'editor.foreground': '#2d3748',
      'editorCursor.foreground': '#008080',
      'editor.lineHighlightBackground': '#edf2f7',
      'editor.selectionBackground': '#cbd5e0',
      'editorLineNumber.foreground': '#a0aec0',
      'editorLineNumber.activeForeground': '#008080',
      'editorWidget.background': '#ffffff',
      'editorWidget.border': '#00a3c4'
    },
    variables: {
      '--bg-darkest': '#f7f9fc',
      '--bg-darker': '#e2e8f0',
      '--bg-sidebar': '#edf2f7',
      '--bg-panel': 'rgba(255, 255, 255, 0.75)',
      '--border-cyan': '#00a3c4',
      '--border-cyan-dim': 'rgba(0, 163, 196, 0.25)',
      '--border-blue': '#2b6cb0',
      '--border-dark': '#cbd5e0',
      '--text-primary': '#2d3748',
      '--text-secondary': '#4a5568',
      '--text-muted': '#718096',
      '--text-cyan': '#008080',
      '--text-blue': '#2b6cb0',
      '--glow-cyan': '0 0 8px rgba(0, 163, 196, 0.2)',
      '--glow-blue': '0 0 8px rgba(43, 108, 176, 0.15)'
    }
  },
  'electric-light': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '2563eb', fontStyle: 'bold' },
      { token: 'predefined', foreground: '1d4ed8' },
      { token: 'typeKeywords', foreground: '0369a1' },
      { token: 'type.identifier', foreground: '0369a1', fontStyle: 'italic' },
      { token: 'string', foreground: '4b5563' },
      { token: 'comment', foreground: '9ca3af', fontStyle: 'italic' },
      { token: 'number', foreground: 'dc2626' }
    ],
    colors: {
      'editor.background': '#f3f4f6',
      'editor.foreground': '#111827',
      'editorCursor.foreground': '#2563eb',
      'editor.lineHighlightBackground': '#e5e7eb',
      'editor.selectionBackground': '#d1d5db',
      'editorLineNumber.foreground': '#9ca3af',
      'editorLineNumber.activeForeground': '#2563eb',
      'editorWidget.background': '#ffffff',
      'editorWidget.border': '#3b82f6'
    },
    variables: {
      '--bg-darkest': '#f3f4f6',
      '--bg-darker': '#e5e7eb',
      '--bg-sidebar': '#d1d5db',
      '--bg-panel': 'rgba(255, 255, 255, 0.7)',
      '--border-cyan': '#3b82f6',
      '--border-cyan-dim': 'rgba(59, 130, 246, 0.25)',
      '--border-blue': '#1d4ed8',
      '--border-dark': '#e5e7eb',
      '--text-primary': '#111827',
      '--text-secondary': '#374151',
      '--text-muted': '#6b7280',
      '--text-cyan': '#2563eb',
      '--text-blue': '#1d4ed8',
      '--glow-cyan': '0 0 8px rgba(59, 130, 246, 0.2)',
      '--glow-blue': '0 0 8px rgba(29, 78, 216, 0.15)'
    }
  },
  'cyan-mint': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '059669', fontStyle: 'bold' },
      { token: 'predefined', foreground: '047857' },
      { token: 'typeKeywords', foreground: '0d9488' },
      { token: 'type.identifier', foreground: '0d9488', fontStyle: 'italic' },
      { token: 'string', foreground: '4b5563' },
      { token: 'comment', foreground: '9ca3af', fontStyle: 'italic' },
      { token: 'number', foreground: 'ea580c' }
    ],
    colors: {
      'editor.background': '#f0fdf4',
      'editor.foreground': '#064e3b',
      'editorCursor.foreground': '#059669',
      'editor.lineHighlightBackground': '#dcfce7',
      'editor.selectionBackground': '#bbf7d0',
      'editorLineNumber.foreground': '#86efac',
      'editorLineNumber.activeForeground': '#059669',
      'editorWidget.background': '#ffffff',
      'editorWidget.border': '#059669'
    },
    variables: {
      '--bg-darkest': '#f0fdf4',
      '--bg-darker': '#dcfce7',
      '--bg-sidebar': '#bbf7d0',
      '--bg-panel': 'rgba(255, 255, 255, 0.75)',
      '--border-cyan': '#059669',
      '--border-cyan-dim': 'rgba(5, 150, 105, 0.25)',
      '--border-blue': '#047857',
      '--border-dark': '#dcfce7',
      '--text-primary': '#064e3b',
      '--text-secondary': '#047857',
      '--text-muted': '#15803d',
      '--text-cyan': '#059669',
      '--text-blue': '#047857',
      '--glow-cyan': '0 0 8px rgba(5, 150, 105, 0.2)',
      '--glow-blue': '0 0 8px rgba(4, 120, 87, 0.15)'
    }
  },
  'soft-sepia': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '8b5a2b', fontStyle: 'bold' },
      { token: 'predefined', foreground: '5c3818' },
      { token: 'typeKeywords', foreground: 'a0522d' },
      { token: 'type.identifier', foreground: 'a0522d', fontStyle: 'italic' },
      { token: 'string', foreground: '705d47' },
      { token: 'comment', foreground: 'a89c8c', fontStyle: 'italic' },
      { token: 'number', foreground: 'cd5c5c' }
    ],
    colors: {
      'editor.background': '#f4ecd8',
      'editor.foreground': '#433422',
      'editorCursor.foreground': '#8b5a2b',
      'editor.lineHighlightBackground': '#e9dfc4',
      'editor.selectionBackground': '#dfd2ae',
      'editorLineNumber.foreground': '#bdae93',
      'editorLineNumber.activeForeground': '#8b5a2b',
      'editorWidget.background': '#fbf6eb',
      'editorWidget.border': '#8b5a2b'
    },
    variables: {
      '--bg-darkest': '#f4ecd8',
      '--bg-darker': '#e9dfc4',
      '--bg-sidebar': '#dfd2ae',
      '--bg-panel': 'rgba(244, 236, 216, 0.75)',
      '--border-cyan': '#8b5a2b',
      '--border-cyan-dim': 'rgba(139, 90, 43, 0.25)',
      '--border-blue': '#5c3818',
      '--border-dark': '#e9dfc4',
      '--text-primary': '#433422',
      '--text-secondary': '#5c3818',
      '--text-muted': '#8b5a2b',
      '--text-cyan': '#8b5a2b',
      '--text-blue': '#5c3818',
      '--glow-cyan': '0 0 8px rgba(139, 90, 43, 0.15)',
      '--glow-blue': '0 0 8px rgba(92, 56, 24, 0.1)'
    }
  },
  'solarized-light': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '859900', fontStyle: 'bold' },
      { token: 'predefined', foreground: '268bd2' },
      { token: 'typeKeywords', foreground: '2aa198' },
      { token: 'type.identifier', foreground: '2aa198', fontStyle: 'italic' },
      { token: 'string', foreground: '2d3748' },
      { token: 'comment', foreground: '93a1a1', fontStyle: 'italic' },
      { token: 'number', foreground: 'cb4b16' }
    ],
    colors: {
      'editor.background': '#fdf6e3',
      'editor.foreground': '#586e75',
      'editorCursor.foreground': '#859900',
      'editor.lineHighlightBackground': '#eee8d5',
      'editor.selectionBackground': '#cbd5e0',
      'editorLineNumber.foreground': '#93a1a1',
      'editorLineNumber.activeForeground': '#859900',
      'editorWidget.background': '#fdf6e3',
      'editorWidget.border': '#2aa198'
    },
    variables: {
      '--bg-darkest': '#fdf6e3',
      '--bg-darker': '#eee8d5',
      '--bg-sidebar': '#93a1a1',
      '--bg-panel': 'rgba(253, 246, 227, 0.75)',
      '--border-cyan': '#2aa198',
      '--border-cyan-dim': 'rgba(42, 161, 152, 0.25)',
      '--border-blue': '#268bd2',
      '--border-dark': '#eee8d5',
      '--text-primary': '#586e75',
      '--text-secondary': '#657b83',
      '--text-muted': '#93a1a1',
      '--text-cyan': '#2aa198',
      '--text-blue': '#268bd2',
      '--glow-cyan': '0 0 8px rgba(42, 161, 152, 0.2)',
      '--glow-blue': '0 0 8px rgba(38, 139, 210, 0.15)'
    }
  },
  'dark-silver-light': {
    base: 'vs',
    rules: [
      { token: 'keyword', foreground: '18181b', fontStyle: 'bold' },
      { token: 'predefined', foreground: '27272a' },
      { token: 'typeKeywords', foreground: '3f3f46' },
      { token: 'type.identifier', foreground: '3f3f46', fontStyle: 'italic' },
      { token: 'string', foreground: '52525b' },
      { token: 'comment', foreground: 'a1a1aa', fontStyle: 'italic' },
      { token: 'number', foreground: 'e11d48' }
    ],
    colors: {
      'editor.background': '#fafafa',
      'editor.foreground': '#27272a',
      'editorCursor.foreground': '#18181b',
      'editor.lineHighlightBackground': '#f4f4f5',
      'editor.selectionBackground': '#e4e4e7',
      'editorLineNumber.foreground': '#a1a1aa',
      'editorLineNumber.activeForeground': '#18181b',
      'editorWidget.background': '#ffffff',
      'editorWidget.border': '#18181b'
    },
    variables: {
      '--bg-darkest': '#fafafa',
      '--bg-darker': '#f0f0f0',
      '--bg-sidebar': '#e4e4e7',
      '--bg-panel': 'rgba(255, 255, 255, 0.75)',
      '--border-cyan': '#18181b',
      '--border-cyan-dim': 'rgba(24, 24, 27, 0.25)',
      '--border-blue': '#27272a',
      '--border-dark': '#e4e4e7',
      '--text-primary': '#27272a',
      '--text-secondary': '#52525b',
      '--text-muted': '#71717a',
      '--text-cyan': '#18181b',
      '--text-blue': '#27272a',
      '--glow-cyan': '0 0 8px rgba(24, 24, 27, 0.15)',
      '--glow-blue': '0 0 8px rgba(39, 39, 42, 0.1)'
    }
  },
  'deep-crimson': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: 'ff3333', fontStyle: 'bold' },
      { token: 'predefined', foreground: 'cc0000' },
      { token: 'typeKeywords', foreground: 'ff6666' },
      { token: 'type.identifier', foreground: 'ff6666', fontStyle: 'italic' },
      { token: 'string', foreground: 'b39999' },
      { token: 'comment', foreground: '7a4e4e', fontStyle: 'italic' },
      { token: 'number', foreground: 'ff9900' }
    ],
    colors: {
      'editor.background': '#0f0303',
      'editor.foreground': '#ffe6e6',
      'editorCursor.foreground': '#ff3333',
      'editor.lineHighlightBackground': '#1c0707',
      'editor.selectionBackground': '#3a1212',
      'editorLineNumber.foreground': '#7a4e4e',
      'editorLineNumber.activeForeground': '#ff3333',
      'editorWidget.background': '#1c0707',
      'editorWidget.border': '#ff3333'
    },
    variables: {
      '--bg-darkest': '#0f0303',
      '--bg-darker': '#1c0707',
      '--bg-sidebar': '#0a0202',
      '--bg-panel': 'rgba(28, 7, 7, 0.7)',
      '--border-cyan': '#ff3333',
      '--border-cyan-dim': 'rgba(255, 51, 51, 0.25)',
      '--border-blue': '#cc0000',
      '--border-dark': '#3a1212',
      '--text-primary': '#ffe6e6',
      '--text-secondary': '#ff9999',
      '--text-muted': '#cc6666',
      '--text-cyan': '#ff3333',
      '--text-blue': '#cc0000',
      '--glow-cyan': '0 0 10px rgba(255, 51, 51, 0.4)',
      '--glow-blue': '0 0 10px rgba(204, 0, 0, 0.3)'
    }
  },
  'cyberpunk-purple': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: 'd946ef', fontStyle: 'bold' },
      { token: 'predefined', foreground: 'a855f7' },
      { token: 'typeKeywords', foreground: 'f472b6' },
      { token: 'type.identifier', foreground: 'f472b6', fontStyle: 'italic' },
      { token: 'string', foreground: 'cbd5e1' },
      { token: 'comment', foreground: '6b21a8', fontStyle: 'italic' },
      { token: 'number', foreground: 'f59e0b' }
    ],
    colors: {
      'editor.background': '#0c0214',
      'editor.foreground': '#fdf4ff',
      'editorCursor.foreground': '#d946ef',
      'editor.lineHighlightBackground': '#140524',
      'editor.selectionBackground': '#2d0b4e',
      'editorLineNumber.foreground': '#6b21a8',
      'editorLineNumber.activeForeground': '#d946ef',
      'editorWidget.background': '#140524',
      'editorWidget.border': '#d946ef'
    },
    variables: {
      '--bg-darkest': '#0c0214',
      '--bg-darker': '#140524',
      '--bg-sidebar': '#07010d',
      '--bg-panel': 'rgba(20, 5, 36, 0.7)',
      '--border-cyan': '#d946ef',
      '--border-cyan-dim': 'rgba(217, 70, 239, 0.25)',
      '--border-blue': '#a855f7',
      '--border-dark': '#2d0b4e',
      '--text-primary': '#fdf4ff',
      '--text-secondary': '#f5d0fe',
      '--text-muted': '#c084fc',
      '--text-cyan': '#d946ef',
      '--text-blue': '#a855f7',
      '--glow-cyan': '0 0 10px rgba(217, 70, 239, 0.4)',
      '--glow-blue': '0 0 10px rgba(168, 85, 247, 0.3)'
    }
  },
  'emerald-forest': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: '10b981', fontStyle: 'bold' },
      { token: 'predefined', foreground: '059669' },
      { token: 'typeKeywords', foreground: '34d399' },
      { token: 'type.identifier', foreground: '34d399', fontStyle: 'italic' },
      { token: 'string', foreground: '94a3b8' },
      { token: 'comment', foreground: '065f46', fontStyle: 'italic' },
      { token: 'number', foreground: 'f59e0b' }
    ],
    colors: {
      'editor.background': '#020f06',
      'editor.foreground': '#ecfdf5',
      'editorCursor.foreground': '#10b981',
      'editor.lineHighlightBackground': '#051c0d',
      'editor.selectionBackground': '#0e3a1d',
      'editorLineNumber.foreground': '#065f46',
      'editorLineNumber.activeForeground': '#10b981',
      'editorWidget.background': '#051c0d',
      'editorWidget.border': '#10b981'
    },
    variables: {
      '--bg-darkest': '#020f06',
      '--bg-darker': '#051c0d',
      '--bg-sidebar': '#010d04',
      '--bg-panel': 'rgba(5, 28, 13, 0.7)',
      '--border-cyan': '#10b981',
      '--border-cyan-dim': 'rgba(16, 185, 129, 0.25)',
      '--border-blue': '#059669',
      '--border-dark': '#0e3a1d',
      '--text-primary': '#ecfdf5',
      '--text-secondary': '#a7f3d0',
      '--text-muted': '#34d399',
      '--text-cyan': '#10b981',
      '--text-blue': '#059669',
      '--glow-cyan': '0 0 10px rgba(16, 185, 129, 0.4)',
      '--glow-blue': '0 0 10px rgba(5, 150, 105, 0.3)'
    }
  },
  'monokai-dark': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: 'f92672', fontStyle: 'bold' },
      { token: 'predefined', foreground: '66d9ef' },
      { token: 'typeKeywords', foreground: 'a6e22e' },
      { token: 'type.identifier', foreground: 'a6e22e', fontStyle: 'italic' },
      { token: 'string', foreground: 'e6db74' },
      { token: 'comment', foreground: '75715e', fontStyle: 'italic' },
      { token: 'number', foreground: 'ae81ff' }
    ],
    colors: {
      'editor.background': '#272822',
      'editor.foreground': '#f8f8f2',
      'editorCursor.foreground': '#f92672',
      'editor.lineHighlightBackground': '#3e3d32',
      'editor.selectionBackground': '#49483e',
      'editorLineNumber.foreground': '#75715e',
      'editorLineNumber.activeForeground': '#f92672',
      'editorWidget.background': '#1e1f1c',
      'editorWidget.border': '#f92672'
    },
    variables: {
      '--bg-darkest': '#272822',
      '--bg-darker': '#1e1f1c',
      '--bg-sidebar': '#191919',
      '--bg-panel': 'rgba(30, 31, 28, 0.75)',
      '--border-cyan': '#f92672',
      '--border-cyan-dim': 'rgba(249, 38, 114, 0.25)',
      '--border-blue': '#a6e22e',
      '--border-dark': '#3e3d32',
      '--text-primary': '#f8f8f2',
      '--text-secondary': '#a6e22e',
      '--text-muted': '#fd971f',
      '--text-cyan': '#f92672',
      '--text-blue': '#66d9ef',
      '--glow-cyan': '0 0 10px rgba(249, 38, 114, 0.4)',
      '--glow-blue': '0 0 10px rgba(102, 217, 239, 0.3)'
    }
  },
  'midnight-solarized': {
    base: 'vs-dark',
    rules: [
      { token: 'keyword', foreground: '859900', fontStyle: 'bold' },
      { token: 'predefined', foreground: '268bd2' },
      { token: 'typeKeywords', foreground: '2aa198' },
      { token: 'type.identifier', foreground: '2aa198', fontStyle: 'italic' },
      { token: 'string', foreground: 'b58900' },
      { token: 'comment', foreground: '586e75', fontStyle: 'italic' },
      { token: 'number', foreground: 'cb4b16' }
    ],
    colors: {
      'editor.background': '#002b36',
      'editor.foreground': '#839496',
      'editorCursor.foreground': '#cb4b16',
      'editor.lineHighlightBackground': '#073642',
      'editor.selectionBackground': '#073642',
      'editorLineNumber.foreground': '#586e75',
      'editorLineNumber.activeForeground': '#2aa198',
      'editorWidget.background': '#073642',
      'editorWidget.border': '#2aa198'
    },
    variables: {
      '--bg-darkest': '#002b36',
      '--bg-darker': '#073642',
      '--bg-sidebar': '#001f27',
      '--bg-panel': 'rgba(7, 54, 66, 0.75)',
      '--border-cyan': '#2aa198',
      '--border-cyan-dim': 'rgba(42, 161, 152, 0.25)',
      '--border-blue': '#268bd2',
      '--border-dark': '#073642',
      '--text-primary': '#839496',
      '--text-secondary': '#93a1a1',
      '--text-muted': '#586e75',
      '--text-cyan': '#2aa198',
      '--text-blue': '#268bd2',
      '--glow-cyan': '0 0 10px rgba(42, 161, 152, 0.3)',
      '--glow-blue': '0 0 10px rgba(38, 139, 210, 0.25)'
    }
  }
};

// --- SUPPORTED FILE TYPES ---
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico', '.tiff', '.tif', '.avif']);
const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown', '.mdown', '.mkd', '.mkdn']);

// Extension to Monaco language ID mapping — add new languages here
const EXTENSION_TO_LANG = {
  '.lsh': 'leash',
  '.leash': 'leash',
  '.json': 'json',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'typescript',
  '.jsx': 'javascript',
  '.html': 'html',
  '.htm': 'html',
  '.css': 'css',
  '.scss': 'scss',
  '.less': 'less',
  '.xml': 'xml',
  '.svg': 'xml',
  '.py': 'python',
  '.rb': 'ruby',
  '.go': 'go',
  '.rs': 'rust',
  '.c': 'c',
  '.cpp': 'cpp',
  '.h': 'c',
  '.hpp': 'cpp',
  '.java': 'java',
  '.kt': 'kotlin',
  '.swift': 'swift',
  '.sh': 'shell',
  '.bash': 'shell',
  '.zsh': 'shell',
  '.ps1': 'powershell',
  '.sql': 'sql',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.toml': 'toml',
  '.ini': 'ini',
  '.cfg': 'ini',
  '.bat': 'bat',
  '.cmd': 'bat',
  '.dockerfile': 'dockerfile',
  '.makefile': 'makefile',
  '.lua': 'lua',
  '.php': 'php',
  '.r': 'r',
  '.dart': 'dart',
  '.scala': 'scala',
  '.vue': 'html',
  '.svelte': 'html',
};

// Markdown extensions
Object.entries({
  '.md': 'markdown', '.markdown': 'markdown', '.mdown': 'markdown',
  '.mkd': 'markdown', '.mkdn': 'markdown',
}).forEach(([ext, lang]) => EXTENSION_TO_LANG[ext] = lang);

function getLanguageForFile(fileName) {
  const ext = path.extname(fileName).toLowerCase();
  return EXTENSION_TO_LANG[ext] || 'plaintext';
}

// --- STATE MANAGEMENT ---
let activeTheme = 'leash-neon';
let isRecordingKeybind = null; // Stores action name if recording hotkey
let keybinds = {
  runScript: 'F5',
  compileScript: 'Ctrl+B',
  saveFile: 'Ctrl+S',
  palette: 'Ctrl+P',
  terminal: 'Ctrl+T'
};
let activeWorkspacePath = null;
let fileTreeData = [];
let openTabs = []; // Array of { name, filePath, model, isModified }
let activeTabIndex = -1;
let editorInstance = null;
let currentRunningCommandId = null;
let typecheckDebounceTimer = null;
let autosaveDebounceTimer = null;

// Settings states
let activeFontFamily = "'Fira Code', monospace";
let activeFontSize = 14;
let isAutosaveEnabled = true;
let activeTabSize = 4;
let showMinimap = true;
let autoCheckInterval = 1000;
let isAutoFixEnabled = true;

// Breakpoints state - stores { filePath: [lineNumbers] }
let breakpoints = {};

// Drag and Drop State
let draggedNode = null;

// Context Menu Target State
let contextMenuTarget = null;
let currentPromptType = null;
let currentPromptTargetFolder = null;

// Interactive Terminal States
let terminalHistory = [];
let terminalHistoryIndex = -1;
let terminalHistoryTempInput = '';
let tabCycleMatches = [];
let tabCycleIndex = -1;
let tabCycleOriginalVal = '';
let tabCyclePrefixWord = '';
let tabCycleDirPart = '';

// --- INITIALIZATION ---
document.addEventListener("DOMContentLoaded", () => {
  setupWindowControls();
  setupSidebarNavigation();
  setupSettingsHandlers();
  setupConsoleHandlers();
  setupWorkspaceOpenBtn();
  setupFileTreeActions();
  setupCommandPalette();
  setupKeyboardShortcuts();
  setupDropdownMenus();
  setupTerminalHandlers();
  setupKeybindHandlers();
  loadSavedKeybinds();
  
  // Load Monaco Editor
  window.require.config({ paths: { 'vs': 'node_modules/monaco-editor/min/vs' } });
  window.require(['vs/editor/editor.main'], function() {
    // Register Leash Language
    registerLeashLanguage();
    
    // Register Markdown Language
    registerMarkdownLanguage();
    
    // Register all 12 themes in Monaco
    for (let themeName in THEME_REGISTRY) {
      const tData = THEME_REGISTRY[themeName];
      monaco.editor.defineTheme(themeName + '-theme', {
        base: tData.base,
        inherit: true,
        rules: tData.rules,
        colors: tData.colors
      });
    }

    // Create a dummy container for editor initialization
    const container = document.getElementById('monaco-editor-instance');
    editorInstance = monaco.editor.create(container, {
      theme: activeTheme + '-theme',
      automaticLayout: true,
      fontFamily: activeFontFamily,
      fontSize: activeFontSize,
      tabSize: activeTabSize,
      minimap: { enabled: showMinimap },
      scrollBeyondLastLine: false,
      renderWhitespace: 'selection',
      cursorBlinking: 'smooth',
      cursorSmoothCaretAnimation: 'on'
    });

    // Initialize extensions
    unleashingAPI = new UnleashingAPI({
      getEditorInstance: () => editorInstance,
      registerCustomTheme: (name) => { /* Placeholder */ },
      setTabOverride: (handler) => { /* Placeholder */ }
    });
    extensionLoader = new ExtensionLoader(unleashingAPI);
    
    ipcRenderer.invoke('get-home-dir').then(home => {
      const extPath = path.join(home, '.UnleashingExtensions');
      extensionLoader.loadExtensions(extPath);
    });


    // Handle Editor Content Changes (autosave and real-time syntax checking)
    editorInstance.onDidChangeModelContent((e) => {
      const activeTab = openTabs[activeTabIndex];
      if (activeTab && !activeTab.isModified) {
        activeTab.isModified = true;
        updateTabsUI();
      }
      
      // Trigger debounced auto-save & diagnostics checking
      handleEditorChanges();
    });
    
    // Track cursor movements for statusbar
    editorInstance.onDidChangeCursorPosition((e) => {
      document.getElementById('status-cursor-pos').innerText = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
    });

    // Restore saved workspace & open editor session!
    const savedWorkspace = localStorage.getItem('unleashing-active-workspace');
    if (savedWorkspace) {
      openWorkspace(savedWorkspace).then(() => {
        restoreSession();
      });
    } else {
      restoreSession();
    }

    // Load all saved settings
    const savedFontFamily = localStorage.getItem('unleashing-font-family');
    if (savedFontFamily) {
      activeFontFamily = savedFontFamily;
      // Update select if it's a standard option, otherwise set custom
      const fontSelect = document.getElementById('settings-font-family');
      const standardFonts = ["'Fira Code', monospace", "'Courier New', monospace", "Consolas, monospace", "'Cascadia Code', monospace"];
      if (standardFonts.includes(savedFontFamily)) {
        fontSelect.value = savedFontFamily;
      } else {
        fontSelect.value = 'custom';
        const customFontInput = document.getElementById('settings-font-custom');
        document.getElementById('sec-custom-font').classList.remove('hidden');
        customFontInput.value = savedFontFamily.split(',')[0].replace(/'/g, '');
      }
    }

    const savedFontSize = localStorage.getItem('unleashing-font-size');
    if (savedFontSize) {
      activeFontSize = parseInt(savedFontSize, 10) || 14;
      document.getElementById('settings-font-size').value = activeFontSize;
    }

    const savedTabSize = localStorage.getItem('unleashing-tab-size');
    if (savedTabSize) {
      activeTabSize = parseInt(savedTabSize, 10) || 4;
      document.getElementById('settings-tab-size').value = activeTabSize;
    }

    const savedMinimap = localStorage.getItem('unleashing-minimap');
    if (savedMinimap !== null) {
      showMinimap = savedMinimap === 'true';
      document.getElementById('settings-minimap').checked = showMinimap;
    }

    const savedAutosave = localStorage.getItem('unleashing-autosave');
    if (savedAutosave !== null) {
      isAutosaveEnabled = savedAutosave === 'true';
      document.getElementById('settings-autosave').checked = isAutosaveEnabled;
    }

    const savedAutoCheck = localStorage.getItem('unleashing-autocheck-interval');
    if (savedAutoCheck) {
      autoCheckInterval = parseInt(savedAutoCheck, 10) || 1000;
      document.getElementById('settings-autocheck-interval').value = autoCheckInterval.toString();
    }

    const savedAutoFix = localStorage.getItem('unleashing-auto-fix');
    if (savedAutoFix !== null) {
      isAutoFixEnabled = savedAutoFix === 'true';
      document.getElementById('settings-auto-fix').checked = isAutoFixEnabled;
    }

    // Apply all loaded settings to editor
    applyEditorSettings();

    // Load saved breakpoints
    const savedBreakpoints = localStorage.getItem('unleashing-breakpoints');
    if (savedBreakpoints) {
      try {
        breakpoints = JSON.parse(savedBreakpoints);
        updateBreakpointsUI();
        updateEditorBreakpoints();
      } catch(e) {}
    }

    // Setup breakpoint click handler in editor gutter
    setupBreakpointHandler();
  });
});

// --- FRAMELESS WINDOW CONTROLS ---
function setupWindowControls() {
  document.getElementById('win-min').addEventListener('click', () => ipcRenderer.send('window-minimize'));
  document.getElementById('win-max').addEventListener('click', () => ipcRenderer.send('window-maximize'));
  document.getElementById('win-close').addEventListener('click', () => ipcRenderer.send('window-close'));
}

// --- SIDEBAR TABS DRAWER SWAP ---
function setupSidebarNavigation() {
  const sidebarButtons = document.querySelectorAll('.sidebar-btn');
  const drawers = document.querySelectorAll('.drawer');

  sidebarButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.id === 'btn-palette') {
        // Toggle Command Palette
        toggleCommandPalette();
        return;
      }
      
      sidebarButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const targetDrawerId = 'drawer-' + btn.id.substring(4);
      drawers.forEach(drawer => {
        if (drawer.id === targetDrawerId) {
          drawer.classList.add('active');
        } else {
          drawer.classList.remove('active');
        }
      });
    });
  });
}

// --- SETTINGS CONTROL HANDLERS ---
function setupSettingsHandlers() {
  // Font Family Custom Selector
  const fontSelect = document.getElementById('settings-font-family');
  const customFontSec = document.getElementById('sec-custom-font');
  const customFontInput = document.getElementById('settings-font-custom');

  fontSelect.addEventListener('change', () => {
    if (fontSelect.value === 'custom') {
      customFontSec.classList.remove('hidden');
      if (customFontInput.value) {
        activeFontFamily = customFontInput.value + ", monospace";
      }
    } else {
      customFontSec.classList.add('hidden');
      activeFontFamily = fontSelect.value;
    }
    applyEditorSettings();
    localStorage.setItem('unleashing-font-family', activeFontFamily);
  });

  customFontInput.addEventListener('input', () => {
    if (customFontInput.value.trim()) {
      activeFontFamily = `'${customFontInput.value.trim()}', monospace`;
      applyEditorSettings();
      localStorage.setItem('unleashing-font-family', activeFontFamily);
    }
  });

  // Font Size
  const fontSizeInput = document.getElementById('settings-font-size');
  fontSizeInput.addEventListener('change', () => {
    activeFontSize = parseInt(fontSizeInput.value, 10) || 14;
    applyEditorSettings();
    localStorage.setItem('unleashing-font-size', activeFontSize.toString());
  });

  // Tab Size
  const tabSizeSelect = document.getElementById('settings-tab-size');
  tabSizeSelect.addEventListener('change', () => {
    activeTabSize = parseInt(tabSizeSelect.value, 10) || 4;
    applyEditorSettings();
    localStorage.setItem('unleashing-tab-size', activeTabSize.toString());
  });

  // Minimap
  const minimapCheck = document.getElementById('settings-minimap');
  minimapCheck.addEventListener('change', () => {
    showMinimap = minimapCheck.checked;
    applyEditorSettings();
    localStorage.setItem('unleashing-minimap', showMinimap ? 'true' : 'false');
  });

  // Autosave
  const autosaveCheck = document.getElementById('settings-autosave');
  autosaveCheck.addEventListener('change', () => {
    isAutosaveEnabled = autosaveCheck.checked;
    localStorage.setItem('unleashing-autosave', isAutosaveEnabled ? 'true' : 'false');
  });

  // Auto-check interval
  const autoCheckSelect = document.getElementById('settings-autocheck-interval');
  autoCheckSelect.addEventListener('change', () => {
    autoCheckInterval = parseInt(autoCheckSelect.value, 10) || 1000;
    localStorage.setItem('unleashing-autocheck-interval', autoCheckInterval.toString());
  });

  // Auto-fix imports
  const autoFixCheck = document.getElementById('settings-auto-fix');
  autoFixCheck.addEventListener('change', () => {
    isAutoFixEnabled = autoFixCheck.checked;
    localStorage.setItem('unleashing-auto-fix', isAutoFixEnabled ? 'true' : 'false');
  });
}

function applyEditorSettings() {
  if (editorInstance) {
    editorInstance.updateOptions({
      fontFamily: activeFontFamily,
      fontSize: activeFontSize,
      tabSize: activeTabSize,
      minimap: { enabled: showMinimap }
    });
  }
}

// --- WORKSPACE FOLDER OPEN HANDLER ---
function setupWorkspaceOpenBtn() {
  const openAction = async () => {
    const selectedFolder = await ipcRenderer.invoke('select-folder');
    if (selectedFolder) {
      openWorkspace(selectedFolder);
    }
  };

  document.getElementById('btn-open-workspace').addEventListener('click', openAction);
}

// --- EDITOR SESSION SAVING SYSTEM ---
function saveSession() {
  const paths = openTabs.map(t => t.filePath);
  localStorage.setItem('unleashing-session-tabs', JSON.stringify(paths));
  localStorage.setItem('unleashing-session-active', activeTabIndex.toString());
}

async function restoreSession() {
  const savedTabs = localStorage.getItem('unleashing-session-tabs');
  const savedActive = localStorage.getItem('unleashing-session-active');
  if (savedTabs && activeWorkspacePath) {
    try {
      const paths = JSON.parse(savedTabs);
      // Normalize workspace path for comparison
      const normalizedWorkspace = activeWorkspacePath.replace(/\\/g, '/').toLowerCase();
      // Sequentially restore all open tabs (only if files exist in current workspace)
      for (const filePath of paths) {
        // Normalize file path and check if it's in the current workspace
        const normalizedFilePath = filePath.replace(/\\/g, '/').toLowerCase();
        if (!normalizedFilePath.startsWith(normalizedWorkspace)) continue;
        const fileName = filePath.split(/[/\\]/).pop();
        await openFileInEditor(filePath, fileName);
      }
      if (savedActive) {
        const activeIdx = parseInt(savedActive, 10);
        if (activeIdx >= 0 && activeIdx < openTabs.length) {
          setActiveTab(activeIdx);
        }
      }
    } catch (e) {
      console.error("Failed to restore editor session:", e);
    }
  }
}

// --- MODULE AND SYMBOL IMPORT CHECKER SYSTEM ---
let workspaceSymbols = {};

async function scanWorkspaceSymbols() {
  if (!activeWorkspacePath) return;
  const newSymbols = {};
  
  const getFiles = async (dir) => {
    let results = [];
    try {
      const list = await fs.promises.readdir(dir, { withFileTypes: true });
      for (const file of list) {
        const res = path.resolve(dir, file.name);
        if (file.isDirectory()) {
          if (file.name !== 'node_modules' && file.name !== '.git') {
            results = results.concat(await getFiles(res));
          }
        } else if (file.name.endsWith('.lsh')) {
          results.push(res);
        }
      }
    } catch (e) {
      // Ignore
    }
    return results;
  };

  try {
    const files = await getFiles(activeWorkspacePath);
    for (const filePath of files) {
      const content = await fs.promises.readFile(filePath, 'utf8');
      const baseName = path.basename(filePath, '.lsh');
      
      const classRegex = /def\s+(\w+)\s*:\s*class/g;
      let match;
      while ((match = classRegex.exec(content)) !== null) {
        const className = match[1];
        newSymbols[className] = {
          filePath: filePath,
          fileBaseName: baseName,
          type: 'class'
        };
      }
      
      const fncRegex = /fnc\s+(\w+)\s*\(/g;
      while ((match = fncRegex.exec(content)) !== null) {
        const fncName = match[1];
        if (fncName && fncName !== 'main') {
          newSymbols[fncName] = {
            filePath: filePath,
            fileBaseName: baseName,
            type: 'function'
          };
        }
      }
    }
  } catch (err) {
    console.error("Error scanning workspace symbols:", err);
  }
  
  workspaceSymbols = newSymbols;
}

function runCustomStaticAnalysis(tab, monacoMarkers) {
  const code = tab.model.getValue();
  const lines = code.split('\n');

  const stdLibs = {
    'Math': { importPath: 'utils::math::Math', name: 'Math' },
    'Str': { importPath: 'utils::str::Str', name: 'Str' },
    'Tuple': { importPath: 'tuple::Tuple', name: 'Tuple' },
    'Reloader': { importPath: 'hotreload::Reloader', name: 'Reloader' },
    'VecMath': { importPath: 'utils::vecmath::VecMath', name: 'VecMath' }
  };

  const useStatements = [];
  const useRegex = /use\s+([\w::\*]+)/g;
  let useMatch;
  while ((useMatch = useRegex.exec(code)) !== null) {
    useStatements.push(useMatch[1]);
  }

  const hasImport = (target) => {
    const parts = target.split('::');
    const wildcard = parts.slice(0, -1).join('::') + '::*';
    return useStatements.some(imp => imp === target || imp === wildcard);
  };

  lines.forEach((lineText, idx) => {
    const lineNum = idx + 1;
    if (lineText.trim().startsWith('//') || lineText.trim().startsWith('use ')) return;

    for (const [ns, info] of Object.entries(stdLibs)) {
      const nsRegex = new RegExp(`\\b${ns}\\b([.<])`);
      if (nsRegex.test(lineText)) {
        if (!hasImport(info.importPath)) {
          const colNum = lineText.indexOf(ns) + 1;
          monacoMarkers.push({
            filePath: tab.filePath,
            severity: monaco.MarkerSeverity.Warning,
            message: `Namespace '${ns}' is referenced but not imported. Try adding: 'use ${info.importPath};'`,
            startLineNumber: lineNum,
            startColumn: colNum,
            endLineNumber: lineNum,
            endColumn: colNum + ns.length,
            fix: {
              type: 'add-import',
              importStatement: `use ${info.importPath};`
            }
          });
        }
      }
    }

    for (const [symbolName, info] of Object.entries(workspaceSymbols)) {
      if (info.filePath.toLowerCase() === tab.filePath.toLowerCase()) continue;

      const symbolRegex = new RegExp(`\\b${symbolName}\\b`);
      if (symbolRegex.test(lineText)) {
        const importTarget = `${info.fileBaseName}::${symbolName}`;
        if (!hasImport(importTarget)) {
          const colNum = lineText.indexOf(symbolName) + 1;
          monacoMarkers.push({
            filePath: tab.filePath,
            severity: monaco.MarkerSeverity.Warning,
            message: `${info.type === 'class' ? 'Class' : 'Function'} '${symbolName}' is defined in '${path.basename(info.filePath)}' but not imported in this file. Try adding: 'use ${importTarget};'`,
            startLineNumber: lineNum,
            startColumn: colNum,
            endLineNumber: lineNum,
            endColumn: colNum + symbolName.length,
            fix: {
              type: 'add-import',
              importStatement: `use ${importTarget};`
            }
          });
        }
      }
    }
  });
}

// --- BREAKPOINT MANAGEMENT ---
let breakpointDecorations = [];
let editorGutterClickListener = null;

function setupBreakpointHandler() {
  if (!editorInstance) return;

  editorInstance.onMouseDown((e) => {
    if (e.target.type === monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN ||
        e.target.type === monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS) {
      const lineNumber = e.target.position.lineNumber;
      const activeTab = openTabs[activeTabIndex];
      if (activeTab && activeTab.name.endsWith('.lsh')) {
        toggleBreakpoint(activeTab.filePath, lineNumber);
      }
    }
  });
}

function toggleBreakpoint(filePath, lineNumber) {
  if (!breakpoints[filePath]) {
    breakpoints[filePath] = [];
  }

  const idx = breakpoints[filePath].indexOf(lineNumber);
  if (idx !== -1) {
    breakpoints[filePath].splice(idx, 1);
  } else {
    breakpoints[filePath].push(lineNumber);
  }

  saveBreakpoints();
  updateBreakpointsUI();
  updateEditorBreakpoints();
}

function saveBreakpoints() {
  localStorage.setItem('unleashing-breakpoints', JSON.stringify(breakpoints));
}

function updateBreakpointsUI() {
  const container = document.getElementById('breakpoints-list');
  if (!container) return;

  let allBreakpoints = [];

  for (const [filePath, lines] of Object.entries(breakpoints)) {
    const fileName = path.basename(filePath);
    for (const line of lines) {
      allBreakpoints.push({ filePath, fileName, line });
    }
  }

  if (allBreakpoints.length === 0) {
    container.innerHTML = '<div class="no-breakpoints-msg">No breakpoints set. Click on a line number in the editor to add a breakpoint.</div>';
    return;
  }

  container.innerHTML = '';
  allBreakpoints.sort((a, b) => {
    if (a.fileName !== b.fileName) return a.fileName.localeCompare(b.fileName);
    return a.line - b.line;
  });

  allBreakpoints.forEach(bp => {
    const item = document.createElement('div');
    item.className = 'breakpoint-item';
    item.innerHTML = `
      <div class="breakpoint-info">
        <span class="breakpoint-icon"></span>
        <span class="breakpoint-file">${bp.fileName}</span>
        <span class="breakpoint-line">:${bp.line}</span>
      </div>
      <button class="breakpoint-remove" title="Remove breakpoint">&times;</button>
    `;
    item.querySelector('.breakpoint-remove').addEventListener('click', (e) => {
      e.stopPropagation();
      removeBreakpoint(bp.filePath, bp.line);
    });
    item.addEventListener('click', async () => {
      const fileName = bp.fileName;
      await openFileInEditor(bp.filePath, fileName);
      if (editorInstance) {
        editorInstance.setPosition({ lineNumber: bp.line, column: 1 });
        editorInstance.revealLineInCenter(bp.line);
        editorInstance.focus();
      }
    });
    container.appendChild(item);
  });
}

function removeBreakpoint(filePath, lineNumber) {
  if (breakpoints[filePath]) {
    const idx = breakpoints[filePath].indexOf(lineNumber);
    if (idx !== -1) {
      breakpoints[filePath].splice(idx, 1);
      if (breakpoints[filePath].length === 0) {
        delete breakpoints[filePath];
      }
    }
  }
  saveBreakpoints();
  updateBreakpointsUI();
  updateEditorBreakpoints();
}

function clearAllBreakpoints() {
  breakpoints = {};
  saveBreakpoints();
  updateBreakpointsUI();
  updateEditorBreakpoints();
}

function toggleBreakpointAtCursor() {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab || !activeTab.name.endsWith('.lsh') || !editorInstance) return;

  const position = editorInstance.getPosition();
  if (position) {
    toggleBreakpoint(activeTab.filePath, position.lineNumber);
  }
}

function triggerQuickFix() {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab || !activeTab.name.endsWith('.lsh')) return;

  // Force run diagnostics and apply fixes
  runBackgroundDiagnostics(activeTab);
}

function updateEditorBreakpoints() {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab || !editorInstance) return;

  const lines = breakpoints[activeTab.filePath] || [];
  const newDecorations = lines.map(lineNumber => ({
    range: new monaco.Range(lineNumber, 1, lineNumber, 1),
    options: {
      isWholeLine: true,
      glyphMarginClassName: 'breakpoint-glyph',
      linesDecorationsClassName: 'breakpoint-line-decoration'
    }
  }));

  breakpointDecorations = editorInstance.deltaDecorations(breakpointDecorations, newDecorations);
}

async function openWorkspace(folderPath) {
  // Clear any previous session since we're opening a different workspace
  localStorage.removeItem('unleashing-session-tabs');
  localStorage.removeItem('unleashing-session-active');

  activeWorkspacePath = folderPath;
  localStorage.setItem('unleashing-active-workspace', folderPath);
  document.getElementById('no-workspace-view').classList.add('hidden');
  document.getElementById('workspace-title').innerText = path.basename(folderPath);

  // Check for .uide project info
  const uidePath = path.join(folderPath, '.uide', 'project.json');
  const configureBtn = document.getElementById('opt-file-configure');
  const installExtBtn = document.getElementById('opt-file-install-ext');
  const compactExtBtn = document.getElementById('opt-file-compact-ext');
  const extSep = document.getElementById('opt-file-ext-sep');

  configureBtn.classList.remove('hidden'); // Always allow config for active workspace
  extSep.style.display = 'block';

  if (fs.existsSync(uidePath)) {
    try {
      const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
      if (data.type === 'Unleashing IDE Extension') {
        installExtBtn.classList.remove('hidden');
        compactExtBtn.classList.remove('hidden');
      } else {
        installExtBtn.classList.add('hidden');
        compactExtBtn.classList.add('hidden');
      }
    } catch(e) {
      installExtBtn.classList.add('hidden');
      compactExtBtn.classList.add('hidden');
    }
  } else {
    installExtBtn.classList.add('hidden');
    compactExtBtn.classList.add('hidden');
  }

  // Spawn background interactive PowerShell terminal in workspace folder
  ipcRenderer.send('spawn-terminal', { cwd: folderPath });

  await scanWorkspaceSymbols();
  await refreshFileTree();
}

let lastFileTreeString = '';

async function refreshFileTree() {
  if (!activeWorkspacePath) return;
  try {
    const newData = await ipcRenderer.invoke('get-file-tree', activeWorkspacePath);
    const newString = JSON.stringify(newData);
    
    if (newString !== lastFileTreeString) {
      lastFileTreeString = newString;
      fileTreeData = newData;
      renderFileTree(fileTreeData, document.getElementById('file-tree'));
    }
  } catch (err) {
    console.error("Error checking workspace file tree changes:", err);
  }
}

// Start optimized 0.1-second periodic workspace changes watcher polling
setInterval(() => {
  if (activeWorkspacePath) {
    refreshFileTree();
  }
}, 100);

// --- RENDER FILE EXPLORER TREE ---
function renderFileTree(nodes, parentEl, level = 0) {
  // If first render, clear parent
  if (level === 0) {
    parentEl.innerHTML = '';
  }

  nodes.forEach(node => {
    const nodeEl = document.createElement('div');
    nodeEl.className = 'tree-node-wrapper';
    
    const itemEl = document.createElement('div');
    itemEl.className = 'tree-node';
    itemEl.style.paddingLeft = `${level * 12 + 6}px`;
    itemEl.dataset.path = node.path;
    itemEl.dataset.isDirectory = node.isDirectory ? 'true' : 'false';

    // File type arrow indicator
    const arrowEl = document.createElement('span');
    arrowEl.className = 'node-arrow-container';
    if (node.isDirectory) {
      arrowEl.innerHTML = Icons.arrowRight;
      arrowEl.firstElementChild.classList.add('node-arrow');
    }
    itemEl.appendChild(arrowEl);

    // Node icon (closed folder, leash, json, image, markdown, zip, or text file)
    const iconEl = document.createElement('span');
    iconEl.className = 'node-icon';
    const ext = path.extname(node.name).toLowerCase();
    if (node.isDirectory) {
      iconEl.innerHTML = Icons.folder;
    } else if (node.name.endsWith('.lsh')) {
      iconEl.innerHTML = Icons.leashFile;
    } else if (node.name.endsWith('.json')) {
      iconEl.innerHTML = Icons.jsonFile;
    } else if (IMAGE_EXTENSIONS.has(ext)) {
      iconEl.innerHTML = Icons.imageFile;
    } else if (MARKDOWN_EXTENSIONS.has(ext)) {
      iconEl.innerHTML = Icons.markdownFile;
    } else if (ext === '.zip') {
      iconEl.innerHTML = Icons.uieFile;
    } else {
      iconEl.innerHTML = Icons.textFile;
    }
    itemEl.appendChild(iconEl);

    // Label name
    const labelEl = document.createElement('span');
    labelEl.className = 'node-label';
    labelEl.innerText = node.name;
    itemEl.appendChild(labelEl);

    nodeEl.appendChild(itemEl);

    // Child elements container
    if (node.isDirectory) {
      const childrenContainer = document.createElement('div');
      childrenContainer.className = 'tree-children hidden';
      nodeEl.appendChild(childrenContainer);
      
      // Recursive rendering
      renderFileTree(node.children || [], childrenContainer, level + 1);

      // Folder Click: expand / collapse
      itemEl.addEventListener('click', (e) => {
        e.stopPropagation();
        const arrow = arrowEl.querySelector('.node-arrow');
        const isCollapsed = childrenContainer.classList.toggle('hidden');
        if (isCollapsed) {
          arrow.classList.remove('expanded');
          iconEl.innerHTML = Icons.folder;
        } else {
          arrow.classList.add('expanded');
          iconEl.innerHTML = Icons.folderOpen;
        }
        selectTreeNode(itemEl);
      });

      // Drag and drop: allow dropping into folders
      setupDragDropForNode(itemEl, node, childrenContainer);
    } else {
      // File Click: open file in tabs (or preview for images)
      itemEl.addEventListener('click', (e) => {
        e.stopPropagation();
        selectTreeNode(itemEl);
        const ext = path.extname(node.name).toLowerCase();
        if (IMAGE_EXTENSIONS.has(ext)) {
          openImagePreview(node.path, node.name);
        } else {
          openFileInEditor(node.path, node.name);
        }
      });

      // Drag source for files
      itemEl.setAttribute('draggable', 'true');
      itemEl.addEventListener('dragstart', (e) => {
        e.stopPropagation();
        draggedNode = node;
        itemEl.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', node.path);
      });
      itemEl.addEventListener('dragend', (e) => {
        e.stopPropagation();
        itemEl.classList.remove('dragging');
        clearAllDragIndicators();
      });
    }

    // Context Menu Trigger on right click
    itemEl.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      selectTreeNode(itemEl);
      showContextMenu(e.clientX, e.clientY, node);
    });

    parentEl.appendChild(nodeEl);
  });
}

function selectTreeNode(itemEl) {
  document.querySelectorAll('.tree-node').forEach(el => el.classList.remove('selected'));
  itemEl.classList.add('selected');
}

// --- FILE TREE CONTEXT ACTIONS (CRUD) ---
function setupFileTreeActions() {
  const contextMenu = document.getElementById('tree-context-menu');

  // Hide context menu on left click anywhere
  document.addEventListener('click', () => {
    contextMenu.classList.add('hidden');
  });

  // Action: New File
  document.getElementById('ctx-new-file').addEventListener('click', () => createNodePrompt('file'));
  document.getElementById('action-new-file').addEventListener('click', () => createNodePrompt('file'));

  // Action: New Folder
  document.getElementById('ctx-new-folder').addEventListener('click', () => createNodePrompt('folder'));
  document.getElementById('action-new-folder').addEventListener('click', () => createNodePrompt('folder'));

  // Action: Move
  document.getElementById('ctx-move').addEventListener('click', async () => {
    if (!contextMenuTarget) return;
    const selectedFolder = await ipcRenderer.invoke('select-folder');
    if (!selectedFolder) return;
    const sourcePath = contextMenuTarget.path;
    const sourceName = contextMenuTarget.name;
    const destPath = path.join(selectedFolder, sourceName);
    performMove(sourcePath, destPath, selectedFolder);
  });

  // Action: Rename
  document.getElementById('ctx-rename').addEventListener('click', () => {
    if (!contextMenuTarget) return;
    openRenamePrompt(contextMenuTarget);
  });

  // Action: Delete
  document.getElementById('ctx-delete').addEventListener('click', () => {
    if (!contextMenuTarget) return;
    if (confirm(`Are you sure you want to delete '${contextMenuTarget.name}'?`)) {
      deleteNode(contextMenuTarget.path);
    }
  });

  // Refresh explorer
  document.getElementById('action-refresh').addEventListener('click', () => {
    refreshFileTree();
  });

  // Refresh extensions
  const refreshExtBtn = document.getElementById('action-refresh-extensions');
  if (refreshExtBtn) {
    refreshExtBtn.addEventListener('click', () => {
      // Reload IDE to load extensions
      window.location.reload();
    });
  }

  // Close workspace
  document.getElementById('action-close-folder').addEventListener('click', () => {
    closeActiveWorkspace();
  });

  // Premium Dialog Modal Event Listeners
  const promptModal = document.getElementById('custom-prompt-modal');
  const promptInput = document.getElementById('prompt-input-field');
  const promptCancel = document.getElementById('prompt-cancel-btn');
  const promptConfirm = document.getElementById('prompt-confirm-btn');

  const executeCreation = () => {
    const name = promptInput.value.trim();
    if (!name) {
      promptModal.close();
      return;
    }

    if (currentPromptType === 'rename') {
      const oldPath = currentPromptTargetFolder;
      const oldName = path.basename(oldPath);
      if (name === oldName) {
        promptModal.close();
        return;
      }
      renameNode(oldPath, name);
      promptModal.close();
      return;
    }

    if (currentPromptType === 'file') {
      ipcRenderer.invoke('create-file', { folderPath: currentPromptTargetFolder, fileName: name })
        .then(res => {
          if (res.success) {
            refreshFileTree().then(() => {
              openFileInEditor(res.filePath, name);
            });
          } else {
            alert(`Error: ${res.error}`);
          }
        });
    } else {
      ipcRenderer.invoke('create-folder', { folderPath: currentPromptTargetFolder, folderName: name })
        .then(res => {
          if (res.success) {
            refreshFileTree();
          } else {
            alert(`Error: ${res.error}`);
          }
        });
    }
    promptModal.close();
  };

  promptCancel.addEventListener('click', () => promptModal.close());
  promptConfirm.addEventListener('click', executeCreation);
  promptInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      executeCreation();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      promptModal.close();
    }
  });
}

function showContextMenu(x, y, node) {
  contextMenuTarget = node;
  const contextMenu = document.getElementById('tree-context-menu');
  contextMenu.style.left = `${x}px`;
  contextMenu.style.top = `${y}px`;
  contextMenu.classList.remove('hidden');
}

function createNodePrompt(type) {
  let targetFolder = activeWorkspacePath;
  
  if (contextMenuTarget) {
    targetFolder = contextMenuTarget.isDirectory ? contextMenuTarget.path : path.dirname(contextMenuTarget.path);
  }

  if (!targetFolder) {
    alert("Please open a workspace folder first.");
    return;
  }

  currentPromptType = type;
  currentPromptTargetFolder = targetFolder;

  const modal = document.getElementById('custom-prompt-modal');
  const titleLabel = document.getElementById('prompt-title-label');
  const confirmBtn = document.getElementById('prompt-confirm-btn');
  const input = document.getElementById('prompt-input-field');

  titleLabel.innerText = 'Create New ' + (type === 'file' ? 'File' : 'Folder');
  confirmBtn.innerText = 'Create';
  input.placeholder = type === 'file' ? 'Enter file name (e.g. main.lsh)...' : 'Enter folder name...';
  input.value = '';

  modal.showModal();
  setTimeout(() => input.focus(), 50);
}

function openRenamePrompt(node) {
  const modal = document.getElementById('custom-prompt-modal');
  const titleLabel = document.getElementById('prompt-title-label');
  const confirmBtn = document.getElementById('prompt-confirm-btn');
  const input = document.getElementById('prompt-input-field');

  currentPromptType = 'rename';
  currentPromptTargetFolder = node.path;

  titleLabel.innerText = 'Rename ' + (node.isDirectory ? 'Folder' : 'File');
  confirmBtn.innerText = 'Rename';
  input.placeholder = 'Enter new name...';
  input.value = node.name;

  modal.showModal();
  setTimeout(() => {
    input.focus();
    // Select the filename without extension for convenience
    const dotIdx = node.name.lastIndexOf('.');
    if (dotIdx > 0 && !node.isDirectory) {
      input.setSelectionRange(0, dotIdx);
    } else {
      input.select();
    }
  }, 50);
}

function renameNode(oldPath, newName) {
  const oldName = path.basename(oldPath);
  const oldExt = path.extname(oldName).toLowerCase();
  const newExt = path.extname(newName).toLowerCase();
  const extensionChanged = oldExt !== newExt;

  ipcRenderer.invoke('rename-path', { oldPath, newName })
    .then(res => {
      if (res.success) {
        // If the renamed file is open, update tab
        const tab = openTabs.find(t => t.filePath === oldPath);
        if (tab) {
          tab.name = newName;
          tab.filePath = res.newPath;

          // If the extension changed, update the model's language
          if (extensionChanged && tab.model) {
            const newLang = getLanguageForFile(newName);
            const oldLang = tab.model.getLanguageId();
            if (newLang !== oldLang) {
              monaco.editor.setModelLanguage(tab.model, newLang);

              // If it was a markdown preview tab, close preview and reopen as editor
              if (tab.isMarkdown && !MARKDOWN_EXTENSIONS.has(newExt)) {
                tab.isMarkdown = false;
                // Reopen as a regular editor tab
                ipcRenderer.invoke('read-file', res.newPath).then(readRes => {
                  if (readRes.success) {
                    const model = monaco.editor.createModel(readRes.content, newLang, monaco.Uri.file(res.newPath));
                    tab.model = model;
                    model.onDidChangeContent(() => {
                      if (!tab.isModified) {
                        tab.isModified = true;
                        updateTabsUI();
                      }
                    });
                    // Switch to editor view if this tab is active
                    if (openTabs[activeTabIndex] === tab) {
                      document.getElementById('monaco-editor-instance').style.display = 'block';
                      document.getElementById('markdown-preview-container').style.display = 'none';
                      document.getElementById('image-preview-container').style.display = 'none';
                      editorInstance.setModel(model);
                    }
                  }
                });
              }
            }
          }
        }
        refreshFileTree();
        updateTabsUI();
      } else {
        alert(`Error: ${res.error}`);
      }
    });
}

function deleteNode(targetPath) {
  ipcRenderer.invoke('delete-path', targetPath)
    .then(res => {
      if (res.success) {
        // If deleted file is open, close tab
        const tabIndex = openTabs.findIndex(t => t.filePath === targetPath);
        if (tabIndex !== -1) {
          closeTab(tabIndex);
        }
        refreshFileTree();
      } else {
        alert(`Error: ${res.error}`);
      }
    });
}

function closeActiveWorkspace() {
  activeWorkspacePath = null;
  fileTreeData = [];
  document.getElementById('file-tree').innerHTML = '';
  document.getElementById('workspace-title').innerText = 'No Folder Opened';
  document.getElementById('no-workspace-view').classList.remove('hidden');

  // Hide extension-specific menu items
  document.getElementById('opt-file-install-ext').classList.add('hidden');
  document.getElementById('opt-file-compact-ext').classList.add('hidden');
  document.getElementById('opt-file-configure').classList.add('hidden');
  document.getElementById('opt-file-ext-sep').style.display = 'none';

  // Clear session so old files won't be restored when opening a new workspace
  localStorage.removeItem('unleashing-session-tabs');
  localStorage.removeItem('unleashing-session-active');

  // Close all open tabs
  while (openTabs.length > 0) {
    closeTab(0);
  }
}

// --- DRAG AND DROP FILE TREE HELPERS ---
function setupDragDropForNode(element, node, childrenContainer) {
  element.setAttribute('draggable', 'true');

  element.addEventListener('dragstart', (e) => {
    e.stopPropagation();
    draggedNode = node;
    element.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', node.path);
  });

  element.addEventListener('dragend', (e) => {
    e.stopPropagation();
    element.classList.remove('dragging');
    clearAllDragIndicators();
  });

  element.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!draggedNode || draggedNode.path === node.path) return;
    // Prevent dropping a parent into its own child
    if (draggedNode.path.startsWith(node.path + path.sep)) return;
    e.dataTransfer.dropEffect = 'move';
    element.classList.add('drag-over', 'drag-over-folder');
  });

  element.addEventListener('dragleave', (e) => {
    e.stopPropagation();
    element.classList.remove('drag-over', 'drag-over-folder');
  });

  element.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    element.classList.remove('drag-over', 'drag-over-folder');
    if (!draggedNode || draggedNode.path === node.path) return;
    if (draggedNode.path.startsWith(node.path + path.sep)) return;

    const sourcePath = draggedNode.path;
    const sourceName = draggedNode.name;
    const destPath = path.join(node.path, sourceName);

    performMove(sourcePath, destPath, node.path);
  });
}

function clearAllDragIndicators() {
  document.querySelectorAll('.drag-over, .drag-over-folder, .dragging').forEach(el => {
    el.classList.remove('drag-over', 'drag-over-folder', 'dragging');
  });
  document.querySelectorAll('.drop-indicator').forEach(el => el.remove());
}

async function performMove(sourcePath, destPath, destFolderPath) {
  if (sourcePath === destPath) return;
  if (destPath.startsWith(sourcePath + path.sep)) return; // prevent moving into self

  const res = await ipcRenderer.invoke('move-path', { sourcePath, destPath });
  if (res.success) {
    // Update open tabs if the moved file is open
    openTabs.forEach(tab => {
      if (tab.filePath === sourcePath) {
        tab.filePath = res.newPath;
        tab.name = path.basename(res.newPath);
      } else if (tab.filePath.startsWith(sourcePath + path.sep)) {
        tab.filePath = tab.filePath.replace(sourcePath, res.newPath);
      }
    });
    updateTabsUI();
    await refreshFileTree();
  } else {
    alert(`Move failed: ${res.error}`);
  }
}

// --- IMAGE PREVIEW ---
function openImagePreview(filePath, fileName) {
  const container = document.getElementById('image-preview-container');
  const img = document.getElementById('image-preview-img');
  const info = document.getElementById('image-preview-info');
  const editorInstance = document.getElementById('monaco-editor-instance');
  const splash = document.getElementById('editor-splash');
  const mdContainer = document.getElementById('markdown-preview-container');

  // Hide other views
  splash.classList.add('hidden');
  editorInstance.style.display = 'none';
  mdContainer.style.display = 'none';
  container.style.display = 'flex';

  // Read file as base64 for display
  try {
    const ext = path.extname(filePath).toLowerCase();
    const buffer = fs.readFileSync(filePath);
    const base64 = buffer.toString('base64');
    let mimeType = 'image/png';
    if (ext === '.jpg' || ext === '.jpeg') mimeType = 'image/jpeg';
    else if (ext === '.gif') mimeType = 'image/gif';
    else if (ext === '.webp') mimeType = 'image/webp';
    else if (ext === '.svg') mimeType = 'image/svg+xml';
    else if (ext === '.bmp') mimeType = 'image/bmp';
    else if (ext === '.ico') mimeType = 'image/x-icon';
    else if (ext === '.avif') mimeType = 'image/avif';

    img.src = `data:${mimeType};base64,${base64}`;

    // Show file info
    const sizeKB = (buffer.length / 1024).toFixed(1);
    info.textContent = `${fileName} · ${sizeKB} KB · ${mimeType}`;
  } catch (err) {
    info.textContent = `Error loading image: ${err.message}`;
  }

  // Open external button
  document.getElementById('image-preview-open-external').onclick = () => {
    window.nodeRequire('electron').shell.openPath(filePath);
  };
}

// --- MARKDOWN PREVIEW ---
let markdownEditorInstance = null;
let markdownPreviewTimeout = null;

function openMarkdownPreview(filePath, content) {
  const container = document.getElementById('markdown-preview-container');
  const editorPane = document.getElementById('markdown-editor-pane');
  const previewPane = document.getElementById('markdown-preview-pane');
  const editorInstance = document.getElementById('monaco-editor-instance');
  const splash = document.getElementById('editor-splash');
  const imgContainer = document.getElementById('image-preview-container');

  // Hide other views
  splash.classList.add('hidden');
  editorInstance.style.display = 'none';
  imgContainer.style.display = 'none';
  container.style.display = 'flex';

  // Create or update the markdown editor in the left pane
  if (!markdownEditorInstance) {
    markdownEditorInstance = monaco.editor.create(editorPane, {
      theme: activeTheme + '-theme',
      automaticLayout: true,
      fontFamily: activeFontFamily,
      fontSize: activeFontSize,
      tabSize: activeTabSize,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      renderWhitespace: 'selection',
      cursorBlinking: 'smooth',
      cursorSmoothCaretAnimation: 'on',
      wordWrap: 'on'
    });

    // Listen for changes to update preview
    markdownEditorInstance.onDidChangeModelContent(() => {
      if (markdownPreviewTimeout) clearTimeout(markdownPreviewTimeout);
      markdownPreviewTimeout = setTimeout(() => {
        const text = markdownEditorInstance.getValue();
        updateMarkdownPreview(text, previewPane);
      }, 300);
    });
  }

  // Set the model
  const model = monaco.editor.createModel(content, 'markdown', monaco.Uri.file(filePath));
  markdownEditorInstance.setModel(model);

  // Initial render
  updateMarkdownPreview(content, previewPane);
}

function updateMarkdownPreview(markdownText, previewPane) {
  // Simple markdown to HTML converter (no external dependency)
  let html = markdownText;

  // Escape HTML first
  html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // Code blocks (must be before inline code)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(match, lang, code) {
    return `<pre><code class="language-${lang}">${code.trim()}</code></pre>`;
  });

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Headings
  html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>');
  html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>');
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');

  // Horizontal rules
  html = html.replace(/^(\s*[-*_]\s*){3,}\s*$/gm, '<hr>');

  // Bold + Italic
  html = html.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  html = html.replace(/___([^_]+)___/g, '<strong><em>$1</em></strong>');
  html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  html = html.replace(/_([^_]+)_/g, '<em>$1</em>');

  // Strikethrough
  html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');

  // Images
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" />');

  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');

  // Blockquotes
  html = html.replace(/^>\s+(.+)$/gm, '<blockquote>$1</blockquote>');

  // Unordered lists
  html = html.replace(/^(\s*)[-*+]\s+(.+)$/gm, '<li>$2</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, function(match) {
    return '<ul>' + match + '</ul>';
  });

  // Ordered lists
  html = html.replace(/^(\s*)\d+\.\s+(.+)$/gm, '<oli>$2</oli>');
  html = html.replace(/(<oli>.*<\/oli>\n?)+/g, function(match) {
    return '<ol>' + match.replace(/<\/?oli>/g, (m) => m === '<oli>' ? '<li>' : '</li>') + '</ol>';
  });

  // Tables
  html = html.replace(/^\|(.+)\|\n\|[-:\s|]+\|\n((?:\|.+\|\n?)*)/gm, function(match, header, body) {
    const headers = header.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('');
    const rows = body.trim().split('\n').map(row => {
      const cells = row.split('|').filter(c => c.trim()).map(c => `<td>${c.trim()}</td>`).join('');
      return `<tr>${cells}</tr>`;
    }).join('');
    return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
  });

  // Paragraphs - wrap consecutive non-tag lines
  html = html.replace(/^(?!<[a-z/])((?!\s*$).+)$/gm, '<p>$1</p>');

  // Clean up empty paragraphs
  html = html.replace(/<p>\s*<\/p>/g, '');

  // Line breaks
  html = html.replace(/\n/g, '');

  previewPane.innerHTML = html;
}

// --- FILE TAB MANAGEMENT ---
async function openFileInEditor(filePath, name) {
  // Check if file is already open
  const existingIndex = openTabs.findIndex(t => t.filePath === filePath);
  if (existingIndex !== -1) {
    setActiveTab(existingIndex);
    return;
  }

  // Read file from disk
  const res = await ipcRenderer.invoke('read-file', filePath);
  if (res.success) {
    const ext = path.extname(name).toLowerCase();
    
    // Handle markdown with preview
    if (MARKDOWN_EXTENSIONS.has(ext)) {
      openMarkdownPreview(filePath, res.content);
      // Still add to tabs for tracking
      openTabs.push({
        name: name,
        filePath: filePath,
        model: null,
        isModified: false,
        isMarkdown: true
      });
      setActiveTab(openTabs.length - 1);
      return;
    }
    
    // Determine language from extension dynamically
    const lang = getLanguageForFile(name);
    
    // Create new Monaco model
    const model = monaco.editor.createModel(res.content, lang, monaco.Uri.file(filePath));
    
    // Listen to changes to mark tab as dirty
    model.onDidChangeContent(() => {
      const tab = openTabs.find(t => t.filePath === filePath);
      if (tab && !tab.isModified) {
        tab.isModified = true;
        updateTabsUI();
      }
    });

    openTabs.push({
      name: name,
      filePath: filePath,
      model: model,
      isModified: false
    });

    setActiveTab(openTabs.length - 1);
  } else {
    alert(`Failed to read file: ${res.error}`);
  }
}

function setActiveTab(index) {
  if (index < 0 || index >= openTabs.length) {
    // Clear editor workspace if no tabs left
    activeTabIndex = -1;
    document.getElementById('editor-splash').classList.remove('hidden');
    document.getElementById('monaco-editor-instance').style.display = 'none';
    document.getElementById('image-preview-container').style.display = 'none';
    document.getElementById('markdown-preview-container').style.display = 'none';
    document.getElementById('status-file-info').innerText = 'No Active File';
    document.getElementById('debug-active-file').innerText = 'None Selected';
    updateTabsUI();
    saveSession();
    return;
  }

  activeTabIndex = index;
  const tab = openTabs[index];
  
  // Hide splash
  document.getElementById('editor-splash').classList.add('hidden');

  // Handle markdown preview tabs
  if (tab.isMarkdown) {
    document.getElementById('monaco-editor-instance').style.display = 'none';
    document.getElementById('image-preview-container').style.display = 'none';
    // Read current content for markdown preview
    ipcRenderer.invoke('read-file', tab.filePath).then(res => {
      if (res.success) openMarkdownPreview(tab.filePath, res.content);
    });
  } else {
    // Regular code editor tab
    document.getElementById('monaco-editor-instance').style.display = 'block';
    document.getElementById('image-preview-container').style.display = 'none';
    document.getElementById('markdown-preview-container').style.display = 'none';

    // Apply model to editor
    editorInstance.setModel(tab.model);
  }
  
  // Update statusbar and Run panel script labels
  document.getElementById('status-file-info').innerText = `${tab.name} (${tab.model.getLanguageId()})`;
  document.getElementById('debug-active-file').innerText = tab.name;

  updateTabsUI();
  saveSession();

  // Update breakpoints for this file
  updateEditorBreakpoints();

  // Run background syntax typecheck
  triggerTypecheck();
}

function updateTabsUI() {
  const container = document.getElementById('tabs-container');
  container.innerHTML = '';

  openTabs.forEach((tab, index) => {
    const tabEl = document.createElement('div');
    tabEl.className = `tab ${index === activeTabIndex ? 'active' : ''}`;
    
    const iconEl = document.createElement('span');
    iconEl.className = 'tab-icon';
    const tabExt = path.extname(tab.name).toLowerCase();
    if (tab.name.endsWith('.lsh')) {
      iconEl.innerHTML = Icons.leashFile;
    } else if (tab.name.endsWith('.json')) {
      iconEl.innerHTML = Icons.jsonFile;
    } else if (IMAGE_EXTENSIONS.has(tabExt)) {
      iconEl.innerHTML = Icons.imageFile;
    } else if (MARKDOWN_EXTENSIONS.has(tabExt)) {
      iconEl.innerHTML = Icons.markdownFile;
    } else if (tabExt === '.zip') {
      iconEl.innerHTML = Icons.uieFile;
    } else {
      iconEl.innerHTML = Icons.textFile;
    }
    tabEl.appendChild(iconEl);

    const nameEl = document.createElement('span');
    nameEl.className = 'tab-name';
    nameEl.innerText = tab.name;
    tabEl.appendChild(nameEl);

    // Unsaved changes dot
    if (tab.isModified) {
      const dotEl = document.createElement('span');
      dotEl.className = 'tab-modified';
      tabEl.appendChild(dotEl);
    }

    const closeEl = document.createElement('span');
    closeEl.className = 'tab-close';
    closeEl.innerHTML = Icons.closeTab;
    closeEl.addEventListener('click', (e) => {
      e.stopPropagation();
      closeTab(index);
    });
    tabEl.appendChild(closeEl);

    tabEl.addEventListener('click', () => {
      setActiveTab(index);
    });

    container.appendChild(tabEl);
  });
}

function closeTab(index) {
  const tab = openTabs[index];
  
  if (tab.isModified) {
    if (!confirm(`Save changes to '${tab.name}' before closing?`)) {
      // User cancelled closing
      return;
    }
    saveFile(tab);
  }

  // Dispose model to free memory (crucial Electron performance optimization!)
  try {
    tab.model.dispose();
  } catch(e) {
    console.error("Error disposing model:", e);
  }
  
  openTabs.splice(index, 1);

  if (activeTabIndex === index) {
    // If we closed the active tab, pick a new active tab
    setActiveTab(openTabs.length - 1);
  } else if (activeTabIndex > index) {
    activeTabIndex--;
    setActiveTab(activeTabIndex);
  } else {
    updateTabsUI();
    saveSession();
  }
}

async function saveActiveFile() {
  if (activeTabIndex !== -1) {
    await saveFile(openTabs[activeTabIndex]);
  }
}

async function saveFile(tab) {
  if (!tab) return;
  const content = tab.model.getValue();
  const res = await ipcRenderer.invoke('write-file', {
    filePath: tab.filePath,
    content: content
  });
  if (res.success) {
    tab.isModified = false;
    updateTabsUI();
    
    // Clear active autosave timers
    if (autosaveDebounceTimer) clearTimeout(autosaveDebounceTimer);
    
    // Instantly check syntax diagnostics
    triggerTypecheck();
  } else {
    console.error("Save failed:", res.error);
  }
}

// --- DEBOUNCED EDITOR INPUT ENGINE ---
function handleEditorChanges() {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab) return;

  // 1. Debounced Autosave (saves after 2 seconds of typing inactivity if enabled)
  if (isAutosaveEnabled) {
    if (autosaveDebounceTimer) clearTimeout(autosaveDebounceTimer);
    autosaveDebounceTimer = setTimeout(() => {
      saveFile(activeTab);
    }, 2000);
  }

  // 2. Update breakpoints when editing
  updateEditorBreakpoints();

  // 3. Debounced real-time Diagnostics (checks syntax after configured interval)
  triggerTypecheck();
}

function triggerTypecheck() {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab || !activeTab.name.endsWith('.lsh')) return;

  // Don't run checks if auto-check is disabled (interval = 0)
  if (autoCheckInterval === 0) return;

  if (typecheckDebounceTimer) clearTimeout(typecheckDebounceTimer);

  // Set statusbar to checking
  const statusGlow = document.getElementById('status-checker-glow');
  const statusText = document.getElementById('status-checker-text');
  statusGlow.className = 'status-indicator status-checking';
  statusText.innerText = 'Checking syntax...';

  typecheckDebounceTimer = setTimeout(() => {
    runBackgroundDiagnostics(activeTab);
  }, autoCheckInterval);
}

// --- AUTO-FIX IMPORTS ---
function applyAutoFixes(tab, monacoMarkers) {
  const code = tab.model.getValue();
  const lines = code.split('\n');

  const useStatements = new Set();
  const useRegex = /use\s+([\w::\*]+)/g;
  let useMatch;
  while ((useMatch = useRegex.exec(code)) !== null) {
    useStatements.add(useMatch[1]);
  }

  const fixesToApply = [];

  monacoMarkers.forEach(marker => {
    if (marker.fix && marker.fix.type === 'add-import') {
      const importStmt = marker.fix.importStatement;
      if (!useStatements.has(importStmt.replace('use ', '').replace(';', '').trim())) {
        fixesToApply.push({
          marker: marker,
          importStatement: importStmt
        });
      }
    }
  });

  if (fixesToApply.length === 0) return;

  // Find the best place to insert the import (after the last use statement or at the top)
  let insertLine = 0;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim().startsWith('use ')) {
      insertLine = i + 1;
    }
  }

  // Collect unique imports to add
  const uniqueImports = [];
  const addedImports = new Set();
  fixesToApply.forEach(fix => {
    const importName = fix.importStatement.replace('use ', '').replace(';', '').trim();
    if (!addedImports.has(importName)) {
      addedImports.add(importName);
      uniqueImports.push(fix.importStatement);
    }
  });

  if (uniqueImports.length === 0) return;

  // Insert imports
  const newLines = [...lines];
  newLines.splice(insertLine, 0, '');
  newLines.splice(insertLine + 1, 0, uniqueImports.join('\n'));

  // Apply the fix
  const fullCode = newLines.join('\n');
  tab.model.setValue(fullCode);

  // Save the file after auto-fix
  saveFile(tab);
}

// --- BACKGROUND COMPILER LSP DIAGNOSTICS ---
function runBackgroundDiagnostics(tab) {
  // Unique command ID for tracking
  const commandId = 'check_' + Date.now();
  let diagnosticsLog = '';

  // Index workspace symbols in background before running compiler
  scanWorkspaceSymbols().then(() => {
    ipcRenderer.send('execute-leash', {
      commandId: commandId,
      action: 'check',
      filePath: tab.filePath
    });
  });

  ipcRenderer.on(`proc-stdout-${commandId}`, (event, data) => {
    diagnosticsLog += data;
  });

  ipcRenderer.on(`proc-stderr-${commandId}`, (event, data) => {
    diagnosticsLog += data;
  });

  ipcRenderer.on(`proc-close-${commandId}`, (event, code) => {
    // Remove listeners
    ipcRenderer.removeAllListeners(`proc-stdout-${commandId}`);
    ipcRenderer.removeAllListeners(`proc-stderr-${commandId}`);
    ipcRenderer.removeAllListeners(`proc-close-${commandId}`);

    // Parse stdout/stderr errors
    const monacoMarkers = parseTypecheckerOutput(diagnosticsLog, tab.filePath);
    
    // Group markers by file path
    const markersByFile = {};
    
    // Initialize all open tabs with empty markers to clear out stale diagnostics
    openTabs.forEach(t => {
      markersByFile[t.filePath.toLowerCase()] = [];
    });

    // Run our real-time smart module dependency import and symbol usages checker
    openTabs.forEach(t => {
      if (t.name.endsWith('.lsh')) {
        runCustomStaticAnalysis(t, monacoMarkers);
      }
    });

    // Auto-fix import issues if enabled
    if (isAutoFixEnabled) {
      applyAutoFixes(tab, monacoMarkers);
    }

    // Distribute markers to their corresponding files
    monacoMarkers.forEach(marker => {
      const filePathKey = marker.filePath.toLowerCase();
      if (!markersByFile[filePathKey]) {
        markersByFile[filePathKey] = [];
      }
      markersByFile[filePathKey].push(marker);
    });
    
    // Apply markers to all open tabs
    openTabs.forEach(t => {
      const key = t.filePath.toLowerCase();
      monaco.editor.setModelMarkers(t.model, 'leash-diagnostics', markersByFile[key]);
    });

    // Update bottom panel Problems list UI
    renderProblemsList(monacoMarkers);
  });
}

function renderProblemsList(markers) {
  const problemsListEl = document.getElementById('problems-list');
  const badgeEl = document.getElementById('problems-badge');
  badgeEl.innerText = markers.length;

  if (markers.length === 0) {
    problemsListEl.innerHTML = '<div class="no-problems-msg">No issues found in your Leash workspace!</div>';
    
    // Statusbar: OK
    const statusGlow = document.getElementById('status-checker-glow');
    const statusText = document.getElementById('status-checker-text');
    statusGlow.className = 'status-indicator status-ok';
    statusText.innerText = 'Leash Ready';
    return;
  }

  problemsListEl.innerHTML = '';
  
  // Statusbar: ERRORS / WARNINGS
  const statusGlow = document.getElementById('status-checker-glow');
  const statusText = document.getElementById('status-checker-text');
  
  const hasErrors = markers.some(m => m.severity === monaco.MarkerSeverity.Error);
  if (hasErrors) {
    statusGlow.className = 'status-indicator status-error';
    statusText.innerText = `Typecheck: ${markers.length} issue(s) found`;
  } else {
    statusGlow.className = 'status-indicator status-checking';
    statusText.innerText = `Typecheck: ${markers.length} warning(s) found`;
  }

  markers.forEach(marker => {
    const isError = marker.severity === monaco.MarkerSeverity.Error;
    const itemEl = document.createElement('div');
    itemEl.className = `problem-item ${isError ? 'error' : 'warning'}`;
    
    const iconEl = document.createElement('span');
    iconEl.className = 'problem-icon';
    iconEl.innerHTML = isError ? Icons.error : Icons.warn;
    itemEl.appendChild(iconEl);

    const detailsEl = document.createElement('div');
    detailsEl.className = 'problem-details';

    const msgEl = document.createElement('div');
    msgEl.className = 'problem-message';
    // Split tooltip from tip text for display
    const msgParts = marker.message.split('\n\nTip: ');
    msgEl.innerText = msgParts[0];
    detailsEl.appendChild(msgEl);

    const metaEl = document.createElement('div');
    metaEl.className = 'problem-meta';
    metaEl.innerText = `[Line ${marker.startLineNumber}, Col ${marker.startColumn}]${marker.code ? ' ['+marker.code+']' : ''}`;
    detailsEl.appendChild(metaEl);

    if (msgParts[1]) {
      const tipEl = document.createElement('div');
      tipEl.className = 'problem-tip';
      tipEl.innerText = `Tip: ${msgParts[1]}`;
      detailsEl.appendChild(tipEl);
    }

    itemEl.appendChild(detailsEl);

    // Clicking Problem switches to correct tab and moves Editor cursor to exact location!
    itemEl.addEventListener('click', async () => {
      const targetPath = marker.filePath;
      const fileName = targetPath.split(/[/\\]/).pop();
      
      await openFileInEditor(targetPath, fileName);
      
      if (editorInstance) {
        editorInstance.setPosition({
          lineNumber: marker.startLineNumber,
          column: marker.startColumn
        });
        editorInstance.revealLineInCenter(marker.startLineNumber);
        editorInstance.focus();
      }
    });

    problemsListEl.appendChild(itemEl);
  });
}

// --- COMPILE & RUN AND DEBUGS CONTROL PANELS ---
function setupConsoleHandlers() {
  // Clear active console tab (preserving cursors and draft spans)
  document.getElementById('action-clear-console').addEventListener('click', () => {
    const activeTab = document.querySelector('.console-tab.active');
    if (activeTab && activeTab.id === 'tab-output') {
      document.getElementById('output-history').innerHTML = '';
      const outputHiddenInput = document.getElementById('output-hidden-input');
      outputHiddenInput.value = '';
      const activeDraft = document.getElementById('output-line-draft');
      if (activeDraft) {
        activeDraft.innerText = '';
      }
      if (currentRunningCommandId) {
        setTimeout(() => outputHiddenInput.focus(), 10);
      }
    } else if (activeTab && activeTab.id === 'tab-terminal') {
      document.getElementById('terminal-history').innerHTML = 'Unleashing IDE Shell Terminal [Screen Cleared]\n';
      const terminalHiddenInput = document.getElementById('terminal-hidden-input');
      terminalHiddenInput.value = '';
      const activeDraft = document.getElementById('terminal-line-draft');
      if (activeDraft) {
        activeDraft.innerText = '';
      }
      ipcRenderer.send('terminal-input', '\r\n');
      setTimeout(() => terminalHiddenInput.focus(), 10);
    }
  });

  // Resizable panel minimize toggle
  const consolePanel = document.getElementById('console-panel');
  document.getElementById('action-toggle-console').addEventListener('click', () => {
    if (consolePanel.style.height === '35px') {
      consolePanel.style.height = '240px';
    } else {
      consolePanel.style.height = '35px';
    }
  });

  // Swapping lower Console tabs (Output vs Problems)
  const tabs = document.querySelectorAll('.console-tab');
  const bodies = document.querySelectorAll('.console-body');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      const targetBodyId = 'body-' + tab.id.substring(4);
      bodies.forEach(b => {
        if (b.id === targetBodyId) {
          b.classList.add('active');
        } else {
          b.classList.remove('active');
        }
      });
    });
  });

  // Wire up Execution drawer controls
  document.getElementById('btn-run-script').addEventListener('click', () => executeActiveScript('run'));
  document.getElementById('btn-compile-script').addEventListener('click', () => executeActiveScript('compile'));
  document.getElementById('btn-dump-ir').addEventListener('click', () => executeActiveScript('dump'));
  
  // Wire up Stop Process Button
  document.getElementById('btn-stop-execution').addEventListener('click', () => {
    if (currentRunningCommandId) {
      ipcRenderer.send('kill-process', currentRunningCommandId);
    }
  });

  // Stdin input support for Output tab execution (VS Code style!)
  const outputHiddenInput = document.getElementById('output-hidden-input');
  const outputTerminalText = document.getElementById('output-terminal-text');

  outputHiddenInput.addEventListener('input', () => {
    if (currentRunningCommandId) {
      const activeDraft = document.getElementById('output-line-draft');
      if (activeDraft) {
        activeDraft.innerText = outputHiddenInput.value;
      }
      outputTerminalText.scrollTop = outputTerminalText.scrollHeight;
    }
  });

  outputHiddenInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const val = outputHiddenInput.value;
      if (currentRunningCommandId) {
        // Send input to the running subprocess stdin
        ipcRenderer.send('process-input', { commandId: currentRunningCommandId, input: val + '\n' });
        
        // Append input inline to history
        document.getElementById('output-history').innerHTML += val + '\n';
      }
      outputHiddenInput.value = '';
      const activeDraft = document.getElementById('output-line-draft');
      if (activeDraft) {
        activeDraft.innerText = '';
      }
      outputTerminalText.scrollTop = outputTerminalText.scrollHeight;
    }
  });
}

function executeActiveScript(action) {
  const activeTab = openTabs[activeTabIndex];
  if (!activeTab) {
    alert("Please open a script in the editor first.");
    return;
  }

  // Force Save active file first before executing, ensuring clean compilation!
  saveFile(activeTab);

  // Switch to Output Log console tab instantly to display compile outputs
  document.getElementById('tab-output').click();
  
  const terminal = document.getElementById('output-terminal-text');
  document.getElementById('output-history').innerHTML = `\n--- [Starting Leash CLI: ${action.toUpperCase()} on '${activeTab.name}'] ---\n`;
  document.getElementById('output-line-draft').innerText = '';
  document.getElementById('output-terminal-cursor').className = 'terminal-cursor hidden';

  // Parse target settings
  const target = document.getElementById('compiler-target').value;
  const opt = document.getElementById('compiler-opt').value;
  
  const flagsInput = document.getElementById('compiler-flags').value;
  const extraFlags = flagsInput ? flagsInput.split(',').map(f => f.trim()) : [];
  
  const argsInput = document.getElementById('program-args').value;
  const programArgs = argsInput ? argsInput.split(' ').map(a => a.trim()) : [];

  // Update Statusbar target labels
  document.getElementById('status-target-info').innerText = target ? target : 'Native';
  document.getElementById('status-opt-info').innerText = `O${opt}`;

  // Unique command ID for IPC tracking
  currentRunningCommandId = 'exec_' + Date.now();

  // Show Stop Button, Hide Run/Compile Buttons
  toggleExecutionButtons(true);

  // Show program input field if we are running an interactive Leash script!
  if (action === 'run') {
    document.getElementById('output-terminal-cursor').classList.remove('hidden');
    setTimeout(() => document.getElementById('output-hidden-input').focus(), 50);
  }

  const startTime = Date.now();

  ipcRenderer.send('execute-leash', {
    commandId: currentRunningCommandId,
    action: action,
    filePath: activeTab.filePath,
    workspacePath: activeWorkspacePath,
    args: programArgs,
    optLevel: opt,
    targetName: target || null,
    extraFlags: extraFlags
  });

  ipcRenderer.on(`proc-stdout-${currentRunningCommandId}`, (event, text) => {
    document.getElementById('output-history').innerHTML += text;
    terminal.scrollTop = terminal.scrollHeight;
  });

  ipcRenderer.on(`proc-stderr-${currentRunningCommandId}`, (event, text) => {
    const coloredErr = `<span style="color: #ff5c6c; text-shadow: 0 0 5px rgba(255, 92, 108, 0.2);">${text}</span>`;
    document.getElementById('output-history').innerHTML += coloredErr;
    terminal.scrollTop = terminal.scrollHeight;
  });

  ipcRenderer.on(`proc-close-${currentRunningCommandId}`, (event, code) => {
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(2);
    
    // Remove listeners
    ipcRenderer.removeAllListeners(`proc-stdout-${currentRunningCommandId}`);
    ipcRenderer.removeAllListeners(`proc-stderr-${currentRunningCommandId}`);
    ipcRenderer.removeAllListeners(`proc-close-${currentRunningCommandId}`);

    toggleExecutionButtons(false);
    currentRunningCommandId = null;

    // Hide program input cursor upon exit
    document.getElementById('output-terminal-cursor').classList.add('hidden');

    let summaryText = '';
    if (code === 0) {
      summaryText = `\n<span style="color: #00f0ff; text-shadow: var(--glow-cyan);">[Execution completed successfully in ${elapsed}s (Exit code: 0)]</span>\n`;
    } else if (code === -2) {
      summaryText = `\n<span style="color: #ffd000;">[Execution cancelled by user after ${elapsed}s]</span>\n`;
    } else {
      summaryText = `\n<span style="color: #ff5c6c; font-weight: bold;">[Process terminated with error code: ${code} after ${elapsed}s]</span>\n`;
    }
    
    document.getElementById('output-history').innerHTML += summaryText;
    document.getElementById('output-line-draft').innerText = '';
    terminal.scrollTop = terminal.scrollHeight;
  });
}

function toggleExecutionButtons(isRunning) {
  const runBtn = document.getElementById('btn-run-script');
  const buildBtn = document.getElementById('btn-compile-script');
  const dumpBtn = document.getElementById('btn-dump-ir');
  const stopBtn = document.getElementById('btn-stop-execution');

  if (isRunning) {
    runBtn.classList.add('hidden');
    buildBtn.classList.add('hidden');
    dumpBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
  } else {
    runBtn.classList.remove('hidden');
    buildBtn.classList.remove('hidden');
    dumpBtn.classList.remove('hidden');
    stopBtn.classList.add('hidden');
  }
}

// --- SECURE COMMAND PALETTE ENGINE ---
let paletteItems = [
  { text: 'Run Current Script', action: () => executeActiveScript('run'), shortcut: 'F5' },
  { text: 'Compile to Binary Executable', action: () => executeActiveScript('compile'), shortcut: 'Ctrl + B' },
  { text: 'Dump generated LLVM IR', action: () => executeActiveScript('dump') },
  { text: 'Check Syntax & Diagnostics', action: () => triggerTypecheck() },
  { text: 'Quick Fix: Add Missing Imports', action: () => triggerQuickFix() },
  { text: 'Toggle Breakpoint at Cursor', action: () => toggleBreakpointAtCursor() },
  { text: 'Clear All Breakpoints', action: () => clearAllBreakpoints() },
  { text: 'Clear Log Terminal', action: () => document.getElementById('action-clear-console').click() },
  { text: 'Increase Editor Font Size', action: () => adjustFontSize(1) },
  { text: 'Decrease Editor Font Size', action: () => adjustFontSize(-1) },
  { text: 'Open Settings Panel', action: () => document.getElementById('btn-settings').click(), shortcut: 'Ctrl + Shift + S' },
  { text: 'Close Active File', action: () => { if (activeTabIndex !== -1) closeTab(activeTabIndex); } },
  { text: 'Close Active Folder Workspace', action: () => closeActiveWorkspace() },
  { text: 'Install .zip Extension Package...', action: () => document.getElementById('opt-file-install-uie').click() },
  { text: 'Compact Extension Project to .zip', action: () => document.getElementById('opt-file-compact-ext').click() },
  { text: 'Reload Window', action: () => window.location.reload() }
];

let selectedPaletteIndex = 0;

function setupCommandPalette() {
  const dialog = document.getElementById('command-palette');
  const searchInput = document.getElementById('palette-search');
  const resultsContainer = document.getElementById('palette-results');

  searchInput.addEventListener('input', () => {
    selectedPaletteIndex = 0;
    renderPaletteItems(searchInput.value);
  });

  searchInput.addEventListener('keydown', (e) => {
    const items = resultsContainer.querySelectorAll('.palette-item');
    if (items.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedPaletteIndex = (selectedPaletteIndex + 1) % items.length;
      highlightPaletteItem(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedPaletteIndex = (selectedPaletteIndex - 1 + items.length) % items.length;
      highlightPaletteItem(items);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const selected = items[selectedPaletteIndex];
      if (selected) {
        selected.click();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      dialog.close();
    }
  });

  // Close dialog on clicking outside the wrapper
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) {
      dialog.close();
    }
  });
}

function toggleCommandPalette() {
  const dialog = document.getElementById('command-palette');
  if (dialog.open) {
    dialog.close();
  } else {
    dialog.showModal();
    selectedPaletteIndex = 0;
    document.getElementById('palette-search').value = '';
    renderPaletteItems('');
    setTimeout(() => document.getElementById('palette-search').focus(), 50);
  }
}

function renderPaletteItems(filterText) {
  const resultsContainer = document.getElementById('palette-results');
  resultsContainer.innerHTML = '';
  
  // Filter core commands
  let filtered = paletteItems.filter(item => 
    item.text.toLowerCase().includes(filterText.toLowerCase())
  );

  // If a workspace is loaded, also allow searching and opening files!
  if (activeWorkspacePath) {
    const addFilesFromTree = (nodes) => {
      nodes.forEach(n => {
        if (!n.isDirectory) {
          if (n.name.toLowerCase().includes(filterText.toLowerCase())) {
            filtered.push({
              text: `Open File: ${n.relativePath}`,
              action: () => openFileInEditor(n.path, n.name),
              kind: 'file'
            });
          }
        } else {
          addFilesFromTree(n.children || []);
        }
      });
    };
    addFilesFromTree(fileTreeData);
  }

  if (filtered.length === 0) {
    resultsContainer.innerHTML = '<div class="no-problems-msg">No commands or files matching query</div>';
    return;
  }

  filtered.forEach((item, index) => {
    const el = document.createElement('div');
    el.className = `palette-item ${index === selectedPaletteIndex ? 'selected' : ''}`;
    
    const labelEl = document.createElement('span');
    labelEl.innerText = item.text;
    el.appendChild(labelEl);

    if (item.shortcut) {
      const sc = document.createElement('span');
      sc.className = 'palette-shortcut';
      sc.innerText = item.shortcut;
      el.appendChild(sc);
    }

    el.addEventListener('click', () => {
      document.getElementById('command-palette').close();
      item.action();
    });

    resultsContainer.appendChild(el);
  });
}

function highlightPaletteItem(items) {
  items.forEach((item, index) => {
    if (index === selectedPaletteIndex) {
      item.classList.add('selected');
      item.scrollIntoView({ block: 'nearest' });
    } else {
      item.classList.remove('selected');
    }
  });
}

function adjustFontSize(delta) {
  activeFontSize = Math.min(Math.max(activeFontSize + delta, 10), 30);
  document.getElementById('settings-font-size').value = activeFontSize;
  applyEditorSettings();
}

// --- GLOBAL KEYBOARD SHORTCUTS ---
function setupKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    // If we're recording a keybind, don't execute actions
    if (isRecordingKeybind) return;

    // 1. Command Palette: Ctrl+Shift+P OR Ctrl+P OR custom keybind
    if ((e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'p') || matchKeybind(e, 'palette')) {
      e.preventDefault();
      toggleCommandPalette();
      return;
    }
    
    // 2. Open Folder: Ctrl+O
    if (e.ctrlKey && e.key.toLowerCase() === 'o') {
      e.preventDefault();
      document.getElementById('btn-open-workspace').click();
      return;
    }

    // 3. Save File: Ctrl+S OR custom keybind
    if (matchKeybind(e, 'saveFile')) {
      e.preventDefault();
      saveActiveFile();
      return;
    }

    // 4. Run Script: F5 OR custom keybind
    if (matchKeybind(e, 'runScript')) {
      e.preventDefault();
      executeActiveScript('run');
      return;
    }

    // 5. Compile Script: Ctrl+B OR custom keybind
    if (matchKeybind(e, 'compileScript')) {
      e.preventDefault();
      executeActiveScript('compile');
      return;
    }

    // 5b. Quick Fix: Ctrl+. (period)
    if (e.ctrlKey && e.key === '.') {
      e.preventDefault();
      triggerQuickFix();
      return;
    }

    // 5c. Toggle Breakpoint: F9
    if (e.key === 'F9') {
      e.preventDefault();
      toggleBreakpointAtCursor();
      return;
    }

    // 6. Toggle Terminal Drawer: custom keybind or Ctrl+` or Ctrl+T
    if (matchKeybind(e, 'terminal') || (e.ctrlKey && e.key === '`')) {
      e.preventDefault();
      const consolePanel = document.getElementById('console-panel');
      const inputField = document.getElementById('terminal-input-field');
      const tabTerminal = document.getElementById('tab-terminal');
      
      // If console panel is minimized, open it and show terminal
      if (consolePanel.style.height === '35px') {
        consolePanel.style.height = '240px';
        tabTerminal.click();
        inputField.focus();
      } else {
        // Toggle minimize/restore
        if (tabTerminal.classList.contains('active')) {
          consolePanel.style.height = '35px';
        } else {
          tabTerminal.click();
          inputField.focus();
        }
      }
      return;
    }

    // 7. Sidebar Navigation hotkeys: Ctrl+Shift+E (Explorer), Ctrl+Shift+D (Debug), Ctrl+Shift+S (Settings)
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'e') {
      e.preventDefault();
      document.getElementById('btn-explorer').click();
      return;
    }
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'd') {
      e.preventDefault();
      document.getElementById('btn-debug').click();
      return;
    }
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 's') {
      e.preventDefault();
      document.getElementById('btn-settings').click();
      return;
    }

    // 8. UI Zooming Hotkeys: Ctrl + Plus, Ctrl + Minus, Ctrl + Zero
    if (e.ctrlKey && (e.key === '=' || e.key === '+')) {
      e.preventDefault();
      const { webFrame } = window.nodeRequire('electron');
      const currentZoom = webFrame.getZoomLevel();
      webFrame.setZoomLevel(currentZoom + 0.5);
      return;
    }
    if (e.ctrlKey && e.key === '-') {
      e.preventDefault();
      const { webFrame } = window.nodeRequire('electron');
      const currentZoom = webFrame.getZoomLevel();
      webFrame.setZoomLevel(currentZoom - 0.5);
      return;
    }
    if (e.ctrlKey && e.key === '0') {
      e.preventDefault();
      const { webFrame } = window.nodeRequire('electron');
      webFrame.setZoomLevel(0);
      return;
    }
  });
}

// --- HOTKEY MATCHING LOGIC ---
function matchKeybind(e, actionName) {
  const bindStr = keybinds[actionName];
  if (!bindStr) return false;
  
  const parts = bindStr.split('+');
  const targetKey = parts[parts.length - 1].toUpperCase();
  
  const ctrlRequired = parts.includes('Ctrl');
  const shiftRequired = parts.includes('Shift');
  const altRequired = parts.includes('Alt');
  
  const matchesCtrl = e.ctrlKey === ctrlRequired;
  const matchesShift = e.shiftKey === shiftRequired;
  const matchesAlt = e.altKey === altRequired;
  
  let pressedKeyName = e.key.toUpperCase();
  if (e.key === ' ') pressedKeyName = 'SPACE';
  
  const matchesKey = pressedKeyName === targetKey || (e.keyCode && getKeyCodeName(e.keyCode) === targetKey);
  
  return matchesCtrl && matchesShift && matchesAlt && matchesKey;
}

function getKeyCodeName(code) {
  if (code >= 112 && code <= 123) return 'F' + (code - 111);
  return null;
}

// --- PREMIUM DROP-DOWN MENUS INTERACTION ---
function setupDropdownMenus() {
  const menuContainers = document.querySelectorAll('.menu-container');
  
  menuContainers.forEach(container => {
    const item = container.querySelector('.menu-item');
    const menu = container.querySelector('.dropdown-menu');
    
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      document.querySelectorAll('.dropdown-menu').forEach(d => {
        if (d !== menu) d.classList.remove('show');
      });
      menu.classList.toggle('show');
    });
  });
  
  window.addEventListener('click', () => {
    document.querySelectorAll('.dropdown-menu').forEach(menu => {
      menu.classList.remove('show');
    });
  });
  
  // File Dropdown
  document.getElementById('opt-file-newfile').addEventListener('click', () => createNodePrompt('file'));
  document.getElementById('opt-file-newfolder').addEventListener('click', () => createNodePrompt('folder'));
  document.getElementById('opt-file-newproject').addEventListener('click', () => {
    document.getElementById('project-modal').showModal();
  });
  document.getElementById('opt-file-openproject').addEventListener('click', async () => {
    const modal = document.getElementById('open-project-modal');
    const listEl = document.getElementById('open-project-list');
    listEl.innerHTML = '<p style="color:var(--text-secondary); text-align:center;">Loading projects...</p>';
    modal.showModal();

    const projects = await ipcRenderer.invoke('get-projects');
    listEl.innerHTML = '';
    
    if (projects.length === 0) {
      listEl.innerHTML = '<p style="color:var(--text-secondary); text-align:center;">No projects found in ~/UnleashingProjects</p>';
    } else {
      projects.forEach(p => {
        const item = document.createElement('div');
        item.style.padding = '10px';
        item.style.borderBottom = '1px solid var(--border-dark)';
        item.style.cursor = 'pointer';
        item.innerHTML = `
          <div style="font-weight: 600; color: var(--text-cyan);">${p.name}</div>
          <div style="font-size: 12px; color: var(--text-secondary);">${p.type}</div>
        `;
        item.addEventListener('mouseover', () => item.style.backgroundColor = 'rgba(255,255,255,0.05)');
        item.addEventListener('mouseout', () => item.style.backgroundColor = 'transparent');
        item.addEventListener('click', () => {
          modal.close();
          openWorkspace(p.path);
        });
        listEl.appendChild(item);
      });
    }
  });

  document.getElementById('open-project-close-btn').addEventListener('click', () => {
    document.getElementById('open-project-modal').close();
  });

  document.getElementById('opt-file-open').addEventListener('click', () => {
    const btn = document.getElementById('btn-open-workspace');
    if (btn) btn.click();
  });
  document.getElementById('opt-file-configure').addEventListener('click', () => {
    if (!activeWorkspacePath) return;
    
    // Attempt to read current type to pre-select it
    const uidePath = path.join(activeWorkspacePath, '.uide', 'project.json');
    let currentType = "Unknown";
    if (fs.existsSync(uidePath)) {
      try {
        const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
        if (data.type) currentType = data.type;
      } catch(e) {}
    }
    
    const selectEl = document.getElementById('config-project-type');
    if (currentType === 'Leash Project' || currentType === 'Unleashing IDE Extension') {
      selectEl.value = currentType;
    } else {
      selectEl.value = 'Unknown';
    }
    
    document.getElementById('config-project-modal').showModal();
  });

  document.getElementById('config-project-cancel').addEventListener('click', () => {
    document.getElementById('config-project-modal').close();
  });

  document.getElementById('config-project-confirm').addEventListener('click', () => {
    document.getElementById('config-project-modal').close();
    if (!activeWorkspacePath) return;

    const type = document.getElementById('config-project-type').value;
    const uideDir = path.join(activeWorkspacePath, '.uide');
    const uidePath = path.join(uideDir, 'project.json');
    
    if (!fs.existsSync(uideDir)) {
      fs.mkdirSync(uideDir, { recursive: true });
    }
    fs.writeFileSync(uidePath, JSON.stringify({ name: path.basename(activeWorkspacePath), type: type }, null, 2), 'utf-8');
    
    // Toggle extension button visibility
    const installExtBtn = document.getElementById('opt-file-install-ext');
    const compactExtBtn = document.getElementById('opt-file-compact-ext');
    if (type === 'Unleashing IDE Extension') {
      installExtBtn.classList.remove('hidden');
      compactExtBtn.classList.remove('hidden');
    } else {
      installExtBtn.classList.add('hidden');
      compactExtBtn.classList.add('hidden');
    }

    openFileInEditor(uidePath, 'project.json');
  });

  document.getElementById('opt-file-install-ext').addEventListener('click', async () => {
    if (!activeWorkspacePath) return;
    const res = await ipcRenderer.invoke('install-extension', activeWorkspacePath);
    if (res.success) {
      alert("Extension installed successfully! Please refresh the extensions in the sidebar.");
    } else {
      alert("Failed to install extension: " + res.error);
    }
  });

  // Install .zip extension
  document.getElementById('opt-file-install-uie').addEventListener('click', async () => {
    const { dialog } = window.nodeRequire('electron').remote || {};
    // Use ipc to open file dialog
    const result = await ipcRenderer.invoke('select-uie-file');
    if (!result) return;

    const zipPath = result;
    const zipName = path.basename(zipPath, '.zip');
    const destFolder = path.join(os.homedir(), '.UnleashingExtensions', zipName);

    // Extract the .zip file
    const extractRes = await ipcRenderer.invoke('extract-uie', { uieFilePath: zipPath, destFolder });
    if (extractRes.success) {
      alert(`Extension "${zipName}" installed successfully from .zip package!\nLocation: ${destFolder}\n\nRefresh extensions to activate.`);
    } else {
      alert(`Failed to install .zip: ${extractRes.error}`);
    }
  });

  // Compact Extension Project to .zip
  document.getElementById('opt-file-compact-ext').addEventListener('click', async () => {
    if (!activeWorkspacePath) {
      alert('Please open a workspace folder first.');
      return;
    }
    const result = await ipcRenderer.invoke('save-uie-dialog');
    if (!result) return;
    const res = await ipcRenderer.invoke('pack-uie', { sourceFolder: activeWorkspacePath, outputPath: result });
    if (res.success) {
      alert(`Extension project compacted successfully!\nSaved to: ${res.outputPath}`);
    } else {
      alert(`Failed to compact: ${res.error}`);
    }
  });
  document.getElementById('opt-file-save').addEventListener('click', () => saveActiveFile());
  document.getElementById('opt-file-close').addEventListener('click', () => closeActiveWorkspace());
  document.getElementById('opt-file-exit').addEventListener('click', () => ipcRenderer.send('window-close'));

  // Setup Project Modal handlers
  const projectModal = document.getElementById('project-modal');
  document.getElementById('project-cancel-btn').addEventListener('click', () => {
    projectModal.close();
  });
  document.getElementById('project-confirm-btn').addEventListener('click', async () => {
    const pType = document.getElementById('project-type-select').value;
    const pName = document.getElementById('project-name-input').value.trim();
    if (!pName) return;

    const res = await ipcRenderer.invoke('create-project', { projectType: pType, projectName: pName });
    if (res.success) {
      projectModal.close();
      openWorkspace(res.projectPath);
    } else {
      alert("Failed to create project: " + res.error);
    }
  });
  
  // Selection Dropdown
  document.getElementById('opt-sel-all').addEventListener('click', () => {
    if (editorInstance) {
      editorInstance.focus();
      editorInstance.trigger('keyboard', 'editor.action.selectAll', null);
    }
  });
  document.getElementById('opt-sel-copy').addEventListener('click', () => {
    document.execCommand('copy');
  });
  document.getElementById('opt-sel-paste').addEventListener('click', () => {
    document.execCommand('paste');
  });
  document.getElementById('opt-sel-cut').addEventListener('click', () => {
    document.execCommand('cut');
  });
  
  // Run Dropdown
  document.getElementById('opt-run-start').addEventListener('click', () => runActiveScript());
  document.getElementById('opt-run-compile').addEventListener('click', () => compileActiveScript());
  document.getElementById('opt-run-dump').addEventListener('click', () => dumpLLVMIR());
  document.getElementById('opt-run-check').addEventListener('click', () => triggerTypecheck());
  
  // Help Dropdown
  document.getElementById('opt-help-about').addEventListener('click', () => {
    alert("Unleashing IDE v1.0.0\nPowered by Electron & Monaco Editor.\nA premium development studio for the Leash Programming Language.");
  });
  document.getElementById('opt-help-shortcuts').addEventListener('click', () => {
    alert(`Keyboard Shortcuts:\n\nRun Script: ${keybinds.runScript}\nCompile Executable: ${keybinds.compileScript}\nSave File: ${keybinds.saveFile}\nCommand Palette: ${keybinds.palette}\nToggle Terminal: ${keybinds.terminal}`);
  });
  document.getElementById('opt-help-docs').addEventListener('click', () => {
    window.nodeRequire('electron').shell.openExternal("https://github.com/foksiny/leash");
  });
}

function runActiveScript() {
  executeActiveScript('run');
}

function compileActiveScript() {
  executeActiveScript('compile');
}

function dumpLLVMIR() {
  executeActiveScript('dump');
}

// --- DYNAMIC INTERACTIVE TERMINAL VIEWER ---
function setupTerminalHandlers() {
  const tabTerminal = document.getElementById('tab-terminal');
  const tabOutput = document.getElementById('tab-output');
  const tabProblems = document.getElementById('tab-problems');
  
  const bodyTerminal = document.getElementById('body-terminal');
  const bodyOutput = document.getElementById('body-output');
  const bodyProblems = document.getElementById('body-problems');
  
  function deactivateAll() {
    tabTerminal.classList.remove('active');
    tabOutput.classList.remove('active');
    tabProblems.classList.remove('active');
    bodyTerminal.classList.remove('active');
    bodyOutput.classList.remove('active');
    bodyProblems.classList.remove('active');
  }
  
  tabTerminal.addEventListener('click', () => {
    deactivateAll();
    tabTerminal.classList.add('active');
    bodyTerminal.classList.add('active');
    setTimeout(() => document.getElementById('terminal-hidden-input').focus(), 30);
  });
  
  tabOutput.addEventListener('click', () => {
    deactivateAll();
    tabOutput.classList.add('active');
    bodyOutput.classList.add('active');
    if (currentRunningCommandId) {
      setTimeout(() => document.getElementById('output-hidden-input').focus(), 30);
    }
  });
  
  tabProblems.addEventListener('click', () => {
    deactivateAll();
    tabProblems.classList.add('active');
    bodyProblems.classList.add('active');
  });

  // Click anywhere inside terminal container to focus the hidden prompt
  bodyTerminal.addEventListener('click', () => {
    document.getElementById('terminal-hidden-input').focus();
  });

  // Click anywhere inside output container to focus the hidden prompt
  bodyOutput.addEventListener('click', () => {
    if (currentRunningCommandId) {
      document.getElementById('output-hidden-input').focus();
    }
  });
  
  const inputField = document.getElementById('terminal-hidden-input');
  const termText = document.getElementById('terminal-text');

  // Sync draft span on typing
  inputField.addEventListener('input', () => {
    const activeDraft = document.getElementById('terminal-line-draft');
    if (activeDraft) {
      activeDraft.innerText = inputField.value;
    }
    termText.scrollTop = termText.scrollHeight;
  });
  
  inputField.addEventListener('keydown', (e) => {
    // 1. Tab autocompletion
    if (e.key === 'Tab') {
      e.preventDefault();
      handleTabCompletion(inputField);
      const activeDraft = document.getElementById('terminal-line-draft');
      if (activeDraft) {
        activeDraft.innerText = inputField.value; // Sync changes
      }
      return;
    } else {
      // Any other key resets tab cycling
      tabCycleMatches = [];
      tabCycleIndex = -1;
    }

    // 2. Command History: ArrowUp / ArrowDown
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      handleHistoryNavigation(inputField, 'up');
      const activeDraft = document.getElementById('terminal-line-draft');
      if (activeDraft) {
        activeDraft.innerText = inputField.value; // Sync changes
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      handleHistoryNavigation(inputField, 'down');
      const activeDraft = document.getElementById('terminal-line-draft');
      if (activeDraft) {
        activeDraft.innerText = inputField.value; // Sync changes
      }
      return;
    }

    // 3. Enter Key: submit command
    if (e.key === 'Enter') {
      const val = inputField.value;
      if (!val) {
        ipcRenderer.send('terminal-input', '\r\n');
        return;
      }
      
      // Save in history
      if (terminalHistory.length === 0 || terminalHistory[terminalHistory.length - 1] !== val) {
        terminalHistory.push(val);
        if (terminalHistory.length > 50) terminalHistory.shift();
      }
      terminalHistoryIndex = -1;
      terminalHistoryTempInput = '';
      
      // Intercept clear / cls command
      const cleanCmd = val.trim().toLowerCase();
      if (cleanCmd === 'clear' || cleanCmd === 'cls') {
        document.getElementById('terminal-history').innerHTML = 'Unleashing IDE Shell Terminal [Screen Cleared]\n';
        inputField.value = '';
        const activeDraft = document.getElementById('terminal-line-draft');
        if (activeDraft) activeDraft.innerText = '';
        
        ipcRenderer.send('terminal-input', '\r\n');
        
        setTimeout(() => inputField.focus(), 10);
        return;
      }
      
      ipcRenderer.send('terminal-input', val + '\r\n');
      
      // Append command inline
      document.getElementById('terminal-history').innerHTML += val + '\n';
      
      inputField.value = '';
      const activeDraft = document.getElementById('terminal-line-draft');
      if (activeDraft) {
        activeDraft.innerText = '';
      }
      termText.scrollTop = termText.scrollHeight;
      setTimeout(() => inputField.focus(), 10);
    }
  });
  
  ipcRenderer.on('terminal-output', (event, data) => {
    document.getElementById('terminal-history').innerHTML += data;
    termText.scrollTop = termText.scrollHeight;
  });
}

function handleTabCompletion(inputField) {
  const val = inputField.value;
  const cursorIdx = inputField.selectionStart;
  
  // Find word preceding cursor
  const lastSpaceIdx = val.lastIndexOf(' ', cursorIdx - 1);
  const wordStart = lastSpaceIdx + 1;
  const word = val.substring(wordStart, cursorIdx);
  
  if (!word) return;
  
  // Cycling existing matches
  if (tabCycleMatches.length > 0 && tabCycleIndex !== -1) {
    tabCycleIndex = (tabCycleIndex + 1) % tabCycleMatches.length;
    const completedWord = path.join(tabCycleDirPart, tabCycleMatches[tabCycleIndex]);
    const newVal = val.substring(0, wordStart) + completedWord + val.substring(cursorIdx);
    inputField.value = newVal;
    inputField.selectionStart = inputField.selectionEnd = wordStart + completedWord.length;
    return;
  }
  
  // Initiate fresh cycle
  tabCycleOriginalVal = val;
  tabCyclePrefixWord = word;
  
  let searchDir = activeWorkspacePath || process.cwd();
  let basePrefix = word;
  let dirPart = '';
  
  const lastSlashIdx = Math.max(word.lastIndexOf('/'), word.lastIndexOf('\\'));
  if (lastSlashIdx !== -1) {
    dirPart = word.substring(0, lastSlashIdx + 1);
    basePrefix = word.substring(lastSlashIdx + 1);
    searchDir = path.resolve(searchDir, dirPart);
  }
  
  const fs = window.nodeRequire('fs');
  try {
    if (fs.existsSync(searchDir)) {
      const items = fs.readdirSync(searchDir);
      const matches = items.filter(item => item.toLowerCase().startsWith(basePrefix.toLowerCase()));
      
      if (matches.length > 0) {
        tabCycleMatches = matches;
        tabCycleIndex = 0;
        tabCycleDirPart = dirPart;
        
        const completedWord = path.join(dirPart, matches[0]);
        const newVal = val.substring(0, wordStart) + completedWord + val.substring(cursorIdx);
        inputField.value = newVal;
        inputField.selectionStart = inputField.selectionEnd = wordStart + completedWord.length;
      }
    }
  } catch(e) {
    console.error("Tab completion error:", e);
  }
}

function handleHistoryNavigation(inputField, direction) {
  if (terminalHistory.length === 0) return;
  
  if (direction === 'up') {
    if (terminalHistoryIndex === -1) {
      terminalHistoryTempInput = inputField.value;
      terminalHistoryIndex = terminalHistory.length - 1;
    } else if (terminalHistoryIndex > 0) {
      terminalHistoryIndex--;
    }
    inputField.value = terminalHistory[terminalHistoryIndex];
  } else if (direction === 'down') {
    if (terminalHistoryIndex === -1) return;
    
    if (terminalHistoryIndex < terminalHistory.length - 1) {
      terminalHistoryIndex++;
      inputField.value = terminalHistory[terminalHistoryIndex];
    } else {
      terminalHistoryIndex = -1;
      inputField.value = terminalHistoryTempInput;
    }
  }
  
  setTimeout(() => {
    inputField.selectionStart = inputField.selectionEnd = inputField.value.length;
  }, 0);
}

// --- KEYBIND RECORDERS & BINDER SETTINGS ---
function setupKeybindHandlers() {
  document.getElementById('settings-theme').addEventListener('change', (e) => {
    applyTheme(e.target.value);
  });
  
  const keyBtns = document.querySelectorAll('.keybind-btn');
  keyBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      
      if (isRecordingKeybind) {
        stopRecordingKeybind();
        return;
      }
      
      isRecordingKeybind = btn.dataset.action;
      btn.classList.add('recording');
      btn.innerText = 'Press key...';
    });
  });
  
  window.addEventListener('keydown', (e) => {
    if (!isRecordingKeybind) return;
    
    e.preventDefault();
    e.stopPropagation();
    
    if (['Control', 'Shift', 'Alt', 'Meta'].includes(e.key)) {
      return;
    }
    
    let keys = [];
    if (e.ctrlKey) keys.push('Ctrl');
    if (e.shiftKey) keys.push('Shift');
    if (e.altKey) keys.push('Alt');
    
    let keyName = e.key;
    if (keyName === ' ') keyName = 'Space';
    if (keyName.length === 1) keyName = keyName.toUpperCase();
    keys.push(keyName);
    
    const keyComboStr = keys.join('+');
    
    keybinds[isRecordingKeybind] = keyComboStr;
    localStorage.setItem('unleashing-keybinds', JSON.stringify(keybinds));
    
    const btn = document.querySelector(`.keybind-btn[data-action="${isRecordingKeybind}"]`);
    if (btn) {
      btn.innerText = keyComboStr;
      btn.classList.remove('recording');
    }
    
    isRecordingKeybind = null;
  });
}

function stopRecordingKeybind() {
  if (!isRecordingKeybind) return;
  const btn = document.querySelector(`.keybind-btn[data-action="${isRecordingKeybind}"]`);
  if (btn) {
    btn.innerText = keybinds[isRecordingKeybind];
    btn.classList.remove('recording');
  }
  isRecordingKeybind = null;
}

function loadSavedKeybinds() {
  const savedTheme = localStorage.getItem('unleashing-theme');
  if (savedTheme) {
    applyTheme(savedTheme);
  } else {
    applyTheme('leash-neon');
  }
  
  const savedBinds = localStorage.getItem('unleashing-keybinds');
  if (savedBinds) {
    try {
      keybinds = JSON.parse(savedBinds);
    } catch(e) {}
  }
  
  for (let actionName in keybinds) {
    const btn = document.querySelector(`.keybind-btn[data-action="${actionName}"]`);
    if (btn) {
      btn.innerText = keybinds[actionName];
    }
  }
}

// --- DYNAMIC THEME APPLIER ---
function applyTheme(themeName) {
  if (!THEME_REGISTRY[themeName]) return;
  activeTheme = themeName;
  const tData = THEME_REGISTRY[themeName];
  
  if (typeof monaco !== 'undefined' && editorInstance) {
    monaco.editor.setTheme(themeName + '-theme');
  }
  
  const root = document.documentElement;
  for (let [varName, value] of Object.entries(tData.variables)) {
    root.style.setProperty(varName, value);
  }
  
  const selector = document.getElementById('settings-theme');
  if (selector) {
    selector.value = themeName;
  }
  
  localStorage.setItem('unleashing-theme', themeName);
}

// --- RESIZER LOGIC ---
document.addEventListener("DOMContentLoaded", () => {
  const sidebarResizer = document.getElementById('sidebar-resizer');
  const sidePanel = document.getElementById('side-panel');
  let isResizingSidebar = false;

  if (sidebarResizer && sidePanel) {
    sidebarResizer.addEventListener('mousedown', (e) => {
      isResizingSidebar = true;
      sidebarResizer.classList.add('active');
      document.body.style.cursor = 'ew-resize';
    });
  }

  const consoleResizer = document.getElementById('console-resizer');
  const consolePanel = document.getElementById('console-panel');
  let isResizingConsole = false;

  if (consoleResizer && consolePanel) {
    consoleResizer.addEventListener('mousedown', (e) => {
      isResizingConsole = true;
      consoleResizer.classList.add('active');
      document.body.style.cursor = 'ns-resize';
    });
  }

  document.addEventListener('mousemove', (e) => {
    if (isResizingSidebar && sidePanel) {
      const newWidth = e.clientX - 50; // 50px is the sidebar nav width
      if (newWidth > 150 && newWidth < window.innerWidth - 200) {
        sidePanel.style.width = `${newWidth}px`;
        if (typeof editorInstance !== 'undefined' && editorInstance) {
          editorInstance.layout();
        }
      }
    }
    if (isResizingConsole && consolePanel) {
      const newHeight = window.innerHeight - e.clientY - 22; // 22 is status bar height
      if (newHeight > 30 && newHeight < window.innerHeight - 100) {
        consolePanel.style.height = `${newHeight}px`;
        consolePanel.style.flex = `0 0 ${newHeight}px`;
        if (typeof editorInstance !== 'undefined' && editorInstance) {
          editorInstance.layout();
        }
      }
    }
  });

  document.addEventListener('mouseup', () => {
    if (isResizingSidebar) {
      isResizingSidebar = false;
      if (sidebarResizer) sidebarResizer.classList.remove('active');
      document.body.style.cursor = '';
    }
    if (isResizingConsole) {
      isResizingConsole = false;
      if (consoleResizer) consoleResizer.classList.remove('active');
      document.body.style.cursor = '';
    }
  });
});
