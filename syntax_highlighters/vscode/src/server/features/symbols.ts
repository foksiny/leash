import { DocumentSymbol, SymbolKind, SymbolInformation, WorkspaceSymbol, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { LshSymbol } from '../types';

function toSymbolKind(kind: string): SymbolKind {
    switch (kind) {
        case 'function':
            return SymbolKind.Function;
        case 'method':
            return SymbolKind.Method;
        case 'type':
            return SymbolKind.Class;
        case 'field':
            return SymbolKind.Field;
        case 'global':
            return SymbolKind.Variable;
        case 'variable':
            return SymbolKind.Variable;
        case 'param':
            return SymbolKind.Variable;
        case 'enumMember':
            return SymbolKind.EnumMember;
        case 'macro':
            return SymbolKind.Function;
        case 'nativeFunction':
            return SymbolKind.Function;
        case 'nativeVariable':
            return SymbolKind.Variable;
        case 'opdef':
            return SymbolKind.Operator;
        case 'errorType':
            return SymbolKind.Class;
        default:
            return SymbolKind.Object;
    }
}

export function documentSymbolsHandler(index: WorkspaceIndex, uri: string): DocumentSymbol[] {
    const symbols = index.getDocSymbols(uri);
    const results: DocumentSymbol[] = [];
    const childrenByOwner = new Map<string, DocumentSymbol[]>();

    for (const sym of symbols) {
        const ds: DocumentSymbol = {
            name: sym.name,
            detail: sym.signature,
            kind: toSymbolKind(sym.kind),
            range: sym.fullRange,
            selectionRange: sym.nameRange,
            children: []
        };
        if (sym.ownerType) {
            const arr = childrenByOwner.get(sym.ownerType) ?? [];
            arr.push(ds);
            childrenByOwner.set(sym.ownerType, arr);
        } else {
            results.push(ds);
        }
    }

    const attach = (ds: DocumentSymbol): void => {
        const children = childrenByOwner.get(ds.name);
        if (children) {
            for (const child of children) {
                attach(child);
            }
            ds.children = children;
        }
    };
    for (const ds of results) {
        attach(ds);
    }

    return results;
}

export function workspaceSymbolsHandler(index: WorkspaceIndex, query: string): WorkspaceSymbol[] {
    const q = query.trim();
    const results: WorkspaceSymbol[] = [];
    let symbols: LshSymbol[];
    if (q === '' || q === ':') {
        symbols = index.getAllSymbols();
    } else {
        symbols = [];
        const parts = q.toLowerCase().split(/\s+/);
        for (const s of index.getAllSymbols()) {
            const lower = s.name.toLowerCase();
            if (parts.some(p => lower.includes(p))) {
                symbols.push(s);
            }
        }
    }
    for (const sym of symbols.slice(0, 500)) {
        results.push({
            name: sym.name,
            kind: toSymbolKind(sym.kind),
            location: {
                uri: sym.uri,
                range: sym.nameRange
            },
            containerName: sym.ownerType ?? undefined
        });
    }
    return results;
}