import * as fs from 'fs';
import * as path from 'path';
import { Position, Range } from 'vscode-languageserver';
import { parseDocument } from './parser';
import { DocModel, LshSymbol, LocalSymbol, UseStmt } from './types';
import { readProjectInfo, resolveModuleFile } from './config';
import { TextPositioner, tokenize, Token, uriToFsPath, fsPathToUri, normalizeTypeName, isTempCheckFile } from './util';

const SKIP_DIRS = new Set([
    'node_modules', '.git', '.hg', '.svn', 'out', 'build', 'dist',
    '.vscode', '.idea', 'target', 'bin', 'obj', '__pycache__'
]);

const MAX_FILES = 5000;

export interface ResolvedUse {
    moduleUri: string;
    symbols: LshSymbol[];
    wildcard: boolean;
}

export class WorkspaceIndex {
    private docs = new Map<string, DocModel>();
    private moduleDocs = new Map<string, DocModel>();
    private byId = new Map<string, LshSymbol>();
    private byName = new Map<string, LshSymbol[]>();
    private workspaceFolders: string[] = [];
    private followImports = true;
    private scanning = false;

    setWorkspaceFolders(folders: string[]): void {
        this.workspaceFolders = folders;
    }

    setFollowImports(follow: boolean): void {
        this.followImports = follow;
    }

    getModel(uri: string): DocModel | undefined {
        return this.docs.get(uri) ?? this.moduleDocs.get(uri);
    }

    getDocSymbols(uri: string): LshSymbol[] {
        return this.docs.get(uri)?.symbols ?? this.moduleDocs.get(uri)?.symbols ?? [];
    }

    getSymbolById(id: string): LshSymbol | undefined {
        return this.byId.get(id);
    }

    getSymbolsByName(name: string): LshSymbol[] {
        return this.byName.get(name) ?? [];
    }

    getAllSymbols(): LshSymbol[] {
        return Array.from(this.byId.values());
    }

    getAllDocUris(): string[] {
        return Array.from(new Set([...this.docs.keys(), ...this.moduleDocs.keys()]));
    }

    upsert(uri: string, text: string, version: number): DocModel {
        const model = parseDocument(uri, text);
        model.version = version;
        const prev = this.docs.get(uri);
        if (prev) this.unregister(prev);
        this.docs.set(uri, model);
        this.register(model);
        return model;
    }

    remove(uri: string): void {
        const model = this.docs.get(uri);
        if (model) {
            this.unregister(model);
            this.docs.delete(uri);
        }
    }

    private register(model: DocModel): void {
        for (const sym of model.symbols) {
            this.byId.set(sym.id, sym);
            const arr = this.byName.get(sym.name) ?? [];
            arr.push(sym);
            this.byName.set(sym.name, arr);
        }
        for (const call of model.callSites) {
            if (!call.targetId) {
                const target = this.resolveCallSite(model.uri, { targetName: call.targetName, range: call.range });
                if (target) call.targetId = target.id;
            }
        }
    }

    private unregister(model: DocModel): void {
        for (const sym of model.symbols) {
            this.byId.delete(sym.id);
            const arr = this.byName.get(sym.name);
            if (arr) {
                const idx = arr.indexOf(sym);
                if (idx >= 0) arr.splice(idx, 1);
                if (arr.length === 0) this.byName.delete(sym.name);
            }
        }
    }

    async scanWorkspace(): Promise<void> {
        if (this.scanning) return;
        this.scanning = true;
        try {
            const files: string[] = [];
            for (const folder of this.workspaceFolders) {
                this.collectFiles(folder, files);
            }
            let count = 0;
            for (const file of files) {
                if (count >= MAX_FILES) break;
                try {
                    const text = fs.readFileSync(file, 'utf-8');
                    this.upsert(fsPathToUri(file), text, -1);
                    count++;
                } catch {
                    // ignore unreadable files
                }
            }
        } finally {
            this.scanning = false;
        }
    }

    private collectFiles(dir: string, out: string[]): void {
        let entries: fs.Dirent[];
        try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
        } catch {
            return;
        }
        for (const entry of entries) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) {
                if (!SKIP_DIRS.has(entry.name)) this.collectFiles(full, out);
            } else if (entry.isFile() && entry.name.endsWith('.lsh') && !isTempCheckFile(full)) {
                out.push(full);
            }
        }
    }

    onFileCreated(fsPath: string): void {
        if (!fsPath.endsWith('.lsh') || isTempCheckFile(fsPath)) return;
        try {
            const text = fs.readFileSync(fsPath, 'utf-8');
            this.upsert(fsPathToUri(fsPath), text, -1);
        } catch {
            // ignore
        }
    }

    onFileChanged(fsPath: string): void {
        if (!fsPath.endsWith('.lsh') || isTempCheckFile(fsPath)) return;
        const uri = fsPathToUri(fsPath);
        const model = this.docs.get(uri);
        if (!model) {
            this.onFileCreated(fsPath);
            return;
        }
        try {
            const text = fs.readFileSync(fsPath, 'utf-8');
            this.upsert(uri, text, model.version + 1);
        } catch {
            // ignore
        }
    }

    onFileDeleted(fsPath: string): void {
        if (!fsPath.endsWith('.lsh') || isTempCheckFile(fsPath)) return;
        this.remove(fsPathToUri(fsPath));
    }

    resolveUse(uri: string, use: UseStmt): ResolvedUse | null {
        const fsPath = uriToFsPath(uri);
        const project = readProjectInfo(fsPath);
        const moduleFile = resolveModuleFile(use.modulePath, fsPath, project.importsDirs);
        if (!moduleFile) return null;
        const moduleUri = fsPathToUri(moduleFile);
        let moduleModel = this.moduleDocs.get(moduleUri) ?? this.docs.get(moduleUri);
        if (!moduleModel && this.followImports) {
            try {
                const text = fs.readFileSync(moduleFile, 'utf-8');
                moduleModel = parseDocument(moduleUri, text);
                moduleModel.version = -1;
                this.moduleDocs.set(moduleUri, moduleModel);
            } catch {
                return null;
            }
        }
        if (!moduleModel) return null;
        if (use.items === null) {
            const symbols = use.isPriv
                ? moduleModel.symbols
                : moduleModel.symbols.filter(s => !s.visibility.includes('priv'));
            return { moduleUri, symbols, wildcard: true };
        }
        const wanted = new Set(use.items);
        const symbols = moduleModel.symbols.filter(s =>
            wanted.has(s.name) && (use.isPriv || !s.visibility.includes('priv'))
        );
        if (symbols.length === 0) return null;
        return { moduleUri, symbols, wildcard: false };
    }

    findImported(uri: string, name: string): LshSymbol[] {
        const model = this.getModel(uri);
        if (!model) return [];
        const results: LshSymbol[] = [];
        const seen = new Set<string>();
        for (const use of model.uses) {
            const resolved = this.resolveUse(uri, use);
            if (!resolved) continue;
            if (use.items !== null && !use.items.includes(name)) continue;
            for (const sym of resolved.symbols) {
                if (sym.name === name && !seen.has(sym.id)) {
                    seen.add(sym.id);
                    results.push(sym);
                }
            }
        }
        return results;
    }

    findImportedTypes(uri: string): LshSymbol[] {
        const model = this.getModel(uri);
        if (!model) return [];
        const results: LshSymbol[] = [];
        const seen = new Set<string>();
        for (const use of model.uses) {
            const resolved = this.resolveUse(uri, use);
            if (!resolved) continue;
            for (const sym of resolved.symbols) {
                if ((sym.kind === 'type' || sym.kind === 'errorType') && !seen.has(sym.id)) {
                    seen.add(sym.id);
                    results.push(sym);
                }
            }
        }
        return results;
    }

    getAllImported(uri: string): LshSymbol[] {
        const model = this.getModel(uri);
        if (!model) return [];
        const results: LshSymbol[] = [];
        const seen = new Set<string>();
        for (const use of model.uses) {
            const resolved = this.resolveUse(uri, use);
            if (!resolved) continue;
            if (use.items !== null) {
                const wanted = new Set(use.items);
                for (const sym of resolved.symbols) {
                    if (wanted.has(sym.name) && !seen.has(sym.id)) {
                        seen.add(sym.id);
                        results.push(sym);
                    }
                }
            } else {
                for (const sym of resolved.symbols) {
                    if (!seen.has(sym.id)) {
                        seen.add(sym.id);
                        results.push(sym);
                    }
                }
            }
        }
        return results;
    }

    getMembers(typeName: string, excludeUri?: string): LshSymbol[] {
        const base = normalizeTypeName(typeName);
        const results: LshSymbol[] = [];
        const seen = new Set<string>();
        for (const sym of this.byName.get(base) ?? []) {
            if (sym.kind !== 'type') continue;
            if (excludeUri && sym.uri === excludeUri) continue;
            for (const member of this.getDocSymbols(sym.uri)) {
                if ((member.kind === 'method' || member.kind === 'field') && member.ownerType === sym.name) {
                    if (!seen.has(member.id)) {
                        seen.add(member.id);
                        results.push(member);
                    }
                }
            }
        }
        return results;
    }

    findTypeSymbols(typeName: string): LshSymbol[] {
        const base = normalizeTypeName(typeName);
        return (this.byName.get(base) ?? []).filter(s => s.kind === 'type');
    }

    findEnclosingFunction(uri: string, position: Position): LshSymbol | null {
        const model = this.getModel(uri);
        if (!model) return null;
        let best: LshSymbol | null = null;
        for (const sym of model.symbols) {
            if (sym.kind !== 'function' && sym.kind !== 'method' && sym.kind !== 'opdef' && sym.kind !== 'macro') continue;
            if (positionWithin(sym.fullRange, position)) {
                if (!best || rangeSize(sym.fullRange) < rangeSize(best.fullRange)) {
                    best = sym;
                }
            }
        }
        return best;
    }

    getLocals(uri: string, ownerId: string): LocalSymbol[] {
        const model = this.getModel(uri);
        if (!model) return [];
        return model.locals.filter(l => l.ownerId === ownerId);
    }

    findLocalSymbol(uri: string, position: Position, name: string): { symbol: LshSymbol; local: LocalSymbol } | null {
        const fnc = this.findEnclosingFunction(uri, position);
        if (!fnc) return null;
        for (const p of fnc.params) {
            if (p.name === name) {
                return null;
            }
        }
        const locals = this.getLocals(uri, fnc.id);
        for (const l of locals) {
            if (l.name === name) {
                const sym: LshSymbol = {
                    id: `${fnc.id}|local|${name}`,
                    name,
                    kind: 'variable',
                    uri,
                    nameRange: l.range,
                    fullRange: l.range,
                    signature: `${name}${l.inferred ? ' := inferred' : ' : ' + (l.type || 'unknown')}`,
                    params: [],
                    returnType: l.type,
                    typeParams: [],
                    visibility: '',
                    docs: '',
                    line: l.range.start.line,
                    col: l.range.start.character,
                    endCol: l.range.start.character + name.length
                };
                return { symbol: sym, local: l };
            }
        }
        return null;
    }

    findParamSymbol(uri: string, position: Position, name: string): LshSymbol | null {
        const fnc = this.findEnclosingFunction(uri, position);
        if (!fnc) return null;
        for (const p of fnc.params) {
            if (p.name === name) {
                return {
                    id: `${fnc.id}|param|${name}`,
                    name,
                    kind: 'param',
                    uri,
                    nameRange: fnc.nameRange,
                    fullRange: fnc.fullRange,
                    signature: `${name} : ${p.type || 'unknown'}`,
                    params: [],
                    returnType: p.type,
                    typeParams: [],
                    visibility: '',
                    docs: '',
                    line: fnc.line,
                    col: fnc.col,
                    endCol: fnc.endCol
                };
            }
        }
        return null;
    }

    findSymbolInDoc(uri: string, name: string): LshSymbol | null {
        const model = this.getModel(uri);
        if (!model) return null;
        for (const sym of model.symbols) {
            if (sym.name === name && sym.kind !== 'field' && sym.kind !== 'method') return sym;
        }
        return null;
    }

    resolveExprType(uri: string, position: Position, exprStart: Position, name: string): string | null {
        const model = this.getModel(uri);
        if (!model) return null;
        const fnc = this.findEnclosingFunction(uri, position);
        if (fnc) {
            for (const p of fnc.params) {
                if (p.name === name) return p.type || null;
            }
            for (const l of this.getLocals(uri, fnc.id)) {
                if (l.name === name) return l.type || null;
            }
        }
        const sym = this.findSymbolInDoc(uri, name);
        if (sym && (sym.kind === 'global' || sym.kind === 'nativeVariable')) return sym.returnType || null;
        return null;
    }

    resolveCallSite(uri: string, callSite: { targetName: string; range: Range }): LshSymbol | null {
        const model = this.getModel(uri);
        if (!model) return null;
        const pos = callSite.range.start;
        const fnc = this.findEnclosingFunction(uri, pos);
        if (fnc && fnc.name === callSite.targetName) return fnc;
        if (fnc) {
            for (const p of fnc.params) {
                if (p.name === callSite.targetName) return null;
            }
        }
        for (const sym of model.symbols) {
            if (sym.name === callSite.targetName && (sym.kind === 'function' || sym.kind === 'method' || sym.kind === 'macro' || sym.kind === 'opdef' || sym.kind === 'nativeFunction' || sym.kind === 'errorType')) {
                return sym;
            }
        }
        const imported = this.findImported(uri, callSite.targetName);
        for (const sym of imported) {
            if (sym.kind === 'function' || sym.kind === 'method' || sym.kind === 'macro' || sym.kind === 'opdef' || sym.kind === 'nativeFunction' || sym.kind === 'errorType') {
                return sym;
            }
        }
        return null;
    }

    tokenStream(uri: string): { tokens: Token[]; pos: TextPositioner } | null {
        const model = this.getModel(uri);
        if (!model) return null;
        return { tokens: tokenize(model.text), pos: new TextPositioner(model.text) };
    }
}

function positionWithin(range: Range, pos: Position): boolean {
    if (pos.line < range.start.line || pos.line > range.end.line) return false;
    if (pos.line === range.start.line && pos.character < range.start.character) return false;
    if (pos.line === range.end.line && pos.character > range.end.character) return false;
    return true;
}

function rangeSize(range: Range): number {
    return (range.end.line - range.start.line) * 100000 + (range.end.character - range.start.character);
}