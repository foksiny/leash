import { tokenize } from './util';
import { LspSettings } from './types';

export function formatDocument(text: string, settings: LspSettings): string {
    const tokens = tokenize(text);
    const lines = text.split('\n');

    const tokensByLine = new Map<number, Array<{ text: string; line: number }>>();
    const inBlockLine = new Set<number>();
    let currentBlock: { type: 'comment' | 'string'; endLine: number } | null = null;

    for (const tok of tokens) {
        const isBlock = (tok.type === 'comment' && tok.text.startsWith('/*')) ||
            (tok.type === 'string' && (tok.text.startsWith('"""') || tok.text.startsWith("'''")));
        if (isBlock && tok.text.includes('\n')) {
            const endLine = tok.line + (tok.text.match(/\n/g)?.length ?? 0);
            for (let l = tok.line; l <= endLine; l++) {
                if (l > tok.line) inBlockLine.add(l);
            }
            currentBlock = null;
            continue;
        }
        const arr = tokensByLine.get(tok.line) ?? [];
        arr.push({ text: tok.text, line: tok.line });
        tokensByLine.set(tok.line, arr);
    }

    const indentSize = settings.formatting.indentSize;
    const indentUnit = settings.formatting.insertSpaces ? ' '.repeat(indentSize) : '\t';

    const out: string[] = [];
    let depth = 0;
    let prevBlank = false;

    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
        const original = lines[lineIdx];
        const trimmed = original.trim();

        if (inBlockLine.has(lineIdx)) {
            out.push(original);
            prevBlank = false;
            continue;
        }

        if (trimmed === '') {
            if (prevBlank && out.length > 0) {
                continue;
            }
            prevBlank = true;
            out.push('');
            continue;
        }
        prevBlank = false;

        const firstChar = trimmed[0];
        let level = depth;
        if (firstChar === '}') {
            level = Math.max(0, depth - 1);
        }

        const lineTokens = tokensByLine.get(lineIdx) ?? [];
        let lineDepth = depth;
        for (const t of lineTokens) {
            if (t.text === '{') lineDepth++;
            else if (t.text === '}') lineDepth--;
        }
        depth = Math.max(0, lineDepth);

        out.push(indentUnit.repeat(level) + trimmed);
    }

    while (out.length > 0 && out[out.length - 1] === '') {
        out.pop();
    }

    return out.join('\n') + (text.endsWith('\n') ? '\n' : '');
}