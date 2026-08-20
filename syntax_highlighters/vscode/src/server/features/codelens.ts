import { CodeLens, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { computeReferences, referenceCount } from './references';
import { LshSymbol } from '../types';

const LENS_KINDS = new Set(['function', 'method', 'macro', 'type', 'errorType', 'opdef', 'nativeFunction']);

export function codeLensHandler(index: WorkspaceIndex, uri: string): CodeLens[] {
    const model = index.getModel(uri);
    if (!model) return [];
    const lenses: CodeLens[] = [];

    for (const sym of model.symbols) {
        if (!LENS_KINDS.has(sym.kind)) continue;
        const count = referenceCount(index, sym);
        lenses.push({
            range: sym.nameRange,
            command: {
                title: count === 0 ? '0 references' : `${count} reference${count === 1 ? '' : 's'}`,
                command: 'leash.showReferences',
                arguments: [
                    uri,
                    {
                        line: sym.nameRange.start.line,
                        character: sym.nameRange.start.character
                    },
                    computeReferences(index, sym, { includeDeclaration: false, maxResults: 200 })
                ]
            }
        });
    }
    return lenses;
}