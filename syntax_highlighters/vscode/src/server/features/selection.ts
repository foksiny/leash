import { Position, Range, SelectionRange } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';

export function selectionRangeHandler(index: WorkspaceIndex, uri: string, positions: Position[]): SelectionRange[] {
    const model = index.getModel(uri);
    if (!model) return [];
    return positions.map(position => {
        const resolved = resolveWordAt(index, uri, position);
        const wordRange: Range = resolved && resolved.range ? resolved.range : {
            start: position,
            end: position
        };

        let parent: SelectionRange | undefined;
        const fnc = index.findEnclosingFunction(uri, position);
        if (fnc) {
            parent = {
                range: fnc.fullRange,
                parent: parent
            };
        }
        const type = index.getDocSymbols(uri).find(s => s.kind === 'type' && s.fullRange.start.line <= position.line && s.fullRange.end.line >= position.line);
        if (type) {
            parent = {
                range: type.fullRange,
                parent: parent
            };
        }
        return {
            range: wordRange,
            parent
        };
    });
}