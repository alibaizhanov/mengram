import * as vscode from 'vscode';
import { MengramClient } from 'mengram-ai';

export class MengramClientManager {
    private client: MengramClient | null = null;
    private clientApiKey = '';
    private clientBaseUrl = '';

    constructor(private readonly secrets: vscode.SecretStorage) {}

    async getClient(): Promise<MengramClient | null> {
        const config = vscode.workspace.getConfiguration('mengram');
        const baseUrl = config.get<string>('baseUrl', 'https://mengram.io');

        let apiKey = await this.secrets.get('mengram.apiKey');
        if (!apiKey) {
            apiKey = config.get<string>('apiKey', '');
        }

        if (!apiKey) {
            return null;
        }

        if (!this.client || this.clientApiKey !== apiKey || this.clientBaseUrl !== baseUrl) {
            this.client = new MengramClient(apiKey, { baseUrl });
            this.clientApiKey = apiKey;
            this.clientBaseUrl = baseUrl;
        }
        return this.client;
    }

    getUserId(): string {
        const config = vscode.workspace.getConfiguration('mengram');
        return config.get<string>('userId', 'default');
    }
}
