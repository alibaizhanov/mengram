import * as vscode from 'vscode';
import * as crypto from 'crypto';
import { MengramClientManager } from './MengramClientManager';

export class MengramViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'mengram.panel';

    private view?: vscode.WebviewView;

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly clientManager: MengramClientManager,
    ) {}

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ) {
        this.view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this.extensionUri, 'media'),
            ],
        };

        webviewView.webview.html = this.getHtml(webviewView.webview);

        const messageDisposable = webviewView.webview.onDidReceiveMessage(
            async (msg) => {
                if (!msg || typeof msg.type !== 'string') return;
                switch (msg.type) {
                    case 'search':
                        await this.handleSearch(msg.query);
                        break;
                    case 'save':
                        await this.handleSave(msg.text);
                        break;
                    case 'getStats':
                        await this.handleStats();
                        break;
                    case 'insertToEditor':
                        this.insertToEditor(msg.text);
                        break;
                }
            },
        );
        webviewView.onDidDispose(() => messageDisposable.dispose());
    }

    public async focusSearch(query?: string) {
        // Ensure sidebar is visible even if never opened
        await vscode.commands.executeCommand('workbench.view.extension.mengram-sidebar');
        if (this.view) {
            this.view.show?.(true);
            if (query) {
                this.view.webview.postMessage({ type: 'searchWithQuery', query });
            } else {
                this.view.webview.postMessage({ type: 'focusSearch' });
            }
        }
    }

    public sendSelectedText(text: string, fileName: string, language: string) {
        if (this.view) {
            this.view.show?.(true);
            this.view.webview.postMessage({
                type: 'selectedText',
                text,
                fileName: fileName.split(/[/\\]/).pop() || fileName,
                language,
            });
        }
    }

    public async saveText(text: string) {
        const client = await this.clientManager.getClient();
        if (!client) return;

        try {
            const userId = this.clientManager.getUserId();
            await client.addText(text, { userId });
            vscode.window.showInformationMessage('Saved to Mengram.');
        } catch (err: unknown) {
            const error = err instanceof Error ? err : new Error(String(err));
            vscode.window.showErrorMessage(`Mengram: ${error.message}`);
        }
    }

    public async requestStats() {
        if (this.view) {
            this.view.show?.(true);
        }
        await this.handleStats();
    }

    private async handleSearch(query: string) {
        const client = await this.clientManager.getClient();
        if (!client) return;

        const userId = this.clientManager.getUserId();

        try {
            const results = await client.searchAll(query, { limit: 10, userId });
            this.view?.webview.postMessage({
                type: 'searchResults',
                semantic: results.semantic || [],
                episodic: results.episodic || [],
                procedural: results.procedural || [],
            });
        } catch (err: unknown) {
            const error = err instanceof Error ? err : new Error(String(err));
            this.view?.webview.postMessage({
                type: 'error',
                message: error.message,
            });
        }
    }

    private async handleSave(text: string) {
        const client = await this.clientManager.getClient();
        if (!client) return;

        const userId = this.clientManager.getUserId();

        try {
            const result = await client.addText(text, { userId });
            this.view?.webview.postMessage({
                type: 'saveSuccess',
                jobId: result.job_id,
            });
        } catch (err: unknown) {
            const error = err instanceof Error ? err : new Error(String(err));
            this.view?.webview.postMessage({
                type: 'error',
                message: error.message,
            });
        }
    }

    private async handleStats() {
        const client = await this.clientManager.getClient();
        if (!client) return;

        const userId = this.clientManager.getUserId();

        try {
            const stats = await client.stats({ userId });
            this.view?.webview.postMessage({ type: 'stats', ...stats });

            if (!this.view) {
                vscode.window.showInformationMessage(
                    `Mengram: ${stats.entities} entities, ${stats.facts} facts, ${stats.knowledge} knowledge, ${stats.relations} relations`,
                );
            }
        } catch (err: unknown) {
            const error = err instanceof Error ? err : new Error(String(err));
            this.view?.webview.postMessage({
                type: 'error',
                message: error.message,
            });
        }
    }

    private insertToEditor(text: string) {
        const editor = vscode.window.activeTextEditor;
        if (!editor) return;

        void editor.edit((editBuilder) => {
            editBuilder.insert(editor.selection.active, text);
        });
    }

    private getHtml(webview: vscode.Webview): string {
        const resetUri = webview.asWebviewUri(
            vscode.Uri.joinPath(this.extensionUri, 'media', 'reset.css'),
        );
        const styleUri = webview.asWebviewUri(
            vscode.Uri.joinPath(this.extensionUri, 'media', 'main.css'),
        );
        const scriptUri = webview.asWebviewUri(
            vscode.Uri.joinPath(this.extensionUri, 'media', 'main.js'),
        );
        const nonce = crypto.randomBytes(16).toString('base64url');

        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
    <link href="${resetUri}" rel="stylesheet">
    <link href="${styleUri}" rel="stylesheet">
    <title>Mengram</title>
</head>
<body>
    <div class="container">
        <div class="search-section">
            <input type="text" id="searchInput" placeholder="Search your memories..." class="input"
                   aria-label="Search memories" />
        </div>

        <div class="tabs" role="tablist" aria-label="Memory types">
            <button class="tab active" data-tab="semantic" role="tab" aria-selected="true">Memories</button>
            <button class="tab" data-tab="episodic" role="tab" aria-selected="false">Episodes</button>
            <button class="tab" data-tab="procedural" role="tab" aria-selected="false">Procedures</button>
        </div>

        <div id="results" class="results" role="tabpanel"></div>

        <div class="save-section">
            <div id="preview" class="preview hidden">
                <div class="preview-header">
                    <span class="preview-label">Selected text</span>
                    <span id="previewFile" class="preview-file"></span>
                    <button id="previewClose" class="preview-close" aria-label="Close preview">&times;</button>
                </div>
                <pre id="previewText" class="preview-text"></pre>
            </div>
            <textarea id="saveInput" placeholder="Add a memory..." class="input save-input" rows="2"
                      aria-label="Memory text"></textarea>
            <button id="saveBtn" class="btn">Save to Mengram</button>
        </div>

        <div id="statsBar" class="stats-bar"></div>
    </div>

    <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
    }
}
