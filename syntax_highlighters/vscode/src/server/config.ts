import * as fs from 'fs';
import * as path from 'path';
import { LspSettings, DEFAULT_SETTINGS } from './types';

export interface ProjectInfo {
    root: string | null;
    importsDirs: string[];
}

export function mergeSettings(raw: unknown): LspSettings {
    const s = (raw ?? {}) as Record<string, unknown>;
    const d = (s.diagnostics ?? {}) as Record<string, unknown>;
    const idx = (s.index ?? {}) as Record<string, unknown>;
    const fmt = (s.formatting ?? {}) as Record<string, unknown>;
    return {
        enabled: typeof s.enabled === 'boolean' ? s.enabled : DEFAULT_SETTINGS.enabled,
        executablePath: typeof s.executablePath === 'string' ? s.executablePath : DEFAULT_SETTINGS.executablePath,
        compilerArgs: Array.isArray(s.compilerArgs)
            ? (s.compilerArgs as string[]).filter(x => typeof x === 'string')
            : DEFAULT_SETTINGS.compilerArgs,
        diagnostics: {
            enabled: typeof d.enabled === 'boolean' ? d.enabled : DEFAULT_SETTINGS.diagnostics.enabled,
            mode: d.mode === 'onType' || d.mode === 'onSave' || d.mode === 'onOpen'
                ? d.mode
                : DEFAULT_SETTINGS.diagnostics.mode,
            debounceMs: typeof d.debounceMs === 'number' ? d.debounceMs : DEFAULT_SETTINGS.diagnostics.debounceMs,
            verbose: typeof d.verbose === 'boolean' ? d.verbose : DEFAULT_SETTINGS.diagnostics.verbose
        },
        index: {
            workspace: typeof idx.workspace === 'boolean' ? idx.workspace : DEFAULT_SETTINGS.index.workspace,
            followImports: typeof idx.followImports === 'boolean' ? idx.followImports : DEFAULT_SETTINGS.index.followImports
        },
        semanticTokens: typeof s.semanticTokens === 'boolean' ? s.semanticTokens : DEFAULT_SETTINGS.semanticTokens,
        inlayHints: typeof s.inlayHints === 'boolean' ? s.inlayHints : DEFAULT_SETTINGS.inlayHints,
        codeLens: typeof s.codeLens === 'boolean' ? s.codeLens : DEFAULT_SETTINGS.codeLens,
        snippets: typeof s.snippets === 'boolean' ? s.snippets : DEFAULT_SETTINGS.snippets,
        formatting: {
            indentSize: typeof fmt.indentSize === 'number' && fmt.indentSize > 0
                ? fmt.indentSize
                : DEFAULT_SETTINGS.formatting.indentSize,
            insertSpaces: typeof fmt.insertSpaces === 'boolean' ? fmt.insertSpaces : DEFAULT_SETTINGS.formatting.insertSpaces
        }
    };
}

export function findProjectRoot(filePath: string): string | null {
    let dir = path.dirname(filePath);
    while (true) {
        if (fs.existsSync(path.join(dir, 'config.lshc'))) {
            return dir;
        }
        const uidePath = path.join(dir, '.uide', 'project.json');
        if (fs.existsSync(uidePath)) {
            try {
                const data = JSON.parse(fs.readFileSync(uidePath, 'utf-8'));
                if (data.type === 'Leash Project') {
                    return dir;
                }
            } catch {
                // ignore
            }
        }
        const parent = path.dirname(dir);
        if (parent === dir) break;
        dir = parent;
    }
    return null;
}

export function readProjectInfo(filePath: string): ProjectInfo {
    const root = findProjectRoot(filePath);
    if (!root) {
        return { root: null, importsDirs: [] };
    }
    const importsDirs: string[] = [];
    const configPath = path.join(root, 'config.lshc');
    if (fs.existsSync(configPath)) {
        try {
            const content = fs.readFileSync(configPath, 'utf-8');
            for (const line of content.split('\n')) {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('#')) continue;
                const commentIdx = trimmed.indexOf(' #');
                const clean = commentIdx >= 0 ? trimmed.slice(0, commentIdx).trim() : trimmed;
                const colonIdx = clean.indexOf(':');
                if (colonIdx === -1) continue;
                const key = clean.slice(0, colonIdx).trim();
                let val = clean.slice(colonIdx + 1).trim();
                if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
                if (key === 'imports') {
                    const abs = path.resolve(root, val);
                    if (fs.existsSync(abs)) importsDirs.push(abs);
                }
            }
        } catch {
            // ignore
        }
    }
    return { root, importsDirs };
}

export function globalLibsDir(): string {
    return path.join(process.env.HOME || process.env.USERPROFILE || '', '.leash', 'libs');
}

export function resolveModuleFile(modulePath: string[], fromFile: string, importsDirs: string[]): string | null {
    const last = modulePath[modulePath.length - 1];
    const flatName = last + '.lsh';
    const nestedRel = [...modulePath, last + '.lsh'].join(path.sep);

    const searchDirs = [path.dirname(fromFile), ...importsDirs];
    const candidates: string[] = [];
    for (const dir of searchDirs) {
        candidates.push(path.join(dir, flatName));
        candidates.push(path.join(dir, nestedRel));
    }
    const libs = globalLibsDir();
    candidates.push(path.join(libs, flatName));
    candidates.push(path.join(libs, nestedRel));

    for (const candidate of candidates) {
        if (fs.existsSync(candidate)) return candidate;
    }

    // Fallback mirroring the Leash compiler: recursively search ~/.leash/libs
    // for a file whose name matches the imported module. Libraries installed
    // via `leashed install` live in subdirectories (e.g. ~/.leash/libs/raylib/)
    // plus a top-level <lib>.lsh stub, so the flat/nested checks above are not
    // enough. If more than one file matches, the module is ambiguous (the
    // compiler treats that as an error), so return null.
    if (fs.existsSync(libs)) {
        const matches: string[] = [];
        const walk = (dir: string): void => {
            let entries: fs.Dirent[];
            try {
                entries = fs.readdirSync(dir, { withFileTypes: true });
            } catch {
                return;
            }
            for (const entry of entries) {
                const full = path.join(dir, entry.name);
                if (entry.isDirectory()) {
                    walk(full);
                } else if (entry.isFile() && entry.name === flatName) {
                    matches.push(full);
                }
            }
        };
        walk(libs);
        if (matches.length === 1) return matches[0];
    }
    return null;
}