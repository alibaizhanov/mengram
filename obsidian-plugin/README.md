# Mengram for Obsidian

Sync your Obsidian notes to [Mengram](https://mengram.io) AI memory. Your notes become searchable knowledge that any AI tool can access via MCP, SDK, or API.

## Features

- **Auto-sync on save** — edit a note, Mengram learns automatically
- **Search memories** — search across all your AI memories from Obsidian (Cmd/Ctrl+P → "Mengram: Search memories")
- **Sync current file** — manually sync the active note
- **Sync entire vault** — bulk-sync all notes at once
- **Folder filtering** — choose which folders to sync or exclude
- **Memory stats** — view your memory vault statistics

## Setup

1. Get a free API key at [mengram.io/dashboard](https://mengram.io/dashboard)
2. Install the plugin (see below)
3. Open Settings → Mengram → paste your API key
4. Start writing notes — they sync automatically

## Installation

### Manual

1. Download `main.js`, `manifest.json`, and `styles.css` from the [latest release](https://github.com/alibaizhanov/obsidian-mengram/releases)
2. Create folder: `<your-vault>/.obsidian/plugins/mengram/`
3. Copy the 3 files into that folder
4. Restart Obsidian → Settings → Community Plugins → Enable "Mengram"

## Commands

| Command | Description |
|---------|-------------|
| Search memories | Open search modal to find memories |
| Sync current file | Sync the active note to Mengram |
| Sync entire vault | Sync all notes in configured folders |
| Show memory stats | Display vault statistics |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| API key | — | Your Mengram API key (om-...) |
| Auto-sync | On | Sync notes automatically when saved |
| Sync folders | (all) | Only sync these folders (comma-separated) |
| Excluded folders | .trash | Never sync these folders |
| Debounce delay | 2s | Wait before syncing after edit |
| User ID | default | For multi-user isolation |
| Base URL | mengram.io | For self-hosted instances |

## How it works

1. You write/edit a note in Obsidian
2. After a short debounce (2s default), the plugin sends the note content to Mengram's API
3. Mengram extracts entities, facts, and relationships from your note
4. Your knowledge is now searchable via:
   - This plugin's search command
   - MCP Server (Claude, Cursor, Windsurf)
   - Python/JS SDK
   - REST API
   - Dashboard at mengram.io/dashboard

## Self-hosted

If you're running Mengram locally, change the base URL in settings to your instance (e.g., `http://localhost:8080`).

## Network usage

This plugin sends note content to the Mengram API (`mengram.io`) for AI-powered knowledge extraction and storage. No data is sent without an API key configured. All communication uses HTTPS.

## Pricing

Mengram offers a free tier (40 adds/month, 200 searches/month). Paid plans start at $5/mo at [mengram.io/#pricing](https://mengram.io/#pricing). An API key is required — sign up for free at [mengram.io/dashboard](https://mengram.io/dashboard).

## License

MIT
