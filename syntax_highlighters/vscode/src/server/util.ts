import { Position, Range } from 'vscode-languageserver';
import { URI } from 'vscode-uri';
import * as path from 'path';
import * as fs from 'fs';

export type TokenType = 'ident' | 'number' | 'string' | 'comment' | 'op' | 'punct' | 'at';

export interface Token {
    type: TokenType;
    text: string;
    start: number;
    end: number;
    line: number;
    char: number;
}

const MULTI_OPS = [
    '|>', '::', ':=', '->', '==', '!=', '<=', '>=', '&&', '||',
    '<<', '>>', '++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=',
    '<<=', '>>=', '**'
];

const IDENT_START = /[a-zA-Z_]/;
const IDENT_CHAR = /[a-zA-Z0-9_]/;

export function tokenize(text: string): Token[] {
    const tokens: Token[] = [];
    let i = 0;
    let line = 0;
    let lineStart = 0;
    const n = text.length;

    while (i < n) {
        const c = text[i];

        if (c === '\n') {
            line++;
            lineStart = i + 1;
            i++;
            continue;
        }
        if (c === ' ' || c === '\t' || c === '\r') {
            i++;
            continue;
        }

        const start = i;
        const char = i - lineStart;

        if (c === '/' && text[i + 1] === '/') {
            while (i < n && text[i] !== '\n') i++;
            tokens.push({ type: 'comment', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if (c === '/' && text[i + 1] === '*') {
            i += 2;
            while (i < n && !(text[i] === '*' && text[i + 1] === '/')) {
                if (text[i] === '\n') {
                    line++;
                    lineStart = i + 1;
                }
                i++;
            }
            if (i < n) i += 2;
            tokens.push({ type: 'comment', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if ((c === '"' && text[i + 1] === '"' && text[i + 2] === '"') ||
            (c === '\'' && text[i + 1] === '\'' && text[i + 2] === '\'')) {
            const q = c;
            i += 3;
            while (i < n) {
                if (text[i] === q && text[i + 1] === q && text[i + 2] === q) {
                    i += 3;
                    break;
                }
                if (text[i] === '\n') {
                    line++;
                    lineStart = i + 1;
                }
                i++;
            }
            tokens.push({ type: 'string', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if (c === '"' || c === '\'') {
            i++;
            while (i < n && text[i] !== c) {
                if (text[i] === '\\') i++;
                if (text[i] === '\n') {
                    line++;
                    lineStart = i + 1;
                }
                i++;
            }
            if (i < n) i++;
            tokens.push({ type: 'string', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if (IDENT_START.test(c)) {
            i++;
            while (i < n && IDENT_CHAR.test(text[i])) i++;
            tokens.push({ type: 'ident', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if (/[0-9]/.test(c)) {
            i++;
            while (i < n && /[0-9a-zA-Z_.]/.test(text[i])) i++;
            tokens.push({ type: 'number', text: text.slice(start, i), start, end: i, line, char });
            continue;
        }
        if (c === '@') {
            i++;
            tokens.push({ type: 'at', text: c, start, end: i, line, char });
            continue;
        }

        const two = text.slice(i, i + 2);
        if (MULTI_OPS.includes(two)) {
            i += 2;
            tokens.push({ type: 'op', text: two, start, end: i, line, char });
            continue;
        }
        if ('+-*/%&|^~!<>=?.'.includes(c)) {
            i++;
            tokens.push({ type: 'op', text: c, start, end: i, line, char });
            continue;
        }
        i++;
        tokens.push({ type: 'punct', text: c, start, end: i, line, char });
    }
    return tokens;
}

export function isIdentifierChar(c: string): boolean {
    return /[A-Za-z0-9_]/.test(c);
}

export function isTempCheckFile(fsPath: string): boolean {
    return path.basename(fsPath).startsWith('.leash-lsp-');
}

export function getWordAt(text: string, offset: number): string {
    let left = offset;
    while (left > 0 && IDENT_CHAR.test(text[left - 1])) left--;
    let right = offset;
    while (right < text.length && IDENT_CHAR.test(text[right])) right++;
    return text.slice(left, right);
}

export function lineStartsOf(text: string): number[] {
    const starts = [0];
    for (let i = 0; i < text.length; i++) {
        if (text[i] === '\n') starts.push(i + 1);
    }
    return starts;
}

export class TextPositioner {
    readonly lineStarts: number[];
    constructor(readonly text: string) {
        this.lineStarts = lineStartsOf(text);
    }
    positionAt(offset: number): Position {
        const ls = this.lineStarts;
        let lo = 0;
        let hi = ls.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (ls[mid] <= offset) lo = mid;
            else hi = mid - 1;
        }
        return { line: lo, character: offset - ls[lo] };
    }
    offsetAt(position: Position): number {
        const ls = this.lineStarts;
        if (position.line < 0 || position.line >= ls.length) return this.text.length;
        let off = ls[position.line] + position.character;
        const next = position.line + 1 < ls.length ? ls[position.line + 1] - 1 : this.text.length;
        if (off > next) off = next;
        return off;
    }
    lineText(line: number): string {
        const start = this.lineStarts[line];
        if (start === undefined) return '';
        const end = line + 1 < this.lineStarts.length ? this.lineStarts[line + 1] - 1 : this.text.length;
        return this.text.slice(start, end);
    }
    wordRangeAt(offset: number): Range | null {
        const text = this.text;
        if (offset < 0 || offset > text.length) return null;
        const c = text[offset];
        if (c !== undefined && IDENT_CHAR.test(c)) {
            let left = offset;
            while (left > 0 && IDENT_CHAR.test(text[left - 1])) left--;
            let right = offset;
            while (right < text.length && IDENT_CHAR.test(text[right])) right++;
            return {
                start: this.positionAt(left),
                end: this.positionAt(right)
            };
        }
        return {
            start: this.positionAt(offset),
            end: this.positionAt(offset)
        };
    }
}

export function uriToFsPath(uri: string): string {
    return URI.parse(uri).fsPath;
}

export function fsPathToUri(fsPath: string): string {
    return URI.file(fsPath).toString();
}

export function isWithin(range: Range, pos: Position): boolean {
    if (pos.line < range.start.line || pos.line > range.end.line) return false;
    if (pos.line === range.start.line && pos.character < range.start.character) return false;
    if (pos.line === range.end.line && pos.character > range.end.character) return false;
    return true;
}

export function markdownCode(sig: string): string {
    return '```leash\n' + sig + '\n```';
}

export function relativePath(fromFs: string, toFs: string): string {
    let rel = path.relative(path.dirname(fromFs), toFs);
    if (!rel.startsWith('.')) rel = './' + rel;
    return rel;
}

export function fileExists(p: string): boolean {
    try {
        return fs.existsSync(p);
    } catch {
        return false;
    }
}

export function readFileSafe(p: string): string | null {
    try {
        return fs.readFileSync(p, 'utf-8');
    } catch {
        return null;
    }
}

export function isBuiltinTypeName(name: string): boolean {
    return BUILTIN_TYPE_NAMES.has(name);
}

export const BUILTIN_TYPE_NAMES = new Set([
    'int', 'uint', 'float', 'bool', 'char', 'string', 'void',
    'hash', 'vec', 'vector', 'matrix', 'array', 'pointer'
]);

export const BUILTIN_TYPE_ALIASES: Record<string, string> = {
    vec: 'vec<T>',
    vector: 'vec<T>',
    hash: 'hash<K, V>',
    matrix: 'matrix<T>'
};

export function normalizeTypeName(name: string): string {
    const base = name.replace(/<.*$/, '').trim();
    return base;
}