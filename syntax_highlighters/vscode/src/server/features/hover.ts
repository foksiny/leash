import { Hover, MarkupKind, Position } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';
import { symbolToMarkdown, BUILTIN_DOCS } from '../builtins';
import { relativePath, uriToFsPath } from '../util';
import { LshSymbol } from '../types';

export function hoverHandler(index: WorkspaceIndex, uri: string, position: Position): Hover | null {
    const resolved = resolveWordAt(index, uri, position);
    const word = resolved.word;
    if (!word) return null;

    let markdown: string;
    let range = resolved.range;

    switch (resolved.kind) {
        case 'builtin':
            markdown = builtinMarkdown(resolved.builtin!);
            break;
        case 'builtinType':
            markdown = builtinMarkdown(resolved.builtin ?? { sig: word, desc: '' });
            break;
        case 'keyword':
            markdown = `**Keyword**: \`${word}\`  \n___  \n${BUILTIN_DOCS[word] ?? KEYWORD_DOC(word)}`;
            break;
        case 'local':
        case 'param':
        case 'symbol':
        case 'imported':
        case 'enumMember':
            markdown = symbolMarkdown(index, resolved.symbol!);
            break;
        case 'member':
            markdown = memberMarkdown(resolved.symbol!);
            break;
        default:
            return null;
    }

    return {
        contents: {
            kind: MarkupKind.Markdown,
            value: markdown
        },
        range
    };
}

function KEYWORD_DOC(word: string): string {
    const { KEYWORD_DOCS } = require('../builtins') as typeof import('../builtins');
    return KEYWORD_DOCS[word] ?? '';
}

function builtinMarkdown(b: { sig: string; desc: string; detail?: string }): string {
    let md = `**Built-in**  \n\`\`\`leash\n${b.sig}\n\`\`\`  \n${b.desc}`;
    if (b.detail) md += `  \n\n${b.detail}`;
    return md;
}

function symbolMarkdown(index: WorkspaceIndex, sym: LshSymbol): string {
    let md = symbolToMarkdown(sym);
    if (sym.kind === 'type') {
        const members = index
            .getDocSymbols(sym.uri)
            .filter(m => m.ownerType === sym.name && (m.kind === 'method' || m.kind === 'field' || m.kind === 'enumMember'))
            .slice(0, 30)
            .map(m => m.name);
        if (members.length > 0) {
            md += `  \n\n**Members**: ${members.join(', ')}`;
        }
    }
    if (sym.kind === 'function' || sym.kind === 'method' || sym.kind === 'macro' || sym.kind === 'opdef') {
        if (sym.returnType) {
            md += `  \n\n**Returns**: \`${sym.returnType}\``;
        }
    }
    return md;
}

function memberMarkdown(sym: LshSymbol): string {
    const builtin = sym.id.startsWith('builtin|');
    let md = `**${builtin ? 'Built-in ' : ''}${sym.kind === 'method' ? 'Method' : 'Field'}**`;
    if (sym.ownerType) md += `  \n_of \`${sym.ownerType}\`_`;
    md += `  \n\`\`\`leash\n${sym.signature}\n\`\`\``;
    if (sym.docs) md += `  \n${sym.docs}`;
    if (!builtin && sym.uri) {
        const file = relativePath(process.cwd(), uriToFsPath(sym.uri));
        md += `  \n\n_Defined in ${file}_`;
    }
    return md;
}