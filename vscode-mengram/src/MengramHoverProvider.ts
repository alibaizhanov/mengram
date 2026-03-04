import * as vscode from 'vscode';
import { MengramClientManager } from './MengramClientManager';

interface SearchResult {
    entity: string;
    type: string;
    score: number;
    facts: string[];
}

interface HoverCacheEntry {
    results: SearchResult[];
    timestamp: number;
}

const CACHE_TTL_MS = 10 * 60 * 1000; // 10 minutes
const MAX_CACHE_SIZE = 200;

export class MengramHoverProvider implements vscode.HoverProvider, vscode.Disposable {
    private disposables: vscode.Disposable[] = [];
    private cache = new Map<string, HoverCacheEntry>();
    private pendingSearches = new Map<string, Promise<SearchResult[]>>();

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
            vscode.languages.registerHoverProvider(selector, this),
        );
    }

    async provideHover(
        document: vscode.TextDocument,
        position: vscode.Position,
        _token: vscode.CancellationToken,
    ): Promise<vscode.Hover | null> {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('codeLens.enabled', true)) return null;

        const wordRange = document.getWordRangeAtPosition(position);
        if (!wordRange) return null;

        const word = document.getText(wordRange);
        if (word.length < 4 || isCommonKeyword(word)) return null;

        const results = await this.getCachedOrSearch(word);
        if (results.length === 0) return null;

        const threshold = 0.4;
        const relevant = results.filter(r => (r.score || 0) >= threshold);
        if (relevant.length === 0) return null;

        const md = new vscode.MarkdownString();
        md.isTrusted = true;
        md.appendMarkdown('**Mengram Memories**\n\n');

        for (const r of relevant.slice(0, 3)) {
            md.appendMarkdown(`**${r.entity}** *(${r.type})*\n`);
            for (const fact of r.facts.slice(0, 2)) {
                md.appendMarkdown(`- ${fact}\n`);
            }
            if (r.facts.length > 2) {
                md.appendMarkdown(`- *...${r.facts.length - 2} more*\n`);
            }
            md.appendMarkdown('\n');
        }

        return new vscode.Hover(md, wordRange);
    }

    private async getCachedOrSearch(word: string): Promise<SearchResult[]> {
        const key = word.toLowerCase();

        const cached = this.cache.get(key);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL_MS) {
            return cached.results;
        }

        // De-duplicate in-flight searches
        if (this.pendingSearches.has(key)) {
            return this.pendingSearches.get(key)!;
        }

        const promise = this.doSearch(key);
        this.pendingSearches.set(key, promise);
        try {
            return await promise;
        } finally {
            this.pendingSearches.delete(key);
        }
    }

    private async doSearch(word: string): Promise<SearchResult[]> {
        const client = await this.clientManager.getClient();
        if (!client) return [];

        try {
            const userId = this.clientManager.getUserId();
            const results = await client.search(word, { limit: 5, userId });
            const typed = results as SearchResult[];

            // Evict old entries if cache too large
            if (this.cache.size > MAX_CACHE_SIZE) {
                const oldest = [...this.cache.entries()]
                    .sort((a, b) => a[1].timestamp - b[1].timestamp)
                    .slice(0, 50);
                for (const [k] of oldest) this.cache.delete(k);
            }

            this.cache.set(word, { results: typed, timestamp: Date.now() });
            return typed;
        } catch {
            return [];
        }
    }

    dispose(): void {
        this.disposables.forEach(d => d.dispose());
        this.cache.clear();
    }
}

const COMMON_KEYWORDS = new Set([
    'const', 'let', 'var', 'function', 'class', 'return', 'import',
    'export', 'from', 'async', 'await', 'new', 'this', 'true', 'false',
    'null', 'undefined', 'void', 'type', 'interface', 'enum',
    'if', 'else', 'for', 'while', 'switch', 'case', 'break', 'continue',
    'try', 'catch', 'throw', 'finally', 'string', 'number', 'boolean',
    'self', 'def', 'print', 'None', 'True', 'False', 'pass', 'with',
    'func', 'struct', 'impl', 'pub', 'mod', 'use', 'crate',
    'public', 'private', 'protected', 'static', 'abstract', 'extends',
    'implements', 'super', 'yield', 'delete', 'typeof', 'instanceof',
]);

function isCommonKeyword(word: string): boolean {
    return COMMON_KEYWORDS.has(word);
}
