import { SemanticTokens, SemanticTokensBuilder, SemanticTokensLegend } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { tokenize } from '../util';
import { KEYWORDS, BUILTIN_FUNCTIONS, BUILTIN_TYPES } from '../builtins';

export const SEMANTIC_TOKEN_TYPES = [
    'keyword',
    'type',
    'function',
    'parameter',
    'variable',
    'property',
    'enumMember',
    'macro',
    'namespace',
    'comment',
    'operator',
    'decorator'
] as const;

export const SEMANTIC_TOKEN_MODIFIERS = [
    'declaration',
    'readonly',
    'static',
    'defaultLibrary'
] as const;

export const SEMANTIC_TOKENS_LEGEND: SemanticTokensLegend = {
    tokenTypes: [...SEMANTIC_TOKEN_TYPES],
    tokenModifiers: [...SEMANTIC_TOKEN_MODIFIERS]
};

const TYPE_IDX = SEMANTIC_TOKEN_TYPES.indexOf('type');
const FUNC_IDX = SEMANTIC_TOKEN_TYPES.indexOf('function');
const KEYWORD_IDX = SEMANTIC_TOKEN_TYPES.indexOf('keyword');
const PARAM_IDX = SEMANTIC_TOKEN_TYPES.indexOf('parameter');
const VAR_IDX = SEMANTIC_TOKEN_TYPES.indexOf('variable');
const PROP_IDX = SEMANTIC_TOKEN_TYPES.indexOf('property');
const ENUM_IDX = SEMANTIC_TOKEN_TYPES.indexOf('enumMember');
const MACRO_IDX = SEMANTIC_TOKEN_TYPES.indexOf('macro');
const NS_IDX = SEMANTIC_TOKEN_TYPES.indexOf('namespace');
const COMMENT_IDX = SEMANTIC_TOKEN_TYPES.indexOf('comment');
const OP_IDX = SEMANTIC_TOKEN_TYPES.indexOf('operator');
const DECOR_IDX = SEMANTIC_TOKEN_TYPES.indexOf('decorator');

const DECL_MOD = 1;
const READONLY_MOD = 2;
const STATIC_MOD = 4;
const LIB_MOD = 8;

export function semanticTokensHandler(index: WorkspaceIndex, uri: string): SemanticTokens {
    const model = index.getModel(uri);
    const builder = new SemanticTokensBuilder();
    if (!model) return builder.build();

    const tokens = tokenize(model.text);
    const symbolsByName = new Map<string, typeof model.symbols>();
    for (const sym of model.symbols) {
        const arr = symbolsByName.get(sym.name) ?? [];
        arr.push(sym);
        symbolsByName.set(sym.name, arr);
    }

    const localNames = new Map<string, number>();
    const localDecls = new Set<string>();
    for (const local of model.locals) {
        if (!localNames.has(local.name)) localNames.set(local.name, 1);
        localDecls.add(`${local.range.start.line}:${local.range.start.character}`);
    }

    const useRanges: Array<{ start: number; end: number }> = [];
    for (const use of model.uses) {
        const s = model.text.indexOf(use.modulePath.join('::'));
        if (s >= 0) useRanges.push({ start: s, end: s + use.modulePath.join('::').length + 10 });
    }

    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        let typeIdx = -1;
        let mods = 0;

        switch (t.type) {
            case 'comment':
                typeIdx = COMMENT_IDX;
                break;
            case 'op':
                typeIdx = OP_IDX;
                break;
            case 'at':
                typeIdx = DECOR_IDX;
                break;
            // Strings and numbers are left to the TextMate grammar so that
            // escape sequences, interpolation and number kinds (hex/binary/
            // float/...) keep their finer highlighting.
            case 'ident': {
                const text = t.text;
                const prev = i > 0 ? tokens[i - 1] : null;
                const next = i + 1 < tokens.length ? tokens[i + 1] : null;

                if (prev && prev.type === 'at') {
                    typeIdx = DECOR_IDX;
                    break;
                }
                if (isInUseRange(useRanges, t.start)) {
                    typeIdx = NS_IDX;
                    break;
                }
                if (KEYWORDS.includes(text)) {
                    typeIdx = KEYWORD_IDX;
                    if (text === 'true' || text === 'false' || text === 'null' || text === 'nil') mods |= READONLY_MOD;
                    break;
                }
                if (BUILTIN_TYPES.includes(text)) {
                    typeIdx = TYPE_IDX;
                    mods |= LIB_MOD;
                    break;
                }
                if (BUILTIN_FUNCTIONS.includes(text) && next?.text === '(') {
                    typeIdx = FUNC_IDX;
                    mods |= LIB_MOD;
                    break;
                }

                const matches = symbolsByName.get(text);
                if (matches && matches.length > 0) {
                    let isDecl = false;
                    for (const sym of matches) {
                        if (sym.line === t.line && sym.col === t.char) {
                            isDecl = true;
                            break;
                        }
                    }
                    const first = matches[0];
                    switch (first.kind) {
                        case 'function':
                        case 'nativeFunction':
                        case 'opdef':
                            typeIdx = FUNC_IDX;
                            break;
                        case 'macro':
                            typeIdx = MACRO_IDX;
                            break;
                        case 'type':
                        case 'errorType':
                            typeIdx = TYPE_IDX;
                            break;
                        case 'method':
                            typeIdx = PROP_IDX;
                            break;
                        case 'field':
                            typeIdx = PROP_IDX;
                            break;
                        case 'global':
                            typeIdx = VAR_IDX;
                            break;
                        case 'param':
                            typeIdx = PARAM_IDX;
                            break;
                        case 'enumMember':
                            typeIdx = ENUM_IDX;
                            break;
                        default:
                            typeIdx = VAR_IDX;
                    }
                    if (isDecl) mods |= DECL_MOD;
                    if (first.visibility.includes('static')) mods |= STATIC_MOD;
                    break;
                }

                if (prev?.text === '.') {
                    typeIdx = PROP_IDX;
                    break;
                }
                if (prev?.text === '->') {
                    typeIdx = PROP_IDX;
                    break;
                }
                if (prev?.text === '::') {
                    typeIdx = ENUM_IDX;
                    break;
                }
                if (next?.text === '(') {
                    typeIdx = FUNC_IDX;
                    break;
                }
                if (localNames.has(text)) {
                    typeIdx = VAR_IDX;
                    if (localDecls.has(`${t.line}:${t.char}`)) mods |= DECL_MOD;
                    break;
                }
                if (prev?.text === ':') {
                    typeIdx = VAR_IDX;
                    break;
                }
                typeIdx = VAR_IDX;
                break;
            }
            default:
                continue;
        }

        if (typeIdx >= 0) {
            builder.push(t.line, t.char, t.text.length, typeIdx, mods);
        }
    }
    return builder.build();
}

function isInUseRange(ranges: Array<{ start: number; end: number }>, offset: number): boolean {
    for (const r of ranges) {
        if (offset >= r.start && offset <= r.end) return true;
    }
    return false;
}