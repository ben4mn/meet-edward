"""Every backend restart through launchd (``kickstart -k``) SIGKILLs uvicorn;
the stdio MCP servers it spawned (``uv … whatsapp-mcp-server run main.py``
and its python child, ``bun run …/apple-mcp/index.ts``) are re-parented to
launchd and live on. On 2026-09-21 eight whatsapp-mcp chains from Aug 22 to
Sep 20 were still running beside the live one. Startup now sweeps them.
"""

from __future__ import annotations

from services.mcp_client import orphaned_mcp_server_pids, parse_ps_output

PS = """  PID  PPID COMMAND
21421     1 uv --directory /Users/me/whatsapp-mcp/whatsapp-mcp-server run main.py
21424 21421 /Users/me/whatsapp-mcp/whatsapp-mcp-server/.venv/bin/python3 main.py
41614     1 /opt/homebrew/…/Python /Users/me/edward/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
41654 41614 uv --directory /Users/me/whatsapp-mcp/whatsapp-mcp-server run main.py
41655 41654 /Users/me/whatsapp-mcp/whatsapp-mcp-server/.venv/bin/python3 main.py
41657 41614 /Users/me/.bun/bin/bun run /Users/me/apple-mcp/index.ts
   99     1 /Users/me/.bun/bin/bun run /Users/me/apple-mcp/index.ts
  500     1 /opt/homebrew/bin/cloudflared --config x tunnel run edward-tunnel
 9920     1 /Users/me/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge
"""


def test_parse_ps_output_reads_pid_ppid_and_the_whole_command():
    rows = parse_ps_output(PS)
    assert rows[0] == (21421, 1, "uv --directory /Users/me/whatsapp-mcp/whatsapp-mcp-server run main.py")
    assert len(rows) == 9


def test_orphans_are_mcp_servers_whose_ancestry_does_not_reach_this_backend():
    rows = parse_ps_output(PS)
    # Ours: 41654/41655/41657 descend from the live backend. The Go bridge
    # (9920) has its own LaunchAgent and is never ours. cloudflared is unrelated.
    assert orphaned_mcp_server_pids(rows, own_pid=41614) == {21421, 21424, 99}


def test_nothing_is_an_orphan_when_every_server_is_ours():
    rows = [r for r in parse_ps_output(PS) if r[0] in (41614, 41654, 41655, 41657)]
    assert orphaned_mcp_server_pids(rows, own_pid=41614) == set()


def test_the_backend_itself_is_never_swept_even_if_its_command_mentions_a_server():
    rows = [(1, 0, "launchd"), (7, 1, "uvicorn main:app --note whatsapp-mcp-server")]
    assert orphaned_mcp_server_pids(rows, own_pid=7) == set()


def test_a_server_another_app_runs_is_never_swept():
    """apple-mcp under another app (Claude Desktop, an editor) or under a
    second Edward is someone else's, not an orphan."""
    rows = parse_ps_output(PS) + [
        (3000, 1, "/Applications/Claude.app/Contents/MacOS/Claude"),
        (3001, 3000, "/Users/me/.bun/bin/bun run /Users/me/apple-mcp/index.ts"),
        (4000, 1, "/opt/homebrew/…/Python /Users/me/other-edward/backend/.venv/bin/uvicorn main:app --port 8001"),
        (4001, 4000, "uv --directory /Users/me/whatsapp-mcp/whatsapp-mcp-server run main.py"),
    ]
    assert orphaned_mcp_server_pids(rows, own_pid=41614) == {21421, 21424, 99}
