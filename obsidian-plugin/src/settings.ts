import { App, PluginSettingTab, Setting } from 'obsidian';
import type MengramPlugin from './main';

export interface MengramSettings {
    apiKey: string;
    autoSync: boolean;
    syncFolders: string;
    excludedFolders: string;
    debounceMs: number;
    userId: string;
    baseUrl: string;
}

export const DEFAULT_SETTINGS: MengramSettings = {
    apiKey: '',
    autoSync: true,
    syncFolders: '',
    excludedFolders: '.trash',
    debounceMs: 2000,
    userId: 'default',
    baseUrl: 'https://mengram.io',
};

export class MengramSettingTab extends PluginSettingTab {
    plugin: MengramPlugin;

    constructor(app: App, plugin: MengramPlugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display(): void {
        const { containerEl } = this;
        containerEl.empty();

        new Setting(containerEl)
            .setName('API key')
            .setDesc('Your API key (starts with `om-`). Get one at mengram.io/dashboard.')
            .addText(text => {
                text.setPlaceholder('Enter API key');
                text.setValue(this.plugin.settings.apiKey);
                text.inputEl.type = 'password';
                text.onChange(async (value) => {
                    this.plugin.settings.apiKey = value.trim();
                    await this.plugin.saveSettings();
                    this.plugin.reinitClient();
                });
            });

        new Setting(containerEl)
            .setName('Auto-sync on save')
            .setDesc('Sync notes automatically when you save them.')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.autoSync)
                .onChange(async (value) => {
                    this.plugin.settings.autoSync = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Sync folders')
            .setDesc('Only sync notes in these folders (comma-separated). Leave empty to sync all.')
            .addText(text => text
                .setPlaceholder('Notes, projects, journal')
                .setValue(this.plugin.settings.syncFolders)
                .onChange(async (value) => {
                    this.plugin.settings.syncFolders = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Excluded folders')
            .setDesc('Never sync notes in these folders (comma-separated).')
            .addText(text => text
                .setPlaceholder('.trash,templates')
                .setValue(this.plugin.settings.excludedFolders)
                .onChange(async (value) => {
                    this.plugin.settings.excludedFolders = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Debounce delay (seconds)')
            .setDesc('Wait this many seconds after editing before syncing.')
            .addSlider(slider => slider
                .setLimits(1, 10, 1)
                .setValue(this.plugin.settings.debounceMs / 1000)
                .setDynamicTooltip()
                .onChange(async (value) => {
                    this.plugin.settings.debounceMs = value * 1000;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('User ID')
            .setDesc('Isolate memories per user (for multi-user setups).')
            .addText(text => text
                .setPlaceholder('Default')
                .setValue(this.plugin.settings.userId)
                .onChange(async (value) => {
                    this.plugin.settings.userId = value.trim() || 'default';
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Base URL')
            .setDesc('API base URL. Only change for self-hosted instances.')
            .addText(text => text
                .setPlaceholder('https://mengram.io')
                .setValue(this.plugin.settings.baseUrl)
                .onChange(async (value) => {
                    this.plugin.settings.baseUrl = value.trim() || 'https://mengram.io';
                    await this.plugin.saveSettings();
                    this.plugin.reinitClient();
                }));
    }
}
