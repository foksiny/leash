import { DocumentLink, Range } from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { uriToFsPath, fsPathToUri, TextPositioner } from '../util';
import { readProjectInfo, resolveModuleFile } from '../config';

export function documentLinksHandler(index: WorkspaceIndex, uri: string): DocumentLink[] {
    const model = index.getModel(uri);
    if (!model) return [];
    const pos = new TextPositioner(model.text);
    const fsPath = uriToFsPath(uri);
    const project = readProjectInfo(fsPath);
    const links: DocumentLink[] = [];

    for (const use of model.uses) {
        const moduleFile = resolveModuleFile(use.modulePath, fsPath, project.importsDirs);
        const start = pos.offsetAt(use.range.start);
        const end = pos.offsetAt(use.fullRange.end);
        const line = use.range.start.line;
        const lineText = pos.lineText(line);
        const modPathStart = lineText.indexOf(use.modulePath.join('::'));
        let linkRange: Range;
        if (modPathStart >= 0) {
            linkRange = {
                start: { line, character: modPathStart },
                end: { line, character: modPathStart + use.modulePath.join('::').length }
            };
        } else {
            linkRange = {
                start: { line: use.range.start.line, character: use.range.start.character },
                end: { line: use.fullRange.end.line, character: use.fullRange.end.character }
            };
        }
        links.push({
            range: linkRange,
            target: moduleFile ? fsPathToUri(moduleFile) : undefined
        });
    }
    return links;
}