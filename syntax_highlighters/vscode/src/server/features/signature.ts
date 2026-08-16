import { Position, SignatureHelp, SignatureInformation, ParameterInformation } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { getTokenContext, resolveMemberAccess } from './resolve';
import { BUILTIN_DOCS, getBuiltinMembers } from '../builtins';
import { LshSymbol, ParamInfo } from '../types';

export function signatureHelpHandler(index: WorkspaceIndex, uri: string, position: Position): SignatureHelp | null {
    const ctx = getTokenContext(index, uri, position);
    if (!ctx) return null;
    const { tokens, pos } = ctx;

    let openParen = -1;
    let depth = 0;
    for (let i = ctx.tokenIndex; i >= 0; i--) {
        const t = tokens[i];
        if (t.text === ')') depth++;
        else if (t.text === '(') {
            if (depth === 0) {
                openParen = i;
                break;
            }
            depth--;
        }
    }
    if (openParen <= 0) return null;

    const fnNameTok = tokens[openParen - 1];
    if (!fnNameTok || fnNameTok.type !== 'ident') return null;
    const name = fnNameTok.text;

    let params: ParamInfo[] | null = null;
    let label = '';
    let docs = '';

    const fnc = index.findEnclosingFunction(uri, pos.positionAt(fnNameTok.start));
    if (fnc && fnc.name === name) {
        params = fnc.params;
        label = `fnc ${name}(${fnc.params.map(p => p.name).join(', ')})`;
    }
    if (!params) {
        const docSymbol = index.getDocSymbols(uri).find(s => s.name === name && (s.kind === 'function' || s.kind === 'macro' || s.kind === 'errorType' || s.kind === 'nativeFunction'));
        if (docSymbol) {
            params = docSymbol.params;
            label = docSymbol.signature;
        }
    }
    if (!params) {
        const prev = tokens[openParen - 2];
        if (prev && prev.text === '.') {
            const member = resolveMemberAccess(index, uri, ctx, fnNameTok, name);
            if (member) {
                params = member.symbol.params;
                label = member.symbol.signature;
                docs = member.symbol.docs;
            }
        }
    }
    if (!params) {
        const imported = index.findImported(uri, name);
        if (imported.length > 0 && (imported[0].kind === 'function' || imported[0].kind === 'macro')) {
            params = imported[0].params;
            label = imported[0].signature;
            docs = imported[0].docs;
        }
    }
    if (!params) {
        const builtin = BUILTIN_DOCS[name];
        if (builtin) {
            const paramNames = /\(([^)]*)\)/.exec(builtin.sig)?.[1] ?? '';
            params = paramNames.split(',').map(p => p.trim()).filter(Boolean).map(p => ({
                name: p.replace(/\?.*$/, '').trim() || p,
                type: p,
                variadic: p.startsWith('...'),
                hasDefault: p.includes('?')
            }));
            label = builtin.sig;
            docs = builtin.desc;
        }
    }
    if (!params) return null;

    let active = 0;
    let commaCount = 0;
    for (let i = openParen + 1; i < ctx.tokenIndex; i++) {
        if (tokens[i].text === ',') commaCount++;
        if (tokens[i].text === '(') commaCount += 0;
    }
    active = Math.min(commaCount, Math.max(params.length - 1, 0));

    return {
        signatures: [
            {
                label,
                documentation: docs || undefined,
                parameters: params.map(p => ParameterInformation.create(`${p.name}`, p.type))
            }
        ],
        activeSignature: 0,
        activeParameter: active
    };
}