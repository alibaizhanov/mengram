import * as vscode from 'vscode';
import { MengramClientManager } from './MengramClientManager';

export class ErrorMemory implements vscode.Disposable {
    private disposables: vscode.Disposable[] = [];
    private executionOutputs = new Map<vscode.TerminalShellExecution, string[]>();
    private searchCooldown = new Map<string, number>();

    constructor(private readonly clientManager: MengramClientManager) {}

    activate(): void {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('errorMemory.enabled', true)) return;

        // Must call read() on start — output before read() is lost
        this.disposables.push(
            vscode.window.onDidStartTerminalShellExecution(async (e) => {
                this.collectOutput(e.execution);
            }),
        );

        this.disposables.push(
            vscode.window.onDidEndTerminalShellExecution(async (e) => {
                if (e.exitCode !== undefined && e.exitCode !== 0) {
                    await this.handleFailedCommand(e.execution, e.exitCode);
                }
                this.executionOutputs.delete(e.execution);
            }),
        );
    }

    private async collectOutput(execution: vscode.TerminalShellExecution): Promise<void> {
        const chunks: string[] = [];
        this.executionOutputs.set(execution, chunks);
        try {
            for await (const data of execution.read()) {
                chunks.push(data);
                // Cap at 50KB to avoid memory issues
                if (chunks.join('').length > 50_000) break;
            }
        } catch {
            // Terminal may close before we finish reading
        }
    }

    private async handleFailedCommand(
        execution: vscode.TerminalShellExecution,
        exitCode: number,
    ): Promise<void> {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('errorMemory.enabled', true)) return;

        const command = execution.commandLine.value;
        const rawOutput = (this.executionOutputs.get(execution) || []).join('');
        const cleanOutput = stripAnsi(rawOutput);
        const errorMessage = extractErrorMessage(cleanOutput);

        if (!errorMessage || errorMessage.length < 10) return;

        // Cooldown: don't search for the same error within 30s
        const errorKey = errorMessage.substring(0, 100);
        const now = Date.now();
        const lastSearch = this.searchCooldown.get(errorKey) || 0;
        if (now - lastSearch < 30_000) return;
        this.searchCooldown.set(errorKey, now);

        const client = await this.clientManager.getClient();
        if (!client) return;

        try {
            const userId = this.clientManager.getUserId();
            const results = await client.search(errorMessage, { limit: 3, userId });
            const threshold = config.get<number>('errorMemory.scoreThreshold', 0.5);
            const relevant = results.filter((r: { score?: number }) => (r.score || 0) >= threshold);

            if (relevant.length > 0) {
                const top = relevant[0] as { entity: string; facts?: string[] };
                const topFact = top.facts?.[0] || 'No details';
                const action = await vscode.window.showInformationMessage(
                    `You've seen this before: ${top.entity} — ${topFact}`,
                    'View in Mengram',
                    'Dismiss',
                );
                if (action === 'View in Mengram') {
                    await vscode.commands.executeCommand('mengram.searchMemories', top.entity);
                }
            } else {
                const action = await vscode.window.showInformationMessage(
                    `Command failed (exit ${exitCode}). Save this error to Mengram?`,
                    'Save Fix',
                    'Dismiss',
                );
                if (action === 'Save Fix') {
                    const solution = await vscode.window.showInputBox({
                        prompt: 'What was the fix for this error?',
                        placeHolder: 'e.g., Missing dependency, run npm install ...',
                    });
                    if (solution) {
                        const text = `Error from command: ${command}\n\nError: ${errorMessage}\n\nFix: ${solution}`;
                        await client.addText(text, { userId });
                        vscode.window.showInformationMessage('Error and fix saved to Mengram.');
                    }
                }
            }
        } catch {
            // Fail silently
        }
    }

    dispose(): void {
        this.disposables.forEach(d => d.dispose());
        this.executionOutputs.clear();
        this.searchCooldown.clear();
    }
}

function stripAnsi(text: string): string {
    // eslint-disable-next-line no-control-regex
    return text.replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}

function extractErrorMessage(output: string): string {
    const lines = output.split('\n').map(l => l.trim()).filter(Boolean);

    const errorPatterns = [
        /^error\b/i, /^Error:/i, /^ERR!/i, /^fatal:/i,
        /^FAIL/i, /^Exception/i, /^Traceback/i,
        /^SyntaxError/i, /^TypeError/i, /^ReferenceError/i,
        /^ModuleNotFoundError/i, /^ImportError/i,
        /^npm ERR!/i, /^command not found/i,
    ];

    const errorLines: string[] = [];
    let capture = false;
    for (const line of lines) {
        if (errorPatterns.some(p => p.test(line))) capture = true;
        if (capture) errorLines.push(line);
    }

    if (errorLines.length > 0) {
        return errorLines.slice(0, 10).join('\n');
    }
    // Fallback: last 5 lines
    return lines.slice(-5).join('\n');
}
