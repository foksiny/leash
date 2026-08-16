import { FoldingRange, FoldingRangeKind } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { tokenize } from '../util';

export function foldingRangesHandler(index: WorkspaceIndex, uri: string): FoldingRange[] {
    const model = index.getModel(uri);
    if (!model) return [];
    const tokens = tokenize(model.text);
    const ranges: FoldingRange[] = [];
    const seen = new Set<string>();

    const openStack: number[] = [];
    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        if (t.text === '{') {
            openStack.push(i);
        } else if (t.text === '}' && openStack.length > 0) {
            const openIdx = openStack.pop()!;
            const open = tokens[openIdx];
            if (open.line < t.line) {
                const key = `${open.line}:${t.line}`;
                if (!seen.has(key)) {
                    seen.add(key);
                    ranges.push({
                        startLine: open.line,
                        endLine: t.line,
                        kind: FoldingRangeKind.Region
                    });
                }
            }
        }
    }

    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        if (t.type === 'comment' && t.text.startsWith('/*') && t.text.includes('\n')) {
            const endLine = t.line + (t.text.match(/\n/g)?.length ?? 0);
            if (endLine > t.line) {
                ranges.push({
                    startLine: t.line,
                    endLine,
                    kind: FoldingRangeKind.Comment
                });
            }
        } else if (t.type === 'string' && (t.text.startsWith('"""') || t.text.startsWith("'''")) && t.text.includes('\n')) {
            const endLine = t.line + (t.text.match(/\n/g)?.length ?? 0);
            if (endLine > t.line) {
                ranges.push({
                    startLine: t.line,
                    endLine,
                    kind: FoldingRangeKind.Region
                });
            }
        }
    }

    let commentRunStart = -1;
    let commentRunEnd = -1;
    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        if (t.type === 'comment' && t.text.startsWith('//')) {
            if (commentRunStart === -1 || t.line !== commentRunEnd + 1) {
                if (commentRunStart !== -1 && commentRunEnd > commentRunStart) {
                    ranges.push({
                        startLine: commentRunStart,
                        endLine: commentRunEnd,
                        kind: FoldingRangeKind.Comment
                    });
                }
                commentRunStart = t.line;
            }
            commentRunEnd = t.line;
        } else if (commentRunStart !== -1) {
            if (commentRunEnd > commentRunStart) {
                ranges.push({
                    startLine: commentRunStart,
                    endLine: commentRunEnd,
                    kind: FoldingRangeKind.Comment
                });
            }
            commentRunStart = -1;
        }
    }
    if (commentRunStart !== -1 && commentRunEnd > commentRunStart) {
        ranges.push({
            startLine: commentRunStart,
            endLine: commentRunEnd,
            kind: FoldingRangeKind.Comment
        });
    }

    return ranges;
}