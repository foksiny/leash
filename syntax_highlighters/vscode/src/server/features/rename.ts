import { Position, Range, TextEdit, WorkspaceEdit } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';
import { computeReferences } from './references';

export function prepareRenameHandler(index: WorkspaceIndex, uri: string, position: Position): Range | null {
    const resolved = resolveWordAt(index, uri, position);
    if (!resolved.symbol || resolved.symbol.uri === '') return null;
    if (resolved.kind === 'keyword' || resolved.kind === 'builtin' || resolved.kind === 'builtinType') return null;
    return resolved.range;
}

export function renameHandler(
    index: WorkspaceIndex,
    uri: string,
    position: Position,
    newName: string
): WorkspaceEdit | null {
    const resolved = resolveWordAt(index, uri, position);
    if (!resolved.symbol || resolved.symbol.uri === '') return null;
    if (resolved.kind === 'keyword' || resolved.kind === 'builtin' || resolved.kind === 'builtinType') return null;

    const symbol = resolved.symbol;
    const locations = computeReferences(index, symbol, { includeDeclaration: true });

    const changes: Record<string, TextEdit[]> = {};
    for (const loc of locations) {
        (changes[loc.uri] = changes[loc.uri] ?? []).push({
            range: loc.range,
            newText: newName
        });
    }
    return { changes };
}