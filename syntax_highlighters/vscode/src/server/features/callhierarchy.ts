import {
    CallHierarchyItem,
    CallHierarchyIncomingCall,
    CallHierarchyOutgoingCall,
    CallHierarchyPrepareParams,
    Position
} from 'vscode-languageserver';
import { WorkspaceIndex } from '../index';
import { resolveWordAt } from './resolve';
import { LshSymbol } from '../types';

const CALLABLE_KINDS = new Set(['function', 'method', 'macro', 'opdef', 'errorType', 'nativeFunction']);

function toItem(index: WorkspaceIndex, sym: LshSymbol): CallHierarchyItem | null {
    if (sym.uri === '') return null;
    return {
        name: sym.name,
        kind: sym.kind === 'macro' ? 6 : sym.kind === 'opdef' ? 9 : 12,
        uri: sym.uri,
        range: sym.fullRange,
        selectionRange: sym.nameRange,
        detail: sym.signature
    };
}

export function prepareCallHierarchy(
    index: WorkspaceIndex,
    params: CallHierarchyPrepareParams
): CallHierarchyItem[] | null {
    const resolved = resolveWordAt(index, params.textDocument.uri, params.position);
    if (!resolved.symbol) return null;
    const item = toItem(index, resolved.symbol);
    return item ? [item] : null;
}

export function incomingCalls(
    index: WorkspaceIndex,
    item: CallHierarchyItem
): CallHierarchyIncomingCall[] | null {
    const target = findSymbolByRange(index, item);
    if (!target) return null;
    const results: CallHierarchyIncomingCall[] = [];
    const seen = new Set<string>();

    for (const uri of index.getAllDocUris()) {
        const model = index.getModel(uri);
        if (!model) continue;
        for (const call of model.callSites) {
            if (call.targetName !== target.name) continue;
            if (call.targetId === target.id) {
                const from = index.getSymbolById(call.ownerId);
                if (from && from.uri !== '') {
                    const key = `${from.id}|${call.range.start.line}`;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    const entry = results.find(r => r.from.uri === from.uri && r.from.range.start.line === from.fullRange.start.line);
                    if (entry) {
                        entry.fromRanges.push(call.range);
                    } else {
                        results.push({
                            from: toItem(index, from)!,
                            fromRanges: [call.range]
                        });
                    }
                }
            }
        }
    }
    return results.length > 0 ? results : null;
}

export function outgoingCalls(
    index: WorkspaceIndex,
    item: CallHierarchyItem
): CallHierarchyOutgoingCall[] | null {
    const target = findSymbolByRange(index, item);
    if (!target) return null;
    const results: CallHierarchyOutgoingCall[] = [];
    const seen = new Set<string>();

    const model = index.getModel(target.uri);
    if (!model) return null;
    for (const call of model.callSites) {
        if (call.ownerId !== target.id) continue;
        const callee = index.resolveCallSite(target.uri, { targetName: call.targetName, range: call.range });
        if (!callee || callee.uri === '') continue;
        const key = `${callee.id}|${call.range.start.line}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const entry = results.find(r => r.to.uri === callee.uri && r.to.range.start.line === callee.fullRange.start.line);
        if (entry) {
            entry.fromRanges.push(call.range);
        } else {
            const toItem2 = toItem(index, callee);
            if (toItem2) {
                results.push({
                    to: toItem2,
                    fromRanges: [call.range]
                });
            }
        }
    }
    return results.length > 0 ? results : null;
}

function findSymbolByRange(index: WorkspaceIndex, item: CallHierarchyItem): LshSymbol | null {
    for (const sym of index.getDocSymbols(item.uri)) {
        if (!CALLABLE_KINDS.has(sym.kind)) continue;
        if (sym.nameRange.start.line === item.selectionRange.start.line &&
            sym.nameRange.start.character === item.selectionRange.start.character) {
            return sym;
        }
    }
    return null;
}