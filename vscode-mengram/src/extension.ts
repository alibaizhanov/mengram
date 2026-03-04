import * as vscode from 'vscode';
import * as path from 'path';
import { MengramClientManager } from './MengramClientManager';
import { MengramViewProvider } from './MengramViewProvider';
import { ErrorMemory } from './ErrorMemory';
import { SessionTracker } from './SessionTracker';
import { MengramCodeLensProvider } from './MengramCodeLensProvider';
import { MengramHoverProvider } from './MengramHoverProvider';

let sessionTracker: SessionTracker | undefined;

export function activate(context: vscode.ExtensionContext) {
    // Shared client manager
    const clientManager = new MengramClientManager(context.secrets);

    // Sidebar webview
    const provider = new MengramViewProvider(context.extensionUri, clientManager);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            MengramViewProvider.viewType,
            provider,
        ),
    );

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('mengram.setApiKey', async () => {
            const key = await vscode.window.showInputBox({
                prompt: 'Enter your Mengram API key (om-...)',
                password: true,
                placeHolder: 'om-...',
            });
            if (key !== undefined) {
                await context.secrets.store('mengram.apiKey', key);
                vscode.window.showInformationMessage(
                    key ? 'Mengram API key saved.' : 'Mengram API key cleared.',
                );
            }
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mengram.searchMemories', (query?: string) => {
            provider.focusSearch(query);
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mengram.saveSelection', () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('No active editor.');
                return;
            }
            const text = editor.document.getText(editor.selection);
            if (!text) {
                vscode.window.showWarningMessage('No text selected.');
                return;
            }
            provider.sendSelectedText(
                text,
                editor.document.fileName,
                editor.document.languageId,
            );
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mengram.saveFile', () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('No active editor.');
                return;
            }
            const content = editor.document.getText();
            const name = path.basename(editor.document.fileName);
            provider.saveText(`File: ${name}\n\n${content}`);
        }),
    );

    context.subscriptions.push(
        vscode.commands.registerCommand('mengram.showStats', () => {
            provider.requestStats();
        }),
    );

    // Feature 1: Error Memory
    const errorMemory = new ErrorMemory(clientManager);
    errorMemory.activate();
    context.subscriptions.push(errorMemory);

    // Feature 2: Context Tracking
    sessionTracker = new SessionTracker(
        clientManager,
        context.workspaceState,
    );
    sessionTracker.activate();
    context.subscriptions.push(sessionTracker);

    // Feature 3: CodeLens + Hover
    const codeLensProvider = new MengramCodeLensProvider(clientManager);
    codeLensProvider.activate();
    context.subscriptions.push(codeLensProvider);

    const hoverProvider = new MengramHoverProvider(clientManager);
    hoverProvider.activate();
    context.subscriptions.push(hoverProvider);

    // Status bar
    const statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right,
        100,
    );
    statusBarItem.text = '$(database) Mengram';
    statusBarItem.tooltip = 'Open Mengram panel';
    statusBarItem.command = 'workbench.view.extension.mengram-sidebar';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
}

export async function deactivate() {
    if (sessionTracker) {
        await sessionTracker.onDeactivate();
    }
}
