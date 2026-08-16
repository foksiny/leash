import { Position, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { LshSymbol } from '../types';
import { Token, TextPositioner, BUILTIN_TYPE_NAMES } from '../util';
import { BUILTIN_DOCS, KEYWORD_DOCS, BuiltinDoc, BUILTIN_FUNCTIONS, getBuiltinMembers } from '../builtins';

export type ResolveKind =
    | 'symbol'
    | 'local'
    | 'param'
    | 'member'
    | 'enumMember'
    | 'imported'
    | 'builtin'
    | 'keyword'
    | 'builtinType'
    | 'none';

export interface ResolvedWord {
    word: string;
    range: Range;
    kind: ResolveKind;
    symbol?: LshSymbol;
    builtin?: BuiltinDoc;
    keyword?: string;
}

export interface TokenContext {
    tokens: Token[];
    pos: TextPositioner;
    tokenIndex: number;
    prev: Token | null;
    next: Token | null;
    prev2: Token | null;
}

export function getTokenContext(index: WorkspaceIndex, uri: string, position: Position): TokenContext | null {
    const stream = index.tokenStream(uri);
    if (!stream) return null;
    const { tokens, pos } = stream;
    const offset = pos.offsetAt(position);
    let tokenIndex = -1;
    for (let i = 0; i < tokens.length; i++) {
        if (offset > tokens[i].start && offset < tokens[i].end) {
            tokenIndex = i;
            break;
        }
    }
    if (tokenIndex === -1) {
        let best = -1;
        let bestLen = 0;
        let bestIsIdent = false;
        for (let i = 0; i < tokens.length; i++) {
            const t = tokens[i];
            if (offset !== t.start && offset !== t.end) continue;
            const len = t.end - t.start;
            const isIdent = t.type === 'ident';
            if (best === -1 ||
                (isIdent && !bestIsIdent) ||
                (isIdent === bestIsIdent && len > bestLen)) {
                best = i;
                bestLen = len;
                bestIsIdent = isIdent;
            }
        }
        if (best !== -1) tokenIndex = best;
    }
    if (tokenIndex === -1) {
        for (let i = 0; i < tokens.length; i++) {
            if (tokens[i].start >= offset) {
                tokenIndex = i;
                break;
            }
        }
        if (tokenIndex === -1) tokenIndex = tokens.length;
        tokenIndex = Math.max(0, tokenIndex - 1);
    }
    return {
        tokens,
        pos,
        tokenIndex,
        prev: tokenIndex > 0 ? tokens[tokenIndex - 1] : null,
        next: tokenIndex + 1 < tokens.length ? tokens[tokenIndex + 1] : null,
        prev2: tokenIndex > 1 ? tokens[tokenIndex - 2] : null
    };
}

function tokenRange(token: Token, pos: TextPositioner): Range {
    return { start: pos.positionAt(token.start), end: pos.positionAt(token.end) };
}

export function resolveWordAt(
    index: WorkspaceIndex,
    uri: string,
    position: Position,
    _text?: string
): ResolvedWord {
    const model = index.getModel(uri);
    const stream = index.tokenStream(uri);
    if (!model || !stream) return { word: '', range: { start: position, end: position }, kind: 'none' };
    const { tokens, pos } = stream;
    const ctx = getTokenContext(index, uri, position);
    if (!ctx) return { word: '', range: { start: position, end: position }, kind: 'none' };
    const offset = pos.offsetAt(position);

    const cur = tokens[ctx.tokenIndex];
    if (!cur || cur.type !== 'ident') {
        return { word: '', range: { start: position, end: position }, kind: 'none' };
    }
    const word = cur.text;
    const range = tokenRange(cur, pos);

    if (cur.start > offset) {
        return { word: '', range: { start: position, end: position }, kind: 'none' };
    }

    if (ctx.prev && ctx.prev.text === '::') {
        const enumMember = resolveEnumMember(index, uri, ctx, cur, word);
        if (enumMember) {
            return { word, range, kind: 'enumMember', symbol: enumMember };
        }
        const sym = findByName(index, uri, word, ['enumMember']);
        if (sym) return { word, range, kind: 'enumMember', symbol: sym };
    }

    if (ctx.prev && ctx.prev.text === '.') {
        const member = resolveMemberAccess(index, uri, ctx, cur, word);
        if (member) {
            return { word, range, kind: 'member', symbol: member.symbol };
        }
    }

    if (isWithinUseStatement(model, offset, pos)) {
        const imported = index.findImported(uri, word);
        if (imported.length > 0) {
            return { word, range, kind: 'imported', symbol: imported[0] };
        }
    }

    const local = index.findLocalSymbol(uri, position, word);
    if (local) {
        return { word, range, kind: 'local', symbol: local.symbol };
    }
    const param = index.findParamSymbol(uri, position, word);
    if (param) {
        return { word, range, kind: 'param', symbol: param };
    }

    const docSymbols = model.symbols.filter(s => s.name === word);
    if (docSymbols.length > 0) {
        const top = docSymbols.find(s => s.kind !== 'field' && s.kind !== 'method' && s.kind !== 'enumMember');
        if (top) return { word, range, kind: 'symbol', symbol: top };
        return { word, range, kind: 'symbol', symbol: docSymbols[0] };
    }

    const imported = index.findImported(uri, word);
    if (imported.length > 0) {
        return { word, range, kind: 'imported', symbol: imported[0] };
    }

    const builtin = BUILTIN_DOCS[word];
    if (builtin) {
        return { word, range, kind: 'builtin', builtin };
    }
    if (BUILTIN_TYPE_NAMES.has(word)) {
        return { word, range, kind: 'builtinType', builtin: BUILTIN_DOCS[word] };
    }
    if (BUILTIN_FUNCTIONS.includes(word)) {
        return {
            word,
            range,
            kind: 'builtin',
            builtin: { sig: `${word}(...args)`, desc: `Built-in Leash function.` }
        };
    }
    const keyword = KEYWORD_DOCS[word];
    if (keyword !== undefined) {
        return { word, range, kind: 'keyword', keyword: word };
    }

    return { word, range, kind: 'none' };
}

function isWithinUseStatement(model: { uses: Array<{ fullRange: Range }> }, offset: number, pos: TextPositioner): boolean {
    for (const use of model.uses) {
        const start = pos.offsetAt(use.fullRange.start);
        const end = pos.offsetAt(use.fullRange.end);
        if (offset >= start && offset <= end) return true;
    }
    return false;
}

function findByName(index: WorkspaceIndex, uri: string, name: string, kinds: string[]): LshSymbol | null {
    for (const sym of index.getSymbolsByName(name)) {
        if (kinds.includes(sym.kind)) return sym;
    }
    const imported = index.findImported(uri, name);
    for (const sym of imported) {
        if (kinds.includes(sym.kind)) return sym;
    }
    return null;
}

export function resolveEnumMember(
    index: WorkspaceIndex,
    uri: string,
    ctx: TokenContext,
    cur: Token,
    word: string
): LshSymbol | null {
    const typeTok = ctx.prev2;
    if (typeTok && typeTok.type === 'ident') {
        const typeSyms = index.findTypeSymbols(typeTok.text);
        for (const t of typeSyms) {
            for (const m of index.getDocSymbols(t.uri)) {
                if (m.kind === 'enumMember' && m.name === word && m.ownerType === t.name) return m;
            }
        }
    }
    return null;
}

export interface MemberResolution {
    symbol: LshSymbol;
    ownerType: string;
}

export function resolveMemberAccess(
    index: WorkspaceIndex,
    uri: string,
    ctx: TokenContext,
    cur: Token,
    word: string
): MemberResolution | null {
    const tokens = ctx.tokens;
    const dotIdx = ctx.tokenIndex - 1;
    let receiverStart = dotIdx;
    while (receiverStart > 0) {
        const t = tokens[receiverStart - 1];
        if (t.type === 'ident' || t.text === '.') {
            receiverStart--;
        } else {
            break;
        }
    }
    const receiverIdents: string[] = [];
    for (let i = receiverStart; i < dotIdx; i++) {
        const t = tokens[i];
        if (t.type === 'ident') receiverIdents.push(t.text);
    }
    if (receiverIdents.length === 0) return null;
    const base = receiverIdents[0];
    const pos = ctx.pos.positionAt(cur.start);

    let typeName = index.resolveExprType(uri, pos, pos, base);
    let chainMatch: LshSymbol | null = null;

    if (receiverIdents.length > 1) {
        for (let i = 1; i < receiverIdents.length; i++) {
            if (!typeName) break;
            const member = findMember(index, typeName, receiverIdents[i]);
            if (!member) {
                chainMatch = null;
                break;
            }
            if (i === receiverIdents.length - 1 && receiverIdents[i] === word) {
                chainMatch = member;
            }
            typeName = member.returnType || null;
        }
        if (chainMatch) {
            return { symbol: chainMatch, ownerType: receiverIdents[0] };
        }
    }

    if (typeName) {
        const member = findMember(index, typeName, word);
        if (member) return { symbol: member, ownerType: typeName };
    }

    const typeSyms = index.findTypeSymbols(base);
    for (const t of typeSyms) {
        const member = findMemberInType(index, t, word);
        if (member) return { symbol: member, ownerType: t.name };
    }
    const builtin = findBuiltinMember(base, word);
    if (builtin) return builtin;
    return null;
}

function findMember(index: WorkspaceIndex, typeName: string, name: string): LshSymbol | null {
    const base = typeName.replace(/<.*$/, '').trim();
    if (BUILTIN_TYPE_NAMES.has(base) || base === 'File') {
        const builtin = findBuiltinMember(base, name);
        return builtin ? builtin.symbol : null;
    }
    const typeSyms = index.findTypeSymbols(base);
    for (const t of typeSyms) {
        const member = findMemberInType(index, t, name);
        if (member) return member;
    }
    return null;
}

function findMemberInType(index: WorkspaceIndex, typeSym: LshSymbol, name: string): LshSymbol | null {
    for (const m of index.getDocSymbols(typeSym.uri)) {
        if (m.ownerType === typeSym.name && m.name === name && (m.kind === 'method' || m.kind === 'field' || m.kind === 'opdef')) {
            return m;
        }
    }
    return null;
}

export function findBuiltinMember(base: string, name: string): MemberResolution | null {
    const members = getBuiltinMembers(base);
    for (const m of members) {
        if (m.name === name) {
            const sym: LshSymbol = {
                id: `builtin|${base}|${name}`,
                name,
                kind: m.kind,
                uri: '',
                nameRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                fullRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                signature: m.sig,
                params: m.params,
                returnType: m.returnType,
                typeParams: [],
                ownerType: base,
                visibility: m.isStatic ? 'static' : '',
                docs: m.desc,
                line: 0,
                col: 0,
                endCol: 0
            };
            return { symbol: sym, ownerType: base };
        }
    }
    return null;
}