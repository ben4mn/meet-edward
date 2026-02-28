# Edward

A personal AI assistant with long-term memory, built with Next.js, FastAPI, and PostgreSQL. Edward remembers your conversations, schedules events, sends messages, executes code, and integrates with Apple services — all running locally on your Mac.

## Features

**Memory** — Automatically extracts and recalls facts, preferences, and context across conversations using hybrid vector + keyword search (pgvector).

**Scheduling** — Schedule reminders, messages, and tasks with one-time or recurring (cron) support. Edward processes events autonomously with full tool access.

**Messaging** — Send and receive via iMessage (AppleScript), SMS and WhatsApp (Twilio), or WhatsApp (MCP bridge). Incoming messages are triaged by urgency.

**Code Execution** — Sandboxed Python, JavaScript, SQL, and shell interpreters with per-conversation working directories.

**Apple Services** — Calendar, Reminders, Notes, Mail, Contacts, and Maps via apple-mcp (macOS only).

**Heartbeat** — Background monitoring of iMessage, Calendar, and Mail. Multi-layer triage classifies urgency and injects briefings into conversations.

**Documents** — Persistent document store with semantic search for recipes, notes, reference guides, and more.

**Web Search** — Search the web and extract page content via Brave Search API.

**Custom MCP Servers** — Discover, install, and use MCP servers at runtime. Edward can self-serve new integrations.

**Push Notifications** — Browser push notifications via Web Push / VAPID for reminders and urgent messages.

**File Storage** — Upload, manage, and reference persistent files with categories and tags.

**Persistent Databases** — Named PostgreSQL schemas that persist across conversations for structured data.

**iOS Widget** — Home screen widget via Scriptable with auto-generated or custom content.

**HTML Hosting** — Create and manage hosted HTML pages.

## Quick Start

### Prerequisites

- macOS (required for iMessage, Apple Services, and AppleScript integrations)
- [Homebrew](https://brew.sh)
- Python 3.11+
- Node.js 18+
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
# Clone the repo
git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward

# Run first-time setup (installs PostgreSQL, pgvector, dependencies)
./setup.sh

# Add your API key
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" >> .env

# Start Edward
./restart.sh
```

Open [http://localhost:3000](http://localhost:3000) and set a password on first visit.

## Architecture

```
Frontend (Next.js :3000)  →  Backend (FastAPI :8000)  →  PostgreSQL (:5432)
                                      ↓
                              LangGraph Agent
                           (preprocess → retrieve
                            memory → respond →
                            extract memory)
                                      ↓
                              Claude API (Anthropic)
```

The backend runs natively on macOS (not in Docker) to enable iMessage, scheduled events, and AppleScript integrations.

## Configuration

Copy `.env.example` to `.env` and configure:

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BRAVE_SEARCH_API_KEY` | No | Enables web search |
| `TWILIO_ACCOUNT_SID` / `AUTH_TOKEN` / `PHONE_NUMBER` | No | SMS & WhatsApp messaging |
| `MCP_APPLE_ENABLED` | No | Apple Services (Calendar, Mail, etc.) |
| `VAPID_PUBLIC_KEY` / `PRIVATE_KEY` | No | Browser push notifications |
| `JWT_SECRET_KEY` | No | Auth secret (auto-generates if unset) |
| `GITHUB_TOKEN` | No | MCP server search via GitHub API |
| `HTML_HOSTING_API_KEY` | No | HTML page hosting |

See `.env.example` for the full list with descriptions.

## Skills

Each skill can be toggled on/off from the settings page. Tools are dynamically bound to the LLM based on enabled skills.

| Skill | Description |
|-------|-------------|
| Code Interpreter | Sandboxed Python execution with numpy/pandas/matplotlib |
| JavaScript Interpreter | Node.js sandbox |
| SQL Database | Per-conversation SQLite + persistent PostgreSQL schemas |
| Shell/Bash | Sandboxed shell commands |
| Brave Search | Web search + page content extraction |
| Twilio SMS | Send/receive SMS via Twilio |
| Twilio WhatsApp | Send/receive WhatsApp via Twilio |
| WhatsApp MCP | Read/send WhatsApp via whatsapp-mcp bridge |
| iMessage | Send iMessages via AppleScript (macOS) |
| Apple Services | Calendar, Reminders, Notes, Mail, Contacts, Maps |
| HTML Hosting | Create/manage hosted HTML pages |
| Scheduled Events | Schedule reminders, messages, and tasks |

Memory, documents, file storage, databases, widgets, push notifications, and contacts are always available.

## Background Systems

These run automatically without user intervention:

| System | Description |
|--------|-------------|
| Heartbeat | Monitors iMessage, Calendar, and Mail; triages by urgency |
| Memory Reflection | Post-turn enrichment via related memory queries |
| Deep Retrieval | Pre-turn multi-query search for complex conversations |
| Memory Consolidation | Hourly clustering of related memories |
| Search Tags | Auto-generated keywords for conversation search |
| Push Notifications | Browser notifications for reminders and urgent items |

## Scripts

```bash
./setup.sh              # First-time setup (PostgreSQL, dependencies, .env)
./restart.sh             # Restart both frontend and backend
./restart.sh frontend    # Restart only frontend
./restart.sh backend     # Restart only backend
```

Logs: `/tmp/edward-backend.log` and `/tmp/edward-frontend.log`

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run the frontend build (`cd frontend && npm run build`)
5. Open a pull request

## License

[MIT](LICENSE)
