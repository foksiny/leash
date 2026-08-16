import { Location, LocationLink, Position } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';
import { LshSymbol } from '../types';

export function definitionHandler(index: WorkspaceIndex, uri: string, position: Position): LocationLink[] | null {
    const resolved = resolveWordAt(index, uri, position);
    if (!resolved.symbol) return null;
    const symbol = resolved.symbol;
    if (symbol.uri === '') return null;

    const candidates = collectCandidates(index, uri, symbol, resolved.word);
    if (candidates.length === 0) return null;

    return candidates.map(c => ({
        targetUri: c.uri,
        targetRange: c.fullRange,
        targetSelectionRange: c.nameRange,
        originSelectionRange: resolved.range
    }));
}

function collectCandidates(index: WorkspaceIndex, uri: string, symbol: LshSymbol, word: string): LshSymbol[] {
    const results: LshSymbol[] = [];
    const seen = new Set<string>();
    if (symbol.uri === uri || symbol.uri !== '') {
        if (!seen.has(symbol.id)) {
            seen.add(symbol.id);
            results.push(symbol);
        }
    }
    if (symbol.kind === 'variable' || symbol.kind === 'param') {
        return results;
    }
    for (const s of index.getSymbolsByName(word)) {
        if (!seen.has(s.id) && s.kind === symbol.kind && s.uri !== uri) {
            seen.add(s.id);
            results.push(s);
        }
    }
    return results;
}

export function typeDefinitionHandler(index: WorkspaceIndex, uri: string, position: Position): Location | Location[] | null {
    const resolved = resolveWordAt(index, uri, position);
    if (!resolved.symbol) return null;
    const sym = resolved.symbol;
    if (sym.uri === '' || sym.kind !== 'type') return null;
    return {
        uri: sym.uri,
        range: sym.nameRange
    };
}