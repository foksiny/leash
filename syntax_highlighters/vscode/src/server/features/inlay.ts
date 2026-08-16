import { InlayHint, InlayHintKind, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { tokenize, TextPositioner } from '../util';
import { inferLiteralType } from '../builtins';

export function inlayHintsHandler(index: WorkspaceIndex, uri: string, range: Range): InlayHint[] | null {
    const model = index.getModel(uri);
    if (!model) return [];
    const tokens = tokenize(model.text);
    const pos = new TextPositioner(model.text);
    const hints: InlayHint[] = [];
    const startOffset = pos.offsetAt(range.start);
    const endOffset = pos.offsetAt(range.end);

    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        if (t.text !== ':=') continue;
        if (t.start < startOffset || t.start > endOffset) continue;
        const prev = tokens[i - 1];
        if (!prev || prev.type !== 'ident') continue;
        const next = tokens[i + 1];
        if (!next) continue;
        if (next.type === 'string' || next.type === 'number' || (next.type === 'ident' && (next.text === 'true' || next.text === 'false'))) {
            const typeName = inferLiteralType(next.text);
            if (!typeName) continue;
            hints.push({
                position: { line: t.line, character: t.char + 2 },
                label: `: ${typeName}`,
                kind: InlayHintKind.Type,
                paddingRight: true
            });
        } else if (next.text === '[') {
            hints.push({
                position: { line: t.line, character: t.char + 2 },
                label: ': vec',
                kind: InlayHintKind.Type,
                paddingRight: true
            });
        }
    }
    return hints;
}