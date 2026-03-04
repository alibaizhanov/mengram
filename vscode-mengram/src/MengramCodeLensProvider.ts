import * as vscode from 'vscode';
import { MengramClientManager } from './MengramClientManager';

interface SearchResult {
    entity: string;
    type: string;
    score: number;
    facts: string[];
}

interface CacheEntry {
    results: SearchResult[];
    timestamp: number;
}

const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

export class MengramCodeLensProvider implements vscode.CodeLensProvider, vscode.Disposable {
    private disposables: vscode.Disposable[] = [];
    private cache = new Map<string, CacheEntry>();
    private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    constructor(private readonly clientManager: MengramClientManager) {}

    activate(): void {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('codeLens.enabled', true)) return;

        const selector: vscode.DocumentSelector = [
            { scheme: 'file', language: 'typescript' },
            { scheme: 'file', language: 'javascript' },
            { scheme: 'file', language: 'python' },
            { scheme: 'file', language: 'go' },
            { scheme: 'file', language: 'rust' },
            { scheme: 'file', language: 'java' },
            { scheme: 'file', language: 'typescriptreact' },
            { scheme: 'file', language: 'javascriptreact' },
        ];

        this.disposables.push(
            vscode.languages.registerCodeLensProvider(selector, this),
        );

        this.disposables.push(
            vscode.commands.registerCommand('mengram.codeLensSearch', (query: string) => {
                vscode.commands.executeCommand('mengram.searchMemories');
            }),
        );

        this.disposables.push(
            vscode.workspace.onDidSaveTextDocument(() => {
                this._onDidChangeCodeLenses.fire();
            }),
        );
    }

    async provideCodeLenses(
        document: vscode.TextDocument,
        _token: vscode.CancellationToken,
    ): Promise<vscode.CodeLens[]> {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('codeLens.enabled', true)) return [];

        const fileName = document.uri.fsPath.split(/[/\\]/).pop() || '';
        const results = await this.getCachedResults(fileName);
        if (results.length === 0) return [];

        let symbols: vscode.DocumentSymbol[] | undefined;
        try {
            symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
                'vscode.executeDocumentSymbolProvider',
                document.uri,
            );
        } catch {
            return [];
        }
        if (!symbols || symbols.length === 0) return [];

        const lenses: vscode.CodeLens[] = [];
        const topLevelSymbols = flattenSymbols(symbols).filter(
            s => s.kind === vscode.SymbolKind.Function
              || s.kind === vscode.SymbolKind.Method
              || s.kind === vscode.SymbolKind.Class,
        );

        for (const sym of topLevelSymbols) {
            const matching = results.filter(r =>
                r.facts.some(f => f.toLowerCase().includes(sym.name.toLowerCase()))
                || r.entity.toLowerCase().includes(sym.name.toLowerCase()),
            );

            if (matching.length > 0) {
                lenses.push(new vscode.CodeLens(sym.range, {
                    title: `$(brain) ${matching.length} memor${matching.length === 1 ? 'y' : 'ies'}`,
                    command: 'mengram.codeLensSearch',
                    arguments: [sym.name],
                }));
            }
        }

        return lenses;
    }

    private async getCachedResults(fileName: string): Promise<SearchResult[]> {
        const cached = this.cache.get(fileName);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
            return cached.results;
        }

        const client = await this.clientManager.getClient();
        if (!client) return [];

        try {
            const userId = this.clientManager.getUserId();
            const results = await client.search(fileName, { limit: 10, userId });
            const typed = results as SearchResult[];
            this.cache.set(fileName, { results: typed, timestamp: Date.now() });
            return typed;
        } catch {
            return [];
        }
    }

    dispose(): void {
        this.disposables.forEach(d => d.dispose());
        this._onDidChangeCodeLenses.dispose();
        this.cache.clear();
    }
}

function flattenSymbols(symbols: vscode.DocumentSymbol[]): vscode.DocumentSymbol[] {
    const result: vscode.DocumentSymbol[] = [];
    for (const sym of symbols) {
        result.push(sym);
        if (sym.children.length > 0) {
            result.push(...flattenSymbols(sym.children));
        }
    }
    return result;
}
