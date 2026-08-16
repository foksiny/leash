import { Range } from 'vscode-languageserver';
import { Token, tokenize, TextPositioner } from './util';
import {
    DocModel,
    LshSymbol,
    SymKind,
    ParamInfo,
    UseStmt,
    LocalSymbol,
    CallSite,
    NativeImport
} from './types';

const MODIFIERS = new Set(['pub', 'priv', 'static', 'imut', 'unsafe', 'inline', 'nogc', 'shared', 'fusion']);

const TYPE_KINDS = new Set(['struct', 'class', 'union', 'enum', 'type', 'template']);

function idOf(uri: string, name: string, line: number, col: number, kind: SymKind): string {
    return `${uri}|${kind}|${name}|${line}:${col}`;
}

interface DeclHead {
    name: string;
    nameTok: Token;
    typeParams: string[];
    params: ParamInfo[];
    returnType: string;
    extensionType: string | null;
    startTok: Token;
    endTok: Token;
    sigTail: string;
}

export function parseDocument(uri: string, text: string): DocModel {
    const tokens = tokenize(text);
    return new Parser(uri, text, tokens).parse();
}

class Parser {
    private readonly pos: TextPositioner;
    private readonly model: DocModel;
    private i = 0;

    constructor(
        private readonly uri: string,
        private readonly text: string,
        private readonly tokens: Token[]
    ) {
        this.pos = new TextPositioner(text);
        this.model = { uri, text, symbols: [], locals: [], uses: [], natives: [], callSites: [], version: 0 };
    }

    parse(): DocModel {
        while (this.i < this.tokens.length) {
            const tok = this.tokens[this.i];
            if (tok.type === 'comment') {
                this.i++;
                continue;
            }
            if (tok.type === 'at') {
                const from = this.peek(1);
                if (from && from.type === 'ident' && from.text === 'from') {
                    this.parseNativeImport();
                } else {
                    this.i++;
                }
                continue;
            }
            if (tok.type !== 'ident') {
                this.i++;
                continue;
            }
            const t = tok.text;
            if (t === 'use') {
                this.parseUse();
            } else if (t === 'error') {
                this.parseErrorDef([]);
            } else if (t === 'def') {
                this.parseDef([]);
            } else if (t === 'fnc' || t === 'worker') {
                this.parseFunction([]);
            } else if (t === 'opdef') {
                this.parseOpdef();
            } else if (t === 'if' || t === 'unless' || t === 'also' || t === 'alsou' || t === 'else') {
                this.parseConditionalDef();
            } else if (MODIFIERS.has(t) && this.isGlobalHeadAhead()) {
                this.parseGlobal();
            } else if (this.isGlobalHeadAhead()) {
                this.parseGlobal();
            } else {
                this.i++;
            }
        }
        return this.model;
    }

    private parseNativeImport(): void {
        const startTok = this.tokens[this.i];
        this.i += 2;
        let lib = '';
        if (this.peek()?.text === '(') {
            this.i++;
            const lt = this.tokens[this.i];
            if (lt && lt.type === 'string') {
                lib = lt.text.slice(1, -1);
                this.i++;
            }
            this.i++;
        }
        const open = this.peek()?.text === '{' ? this.i : null;
        if (open !== null) {
            const close = this.matchBrace(open);
            if (close !== null) {
                let j = open + 1;
                while (j < close) {
                    const t = this.tokens[j];
                    if (!t) break;
                    if (t.text === 'fnc') {
                        const saved = this.i;
                        this.i = j + 1;
                        const nameTok = this.tokens[this.i];
                        if (nameTok && nameTok.type === 'ident') {
                            this.i++;
                            const params = this.parseParams();
                            let ret = '';
                            if (this.peek()?.text === ':' || this.peek()?.text === '->') {
                                this.i++;
                                ret = this.readTypeName();
                            }
                            this.consumeUntil(';');
                            this.model.symbols.push({
                                id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'nativeFunction'),
                                name: nameTok.text,
                                kind: 'nativeFunction',
                                uri: this.uri,
                                nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                                fullRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                                signature: `[native] fnc ${nameTok.text}(${params.map(p => `${p.name}${p.type ? ' ' + p.type : ''}`).join(', ')}) : ${ret || 'void'}`,
                                params,
                                returnType: ret,
                                typeParams: [],
                                visibility: '',
                                docs: '',
                                line: nameTok.line,
                                col: nameTok.char,
                                endCol: nameTok.char + nameTok.text.length
                            });
                        }
                        this.i = saved;
                        j++;
                        continue;
                    }
                    if (t.type === 'ident') {
                        const colon = this.tokens[j + 1];
                        if (colon && colon.text === ':') {
                            const saved = this.i;
                            this.i = j + 2;
                            const vtype = this.readTypeName();
                            this.consumeUntil(';');
                            this.model.symbols.push({
                                id: idOf(this.uri, t.text, t.line, t.char, 'nativeVariable'),
                                name: t.text,
                                kind: 'nativeVariable',
                                uri: this.uri,
                                nameRange: { start: this.p(t.start), end: this.p(t.end) },
                                fullRange: { start: this.p(t.start), end: this.p(t.end) },
                                signature: `[native] ${t.text} : ${vtype}`,
                                params: [],
                                returnType: vtype,
                                typeParams: [],
                                visibility: '',
                                docs: '',
                                line: t.line,
                                col: t.char,
                                endCol: t.char + t.text.length
                            });
                            this.i = saved;
                        }
                    }
                    j++;
                }
                this.i = close + 1;
            }
        }
        this.consumeUntil(';');
    }

    private peek(n = 0): Token | undefined {
        return this.tokens[this.i + n];
    }

    private isGlobalHeadAhead(): boolean {
        let j = this.i;
        const n = this.tokens.length;
        while (j < n && MODIFIERS.has(this.tokens[j].text) && this.tokens[j].type === 'ident') j++;
        if (j >= n || this.tokens[j].type !== 'ident') return false;
        const colon = this.tokens[j + 1];
        return !!colon && colon.text === ':' && colon.type === 'op';
    }

    private parseUse(): void {
        const startTok = this.tokens[this.i];
        this.i++;
        let isPriv = false;
        if (this.peek()?.text === 'priv') {
            isPriv = true;
            this.i++;
        }
        const modulePath: string[] = [];
        let items: string[] | null = null;
        const first = this.tokens[this.i];
        if (first && first.type === 'ident') {
            modulePath.push(first.text);
            this.i++;
        }
        while (this.peek()?.text === '::') {
            const after = this.peek(1);
            if (!after) break;
            if (after.text === '*') {
                this.i += 2;
                items = null;
                break;
            }
            if (after.type !== 'ident') {
                this.i++;
                continue;
            }
            const afterAfter = this.peek(2);
            if (afterAfter && afterAfter.text === '::') {
                modulePath.push(after.text);
                this.i += 2;
                continue;
            }
            this.i += 2;
            items = [];
            items.push(after.text);
            if (this.tokens[this.i] && this.tokens[this.i].text !== ';') {
                this.i++;
            }
            while (this.peek()?.text === ',') {
                this.i++;
                const it = this.tokens[this.i];
                if (it && it.type === 'ident') {
                    items.push(it.text);
                    this.i++;
                }
            }
            break;
        }
        while (this.peek()?.text !== ';' && this.i < this.tokens.length) this.i++;
        if (this.peek()?.text === ';') this.i++;
        const endTok = this.tokens[this.i - 1];
        this.model.uses.push({
            modulePath,
            items,
            isPriv,
            range: {
                start: this.p(startTok.start),
                end: this.p(startTok.end)
            },
            fullRange: {
                start: this.p(startTok.start),
                end: this.p(endTok.end)
            }
        });
    }

    private parseConditionalDef(): void {
        this.i++;
        this.skipExpression();
        const open = this.match('{');
        if (open !== null) {
            const close = this.matchBrace(open);
            if (close !== null) {
                this.parseTopLevelRange(open + 1, close);
            }
            this.i = close !== null ? close + 1 : this.i;
        }
        while (this.peek()?.text === 'also' || this.peek()?.text === 'alsou' || this.peek()?.text === 'else') {
            this.i++;
            this.skipExpression();
            const ob = this.match('{');
            if (ob !== null) {
                const cb = this.matchBrace(ob);
                if (cb !== null) this.parseTopLevelRange(ob + 1, cb);
                this.i = cb !== null ? cb + 1 : this.i;
            }
        }
    }

    private parseTopLevelRange(start: number, end: number): void {
        const saved = this.i;
        this.i = start;
        while (this.i < end) {
            const tok = this.tokens[this.i];
            if (!tok || tok.type !== 'ident') {
                this.i++;
                continue;
            }
            const t = tok.text;
            if (t === 'def') this.parseDef([]);
            else if (t === 'fnc' || t === 'worker') this.parseFunction([]);
            else if (t === 'error') this.parseErrorDef([]);
            else if (t === 'opdef') this.parseOpdef();
            else if (t === 'if' || t === 'unless') this.parseConditionalDef();
            else if (this.isGlobalHeadAhead()) this.parseGlobal();
            else this.i++;
        }
        this.i = saved;
    }

    private collectDocs(beforeTok: Token): string {
        const docs: string[] = [];
        let k = this.tokens.indexOf(beforeTok) - 1;
        while (k >= 0) {
            const t = this.tokens[k];
            if (t.type === 'comment' && t.text.startsWith('//')) {
                const tline = t.line;
                if (docs.length === 0 || tline + 1 === this.tokens[k + 1].line) {
                    docs.unshift(t.text.replace(/^\/\/\s?/, ''));
                    k--;
                    continue;
                }
                break;
            }
            if (t.type === 'comment' && t.text.startsWith('/*') && docs.length === 0) {
                const inner = t.text.slice(2, -2).trim();
                if (inner) docs.unshift(inner);
                k--;
                continue;
            }
            break;
        }
        return docs.join('\n');
    }

    private skipExpression(): void {
        const n = this.tokens.length;
        let depth = 0;
        while (this.i < n) {
            const t = this.tokens[this.i].text;
            if (t === '{') {
                if (depth === 0) return;
                depth++;
            } else if (t === '}') {
                if (depth === 0) return;
                depth--;
            } else if (t === '(' || t === '[') {
                depth++;
            } else if (t === ')' || t === ']') {
                depth--;
            } else if (t === ';' && depth === 0) {
                this.i++;
                return;
            }
            this.i++;
        }
    }

    private match(text: string): number | null {
        const t = this.tokens[this.i];
        if (t && t.text === text) {
            this.i++;
            return this.i - 1;
        }
        return null;
    }

    private matchBrace(openIdx: number): number | null {
        let depth = 0;
        for (let j = openIdx; j < this.tokens.length; j++) {
            const t = this.tokens[j].text;
            if (t === '{') depth++;
            else if (t === '}') {
                depth--;
                if (depth === 0) return j;
            }
        }
        return null;
    }

    private parseParams(): ParamInfo[] {
        const params: ParamInfo[] = [];
        if (this.peek()?.text !== '(') return params;
        this.i++;
        while (this.i < this.tokens.length) {
            const tok = this.tokens[this.i];
            if (tok.text === ')') {
                this.i++;
                break;
            }
            if (tok.text === ',' || tok.text === ';') {
                this.i++;
                continue;
            }
            if (tok.type === 'ident' || tok.type === 'number' || tok.text === '...') {
                const variadic = tok.text === '...';
                let name = tok.text;
                this.i++;
                if (name === '...') {
                    const nt = this.tokens[this.i];
                    if (nt && nt.type === 'ident') {
                        name = nt.text;
                        this.i++;
                    }
                }
                let type = '';
                if (this.peek()?.text === ':') {
                    this.i++;
                    type = this.readTypeName();
                } else if (this.peek()?.type === 'ident' || this.peek()?.type === 'number') {
                    type = this.readTypeName();
                }
                let hasDefault = false;
                if (this.peek()?.text === '=') {
                    hasDefault = true;
                    this.i++;
                    let depth = 0;
                    while (this.i < this.tokens.length) {
                        const t = this.tokens[this.i];
                        if (t.text === ',' && depth === 0) break;
                        if (t.text === ')' && depth === 0) break;
                        if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
                        if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
                        this.i++;
                    }
                }
                params.push({ name, type: type.trim(), variadic, hasDefault });
                continue;
            }
            this.i++;
        }
        return params;
    }

    private readTypeName(): string {
        let out = '';
        let depth = 0;
        while (this.i < this.tokens.length) {
            const t = this.tokens[this.i];
            if (t.text === ',' || t.text === ')' || t.text === ';' || t.text === '{' || t.text === '=') {
                if (depth === 0) break;
            }
            if (t.text === '<' || t.text === '[' || t.text === '(') depth++;
            if (t.text === '>' || t.text === ']' || t.text === ')') {
                if (depth === 0) break;
                depth--;
            }
            out += t.text;
            this.i++;
        }
        return out;
    }

    private parseFunctionHead(modifiers: string[], kind: SymKind = 'function'): DeclHead | null {
        const startTok = this.tokens[this.i];
        if (startTok.text === 'worker') {
            this.i++;
        }
        if (this.peek()?.text !== 'fnc') return null;
        this.i++;
        const nameTok = this.tokens[this.i];
        if (!nameTok || nameTok.type !== 'ident') return null;
        this.i++;
        const typeParams: string[] = [];
        if (this.peek()?.text === '<') {
            this.i++;
            while (this.i < this.tokens.length) {
                const t = this.tokens[this.i];
                if (t.text === '>') {
                    this.i++;
                    break;
                }
                if (t.type === 'ident') typeParams.push(t.text);
                this.i++;
            }
        }
        const params = this.parseParams();
        let returnType = '';
        let extensionType: string | null = null;
        let sigTail = '';
        if (this.peek()?.text === ':') {
            this.i++;
            const rt = this.tokens[this.i];
            if (rt && (rt.type === 'ident' || rt.type === 'number')) {
                const before = this.i;
                returnType = this.readTypeName();
                if (this.peek()?.text === '->' && kind === 'function') {
                    this.i++;
                    const ext = this.tokens[this.i];
                    if (ext && ext.type === 'ident') {
                        extensionType = ext.text;
                        this.i++;
                    }
                }
                if (this.i === before) this.i++;
            }
        } else if (this.peek()?.text === '->') {
            this.i++;
            returnType = this.readTypeName();
        }
        if (this.peek()?.text === '|>') {
            sigTail = ' |> ...';
            this.i++;
            let depth = 0;
            while (this.i < this.tokens.length) {
                const t = this.tokens[this.i];
                if (t.text === ';' && depth === 0) {
                    this.i++;
                    break;
                }
                if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
                if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
                this.i++;
            }
        }
        return {
            name: nameTok.text,
            nameTok,
            typeParams,
            params,
            returnType,
            extensionType,
            startTok,
            endTok: nameTok,
            sigTail
        };
    }

    private parseFunction(modifiers: string[]): void {
        const head = this.parseFunctionHead(modifiers);
        if (!head) {
            this.i++;
            return;
        }
        const kind: SymKind = head.extensionType ? 'method' : 'function';
        const ownerType = head.extensionType ?? undefined;
        const nameRange: Range = {
            start: this.p(head.nameTok.start),
            end: this.p(head.nameTok.end)
        };
        const open = this.peek()?.text === '{' ? this.i : null;
        let bodyTokens: Token[] = [];
        let fullEnd: Token = head.endTok;
        if (open !== null) {
            const close = this.matchBrace(open);
            if (close !== null) {
                bodyTokens = this.tokens.slice(open + 1, close);
                fullEnd = this.tokens[close];
                this.i = close + 1;
            } else {
                this.i = open + 1;
            }
        }
        const vis = modifiers.join(' ');
        const sig = this.buildFunctionSig(head, kind, vis, ownerType);
        const symbol: LshSymbol = {
            id: idOf(this.uri, head.name, head.nameTok.line, head.nameTok.char, kind),
            name: head.name,
            kind,
            uri: this.uri,
            nameRange,
            fullRange: { start: this.p(head.startTok.start), end: this.p(fullEnd.end) },
            signature: sig,
            params: head.params,
            returnType: head.returnType,
            typeParams: head.typeParams,
            ownerType,
            visibility: vis,
            docs: this.collectDocs(head.startTok),
            line: head.nameTok.line,
            col: head.nameTok.char,
            endCol: head.nameTok.char + head.name.length
        };
        this.model.symbols.push(symbol);
        if (bodyTokens.length > 0) {
            this.scanBody(bodyTokens, symbol.id, symbol.name);
        }
    }

    private buildFunctionSig(
        head: DeclHead,
        kind: SymKind,
        vis: string,
        ownerType?: string
    ): string {
        const params = head.params
            .map(p => `${p.name}${p.type ? ' ' + p.type : ''}${p.hasDefault ? ' = ...' : ''}${p.variadic ? '...' : ''}`)
            .join(', ');
        let sig = `fnc ${head.name}${head.typeParams.length ? `<${head.typeParams.join(', ')}>` : ''}(${params})`;
        if (head.returnType) sig += ` : ${head.returnType}`;
        if (kind === 'method' && ownerType && head.extensionType) {
            sig += ` -> ${ownerType}`;
        }
        if (head.sigTail) sig += head.sigTail;
        if (vis) sig = `${vis} ${sig}`;
        return sig;
    }

    private parseDef(modifiers: string[]): void {
        const startTok = this.tokens[this.i];
        this.i++;
        const nameTok = this.tokens[this.i];
        if (!nameTok || nameTok.type !== 'ident') return;
        this.i++;
        if (this.peek()?.text !== ':') return;
        this.i++;
        const kindTok = this.tokens[this.i];
        if (!kindTok || kindTok.type !== 'ident' || !TYPE_KINDS.has(kindTok.text)) return;
        this.i++;
        const typeKind = kindTok.text;
        const typeParams: string[] = [];
        if (this.peek()?.text === '<') {
            this.i++;
            while (this.i < this.tokens.length) {
                const t = this.tokens[this.i];
                if (t.text === '>') {
                    this.i++;
                    break;
                }
                if (t.type === 'ident') typeParams.push(t.text);
                this.i++;
            }
        }

        if (typeKind === 'macro') {
            const params = this.parseParams();
            const vis = modifiers.join(' ');
            const sig = `def ${nameTok.text} : macro(${params.map(p => `${p.name}${p.type ? ' ' + p.type : ''}`).join(', ')})`;
            const open = this.peek()?.text === '{' ? this.i : null;
            let fullEnd: Token = nameTok;
            if (open !== null) {
                const close = this.matchBrace(open);
                fullEnd = close !== null ? this.tokens[close] : nameTok;
                this.i = close !== null ? close + 1 : this.i;
            } else {
                let depth = 0;
                while (this.i < this.tokens.length) {
                    const t = this.tokens[this.i];
                    if (t.text === ';' && depth === 0) {
                        fullEnd = t;
                        this.i++;
                        break;
                    }
                    if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
                    if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
                    this.i++;
                }
            }
            this.model.symbols.push({
                id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'macro'),
                name: nameTok.text,
                kind: 'macro',
                uri: this.uri,
                nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                fullRange: { start: this.p(startTok.start), end: this.p(fullEnd.end) },
                signature: vis ? `${vis} ${sig}` : sig,
                params,
                returnType: '',
                typeParams: [],
                visibility: vis,
                docs: this.collectDocs(startTok),
                line: nameTok.line,
                col: nameTok.char,
                endCol: nameTok.char + nameTok.text.length
            });
            return;
        }

        if (typeKind === 'template') {
            this.consumeUntil(';');
            const vis = modifiers.join(' ');
            this.model.symbols.push({
                id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'type'),
                name: nameTok.text,
                kind: 'type',
                uri: this.uri,
                nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                fullRange: { start: this.p(startTok.start), end: this.p(nameTok.end) },
                signature: `def ${nameTok.text} : template;`,
                params: [],
                returnType: '',
                typeParams: [],
                visibility: vis,
                docs: this.collectDocs(startTok),
                line: nameTok.line,
                col: nameTok.char,
                endCol: nameTok.char + nameTok.text.length
            });
            return;
        }

        if (typeKind === 'type') {
            let alias = '';
            if (this.peek()?.type === 'ident') {
                alias = this.readTypeName();
            }
            this.consumeUntil(';');
            const vis = modifiers.join(' ');
            this.model.symbols.push({
                id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'type'),
                name: nameTok.text,
                kind: 'type',
                uri: this.uri,
                nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                fullRange: { start: this.p(startTok.start), end: this.p(nameTok.end) },
                signature: `def ${nameTok.text} : type ${alias};`,
                params: [],
                returnType: alias,
                typeParams: [],
                visibility: vis,
                docs: this.collectDocs(startTok),
                line: nameTok.line,
                col: nameTok.char,
                endCol: nameTok.char + nameTok.text.length
            });
            return;
        }

        const open = this.peek()?.text === '{' ? this.i : null;
        let close: number | null = null;
        if (open !== null) {
            close = this.matchBrace(open);
            this.i = close !== null ? close + 1 : this.i;
        }
        const endTok = close !== null ? this.tokens[close] : nameTok;
        const vis = modifiers.join(' ');
        const typeSymbol: LshSymbol = {
            id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'type'),
            name: nameTok.text,
            kind: 'type',
            uri: this.uri,
            nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
            fullRange: { start: this.p(startTok.start), end: this.p(endTok.end) },
            signature: `def ${nameTok.text} : ${typeKind}${typeParams.length ? `<${typeParams.join(', ')}>` : ''}`,
            params: [],
            returnType: '',
            typeParams,
            visibility: vis,
            docs: this.collectDocs(startTok),
            line: nameTok.line,
            col: nameTok.char,
            endCol: nameTok.char + nameTok.text.length
        };
        this.model.symbols.push(typeSymbol);
        if (open !== null && close !== null) {
            this.parseTypeBody(typeSymbol, open + 1, close);
        }
    }

    private parseTypeBody(typeSymbol: LshSymbol, start: number, end: number): void {
        let j = start;
        const isEnum = typeSymbol.signature.includes(': enum');
        const isUnion = typeSymbol.signature.includes(': union');
        while (j < end) {
            const tok = this.tokens[j];
            if (!tok || tok.type !== 'ident') {
                j++;
                continue;
            }
            if (tok.text === 'fnc' || tok.text === 'worker') {
                const saved = this.i;
                this.i = j;
                const head = this.parseFunctionHead([], 'method');
                if (head) {
                    const openB = this.peek()?.text === '{' ? this.i : null;
                    let bodyTokens: Token[] = [];
                    let fullEnd: Token = head.endTok;
                    if (openB !== null) {
                        const closeB = this.matchBrace(openB);
                        if (closeB !== null) {
                            bodyTokens = this.tokens.slice(openB + 1, closeB);
                            fullEnd = this.tokens[closeB];
                            j = closeB + 1;
                        } else {
                            j = openB + 1;
                        }
                    } else {
                        j = this.i;
                    }
                    const kind: SymKind = 'method';
                    const sig = this.buildFunctionSig(head, kind, '', typeSymbol.name);
                    const sym: LshSymbol = {
                        id: idOf(this.uri, head.name, head.nameTok.line, head.nameTok.char, 'method'),
                        name: head.name,
                        kind,
                        uri: this.uri,
                        nameRange: { start: this.p(head.nameTok.start), end: this.p(head.nameTok.end) },
                        fullRange: { start: this.p(head.startTok.start), end: this.p(fullEnd.end) },
                        signature: sig,
                        params: head.params,
                        returnType: head.returnType,
                        typeParams: head.typeParams,
                        ownerType: typeSymbol.name,
                        visibility: '',
                        docs: this.collectDocs(head.startTok),
                        line: head.nameTok.line,
                        col: head.nameTok.char,
                        endCol: head.nameTok.char + head.name.length
                    };
                    this.model.symbols.push(sym);
                    if (bodyTokens.length > 0) {
                        this.scanBody(bodyTokens, sym.id, sym.name);
                    }
                }
                this.i = saved;
                j++;
                continue;
            }
            if (MODIFIERS.has(tok.text) && !isEnum && !isUnion) {
                const mods: string[] = [];
                let k = j;
                while (k < end && this.tokens[k] && this.tokens[k].type === 'ident' && MODIFIERS.has(this.tokens[k].text)) {
                    mods.push(this.tokens[k].text);
                    k++;
                }
                if (k < end && this.tokens[k] && this.tokens[k].text === 'fnc') {
                    const saved = this.i;
                    this.i = k;
                    const head = this.parseFunctionHead(mods, 'method');
                    if (head) {
                        const openB = this.peek()?.text === '{' ? this.i : null;
                        let bodyTokens: Token[] = [];
                        let fullEnd: Token = head.endTok;
                        if (openB !== null) {
                            const closeB = this.matchBrace(openB);
                            if (closeB !== null) {
                                bodyTokens = this.tokens.slice(openB + 1, closeB);
                                fullEnd = this.tokens[closeB];
                                j = closeB + 1;
                            } else {
                                j = openB + 1;
                            }
                        } else {
                            j = this.i;
                        }
                        const sig = this.buildFunctionSig(head, 'method', mods.join(' '), typeSymbol.name);
                        const sym: LshSymbol = {
                            id: idOf(this.uri, head.name, head.nameTok.line, head.nameTok.char, 'method'),
                            name: head.name,
                            kind: 'method',
                            uri: this.uri,
                            nameRange: { start: this.p(head.nameTok.start), end: this.p(head.nameTok.end) },
                            fullRange: { start: this.p(head.startTok.start), end: this.p(fullEnd.end) },
                            signature: sig,
                            params: head.params,
                            returnType: head.returnType,
                            typeParams: head.typeParams,
                            ownerType: typeSymbol.name,
                            visibility: mods.join(' '),
                            docs: this.collectDocs(head.startTok),
                            line: head.nameTok.line,
                            col: head.nameTok.char,
                            endCol: head.nameTok.char + head.name.length
                        };
                        this.model.symbols.push(sym);
                        if (bodyTokens.length > 0) {
                            this.scanBody(bodyTokens, sym.id, sym.name);
                        }
                    }
                    this.i = saved;
                    j++;
                    continue;
                }
                j = k;
                continue;
            }
            if (isEnum || isUnion) {
                const nameTok = tok;
                let type = '';
                j++;
                if (this.tokens[j] && this.tokens[j].text === ':') {
                    j++;
                    const before = j;
                    type = '';
                    let depth = 0;
                    while (j < end) {
                        const t = this.tokens[j];
                        if ((t.text === '=' || t.text === ',' || t.text === '}') && depth === 0) break;
                        if (t.text === '<' || t.text === '[') depth++;
                        if (t.text === '>' || t.text === ']') depth--;
                        type += t.text;
                        j++;
                    }
                    if (j === before) j++;
                }
                while (j < end && this.tokens[j] && (this.tokens[j].text === '=' || this.tokens[j].text === ',')) {
                    if (this.tokens[j].text === '=') {
                        let depth = 0;
                        j++;
                        while (j < end) {
                            const t = this.tokens[j];
                            if (t.text === ',' && depth === 0) break;
                            if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
                            if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
                            j++;
                        }
                    }
                    j++;
                }
                const kind: SymKind = isUnion ? 'field' : 'enumMember';
                this.model.symbols.push({
                    id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, kind),
                    name: nameTok.text,
                    kind,
                    uri: this.uri,
                    nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                    fullRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                    signature: `def ${typeSymbol.name} : ${isUnion ? 'union' : 'enum'} { ${nameTok.text}${type ? ' : ' + type : ''} }`,
                    params: [],
                    returnType: type,
                    typeParams: [],
                    ownerType: typeSymbol.name,
                    visibility: '',
                    docs: '',
                    line: nameTok.line,
                    col: nameTok.char,
                    endCol: nameTok.char + nameTok.text.length
                });
                continue;
            }
            const nameTok = tok;
            let j2 = j + 1;
            if (this.tokens[j2] && this.tokens[j2].text === ':') {
                j2++;
                const before = j2;
                let type = '';
                let depth = 0;
                while (j2 < end) {
                    const t = this.tokens[j2];
                    if ((t.text === '=' || t.text === ';') && depth === 0) break;
                    if (t.text === '<' || t.text === '[' || t.text === '(') depth++;
                    if (t.text === '>' || t.text === ']' || t.text === ')') depth--;
                    type += t.text;
                    j2++;
                }
                if (j2 === before) j2++;
                while (j2 < end && this.tokens[j2] && this.tokens[j2].text === '=') {
                    let depth = 0;
                    j2++;
                    while (j2 < end) {
                        const t = this.tokens[j2];
                        if (t.text === ';' && depth === 0) break;
                        if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
                        if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
                        j2++;
                    }
                }
                if (j2 < end && this.tokens[j2] && this.tokens[j2].text === ';') j2++;
                this.model.symbols.push({
                    id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'field'),
                    name: nameTok.text,
                    kind: 'field',
                    uri: this.uri,
                    nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                    fullRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
                    signature: `${nameTok.text} : ${type}`,
                    params: [],
                    returnType: type,
                    typeParams: [],
                    ownerType: typeSymbol.name,
                    visibility: '',
                    docs: '',
                    line: nameTok.line,
                    col: nameTok.char,
                    endCol: nameTok.char + nameTok.text.length
                });
                j = j2;
                continue;
            }
            j++;
        }
    }

    private parseOpdef(): void {
        const startTok = this.tokens[this.i];
        this.i++;
        const typeTok = this.tokens[this.i];
        if (!typeTok || typeTok.type !== 'ident') return;
        this.i++;
        let opName = '';
        let owner = typeTok.text;
        if (this.peek()?.text === '.') {
            this.i++;
            const nTok = this.tokens[this.i];
            if (nTok && nTok.type === 'ident') {
                opName = nTok.text;
                this.i++;
            }
        } else if (this.peek()?.text === '[') {
            this.i++;
            if (this.peek()?.text === ']') {
                this.i++;
                opName = '[]';
            }
        } else {
            const opTok = this.tokens[this.i];
            if (opTok && opTok.type === 'op') {
                opName = opTok.text;
                this.i++;
            }
        }
        if (!opName) return;
        const params = this.parseParams();
        let returnType = '';
        if (this.peek()?.text === ':') {
            this.i++;
            returnType = this.readTypeName();
        }
        const open = this.peek()?.text === '{' ? this.i : null;
        let bodyTokens: Token[] = [];
        let fullEnd: Token = typeTok;
        if (open !== null) {
            const close = this.matchBrace(open);
            if (close !== null) {
                bodyTokens = this.tokens.slice(open + 1, close);
                fullEnd = this.tokens[close];
                this.i = close + 1;
            } else {
                this.i = open + 1;
            }
        }
        const kind: SymKind = 'opdef';
        const sig = `opdef ${owner}.${opName}(${params.map(p => `${p.name}${p.type ? ' ' + p.type : ''}`).join(', ')})${returnType ? ' : ' + returnType : ''}`;
        const sym: LshSymbol = {
            id: idOf(this.uri, opName, startTok.line, startTok.char, kind),
            name: opName,
            kind,
            uri: this.uri,
            nameRange: { start: this.p(startTok.start), end: this.p(startTok.end) },
            fullRange: { start: this.p(startTok.start), end: this.p(fullEnd.end) },
            signature: sig,
            params,
            returnType,
            typeParams: [],
            ownerType: owner,
            visibility: '',
            docs: this.collectDocs(startTok),
            line: startTok.line,
            col: startTok.char,
            endCol: startTok.char + opName.length
        };
        this.model.symbols.push(sym);
        if (bodyTokens.length > 0) {
            this.scanBody(bodyTokens, sym.id, sym.name);
        }
    }

    private parseErrorDef(modifiers: string[]): void {
        const startTok = this.tokens[this.i];
        this.i++;
        const nameTok = this.tokens[this.i];
        if (!nameTok || nameTok.type !== 'ident') return;
        this.i++;
        const params = this.parseParams();
        let msg = '';
        if (this.peek()?.text === '->') {
            this.i++;
            const before = this.i;
            while (this.i < this.tokens.length && this.tokens[this.i].text !== ';') this.i++;
            msg = this.tokens.slice(before, this.i).map(t => t.text).join(' ').trim();
        }
        this.consumeUntil(';');
        const vis = modifiers.join(' ');
        this.model.symbols.push({
            id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'errorType'),
            name: nameTok.text,
            kind: 'errorType',
            uri: this.uri,
            nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
            fullRange: { start: this.p(startTok.start), end: this.p(nameTok.end) },
            signature: `error ${nameTok.text}(${params.map(p => `${p.name}${p.type ? ' ' + p.type : ''}`).join(', ')}) -> ${msg}`,
            params,
            returnType: '',
            typeParams: [],
            visibility: vis,
            docs: this.collectDocs(startTok),
            line: nameTok.line,
            col: nameTok.char,
            endCol: nameTok.char + nameTok.text.length
        });
    }

    private parseGlobal(): void {
        const mods: string[] = [];
        const startTok = this.tokens[this.i];
        while (this.peek() && this.peek()!.type === 'ident' && MODIFIERS.has(this.peek()!.text)) {
            mods.push(this.peek()!.text);
            this.i++;
        }
        const nameTok = this.tokens[this.i];
        if (!nameTok || nameTok.type !== 'ident') return;
        this.i++;
        if (this.peek()?.text !== ':') return;
        this.i++;
        let type = '';
        const before = this.i;
        type = this.readTypeName();
        if (this.i === before) {
            this.i++;
        }
        let depth = 0;
        while (this.i < this.tokens.length) {
            const t = this.tokens[this.i];
            if (t.text === ';' && depth === 0) {
                this.i++;
                break;
            }
            if (t.text === '(' || t.text === '[' || t.text === '{') depth++;
            if (t.text === ')' || t.text === ']' || t.text === '}') depth--;
            this.i++;
        }
        const vis = mods.join(' ');
        this.model.symbols.push({
            id: idOf(this.uri, nameTok.text, nameTok.line, nameTok.char, 'global'),
            name: nameTok.text,
            kind: 'global',
            uri: this.uri,
            nameRange: { start: this.p(nameTok.start), end: this.p(nameTok.end) },
            fullRange: { start: this.p(startTok.start), end: this.p(nameTok.end) },
            signature: `${vis ? vis + ' ' : ''}${nameTok.text} : ${type || 'unknown'}`,
            params: [],
            returnType: type,
            typeParams: [],
            visibility: vis,
            docs: this.collectDocs(startTok),
            line: nameTok.line,
            col: nameTok.char,
            endCol: nameTok.char + nameTok.text.length
        });
    }

    private consumeUntil(text: string): void {
        while (this.i < this.tokens.length) {
            const t = this.tokens[this.i];
            this.i++;
            if (t.text === text) return;
        }
    }

    private scanBody(bodyTokens: Token[], ownerId: string, ownerName: string): void {
        const n = bodyTokens.length;
        let j = 0;
        while (j < n) {
            const tok = bodyTokens[j];
            if (tok.text === 'fnc' || tok.text === 'worker' || tok.text === 'def' || tok.text === 'opdef' || tok.text === 'macro') {
                let k = j + 1;
                let foundBrace = false;
                while (k < n) {
                    if (bodyTokens[k].text === '{') {
                        let depth = 0;
                        let m = k;
                        while (m < n) {
                            const t = bodyTokens[m].text;
                            if (t === '{') depth++;
                            else if (t === '}') {
                                depth--;
                                if (depth === 0) break;
                            }
                            m++;
                        }
                        j = Math.min(m + 1, n);
                        foundBrace = true;
                        break;
                    }
                    if (bodyTokens[k].text === ';') break;
                    k++;
                }
                if (foundBrace) continue;
                j++;
                continue;
            }
            if (tok.type === 'ident' && this.tokensIndexOf(bodyTokens, j + 1) === ':') {
                const name = tok.text;
                if (this.tokensIndexOf(bodyTokens, j + 2) === ':') {
                    j += 2;
                    continue;
                }
                let k = j + 2;
                let type = '';
                let depth = 0;
                let isAssign = false;
                while (k < n) {
                    const t = bodyTokens[k].text;
                    if (t === '=' && depth === 0) {
                        isAssign = true;
                        break;
                    }
                    if (t === ';' || t === '{' || t === ')' || t === ',' || t === '}') {
                        if (depth === 0) break;
                    }
                    if (t === '<' || t === '[' || t === '(') depth++;
                    if (t === '>' || t === ']' || t === ')') depth--;
                    type += t;
                    k++;
                }
                if (isAssign) {
                    this.model.locals.push({
                        name,
                        type: type.trim(),
                        inferred: false,
                        range: { start: this.p(tok.start), end: this.p(tok.end) },
                        ownerId
                    });
                }
                j++;
                continue;
            }
            if (tok.text === ':=') {
                const prev = bodyTokens[j - 1];
                if (prev && prev.type === 'ident') {
                    this.model.locals.push({
                        name: prev.text,
                        type: '',
                        inferred: true,
                        range: { start: this.p(prev.start), end: this.p(prev.end) },
                        ownerId
                    });
                }
            }
            if (tok.type === 'ident') {
                const next = bodyTokens[j + 1];
                if (next && next.text === '(') {
                    this.model.callSites.push({
                        targetName: tok.text,
                        targetId: null,
                        ownerId,
                        range: { start: this.p(tok.start), end: this.p(tok.end) }
                    });
                }
            }
            j++;
        }
    }

    private tokensIndexOf(tokens: Token[], idx: number): string {
        const t = tokens[idx];
        return t ? t.text : '';
    }

    private p(offset: number): { line: number; character: number } {
        return this.pos.positionAt(offset);
    }
}