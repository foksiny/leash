import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { spawn, spawnSync } from 'child_process';
import { DiagnosticSeverity } from 'vscode-languageserver';
import { buildCheckArgs, DiagnosticsRunner } from '../server/diagnostics';
import { parseDocument } from '../server/parser';
import { BUILTIN_FUNCTIONS } from '../server/builtins';

const runner = new DiagnosticsRunner(() => undefined, () => ({} as never));
const extract = (out: string) =>
    (runner as unknown as { extractProblems(s: string): unknown[] }).extractProblems(out) as Array<{
        severity: number; message: string; code: string; file: string;
        line: number; col: number; nameHint?: string;
    }>;

function check(name: string, fn: () => void): void {
    fn();
    console.log(`  ok - ${name}`);
}

console.log('Leash LSP diagnostics tests');

check('compiler argv order', () => {
    assert.deepStrictEqual(
        buildCheckArgs(['-O2'], ['/libs'], true, '/tmp/t.lsh'),
        ['check', '/tmp/t.lsh', '-O2', '--other-imports', '/libs', '--verbose']
    );
});

check('located diagnostic with column', () => {
    const p = extract("error[E001]: boom\n  --> /src/a.lsh:12:5\n");
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].file, '/src/a.lsh');
    assert.strictEqual(p[0].line, 12);
    assert.strictEqual(p[0].col, 5);
    assert.strictEqual(p[0].code, 'E001');
});

check('arena diagnostic without column keeps why/help', () => {
    const p = extract([
        'error[E_ARENA_LIFETIME]: raw pointer stored into long-lived slot',
        '  --> /src/a.lsh:7',
        '   = why: The slot outlives the function.',
        '   = help: Store a value type instead.'
    ].join('\n') + '\n');
    assert.strictEqual(p.length, 1);
    assert.strictEqual(p[0].line, 7);
    assert.strictEqual(p[0].col, 1);
    assert.ok(p[0].message.includes('why: The slot outlives'));
    assert.ok(p[0].message.includes('help: Store a value type'));
});

check('bare typechecker error is kept with name hint', () => {
    const p = extract([
        "error: Variable 'x' declared as 'int' but assigned a value of type 'string'.",
        '  why: No local with this name is in scope.',
        "error: Undefined variable: 'y'"
    ].join('\n') + '\n');
    assert.strictEqual(p.length, 2);
    assert.strictEqual(p[0].file, '');
    assert.strictEqual(p[0].line, 0);
    assert.strictEqual(p[0].nameHint, 'x');
    assert.strictEqual(p[0].severity, DiagnosticSeverity.Error);
    assert.ok(p[0].message.includes('why:'));
    assert.strictEqual(p[1].nameHint, 'y');
});

check('real compiler check with import flags', () => {
    const root = path.resolve(__dirname, '../../..');
    const exe = path.join(root, 'bin', 'leashc');
    if (!fs.existsSync(exe)) return;
    const file = path.join(os.tmpdir(), `leash-lsp-test-${process.pid}.lsh`);
    fs.writeFileSync(file, 'fnc main() {\n    x: int = "bad";\n}\n');
    try {
        const args = buildCheckArgs([], [path.join(root, 'installthis')], true, file);
        const r = spawnSync(exe, args, { cwd: root, encoding: 'utf8' });
        const out = r.stdout + r.stderr;
        assert.ok(!out.includes("cannot open file '--"), out);
        assert.ok(out.includes("declared as 'int'"), out);
        const parsed = extract(out);
        assert.ok(parsed.length >= 1);
        assert.strictEqual(parsed[0].nameHint, 'x');
    } finally {
        fs.unlinkSync(file);
    }
});

check('real compiler check reports arena lifetime errors', () => {
    const root = path.resolve(__dirname, '../../..');
    const exe = path.join(root, 'bin', 'leashc');
    if (!fs.existsSync(exe)) return;
    const file = path.join(os.tmpdir(), `leash-lsp-arena-${process.pid}.lsh`);
    fs.writeFileSync(file, [
        'worker fnc w(p *int) : void { show(1); }',
        'fnc relay(p *int) : void { spawn w(p); }',
        'fnc main() { show(1); }'
    ].join('\n'));
    try {
        const r = spawnSync(exe, buildCheckArgs([], [], false, file), { cwd: root, encoding: 'utf8' });
        const out = r.stdout + r.stderr;
        assert.ok(out.includes('E_ARENA_LIFETIME'), out);
        const parsed = extract(out);
        assert.ok(parsed.some(p => p.code === 'E_ARENA_LIFETIME' && p.line === 2), JSON.stringify(parsed));
    } finally {
        fs.unlinkSync(file);
    }
});

check('parser accepts macro definitions and async modifier', () => {
    const model = parseDocument('file:///src/macros.lsh', [
        'pub def GRAY : macro() |> int { 1 };',
        'async fnc work() : void { show(1); }'
    ].join('\n'));
    assert.ok(model.symbols.some(s => s.name === 'GRAY' && s.kind === 'macro'),
        JSON.stringify(model.symbols.map(s => [s.name, s.kind])));
    const fn = model.symbols.find(s => s.name === 'work');
    assert.ok(fn, 'async function was not parsed');
    assert.ok(fn.signature.includes('async'), fn.signature);
});

check('new builtins are advertised', () => {
    assert.ok(BUILTIN_FUNCTIONS.some(b => b.startsWith('getint')));
    assert.ok(BUILTIN_FUNCTIONS.some(b => b.startsWith('getfloat')));
    assert.ok(BUILTIN_FUNCTIONS.some(b => b.startsWith('getstruct')));
});


check('LSP settings cannot be overridden by workspace config', () => {
    const pkg = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../package.json'), 'utf8'));
    const props = pkg.contributes.configuration.properties;
    assert.strictEqual(props['leash.lsp.executablePath'].scope, 'machine');
    assert.strictEqual(props['leash.lsp.compilerArgs'].scope, 'machine');
    assert.strictEqual(pkg.capabilities.untrustedWorkspaces.supported, false);
    const ext = fs.readFileSync(path.resolve(__dirname, '../../src/client/extension.ts'), 'utf8');
    assert.ok(ext.includes("inspect<string>('executablePath')"));
});

check('LSP diagnostics use a private exclusive temp file', () => {
    const d = fs.readFileSync(path.resolve(__dirname, '../../src/server/diagnostics.ts'), 'utf8');
    assert.ok(d.includes('mkdtempSync'));
    assert.ok(d.includes('O_EXCL'));
    assert.ok(d.includes('0o600'));
});

check('source-derived hover text is markdown-sanitized', () => {
    const util = fs.readFileSync(path.resolve(__dirname, '../../src/server/util.ts'), 'utf8');
    assert.ok(util.includes('export function markdownSafe'));
    const builtins = fs.readFileSync(path.resolve(__dirname, '../../src/server/builtins.ts'), 'utf8');
    assert.ok(builtins.includes('markdownSafe(sym.signature)'));
    assert.ok(builtins.includes('markdownSafe(sym.docs)'));
});

async function serverStartsWithoutFolderChangeSupport(): Promise<void> {
    const exe = process.execPath;
    const server = path.resolve(__dirname, '../server/server.js');
    if (!fs.existsSync(server)) return;
    const child = spawn(exe, [server, '--stdio'], { cwd: path.resolve(__dirname, '../..') });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', d => (stdout += d.toString()));
    child.stderr.on('data', d => (stderr += d.toString()));
    const frame = (o: unknown) => {
        const s = JSON.stringify(o);
        return `Content-Length: ${Buffer.byteLength(s)}\r\n\r\n${s}`;
    };
    // A client with no workspace capabilities at all: the server must not
    // crash while registering an unsupported folder-change handler.
    child.stdin.write(frame({
        jsonrpc: '2.0', id: 1, method: 'initialize',
        params: { processId: process.pid, rootUri: null, capabilities: {} }
    }));
    await new Promise(resolve => setTimeout(resolve, 600));
    try {
        assert.strictEqual(stderr.includes("Client doesn't support sending workspace folder change events"), false, stderr);
        assert.ok(stdout.includes('"id":1'), stdout);
        assert.ok(/"changeNotifications":false/.test(stdout), stdout);
        console.log('  ok - server starts without workspace-folder change support');
    } finally {
        child.stdin.end();
        child.kill();
    }
}

serverStartsWithoutFolderChangeSupport()
    .then(() => console.log('all diagnostics tests passed'))
    .catch(err => { console.error(err); process.exitCode = 1; });
