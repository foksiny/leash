import { CodeAction, CodeActionKind, Diagnostic, Position, Range, TextEdit, WorkspaceEdit } from 'vscode-languageserver';
import { TextPositioner, tokenize } from '../util';

export function codeActionsHandler(
    uri: string,
    _range: Range,
    context: { diagnostics: Diagnostic[] },
    text: string
): CodeAction[] {
    const actions: CodeAction[] = [];
    const pos = new TextPositioner(text);

    for (const diag of context.diagnostics) {
        const message = diag.message;
        if (message.includes('Expected SEMI')) {
            const action = makeSemicolonAction(uri, pos, diag);
            if (action) actions.push(action);
        }
        if (/let|var/.test(message) && /don/.test(message) && /keyword/.test(message)) {
            const action = makeRemoveLetVarAction(uri, pos, diag);
            if (action) actions.push(action);
        }
        const typeMatch = /Variable '.*' declared as '([^']+)' but assigned/.exec(message) ||
            /Cannot assign '.*' to a variable of type '([^']+)'/.exec(message);
        if (typeMatch) {
            const action = makeCastAction(uri, pos, diag, typeMatch[1]);
            if (action) actions.push(action);
        }
    }

    return actions;
}

function makeSemicolonAction(uri: string, pos: TextPositioner, diag: Diagnostic): CodeAction | null {
    const line = diag.range.start.line;
    const char = diag.range.start.character;
    const lineText = pos.lineText(line);
    if (lineText.trim() === '') return null;
    const insertPos: Position = { line, character: char };
    const edit: WorkspaceEdit = {
        changes: {
            [uri]: [{ range: { start: insertPos, end: insertPos }, newText: ';' }]
        }
    };
    return {
        title: 'Add missing `;`',
        kind: CodeActionKind.QuickFix,
        diagnostics: [diag],
        edit
    };
}

function makeRemoveLetVarAction(uri: string, pos: TextPositioner, diag: Diagnostic): CodeAction | null {
    const tokens = tokenize(pos.text);
    const line = diag.range.start.line;
    const lineStart = pos.lineStarts[line] ?? 0;
    const lineEnd = line + 1 < pos.lineStarts.length ? pos.lineStarts[line + 1] - 1 : pos.text.length;
    const edits: TextEdit[] = [];
    for (const t of tokens) {
        if (t.start < lineStart || t.start > lineEnd) continue;
        if (t.type === 'ident' && (t.text === 'let' || t.text === 'var')) {
            let endOffset = t.end;
            if (pos.text[endOffset] === ' ') endOffset++;
            edits.push({
                range: {
                    start: pos.positionAt(t.start),
                    end: pos.positionAt(endOffset)
                },
                newText: ''
            });
        }
    }
    if (edits.length === 0) return null;
    return {
        title: 'Remove `let`/`var` keyword',
        kind: CodeActionKind.QuickFix,
        diagnostics: [diag],
        edit: { changes: { [uri]: edits } }
    };
}

function makeCastAction(uri: string, pos: TextPositioner, diag: Diagnostic, targetType: string): CodeAction | null {
    const typeName = targetType.replace(/<.*$/, '').trim();
    if (!/^[A-Za-z_]\w*$/.test(typeName)) return null;
    const line = diag.range.start.line;
    const lineText = pos.lineText(line);
    let eq = -1;
    let depth = 0;
    for (let i = 0; i < lineText.length; i++) {
        const c = lineText[i];
        if (c === '(' || c === '[' || c === '{') depth++;
        if (c === ')' || c === ']' || c === '}') depth--;
        if (c === '=' && depth === 0) {
            const prev = lineText[i - 1];
            const next = lineText[i + 1];
            if (prev !== ':' && prev !== '=' && prev !== '<' && prev !== '>' && prev !== '!' && next !== '=' && next !== '>') {
                eq = i;
                break;
            }
        }
    }
    if (eq === -1) return null;
    const insertAt: Position = { line, character: eq + 1 };
    return {
        title: `Cast expression to \`${typeName}\``,
        kind: CodeActionKind.QuickFix,
        diagnostics: [diag],
        edit: {
            changes: {
                [uri]: [{ range: { start: insertAt, end: insertAt }, newText: ` (${typeName})` }]
            }
        }
    };
}