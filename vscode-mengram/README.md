# Mengram for VS Code

AI memory for developers. Search and save knowledge from VS Code.

## Features

- **Sidebar panel** — search memories, episodes, and procedures in a Cursor-like panel
- **Save selection** — right-click selected text to save to Mengram
- **Save file** — sync the current file to your memory
- **Quick add** — type and save a memory from the sidebar
- **Stats** — view your memory vault statistics

## Setup

1. Get a free API key at [mengram.io/dashboard](https://mengram.io/dashboard)
2. Install the extension from VS Code Marketplace
3. Open Settings → search "Mengram" → paste your API key
4. Open the Mengram sidebar (brain icon in activity bar)

## Commands

| Command | Description |
|---------|-------------|
| Mengram: Search memories | Focus search input in sidebar |
| Mengram: Save to Mengram | Save selected text to memory |
| Mengram: Save current file | Sync the active file to memory |
| Mengram: Show stats | Display memory statistics |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| mengram.apiKey | — | Your Mengram API key (om-...) |
| mengram.baseUrl | mengram.io | For self-hosted instances |
| mengram.userId | default | For multi-user isolation |

## Network usage

This extension sends data to the Mengram API (`mengram.io`) for AI-powered knowledge extraction and search. No data is sent without an API key configured. All communication uses HTTPS.

## Pricing

Mengram offers a free tier (40 adds/month, 200 searches/month). Paid plans start at $5/mo at [mengram.io/#pricing](https://mengram.io/#pricing).

## License

MIT
