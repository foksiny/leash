import { Range } from 'vscode-languageserver';

export type SymKind =
    | 'function'
    | 'method'
    | 'type'
    | 'field'
    | 'global'
    | 'variable'
    | 'param'
    | 'enumMember'
    | 'macro'
    | 'nativeFunction'
    | 'nativeVariable'
    | 'opdef'
    | 'errorType';

export interface ParamInfo {
    name: string;
    type: string;
    variadic: boolean;
    hasDefault: boolean;
}

export interface LshSymbol {
    id: string;
    name: string;
    kind: SymKind;
    uri: string;
    nameRange: Range;
    fullRange: Range;
    signature: string;
    params: ParamInfo[];
    returnType: string;
    typeParams: string[];
    ownerType?: string;
    visibility: string;
    docs: string;
    line: number;
    col: number;
    endCol: number;
}

export interface LocalSymbol {
    name: string;
    type: string;
    inferred: boolean;
    range: Range;
    ownerId: string;
}

export interface UseStmt {
    modulePath: string[];
    items: string[] | null;
    alias: string | null;
    isPriv: boolean;
    range: Range;
    fullRange: Range;
}

export interface NativeImport {
    lib: string;
    range: Range;
}

export interface CallSite {
    targetName: string;
    targetId: string | null;
    ownerId: string;
    range: Range;
}

export interface DocModel {
    uri: string;
    text: string;
    symbols: LshSymbol[];
    locals: LocalSymbol[];
    uses: UseStmt[];
    natives: NativeImport[];
    callSites: CallSite[];
    version: number;
}

export interface LspSettings {
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

export const DEFAULT_SETTINGS: LspSettings = {
    enabled: true,
    executablePath: 'leash',
    compilerArgs: [],
    diagnostics: {
        enabled: true,
        mode: 'onType',
        debounceMs: 500,
        verbose: false
    },
    index: {
        workspace: true,
        followImports: true
    },
    semanticTokens: true,
    inlayHints: true,
    codeLens: true,
    snippets: true,
    formatting: {
        indentSize: 4,
        insertSpaces: true
    }
};