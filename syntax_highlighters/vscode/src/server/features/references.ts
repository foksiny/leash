import { Location, Position, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { LshSymbol } from '../types';
import { Token, TextPositioner, tokenize } from '../util';

interface ScanOptions {
    includeDeclaration?: boolean;
    uriOnly?: string;
    maxResults?: number;
}

function matchesKind(sym: LshSymbol, prev: Token | null, next: Token | null, next2: Token | null): boolean {
    const prevText = prev ? prev.text : '';
    const nextText = next ? next.text : '';
    switch (sym.kind) {
        case 'function':
        case 'macro':
        case 'nativeFunction':
        case 'errorType':
            return nextText === '(';
        case 'opdef':
            return nextText === '(' && (prevText === '.' || prevText === '::' || prev!.type === 'ident');
        case 'method':
            return nextText === '(' && (prevText === '.' || prevText === '::' || (prev !== null && prev.type === 'ident'));
        case 'field':
            return nextText !== '(' && (prevText === '.' || prevText === '::' || (prev !== null && prev.type === 'ident'));
        case 'enumMember':
            return prevText === '::';
        case 'type': {
            if (prevText === '.' || prevText === '::') return false;
            if ([':', '(', '[', '<', ',', '->'].includes(prevText)) return true;
            if (nextText === '{' || nextText === '<' || nextText === '[' || nextText === '.') return true;
            if (prev !== null && prev.type === 'ident' && [',', ')', ';', '='].includes(nextText)) return true;
            if (next2 && next2.text === '(' && prevText === '=') return true;
            return false;
        }
        case 'global':
        case 'nativeVariable':
            return true;
        case 'variable':
        case 'param':
            return true;
        default:
            return false;
    }
}

function isDeclaration(sym: LshSymbol, token: Token): boolean {
    if (sym.uri === '') return false;
    return sym.line === token.line && sym.col === token.char;
}

export function computeReferences(
    index: WorkspaceIndex,
    symbol: LshSymbol,
    options: ScanOptions = {}
): Location[] {
    if (symbol.uri === '') return [];
    const locations: Location[] = [];
    const uris = options.uriOnly
        ? [options.uriOnly]
        : index.getAllDocUris();

    for (const uri of uris) {
        const model = index.getModel(uri);
        if (!model) continue;
        if (symbol.kind === 'variable' || symbol.kind === 'param') {
            if (uri !== symbol.uri) continue;
        }
        const tokens = tokenize(model.text);
        const pos = new TextPositioner(model.text);

        let ownerRange: Range | null = null;
        if ((symbol.kind === 'variable' || symbol.kind === 'param') && symbol.uri === uri) {
            const fnc = index.findEnclosingFunction(symbol.uri, {
                line: symbol.line,
                character: symbol.col
            });
            ownerRange = fnc ? fnc.fullRange : symbol.fullRange;
        }

        for (let i = 0; i < tokens.length; i++) {
            const t = tokens[i];
            if (t.type !== 'ident' || t.text !== symbol.name) continue;
            if (isDeclaration(symbol, t)) {
                if (!options.includeDeclaration) continue;
            } else if (ownerRange) {
                const tp = pos.positionAt(t.start);
                if (tp.line < ownerRange.start.line || tp.line > ownerRange.end.line) continue;
            }
            const prev = i > 0 ? tokens[i - 1] : null;
            const next = i + 1 < tokens.length ? tokens[i + 1] : null;
            const next2 = i + 2 < tokens.length ? tokens[i + 2] : null;
            if (!matchesKind(symbol, prev, next, next2)) continue;
            if (!options.includeDeclaration && isDeclaration(symbol, t)) continue;
            locations.push({
                uri,
                range: {
                    start: pos.positionAt(t.start),
                    end: pos.positionAt(t.end)
                }
            });
            if (options.maxResults && locations.length >= options.maxResults) {
                return locations;
            }
        }
    }
    return locations;
}

export function referenceCount(index: WorkspaceIndex, symbol: LshSymbol): number {
    return computeReferences(index, symbol, { includeDeclaration: false, maxResults: 500 }).length;
}