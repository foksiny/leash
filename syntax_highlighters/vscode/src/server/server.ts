import {
    createConnection,
    TextDocuments,
    ProposedFeatures,
    InitializeParams,
    DidChangeConfigurationNotification,
    TextDocumentSyncKind,
    InitializeResult,
    CallHierarchyPrepareRequest,
    TextDocumentPositionParams,
    CompletionParams,
    CompletionList,
    Hover,
    Location,
    LocationLink,
    Position,
    Range,
    DocumentSymbol,
    WorkspaceSymbol,
    FoldingRange,
    DocumentHighlight,
    SelectionRange,
    DocumentLink,
    SignatureHelp,
    InlayHint,
    CodeLens,
    CodeActionParams,
    CodeAction,
    RenameParams,
    WorkspaceEdit,
    SemanticTokens,
    CallHierarchyPrepareParams,
    CallHierarchyItem,
    CallHierarchyIncomingCall,
    CallHierarchyOutgoingCall,
    DidOpenTextDocumentParams,
    DidChangeTextDocumentParams,
    DidSaveTextDocumentParams,
    DidCloseTextDocumentParams,
    DidChangeWatchedFilesParams,
    FileChangeType,
    Diagnostic
} from 'vscode-languageserver/node';

import { TextDocument } from 'vscode-languageserver-textdocument';

import { WorkspaceIndex } from './index';
import { DiagnosticsRunner } from './diagnostics';
import { mergeSettings } from './config';
import { LspSettings, DEFAULT_SETTINGS } from './types';
import { uriToFsPath, fsPathToUri } from './util';

import { hoverHandler } from './features/hover';
import { completionHandler } from './features/completion';
import { definitionHandler, typeDefinitionHandler } from './features/definition';
import { computeReferences } from './features/references';
import { prepareRenameHandler, renameHandler } from './features/rename';
import { signatureHelpHandler } from './features/signature';
import { documentSymbolsHandler, workspaceSymbolsHandler } from './features/symbols';
import { foldingRangesHandler } from './features/folding';
import { documentHighlightHandler } from './features/highlight';
import { selectionRangeHandler } from './features/selection';
import { documentLinksHandler } from './features/links';
import { inlayHintsHandler } from './features/inlay';
import { codeLensHandler } from './features/codelens';
import { codeActionsHandler } from './features/codeactions';
import { semanticTokensHandler, SEMANTIC_TOKENS_LEGEND } from './features/semantic';
import { prepareCallHierarchy, incomingCalls, outgoingCalls } from './features/callhierarchy';
import { formatDocument } from './formatting';

const connection = createConnection(ProposedFeatures.all);
const documents: TextDocuments<TextDocument> = new TextDocuments(TextDocument);

let settings: LspSettings = DEFAULT_SETTINGS;
let hasConfigurationCapability = false;
let hasWorkspaceFolderCapability = false;

const index = new WorkspaceIndex();
const diagnostics = new DiagnosticsRunner((uri, diags) => {
    connection.sendDiagnostics({ uri, diagnostics: diags });
}, () => settings);

function getSettings(): LspSettings {
    return settings;
}

function updateSettings(next: unknown): void {
    settings = mergeSettings(next);
    index.setFollowImports(settings.index.followImports);
}

connection.onInitialize((params: InitializeParams) => {
    const capabilities = params.capabilities;
    hasConfigurationCapability = !!(capabilities.workspace && !!capabilities.workspace.configuration);
    hasWorkspaceFolderCapability = !!(capabilities.workspace && !!capabilities.workspace.workspaceFolders);

    const initOptions = (params.initializationOptions ?? {}) as { settings?: unknown };
    updateSettings(initOptions.settings);

    const result: InitializeResult = {
        capabilities: {
            textDocumentSync: TextDocumentSyncKind.Incremental,
            completionProvider: {
                resolveProvider: true,
                triggerCharacters: ['.', ':', '>']
            },
            hoverProvider: true,
            definitionProvider: true,
            typeDefinitionProvider: true,
            referencesProvider: true,
            renameProvider: { prepareProvider: true },
            documentSymbolProvider: true,
            workspaceSymbolProvider: true,
            documentFormattingProvider: true,
            documentRangeFormattingProvider: false,
            foldingRangeProvider: true,
            documentHighlightProvider: true,
            selectionRangeProvider: true,
            documentLinkProvider: { resolveProvider: false },
            signatureHelpProvider: { triggerCharacters: ['(', ','] },
            codeActionProvider: true,
            codeLensProvider: { resolveProvider: false },
            semanticTokensProvider: {
                legend: SEMANTIC_TOKENS_LEGEND,
                full: true,
                range: false
            },
            callHierarchyProvider: true,
            inlayHintProvider: true,
            workspace: {
                workspaceFolders: {
                    supported: true,
                    changeNotifications: true
                }
            }
        }
    };
    return result;
});

connection.onInitialized(async () => {
    if (hasConfigurationCapability) {
        connection.client.register(DidChangeConfigurationNotification.type, undefined);
        const config = await connection.workspace.getConfiguration('leash');
        updateSettings(config);
    }
    if (hasWorkspaceFolderCapability) {
        const folders = await connection.workspace.getWorkspaceFolders();
        if (folders) {
            index.setWorkspaceFolders(folders.map(f => uriToFsPath(f.uri)));
            if (settings.index.workspace) {
                await index.scanWorkspace();
            }
        }
    } else {
        index.setWorkspaceFolders([]);
    }
});

connection.onDidChangeConfiguration(change => {
    if (hasConfigurationCapability) {
        connection.workspace.getConfiguration('leash').then(updateSettings);
    } else {
        const raw = change.settings as { leash?: unknown };
        updateSettings(raw?.leash);
    }
});

connection.onDidChangeWatchedFiles((params: DidChangeWatchedFilesParams) => {
    for (const event of params.changes) {
        const fsPath = uriToFsPath(event.uri);
        if (event.type === FileChangeType.Created) {
            index.onFileCreated(fsPath);
        } else if (event.type === FileChangeType.Changed) {
            index.onFileChanged(fsPath);
        } else if (event.type === FileChangeType.Deleted) {
            index.onFileDeleted(fsPath);
            connection.sendDiagnostics({ uri: event.uri, diagnostics: [] });
        }
    }
});

// ---------------------------------------------------------------- documents
documents.onDidOpen((e) => {
    const doc = e.document;
    index.upsert(doc.uri, doc.getText(), doc.version);
    diagnostics.schedule(doc.uri, doc, 'onOpen');
});

documents.onDidChangeContent((e) => {
    const doc = e.document;
    index.upsert(doc.uri, doc.getText(), doc.version);
    diagnostics.schedule(doc.uri, doc, 'onType');
});

documents.onDidSave((e) => {
    const doc = e.document;
    index.upsert(doc.uri, doc.getText(), doc.version);
    diagnostics.schedule(doc.uri, doc, 'onSave');
});

documents.onDidClose((e) => {
    index.remove(e.document.uri);
    connection.sendDiagnostics({ uri: e.document.uri, diagnostics: [] });
});

// ---------------------------------------------------------------- hover
connection.onHover((params: TextDocumentPositionParams): Hover | null => {
    return hoverHandler(index, params.textDocument.uri, params.position);
});

// ---------------------------------------------------------------- definition
connection.onDefinition((params: TextDocumentPositionParams): LocationLink[] | null => {
    return definitionHandler(index, params.textDocument.uri, params.position);
});

connection.onTypeDefinition((params: TextDocumentPositionParams): Location | Location[] | null => {
    return typeDefinitionHandler(index, params.textDocument.uri, params.position);
});

// ---------------------------------------------------------------- references
connection.onReferences((params: TextDocumentPositionParams): Location[] | null => {
    const resolved = resolveWord(index, params.textDocument.uri, params.position);
    if (!resolved) return null;
    return computeReferences(index, resolved, { includeDeclaration: true });
});

function resolveWord(index: WorkspaceIndex, uri: string, position: Position) {
    const { resolveWordAt } = require('./features/resolve') as typeof import('./features/resolve');
    const r = resolveWordAt(index, uri, position);
    return r.symbol ?? null;
}

// ---------------------------------------------------------------- rename
connection.onPrepareRename((params: TextDocumentPositionParams): Range | null => {
    return prepareRenameHandler(index, params.textDocument.uri, params.position);
});

connection.onRenameRequest((params: RenameParams): WorkspaceEdit | null => {
    return renameHandler(index, params.textDocument.uri, params.position, params.newName);
});

// ---------------------------------------------------------------- completion
connection.onCompletion((params: CompletionParams): CompletionList => {
    return completionHandler(index, params.textDocument.uri, params.position, getSettings());
});

connection.onCompletionResolve(item => item);

// ---------------------------------------------------------------- signature
connection.onSignatureHelp((params: TextDocumentPositionParams): SignatureHelp | null => {
    return signatureHelpHandler(index, params.textDocument.uri, params.position);
});

// ---------------------------------------------------------------- symbols
connection.onDocumentSymbol((params: { textDocument: { uri: string } }): DocumentSymbol[] => {
    return documentSymbolsHandler(index, params.textDocument.uri);
});

connection.onWorkspaceSymbol((params: { query: string }): WorkspaceSymbol[] => {
    return workspaceSymbolsHandler(index, params.query);
});

// ---------------------------------------------------------------- formatting
connection.onDocumentFormatting(params => {
    const doc = documents.get(params.textDocument.uri);
    if (!doc) return null;
    const formatted = formatDocument(doc.getText(), getSettings());
    if (formatted === doc.getText()) return [];
    return [{
        range: {
            start: { line: 0, character: 0 },
            end: { line: Number.MAX_SAFE_INTEGER, character: Number.MAX_SAFE_INTEGER }
        },
        newText: formatted
    }];
});

// ---------------------------------------------------------------- folding
connection.onFoldingRanges((params: { textDocument: { uri: string } }): FoldingRange[] => {
    return foldingRangesHandler(index, params.textDocument.uri);
});

// ---------------------------------------------------------------- highlight
connection.onDocumentHighlight((params: TextDocumentPositionParams): DocumentHighlight[] | null => {
    return documentHighlightHandler(index, params.textDocument.uri, params.position);
});

// ---------------------------------------------------------------- selection
connection.onSelectionRanges((params: { textDocument: { uri: string }; positions: Position[] }): SelectionRange[] => {
    return selectionRangeHandler(index, params.textDocument.uri, params.positions);
});

// ---------------------------------------------------------------- links
connection.onDocumentLinks((params: { textDocument: { uri: string } }): DocumentLink[] => {
    return documentLinksHandler(index, params.textDocument.uri);
});

// ---------------------------------------------------------------- inlay hints
connection.languages.inlayHint.on((params) => {
    const doc = documents.get(params.textDocument.uri);
    if (!doc || !getSettings().inlayHints) return null;
    return inlayHintsHandler(index, params.textDocument.uri, params.range);
});

// ---------------------------------------------------------------- code lens
connection.onCodeLens((params) => {
    if (!getSettings().codeLens) return [];
    return codeLensHandler(index, params.textDocument.uri);
});

// ---------------------------------------------------------------- code actions
connection.onCodeAction((params: CodeActionParams): CodeAction[] => {
    const doc = documents.get(params.textDocument.uri);
    if (!doc) return [];
    return codeActionsHandler(params.textDocument.uri, params.range, params.context, doc.getText());
});

// ---------------------------------------------------------------- semantic tokens
connection.languages.semanticTokens.on((params) => {
    if (!getSettings().semanticTokens) {
        return { data: [] };
    }
    return semanticTokensHandler(index, params.textDocument.uri);
});

// ---------------------------------------------------------------- call hierarchy
connection.onRequest(CallHierarchyPrepareRequest.type, (params: CallHierarchyPrepareParams): CallHierarchyItem[] | null => {
    return prepareCallHierarchy(index, params);
});

connection.onRequest('textDocument/incomingCalls', (params): CallHierarchyIncomingCall[] | null => {
    return incomingCalls(index, params.item);
});

connection.onRequest('textDocument/outgoingCalls', (params): CallHierarchyOutgoingCall[] | null => {
    return outgoingCalls(index, params.item);
});

// ---------------------------------------------------------------- custom: check file
connection.onRequest('leash/checkFile', (params: { uri: string }) => {
    const doc = documents.get(params.uri);
    if (!doc) return null;
    index.upsert(doc.uri, doc.getText(), doc.version);
    diagnostics.run(doc.uri, doc);
    return null;
});

// ---------------------------------------------------------------- shutdown
connection.onShutdown(() => {
    diagnostics.cancelAll();
});

connection.onExit(() => {
    diagnostics.cancelAll();
});

documents.listen(connection);
connection.listen();