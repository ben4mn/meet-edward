# Contributing to Edward

Thanks for your interest in contributing! This guide will help you get set up.

## Prerequisites

- **macOS** (recommended) or Linux/WSL
- **Python 3.11+**
- **Node.js 18+**
- **PostgreSQL** with pgvector extension
- **Anthropic API key** ([console.anthropic.com](https://console.anthropic.com/))

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward

# Run first-time setup (installs PostgreSQL, pgvector, Python & Node deps)
./setup.sh

# Add your Anthropic API key to .env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# Start the dev environment
./restart.sh
```

This starts the backend (FastAPI on :8000) and frontend (Next.js on :3000).

## Project Structure

```
meet-edward/
├── backend/          # FastAPI + LangGraph (Python)
│   ├── services/     # Core services (memory, graph, heartbeat, etc.)
│   ├── routers/      # API endpoints
│   └── main.py       # App entrypoint
├── frontend/         # Next.js chat UI (TypeScript)
│   ├── app/          # App router pages
│   ├── components/   # React components
│   └── lib/          # Utilities and context
├── site/             # Marketing site (Next.js static export)
│   ├── app/          # Pages (landing, docs, blog)
│   ├── components/   # Site components
│   └── scripts/      # Build-time generators (sitemap, feeds, OG images)
├── setup.sh          # First-time setup script
└── restart.sh        # Dev server management
```

## Running the Dev Environment

```bash
./restart.sh             # Restart both frontend + backend
./restart.sh frontend    # Restart only frontend
./restart.sh backend     # Restart only backend
```

The backend runs natively on macOS (not in Docker) for AppleScript and iMessage access.

Logs are at `/tmp/edward-backend.log` and `/tmp/edward-frontend.log`.

## Code Style

- **Python**: Follow the existing code style. No linter is enforced, but keep it consistent with surrounding code.
- **TypeScript**: Run `cd frontend && npm run lint` before submitting. ESLint is configured via `eslint-config-next`.
- **Site**: Run `cd site && npm run lint` for the marketing site.

## Making Changes

1. **Open an issue first** to discuss what you'd like to change
2. **Fork** the repository
3. **Create a branch** from `main`: `git checkout -b feat/your-feature`
4. **Make your changes** and test locally
5. **Lint** the frontend: `cd frontend && npm run lint`
6. **Commit** with a clear message (e.g., `feat: add X`, `fix: resolve Y`)
7. **Push** to your fork and **open a Pull Request**

## Pull Requests

- Keep PRs focused — one feature or fix per PR
- Include a description of what changed and why
- Test your changes locally before submitting
- Link any related issues

## Issue Labels

- `good first issue` — Great for new contributors
- `bug` — Something isn't working correctly
- `enhancement` — New feature or improvement

## Questions?

Check the [docs](https://meet-edward.com/docs) for architecture details, or open an issue if you're unsure about something.
