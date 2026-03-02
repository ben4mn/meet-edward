<p align="center">
  <img src="site/public/favicon.svg" width="100" height="100" alt="Edward" />
</p>

<h1 align="center">Edward</h1>

<p align="center">
  <strong>Your AI assistant that remembers everything.</strong>
</p>

<p align="center">
  Long-term memory &nbsp;·&nbsp; Scheduled actions &nbsp;·&nbsp; Multi-channel messaging &nbsp;·&nbsp; Code execution
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS-blue.svg" alt="macOS" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/Next.js-15-black.svg" alt="Next.js" />
</p>

<p align="center">
  <a href="https://meet-edward.com">Website</a> &nbsp;·&nbsp;
  <a href="https://meet-edward.com/docs">Docs</a> &nbsp;·&nbsp;
  <a href="#quick-start">Quick Start</a>
</p>

---

## What is Edward?

Edward is a full-stack AI assistant built on **Next.js**, **FastAPI**, **LangGraph**, and **PostgreSQL with pgvector**. He extracts memories from every conversation, schedules his own reminders, sends messages across iMessage / SMS / WhatsApp, and runs code — all locally on your Mac.

Unlike chat wrappers, Edward has persistent memory, background autonomy (heartbeat monitoring, scheduled events, memory consolidation), and a growing set of self-serve integrations via MCP.

## Features

| | | | |
|:--|:--|:--|:--|
| 🧠 **Long-Term Memory** | Hybrid vector + keyword recall | 📅 **Scheduled Events** | One-time & cron recurring |
| 💬 **Multi-Channel Messaging** | iMessage, SMS, WhatsApp | 🐍 **Code Execution** | Python, JS, SQL, Shell sandboxes |
| 🍎 **Apple Services** | Calendar, Mail, Reminders, Notes | 🔍 **Web Search** | Brave Search + page extraction |
| 📄 **Document Store** | Semantic search over saved docs | 🔌 **Custom MCP Servers** | Self-serve install at runtime |
| 🔔 **Push Notifications** | Web Push via VAPID | 💾 **File Storage** | Persistent files with tags |
| 🗄️ **Persistent Databases** | Named PostgreSQL schemas | 📱 **iOS Widget** | Scriptable home screen widget |

## Quick Start

```bash
git clone https://github.com/ben4mn/meet-edward.git && cd meet-edward
./setup.sh            # Installs PostgreSQL, pgvector, Python & Node deps
./restart.sh           # Starts backend (:8000) + frontend (:3000)
```

Add your Anthropic API key to `.env` — that's the only required variable. Open [localhost:3000](http://localhost:3000) and set a password on first visit.

> **Prerequisites:** macOS, [Homebrew](https://brew.sh), Python 3.11+, Node.js 18+, [Anthropic API key](https://console.anthropic.com/)

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

The backend runs natively on macOS (not in Docker) to support iMessage, AppleScript, and the scheduled event scheduler.

<details>
<summary><strong>Background Systems</strong></summary>

| System | Description |
|--------|-------------|
| Heartbeat | Monitors iMessage, Calendar, and Mail; triages by urgency |
| Memory Reflection | Post-turn enrichment via related memory queries |
| Deep Retrieval | Pre-turn multi-query search for complex conversations |
| Memory Consolidation | Hourly clustering of related memories |
| Search Tags | Auto-generated keywords for conversation search |

</details>

<details>
<summary><strong>Configuration</strong></summary>

Copy [`.env.example`](.env.example) to `.env` and configure:

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

See [`.env.example`](.env.example) for the full list.

</details>

<details>
<summary><strong>Skills</strong></summary>

Each skill is toggled from the settings page. Tools are dynamically bound to the LLM based on what's enabled.

| Skill | Description |
|-------|-------------|
| Code Interpreter | Sandboxed Python with numpy/pandas/matplotlib |
| JavaScript Interpreter | Node.js sandbox |
| SQL Database | Per-conversation SQLite + persistent PostgreSQL schemas |
| Shell/Bash | Sandboxed shell commands |
| Brave Search | Web search + page extraction |
| Twilio SMS | Send/receive SMS |
| Twilio WhatsApp | Send/receive WhatsApp |
| WhatsApp MCP | WhatsApp via whatsapp-mcp bridge |
| iMessage | Send iMessages via AppleScript |
| Apple Services | Calendar, Reminders, Notes, Mail, Contacts, Maps |
| HTML Hosting | Create/manage hosted pages |
| Scheduled Events | Reminders, messages, and recurring tasks |

Memory, documents, file storage, databases, widgets, push notifications, and contacts are always available.

</details>

## Scripts

```bash
./setup.sh              # First-time setup (PostgreSQL, dependencies, .env)
./restart.sh             # Restart both frontend and backend
./restart.sh frontend    # Restart only frontend
./restart.sh backend     # Restart only backend
```

Logs: `/tmp/edward-backend.log` and `/tmp/edward-frontend.log`

## Contributing

Contributions welcome — open an issue first to discuss what you'd like to change. Fork, branch, and open a PR. See the [docs site](https://meet-edward.com/docs) for architecture details.

## License

[MIT](LICENSE)
