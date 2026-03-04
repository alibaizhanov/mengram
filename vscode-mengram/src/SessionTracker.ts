import * as vscode from 'vscode';
import { MengramClientManager } from './MengramClientManager';

interface SessionEntry {
    uri: string;
    fileName: string;
    editCount: number;
    firstOpened: number;
    lastActive: number;
}

interface SessionData {
    files: SessionEntry[];
    startTime: number;
    endTime: number;
    workspaceName: string;
}

const SESSION_STATE_KEY = 'mengram.lastSession';
const BREAK_THRESHOLD_MS = 5 * 60 * 1000;    // 5 min to end session
const WELCOME_THRESHOLD_MS = 30 * 60 * 1000;  // 30 min to show welcome

export class SessionTracker implements vscode.Disposable {
    private disposables: vscode.Disposable[] = [];
    private currentSession = new Map<string, SessionEntry>();
    private sessionStartTime = Date.now();
    private lastActiveTime = Date.now();
    private unfocusedSince: number | null = null;

    constructor(
        private readonly clientManager: MengramClientManager,
        private readonly workspaceState: vscode.Memento,
    ) {}

    activate(): void {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('contextTracking.enabled', true)) return;

        // Check for returning user on activation
        this.checkWelcomeBack();

        // Track file switches
        this.disposables.push(
            vscode.window.onDidChangeActiveTextEditor((editor) => {
                if (editor) this.trackFile(editor.document);
                this.lastActiveTime = Date.now();
            }),
        );

        // Track saves
        this.disposables.push(
            vscode.workspace.onDidSaveTextDocument((doc) => {
                this.trackSave(doc);
                this.lastActiveTime = Date.now();
            }),
        );

        // Track window focus
        this.disposables.push(
            vscode.window.onDidChangeWindowState((state) => {
                if (!state.focused) {
                    this.unfocusedSince = Date.now();
                } else {
                    if (this.unfocusedSince) {
                        const away = Date.now() - this.unfocusedSince;
                        if (away >= BREAK_THRESHOLD_MS) {
                            this.endSession();
                        }
                        if (away >= WELCOME_THRESHOLD_MS) {
                            this.checkWelcomeBack();
                        }
                        this.unfocusedSince = null;
                    }
                    this.lastActiveTime = Date.now();
                }
            }),
        );

        // Track initial active editor
        if (vscode.window.activeTextEditor) {
            this.trackFile(vscode.window.activeTextEditor.document);
        }
    }

    private trackFile(doc: vscode.TextDocument): void {
        if (doc.uri.scheme !== 'file') return;
        const key = doc.uri.toString();
        const existing = this.currentSession.get(key);
        if (existing) {
            existing.lastActive = Date.now();
        } else {
            this.currentSession.set(key, {
                uri: key,
                fileName: doc.uri.fsPath.split(/[/\\]/).pop() || doc.uri.fsPath,
                editCount: 0,
                firstOpened: Date.now(),
                lastActive: Date.now(),
            });
        }
    }

    private trackSave(doc: vscode.TextDocument): void {
        if (doc.uri.scheme !== 'file') return;
        const key = doc.uri.toString();
        const existing = this.currentSession.get(key);
        if (existing) {
            existing.editCount++;
            existing.lastActive = Date.now();
        } else {
            this.trackFile(doc);
            const entry = this.currentSession.get(key);
            if (entry) entry.editCount = 1;
        }
    }

    private async endSession(): Promise<void> {
        if (this.currentSession.size === 0) return;

        const sessionData: SessionData = {
            files: Array.from(this.currentSession.values()),
            startTime: this.sessionStartTime,
            endTime: this.lastActiveTime,
            workspaceName: vscode.workspace.name || 'unknown',
        };

        await this.workspaceState.update(SESSION_STATE_KEY, sessionData);

        // Optionally save to Mengram
        const config = vscode.workspace.getConfiguration('mengram');
        if (config.get<boolean>('contextTracking.saveToMengram', false)) {
            const client = await this.clientManager.getClient();
            if (client) {
                try {
                    const userId = this.clientManager.getUserId();
                    const fileList = sessionData.files
                        .sort((a, b) => b.editCount - a.editCount)
                        .slice(0, 10)
                        .map(f => `${f.fileName} (${f.editCount} saves)`)
                        .join(', ');
                    const duration = Math.round(
                        (sessionData.endTime - sessionData.startTime) / 60_000,
                    );
                    const text = `Work session in ${sessionData.workspaceName} (${duration} min): edited ${fileList}`;
                    await client.addText(text, { userId });
                } catch {
                    // silent
                }
            }
        }

        // Reset for next session
        this.currentSession.clear();
        this.sessionStartTime = Date.now();
    }

    private async checkWelcomeBack(): Promise<void> {
        const config = vscode.workspace.getConfiguration('mengram');
        if (!config.get<boolean>('contextTracking.enabled', true)) return;

        const lastSession = this.workspaceState.get<SessionData>(SESSION_STATE_KEY);
        if (!lastSession) return;

        const gap = Date.now() - lastSession.endTime;
        if (gap < WELCOME_THRESHOLD_MS) return;

        const topFiles = lastSession.files
            .sort((a, b) => b.editCount - a.editCount)
            .slice(0, 3)
            .map(f => f.fileName)
            .join(', ');

        const duration = Math.round(
            (lastSession.endTime - lastSession.startTime) / 60_000,
        );

        const action = await vscode.window.showInformationMessage(
            `Welcome back! Last session (${duration} min): ${topFiles}`,
            'Open Last Files',
            'View Recent Memories',
            'Dismiss',
        );

        if (action === 'Open Last Files') {
            for (const file of lastSession.files
                .sort((a, b) => b.editCount - a.editCount)
                .slice(0, 3)) {
                try {
                    const uri = vscode.Uri.parse(file.uri);
                    await vscode.window.showTextDocument(uri, { preview: false });
                } catch {
                    // File may have been deleted
                }
            }
        } else if (action === 'View Recent Memories') {
            const client = await this.clientManager.getClient();
            if (client) {
                try {
                    const userId = this.clientManager.getUserId();
                    const episodes = await client.episodes({ limit: 3, userId });
                    if (episodes.length > 0) {
                        const ep = episodes[0] as { summary?: string };
                        vscode.window.showInformationMessage(
                            `Recent: ${ep.summary || 'No summary'}`,
                        );
                    }
                } catch {
                    // silent
                }
            }
        }

        // Clear so we don't show again
        await this.workspaceState.update(SESSION_STATE_KEY, undefined);
    }

    async onDeactivate(): Promise<void> {
        await this.endSession();
    }

    dispose(): void {
        this.disposables.forEach(d => d.dispose());
    }
}
