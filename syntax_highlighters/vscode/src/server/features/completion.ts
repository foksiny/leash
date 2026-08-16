import {
    CompletionItem,
    CompletionItemKind,
    CompletionList,
    InsertTextFormat,
    MarkupKind,
    Position
} from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { getTokenContext, TokenContext } from './resolve';
import { LshSymbol } from '../types';
import { BUILTIN_DOCS, BUILTIN_FUNCTIONS, BUILTIN_TYPES, KEYWORDS, getBuiltinMembers, KEYWORD_DOCS } from '../builtins';
import { BUILTIN_TYPE_NAMES } from '../util';
import { LspSettings } from '../types';

const KEYWORD_KIND: Record<string, CompletionItemKind> = {
    fnc: CompletionItemKind.Function,
    worker: CompletionItemKind.Function,
    def: CompletionItemKind.Class,
    struct: CompletionItemKind.Class,
    class: CompletionItemKind.Class,
    union: CompletionItemKind.Class,
    enum: CompletionItemKind.Class,
    type: CompletionItemKind.Class,
    template: CompletionItemKind.Class,
    macro: CompletionItemKind.Function,
    opdef: CompletionItemKind.Operator,
    error: CompletionItemKind.Class,
    return: CompletionItemKind.Keyword,
    if: CompletionItemKind.Keyword,
    else: CompletionItemKind.Keyword,
    also: CompletionItemKind.Keyword,
    unless: CompletionItemKind.Keyword,
    alsou: CompletionItemKind.Keyword,
    while: CompletionItemKind.Keyword,
    with: CompletionItemKind.Keyword,
    for: CompletionItemKind.Keyword,
    do: CompletionItemKind.Keyword,
    foreach: CompletionItemKind.Keyword,
    loop: CompletionItemKind.Keyword,
    in: CompletionItemKind.Keyword,
    stop: CompletionItemKind.Keyword,
    continue: CompletionItemKind.Keyword,
    switch: CompletionItemKind.Keyword,
    case: CompletionItemKind.Keyword,
    default: CompletionItemKind.Keyword,
    empty: CompletionItemKind.Keyword,
    ignore: CompletionItemKind.Keyword,
    defer: CompletionItemKind.Keyword,
    throw: CompletionItemKind.Keyword,
    works: CompletionItemKind.Keyword,
    otherwise: CompletionItemKind.Keyword,
    pub: CompletionItemKind.Keyword,
    priv: CompletionItemKind.Keyword,
    static: CompletionItemKind.Keyword,
    use: CompletionItemKind.Keyword,
    unsafe: CompletionItemKind.Keyword,
    as: CompletionItemKind.Keyword,
    inline: CompletionItemKind.Keyword,
    imut: CompletionItemKind.Keyword,
    create: CompletionItemKind.Keyword,
    del: CompletionItemKind.Keyword,
    is: CompletionItemKind.Keyword,
    isnt: CompletionItemKind.Keyword,
    spawn: CompletionItemKind.Keyword,
    shared: CompletionItemKind.Keyword,
    fusion: CompletionItemKind.Keyword,
    this: CompletionItemKind.Keyword,
    thisop: CompletionItemKind.Keyword,
    thisworker: CompletionItemKind.Keyword,
    self: CompletionItemKind.Keyword,
    true: CompletionItemKind.Keyword,
    false: CompletionItemKind.Keyword,
    null: CompletionItemKind.Keyword,
    nil: CompletionItemKind.Keyword,
    extern: CompletionItemKind.Keyword,
    pubif: CompletionItemKind.Keyword
};

function symbolKindToCompletion(sym: LshSymbol): CompletionItemKind {
    switch (sym.kind) {
        case 'function':
        case 'nativeFunction':
            return CompletionItemKind.Function;
        case 'method':
        case 'opdef':
            return CompletionItemKind.Method;
        case 'type':
            return CompletionItemKind.Class;
        case 'field':
            return CompletionItemKind.Field;
        case 'global':
        case 'variable':
        case 'nativeVariable':
            return CompletionItemKind.Variable;
        case 'param':
            return CompletionItemKind.Variable;
        case 'enumMember':
            return CompletionItemKind.EnumMember;
        case 'macro':
            return CompletionItemKind.Function;
        case 'errorType':
            return CompletionItemKind.Class;
        default:
            return CompletionItemKind.Text;
    }
}

function symbolDoc(sym: LshSymbol): string {
    let md = `**${sym.kind === 'type' ? 'Type' : sym.kind === 'method' ? 'Method' : sym.kind === 'function' ? 'Function' : sym.kind === 'field' ? 'Field' : sym.kind === 'global' ? 'Global' : sym.kind === 'param' ? 'Parameter' : sym.kind === 'enumMember' ? 'Enum Member' : sym.kind === 'macro' ? 'Macro' : sym.kind === 'opdef' ? 'Operator' : sym.kind === 'errorType' ? 'Error Type' : 'Symbol'}**`;
    if (sym.ownerType) md += ` of \`${sym.ownerType}\``;
    md += `\n\n\`\`\`leash\n${sym.signature}\n\`\`\``;
    if (sym.docs) md += `\n${sym.docs}`;
    return md;
}

export function completionHandler(
    index: WorkspaceIndex,
    uri: string,
    position: Position,
    settings: LspSettings
): CompletionList {
    const model = index.getModel(uri);
    const items: CompletionItem[] = [];
    if (!model) return { isIncomplete: false, items };

    const ctx = getTokenContext(index, uri, position);
    if (!ctx) return { isIncomplete: false, items };
    const { tokens, pos } = ctx;

    const offset = pos.offsetAt(position);
    let prefix = '';
    {
        const text = model.text;
        let left = offset;
        while (left > 0 && /[a-zA-Z0-9_]/.test(text[left - 1])) left--;
        prefix = text.slice(left, offset);
    }

    const prevTok = ctx.tokenIndex >= 0 && ctx.tokenIndex < tokens.length ? tokens[ctx.tokenIndex] : null;
    const prevPrev = ctx.prev;

    const matches = (name: string): boolean => {
        if (!prefix) return true;
        return name.startsWith(prefix);
    };

    // ---- Member access completion: obj.<TAB> / Type.<TAB>
    if (prevPrev && prevPrev.text === '.' && prevTok) {
        return memberCompletion(index, uri, ctx, items, matches);
    }
    if (prevTok && prevTok.text === '.' && ctx.prev) {
        return memberCompletion(index, uri, ctx, items, matches);
    }

    // ---- Enum member completion: Type::<TAB>
    if (prevPrev && prevPrev.text === '::' && prevTok) {
        const typeTok = ctx.prev2;
        if (typeTok && typeTok.type === 'ident') {
            const typeSyms = index.findTypeSymbols(typeTok.text);
            for (const t of typeSyms) {
                for (const m of index.getDocSymbols(t.uri)) {
                    if (m.kind === 'enumMember' && m.ownerType === t.name && matches(m.name)) {
                        items.push({
                            label: m.name,
                            kind: CompletionItemKind.EnumMember,
                            detail: m.signature,
                            documentation: { kind: MarkupKind.Markdown, value: symbolDoc(m) }
                        });
                    }
                }
            }
        }
        return { isIncomplete: false, items };
    }

    // ---- Type position completion: name : <TAB> or in<<TAB> or generic <TAB>
    const isTypePosition = (prevPrev && prevPrev.text === ':') ||
        (prevPrev && prevPrev.text === '<' && prevPrev.type === 'op');
    if (isTypePosition) {
        for (const t of BUILTIN_TYPES) {
            if (!matches(t)) continue;
            items.push({
                label: t,
                kind: CompletionItemKind.Class,
                detail: BUILTIN_DOCS[t]?.sig ?? t,
                documentation: { kind: MarkupKind.Markdown, value: BUILTIN_DOCS[t]?.desc ?? '' }
            });
        }
        for (const sym of index.getDocSymbols(uri)) {
            if (sym.kind !== 'type' || !matches(sym.name)) continue;
            items.push({
                label: sym.name,
                kind: CompletionItemKind.Class,
                detail: sym.signature,
                documentation: { kind: MarkupKind.Markdown, value: symbolDoc(sym) }
            });
        }
        for (const sym of index.findImportedTypes(uri)) {
            if (!matches(sym.name)) continue;
            items.push({
                label: sym.name,
                kind: CompletionItemKind.Class,
                detail: sym.signature,
                documentation: { kind: MarkupKind.Markdown, value: symbolDoc(sym) }
            });
        }
        return { isIncomplete: false, items };
    }

    // ---- General completion
    const fnc = index.findEnclosingFunction(uri, position);

    if (fnc) {
        for (const p of fnc.params) {
            if (!matches(p.name)) continue;
            items.push({
                label: p.name,
                kind: CompletionItemKind.Variable,
                detail: `${p.name} : ${p.type || 'unknown'} (parameter)`,
                sortText: '0'
            });
        }
        for (const l of index.getLocals(uri, fnc.id)) {
            if (!matches(l.name)) continue;
            items.push({
                label: l.name,
                kind: CompletionItemKind.Variable,
                detail: `${l.name}${l.inferred ? ' := inferred' : ' : ' + (l.type || 'unknown')} (local)`,
                sortText: '0'
            });
        }
    }

    for (const sym of model.symbols) {
        if (!matches(sym.name)) continue;
        if (sym.kind === 'field' || sym.kind === 'method' || sym.kind === 'enumMember') {
            if (sym.kind === 'enumMember') {
                items.push({
                    label: sym.name,
                    kind: CompletionItemKind.EnumMember,
                    detail: sym.signature,
                    sortText: '1',
                    documentation: { kind: MarkupKind.Markdown, value: symbolDoc(sym) }
                });
            }
            continue;
        }
        items.push({
            label: sym.name,
            kind: symbolKindToCompletion(sym),
            detail: sym.signature,
            sortText: '1',
            documentation: { kind: MarkupKind.Markdown, value: symbolDoc(sym) }
        });
    }

    for (const sym of index.getAllImported(uri)) {
        if (!matches(sym.name)) continue;
        items.push({
            label: sym.name,
            kind: symbolKindToCompletion(sym),
            detail: sym.signature,
            sortText: '1',
            documentation: { kind: MarkupKind.Markdown, value: symbolDoc(sym) }
        });
    }

    for (const kw of KEYWORDS) {
        if (!matches(kw)) continue;
        items.push({
            label: kw,
            kind: KEYWORD_KIND[kw] ?? CompletionItemKind.Keyword,
            detail: KEYWORD_DOCS[kw],
            sortText: '2'
        });
    }

    for (const fn of BUILTIN_FUNCTIONS) {
        if (!matches(fn)) continue;
        const doc = BUILTIN_DOCS[fn];
        items.push({
            label: fn,
            kind: CompletionItemKind.Function,
            detail: doc?.sig ?? fn,
            sortText: '3',
            documentation: doc ? { kind: MarkupKind.Markdown, value: `**Built-in**\n\n\`${doc.sig}\`\n\n${doc.desc}` } : undefined
        });
    }

    if (settings.snippets) {
        pushSnippets(items, matches, prefix);
    }

    return { isIncomplete: false, items };
}

function memberCompletion(
    index: WorkspaceIndex,
    uri: string,
    ctx: TokenContext,
    items: CompletionItem[],
    matches: (name: string) => boolean
): CompletionList {
    const tokens = ctx.tokens;
    let dotIdx = ctx.tokenIndex;
    if (tokens[dotIdx] && tokens[dotIdx].text !== '.') dotIdx--;
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
    if (receiverIdents.length === 0) return { isIncomplete: false, items };
    const base = receiverIdents[0];

    let typeName: string | null = null;
    if (base === 'this') {
        const fnc = index.findEnclosingFunction(uri, ctx.pos.positionAt(tokens[dotIdx].start));
        typeName = fnc?.ownerType ?? null;
    } else {
        typeName = index.resolveExprType(uri, ctx.pos.positionAt(tokens[dotIdx].start), ctx.pos.positionAt(tokens[dotIdx].start), base);
    }

    const collected = new Set<string>();
    const pushMember = (sym: LshSymbol, isBuiltin: boolean): void => {
        if (!matches(sym.name) || collected.has(sym.name)) return;
        collected.add(sym.name);
        const isMethod = sym.kind === 'method' || sym.kind === 'opdef';
        const item: CompletionItem = {
            label: sym.name,
            kind: isMethod ? CompletionItemKind.Method : CompletionItemKind.Field,
            detail: sym.signature,
            documentation: { kind: MarkupKind.Markdown, value: isBuiltin ? `**Built-in member** of \`${sym.ownerType ?? base}\`\n\n\`${sym.signature}\`\n\n${sym.docs}` : symbolDoc(sym) }
        };
        if (isMethod && sym.params && sym.params.length > 0) {
            item.insertText = `${sym.name}(${sym.params.map((_, i) => `\${${i + 1}}`).join(', ')})`;
            item.insertTextFormat = InsertTextFormat.Snippet;
        }
        items.push(item);
    };

    const typeBase = typeName ? typeName.replace(/<.*$/, '').trim() : null;
    if (typeBase) {
        if (BUILTIN_TYPE_NAMES.has(typeBase) || typeBase === 'File') {
            for (const m of getBuiltinMembers(typeBase)) {
                const sym: LshSymbol = {
                    id: `builtin|${typeBase}|${m.name}`,
                    name: m.name,
                    kind: m.kind,
                    uri: '',
                    nameRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                    fullRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                    signature: m.sig,
                    params: m.params,
                    returnType: m.returnType,
                    typeParams: [],
                    ownerType: typeBase,
                    visibility: '',
                    docs: m.desc,
                    line: 0,
                    col: 0,
                    endCol: 0
                };
                pushMember(sym, true);
            }
        }
        for (const t of index.findTypeSymbols(typeBase)) {
            for (const m of index.getDocSymbols(t.uri)) {
                if (m.ownerType === t.name && (m.kind === 'method' || m.kind === 'field' || m.kind === 'enumMember' || m.kind === 'opdef')) {
                    pushMember(m, false);
                }
            }
        }
    } else {
        for (const t of index.findTypeSymbols(base)) {
            for (const m of index.getDocSymbols(t.uri)) {
                if (m.ownerType === t.name && (m.kind === 'method' || m.kind === 'field' || m.kind === 'enumMember' || m.kind === 'opdef')) {
                    pushMember(m, false);
                }
            }
        }
        if (BUILTIN_TYPE_NAMES.has(base) || base === 'File') {
            for (const m of getBuiltinMembers(base)) {
                const sym: LshSymbol = {
                    id: `builtin|${base}|${m.name}`,
                    name: m.name,
                    kind: m.kind,
                    uri: '',
                    nameRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                    fullRange: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
                    signature: m.sig,
                    params: m.params,
                    returnType: m.returnType,
                    typeParams: [],
                    ownerType: base,
                    visibility: '',
                    docs: m.desc,
                    line: 0,
                    col: 0,
                    endCol: 0
                };
                pushMember(sym, true);
            }
        }
    }
    return { isIncomplete: false, items };
}

function pushSnippets(items: CompletionItem[], matches: (name: string) => boolean, prefix: string): void {
    void prefix;
    const SNIPPETS: Array<{ label: string; kind: CompletionItemKind; insert: string; doc: string }> = [
        { label: 'fnc', kind: CompletionItemKind.Snippet, insert: 'fnc ${1:name}(${2:args}) {\n\t${3}\n}', doc: 'Function definition' },
        { label: 'worker fnc', kind: CompletionItemKind.Snippet, insert: 'worker fnc ${1:name}(${2:args}) {\n\t${3}\n}', doc: 'Worker function definition' },
        { label: 'def struct', kind: CompletionItemKind.Snippet, insert: 'def ${1:Name} : struct {\n\t${2}\n};', doc: 'Struct definition' },
        { label: 'def class', kind: CompletionItemKind.Snippet, insert: 'def ${1:Name} : class {\n\t${2}\n};', doc: 'Class definition' },
        { label: 'def enum', kind: CompletionItemKind.Snippet, insert: 'def ${1:Name} : enum {\n\t${2}\n};', doc: 'Enum definition' },
        { label: 'if', kind: CompletionItemKind.Snippet, insert: 'if ${1:condition} {\n\t${2}\n}', doc: 'If statement' },
        { label: 'if/also', kind: CompletionItemKind.Snippet, insert: 'if ${1:condition} {\n\t${2}\n} also ${3:condition2} {\n\t${4}\n}', doc: 'If/else-if statement' },
        { label: 'while', kind: CompletionItemKind.Snippet, insert: 'while ${1:condition} {\n\t${2}\n}', doc: 'While loop' },
        { label: 'for', kind: CompletionItemKind.Snippet, insert: 'for ${1:i}: int = ${2:0}; ${3:condition}; ${4:step} {\n\t${5}\n}', doc: 'For loop' },
        { label: 'foreach array', kind: CompletionItemKind.Snippet, insert: 'foreach ${1:i}, ${2:v} in<array> ${3:collection} {\n\t${4}\n}', doc: 'Iterate over an array' },
        { label: 'foreach vector', kind: CompletionItemKind.Snippet, insert: 'foreach ${1:i}, ${2:v} in<vector> ${3:collection} {\n\t${4}\n}', doc: 'Iterate over a vector' },
        { label: 'loop', kind: CompletionItemKind.Snippet, insert: 'loop {\n\t${1}\n}', doc: 'Infinite loop' },
        { label: 'with', kind: CompletionItemKind.Snippet, insert: 'with ${1:name}: ${2:type} = ${3:value} {\n\t${4}\n}', doc: 'Scoped variable block' },
        { label: 'switch', kind: CompletionItemKind.Snippet, insert: 'switch ${1:expr} {\n\tcase ${2:value} {\n\t\t${3}\n\t}\n\tdefault {\n\t\t${4}\n\t}\n}', doc: 'Switch statement' },
        { label: 'opdef', kind: CompletionItemKind.Snippet, insert: 'opdef ${1:Type}.${2:method}(${3:args}) : ${4:ret} {\n\t${5}\n}', doc: 'Operator / extension method definition' },
        { label: 'error', kind: CompletionItemKind.Snippet, insert: 'error ${1:Name}(${2:args}) -> "${3:message}";', doc: 'Custom error type' },
        { label: 'macro', kind: CompletionItemKind.Snippet, insert: 'def ${1:NAME} : macro(${2:args}) |> ${3:expr};', doc: 'Macro definition' },
        { label: 'use', kind: CompletionItemKind.Snippet, insert: 'use ${1:module}::${2:Item};', doc: 'Import statement' },
        { label: 'works/otherwise', kind: CompletionItemKind.Snippet, insert: 'works {\n\t${1}\n} otherwise {\n\t${2}\n}', doc: 'Error handling block' }
    ];
    for (const s of SNIPPETS) {
        if (!matches(s.label.split(' ')[0])) continue;
        items.push({
            label: s.label,
            kind: s.kind,
            insertText: s.insert,
            insertTextFormat: InsertTextFormat.Snippet,
            detail: s.doc,
            sortText: '4'
        });
    }
}