import { DocumentHighlight, DocumentHighlightKind, Position } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';
import { computeReferences } from './references';

export function documentHighlightHandler(index: WorkspaceIndex, uri: string, position: Position): DocumentHighlight[] | null {
    const resolved = resolveWordAt(index, uri, position);
    if (!resolved.symbol || resolved.symbol.uri === '') return null;

    const locations = computeReferences(index, resolved.symbol, {
        includeDeclaration: true,
        uriOnly: uri
    });

    const highlights: DocumentHighlight[] = [];
    for (const loc of locations) {
        const isDecl = resolved.symbol.nameRange.start.line === loc.range.start.line &&
            resolved.symbol.nameRange.start.character === loc.range.start.character;
        highlights.push({
            range: loc.range,
            kind: isDecl ? DocumentHighlightKind.Write : DocumentHighlightKind.Text
        });
    }
    return highlights.length > 0 ? highlights : null;
}