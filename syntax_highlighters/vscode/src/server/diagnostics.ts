import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { Diagnostic, DiagnosticSeverity, Range } from 'vscode-languageserver';
import { TextDocument } from 'vscode-languageserver-textdocument';
import { URI } from 'vscode-uri';
import { readProjectInfo } from './config';
import { TextPositioner, isIdentifierChar, isTempCheckFile } from './util';
import { LspSettings } from './types';

interface RunningCheck {
    proc: ChildProcess;
    timer: NodeJS.Timeout;
}

interface ParsedProblem {
    severity: DiagnosticSeverity;
    message: string;
    code: string;
    file: string;
    line: number;
    col: number;
    tip?: string;
    nameHint?: string;
}

/** Compiler CLI: argv[1] is the subcommand, argv[2] the input file, and
 *  flags are consumed from argv[3] onward. Keep this order testable. */
export function buildCheckArgs(
    compilerArgs: string[], importsDirs: string[], verbose: boolean, tempFile: string
): string[] {
    const args: string[] = ['check', tempFile, ...compilerArgs];
    for (const dir of importsDirs) args.push('--other-imports', dir);
    if (verbose) args.push('--verbose');
    return args;
}

export class DiagnosticsRunner {
    private checks = new Map<string, RunningCheck>();

    constructor(
        private readonly onResult: (uri: string, diagnostics: Diagnostic[]) => void,
        private readonly getSettings: () => LspSettings
    ) {}

    schedule(uri: string, doc: TextDocument, mode: 'onType' | 'onSave' | 'onOpen'): void {
        const settings = this.getSettings();
        if (!settings.diagnostics.enabled || !settings.enabled) return;
        const cfg = settings.diagnostics.mode;
        if (mode === 'onType' && cfg !== 'onType') return;
        if (mode === 'onSave' && cfg !== 'onSave') return;
        if (mode === 'onOpen' && cfg === 'onSave') return;
        const existing = this.checks.get(uri);
        if (existing) {
            clearTimeout(existing.timer);
            if (mode !== 'onType') {
                try {
                    existing.proc.kill('SIGKILL');
                } catch {
                    // ignore
                }
                this.checks.delete(uri);
            }
        }
        if (mode === 'onType') {
            const timer = setTimeout(() => this.run(uri, doc), settings.diagnostics.debounceMs);
            this.checks.set(uri, { proc: null as unknown as ChildProcess, timer });
            return;
        }
        this.run(uri, doc);
    }

    run(uri: string, doc: TextDocument): void {
        const settings = this.getSettings();
        const text = doc.getText();
        const fsPath = URI.parse(uri).fsPath;
        if (fsPath && isTempCheckFile(fsPath)) return;

        let tempFile: string;
        let baseDir: string;
        if (fsPath && fs.existsSync(fsPath)) {
            baseDir = path.dirname(fsPath);
            tempFile = path.join(baseDir, `.leash-lsp-${path.basename(fsPath)}`);
        } else {
            baseDir = path.join(os.tmpdir(), 'leash-lsp');
            if (!fs.existsSync(baseDir)) fs.mkdirSync(baseDir, { recursive: true });
            tempFile = path.join(baseDir, path.basename(fsPath || 'untitled.lsh'));
        }
        try {
            fs.writeFileSync(tempFile, text);
        } catch {
            return;
        }

        const project = readProjectInfo(fsPath);
        const args = buildCheckArgs(
            settings.compilerArgs, project.importsDirs,
            settings.diagnostics.verbose, tempFile
        );

        const existing = this.checks.get(uri);
        if (existing && existing.proc && existing.proc.pid) {
            try {
                existing.proc.kill('SIGKILL');
            } catch {
                // ignore
            }
        }

        const proc = spawn(settings.executablePath, args, {
            cwd: project.root ?? baseDir,
            windowsHide: true
        });

        const entry: RunningCheck = {
            proc,
            timer: setTimeout(() => {
                try {
                    proc.kill('SIGKILL');
                } catch {
                    // ignore
                }
            }, 15000)
        };

        this.checks.set(uri, entry);

        let stdout = '';
        let stderr = '';
        proc.stdout.on('data', d => (stdout += d.toString()));
        proc.stderr.on('data', d => (stderr += d.toString()));

        const finish = () => {
            if (this.checks.get(uri) !== entry) return;
            clearTimeout(entry.timer);
            this.checks.delete(uri);
            try {
                fs.unlinkSync(tempFile);
            } catch {
                // ignore
            }
            const diagnostics = this.parseOutput(stdout + stderr, text, tempFile, fsPath);
            this.onResult(uri, diagnostics);
        };

        proc.on('error', () => {
            if (this.checks.get(uri) !== entry) return;
            clearTimeout(entry.timer);
            this.checks.delete(uri);
        });
        proc.on('close', finish);
    }

    cancelAll(): void {
        for (const [, entry] of this.checks) {
            clearTimeout(entry.timer);
            try {
                entry.proc.kill('SIGKILL');
            } catch {
                // ignore
            }
        }
        this.checks.clear();
    }

    private parseOutput(output: string, sourceText: string, tempFile: string, realFile: string): Diagnostic[] {
        const diagnostics: Diagnostic[] = [];
        const problems = this.extractProblems(output);
        const positioner = new TextPositioner(sourceText);

        for (const problem of problems) {
            let uri: string;
            if (!problem.file || path.resolve(problem.file) === path.resolve(tempFile)) {
                uri = URI.file(realFile).toString();
            } else {
                const resolved = path.isAbsolute(problem.file)
                    ? problem.file
                    : path.resolve(path.dirname(tempFile), problem.file);
                uri = URI.file(resolved).toString();
            }

            let line = Math.max(0, problem.line - 1);
            let col = Math.max(0, problem.col - 1);
            const isCurrentFile = !problem.file || path.resolve(problem.file) === path.resolve(tempFile);
            // Bare typechecker errors carry no location: place them on the
            // first occurrence of the identifier the message mentions.
            if (isCurrentFile && problem.line === 0 && problem.nameHint) {
                const found = positioner.findToken(problem.nameHint);
                if (found) { line = found.line; col = found.character; }
            }
            let range: Range;
            if (isCurrentFile) {
                const lineText = positioner.lineText(line);
                let start = col;
                let end = col;
                if (lineText[col] !== undefined && isIdentifierChar(lineText[col])) {
                    while (start > 0 && isIdentifierChar(lineText[start - 1])) start--;
                    while (end < lineText.length && isIdentifierChar(lineText[end])) end++;
                } else if (lineText[col] !== undefined) {
                    end = col + 1;
                }
                range = {
                    start: { line, character: start },
                    end: { line, character: end }
                };
            } else {
                range = {
                    start: { line, character: col },
                    end: { line, character: col + 1 }
                };
            }

            let message = problem.message;
            if (problem.tip) {
                message += `\n\nTip: ${problem.tip}`;
            }

            diagnostics.push({
                severity: problem.severity,
                range,
                message,
                code: problem.code || undefined,
                source: 'leash'
            });
        }
        return diagnostics;
    }

    private extractProblems(output: string): ParsedProblem[] {
        const problems: ParsedProblem[] = [];
        const lines = output.split('\n');
        let i = 0;
        const n = lines.length;
        while (i < n) {
            const line = lines[i];
            const header = /^(error|warning)(?:\s*\[([A-Za-z0-9_-]+)\])?\s*:\s*(.*)$/.exec(line);
            if (!header) {
                i++;
                continue;
            }
            const severity = header[1] === 'error' ? DiagnosticSeverity.Error : DiagnosticSeverity.Warning;
            const code = header[2];
            const headline = header[3].trim();
            i++;
            let file = '';
            let lineNo = 0;
            let colNo = 0;
            const details: string[] = [];
            const isHeader = (l: string) => /^(error|warning)(?:\s*\[[A-Za-z0-9_-]+\])?\s*:/.test(l);
            const isStopLine = (l: string) =>
                isHeader(l) || /^\s*-->\s*.*:\d+/.test(l) ||
                /^Checking '.*'\.\.\.$/.test(l) || /^Summary:/.test(l);
            while (i < n && !isHeader(lines[i])) {
                const l = lines[i];
                // Arena diagnostics use `file:line` (no column); parser and
                // security diagnostics use `file:line:col`.
                const loc = /^\s*-->\s*(.*?):(\d+)(?::(\d+))?/.exec(l);
                if (loc) {
                    file = loc[1].trim();
                    lineNo = parseInt(loc[2], 10);
                    colNo = loc[3] ? parseInt(loc[3], 10) : 1;
                    i++;
                    continue;
                }
                if (isStopLine(l)) break;
                const teaching = /^\s*=?\s*(why|help|example|trace|tip|note):\s*(.*)$/.exec(l);
                if (teaching) {
                    details.push(`${teaching[1]}: ${teaching[2]}`);
                }
                i++;
            }
            const message = details.length ? `${headline}\n\n${details.join('\n\n')}` : headline;
            // Typechecker errors are bare `error: ...` lines with no source
            // location. Keep them as a whole-file problem; parseOutput will
            // try to place them on a quoted identifier from the message.
            problems.push({
                severity,
                message,
                code: code ?? '',
                file,
                line: lineNo,
                col: colNo,
                nameHint: /'([^']+)'|"([^"]+)"/.exec(headline)?.slice(1).find(Boolean)
            });
        }
        return problems;
    }
}