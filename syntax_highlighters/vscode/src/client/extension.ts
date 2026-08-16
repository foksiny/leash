import * as path from 'path';
import {
    workspace,
    ExtensionContext,
    commands,
    window,
    ConfigurationChangeEvent
} from 'vscode';
import {
    LanguageClient,
    LanguageClientOptions,
    ServerOptions,
    TransportKind,
    DidChangeConfigurationNotification
} from 'vscode-languageclient/node';

let client: LanguageClient | undefined;

interface LspSettings {
    enabled: boolean;
    executablePath: string;
    compilerArgs: string[];
    diagnostics: {
        enabled: boolean;
        mode: 'onType' | 'onSave' | 'onOpen';
        debounceMs: number;
        verbose: boolean;
    };
    index: {
        workspace: boolean;
        followImports: boolean;
    };
    semanticTokens: boolean;
    inlayHints: boolean;
    codeLens: boolean;
    snippets: boolean;
    formatting: {
        indentSize: number;
        insertSpaces: boolean;
    };
}

function readSettings(): LspSettings {
    const cfg = workspace.getConfiguration('leash.lsp');
    const formatting = workspace.getConfiguration('leash.lsp.formatting');
    const index = workspace.getConfiguration('leash.lsp.index');
    const diagnostics = workspace.getConfiguration('leash.lsp.diagnostics');
    return {
        enabled: cfg.get<boolean>('enabled', true),
        executablePath: cfg.get<string>('executablePath', 'leash'),
        compilerArgs: cfg.get<string[]>('compilerArgs', []),
        diagnostics: {
            enabled: diagnostics.get<boolean>('enabled', true),
            mode: diagnostics.get<'onType' | 'onSave' | 'onOpen'>('mode', 'onType'),
            debounceMs: diagnostics.get<number>('debounceMs', 500),
            verbose: diagnostics.get<boolean>('verbose', false)
        },
        index: {
            workspace: index.get<boolean>('workspace', true),
            followImports: index.get<boolean>('followImports', true)
        },
        semanticTokens: cfg.get<boolean>('semanticTokens', true),
        inlayHints: cfg.get<boolean>('inlayHints', true),
        codeLens: cfg.get<boolean>('codeLens', true),
        snippets: cfg.get<boolean>('snippets', true),
        formatting: {
            indentSize: formatting.get<number>('indentSize', 4),
            insertSpaces: formatting.get<boolean>('insertSpaces', true)
        }
    };
}

export function activate(context: ExtensionContext) {
    const serverModule = context.asAbsolutePath(
        path.join('out', 'server', 'server.js')
    );

    const serverOptions: ServerOptions = {
        run: { module: serverModule, transport: TransportKind.ipc },
        debug: {
            module: serverModule,
            transport: TransportKind.ipc,
            options: { execArgv: ['--inspect=6009'] }
        }
    };

    const clientOptions: LanguageClientOptions = {
        documentSelector: [
            { scheme: 'file', language: 'leash' },
            { scheme: 'untitled', language: 'leash' }
        ],
        synchronize: {
            configurationSection: 'leash',
            fileEvents: workspace.createFileSystemWatcher('**/*.lsh')
        },
        outputChannelName: 'Leash LSP',
        traceOutputChannel: window.createOutputChannel('Leash LSP Trace'),
        initializationOptions: { settings: readSettings() },
        middleware: {
            workspace: {
                configuration: (params, token, next) => {
                    if (!params.items) return next(params, token);
                    const result: unknown[] = [];
                    for (const item of params.items) {
                        if (item.section === 'leash') {
                            result.push(readSettings());
                        } else {
                            const scope = item.scopeUri ? { uri: item.scopeUri } : null;
                            result.push(workspace.getConfiguration(item.section, scope as any));
                        }
                    }
                    return result;
                }
            }
        }
    };

    client = new LanguageClient(
        'leashLSP',
        'Leash LSP',
        serverOptions,
        clientOptions
    );

    client.registerProposedFeatures?.();
    client.start();

    context.subscriptions.push(
        {
            dispose: () => {
                if (client) {
                    client.stop();
                }
            }
        },

        commands.registerCommand('leash.restartServer', async () => {
            if (!client) return;
            await client.stop();
            client.start();
            window.showInformationMessage('Leash language server restarted.');
        }),

        commands.registerCommand('leash.checkFile', async () => {
            const editor = window.activeTextEditor;
            if (!editor) return;
            await client?.sendNotification(
                DidChangeConfigurationNotification.type,
                { settings: { leash: readSettings() } }
            );
            await client?.sendRequest('leash/checkFile', {
                uri: editor.document.uri.toString()
            });
        })
    );

    const onConfigChanged = (e: ConfigurationChangeEvent) => {
        if (!e.affectsConfiguration('leash')) return;
        client?.sendNotification(
            DidChangeConfigurationNotification.type,
            { settings: { leash: readSettings() } }
        );
    };
    context.subscriptions.push(workspace.onDidChangeConfiguration(onConfigChanged));
}

export function deactivate(): Thenable<void> | undefined {
    if (!client) {
        return undefined;
    }
    return client.stop();
}