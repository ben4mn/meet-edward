# Codex OAuth: Using ChatGPT Subscription Credits for API Calls

A guide to authenticating with OpenAI's Codex OAuth flow to use GPT-5.4 (and other models) via ChatGPT Plus/Pro subscription credits instead of pay-per-token API billing.

## Overview

OpenAI's Codex OAuth lets third-party apps make API calls that bill against a user's ChatGPT Plus/Pro subscription rather than API credits. This uses a **different endpoint** (`chatgpt.com`) than the standard API (`api.openai.com`) and has several non-obvious requirements.

**Auth priority** (recommended):
1. Codex OAuth (subscription credits) — free with ChatGPT Plus/Pro
2. `OPENAI_API_KEY` (pay-per-token) — fallback
3. No OpenAI access — hide OpenAI models

## The OAuth Flow

### 1. PKCE Authorization

Standard OAuth 2.0 Authorization Code + PKCE flow:

```
Authorize URL: https://auth.openai.com/authorize
Token URL:     https://auth.openai.com/oauth/token
Client ID:     app_EMoamEEZ73f0CkXaXp7hrann
Scopes:        openid profile email offline_access
Audience:      https://api.openai.com/v1
```

The Client ID `app_EMoamEEZ73f0CkXaXp7hrann` is the public Codex client — no client secret needed.

```python
import hashlib, base64, secrets

code_verifier = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).decode().rstrip("=")

auth_url = (
    f"https://auth.openai.com/authorize"
    f"?client_id=app_EMoamEEZ73f0CkXaXp7hrann"
    f"&redirect_uri=http://localhost:1455/callback"
    f"&response_type=code"
    f"&scope=openid+profile+email+offline_access"
    f"&audience=https://api.openai.com/v1"
    f"&code_challenge={code_challenge}"
    f"&code_challenge_method=S256"
    f"&state={secrets.token_urlsafe(32)}"
)
# Open auth_url in user's browser
```

### 2. Local Callback Server

Run a temporary HTTP server on `localhost:1455` to receive the callback with the authorization code:

```python
from aiohttp import web

async def handle_callback(request):
    code = request.query.get("code")
    # Exchange code for tokens (see step 3)
    return web.Response(text="Login successful! You can close this tab.")

app = web.Application()
app.router.add_get("/callback", handle_callback)
runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, "localhost", 1455)
await site.start()
```

### 3. Token Exchange

```python
import httpx

async with httpx.AsyncClient() as client:
    resp = await client.post(
        "https://auth.openai.com/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
            "code": code,
            "redirect_uri": "http://localhost:1455/callback",
            "code_verifier": code_verifier,
        },
    )
    tokens = resp.json()
    # tokens = {access_token, refresh_token, id_token, expires_in, token_type}
```

### 4. Extract Account ID

The `ChatGPT-Account-Id` header is **required** for every API call. Extract it from the JWT:

```python
import base64, json

# Decode the access_token JWT (no verification needed — just extracting claims)
payload = tokens["access_token"].split(".")[1]
payload += "=" * (4 - len(payload) % 4)  # pad base64
claims = json.loads(base64.urlsafe_b64decode(payload))

account_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
```

### 5. Token Refresh

Access tokens expire (typically 1 hour). Refresh proactively with a 5-minute buffer:

```python
async def refresh_tokens(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://auth.openai.com/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code != 200:
            # Token revoked or expired — user must re-authenticate
            raise ValueError("Refresh failed — clear tokens and re-auth")
        return resp.json()
```

**Failure modes**: expired refresh token, reused refresh token (one-time use), revoked by user. All require clearing stored tokens and re-initiating the OAuth flow.

## Making API Calls

### The Codex Endpoint

```
POST https://chatgpt.com/backend-api/codex/responses
```

This is **NOT** `api.openai.com/v1/responses` — the standard API endpoint doesn't work with Codex OAuth tokens (wrong scope).

### Required Parameters

| Parameter | Value | Why |
|-----------|-------|-----|
| `stream` | `true` | **REQUIRED** — ChatGPT backend rejects `stream: false` with 400 |
| `store` | `false` | **REQUIRED** — prevents conversation persistence in ChatGPT |
| `include` | `["reasoning.encrypted_content"]` | **REQUIRED** for stateless multi-turn with reasoning models |

### Required Headers

```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "ChatGPT-Account-Id": account_id,        # From JWT (step 4)
    "originator": "your-app-name",            # Identifies your app
    "OpenAI-Beta": "responses=experimental",  # Required for Responses API
    "Content-Type": "application/json",
}
```

### Request Body (OpenAI Responses API format)

```python
body = {
    "model": "gpt-5.4",
    "instructions": "System prompt here",       # NOT "system" — Responses API uses "instructions"
    "input": [                                   # NOT "messages" — Responses API uses "input"
        {"role": "user", "content": "Hello!"},
    ],
    "stream": True,
    "store": False,
    "include": ["reasoning.encrypted_content"],
    "reasoning": {"effort": "medium", "summary": "auto"},
    # NO max_output_tokens — unsupported by ChatGPT backend
}

# Optional: function calling
body["tools"] = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name"}
            },
            "required": ["location"],
        },
    }
]
```

### Gotchas vs Standard OpenAI API

| Standard API (`api.openai.com`) | Codex (`chatgpt.com`) |
|---|---|
| `stream: true/false` both work | `stream: true` **required** |
| `max_output_tokens` supported | Not supported (omit it) |
| Response returned as JSON | Response returned as SSE stream |
| Auth: `Bearer sk-...` API key | Auth: `Bearer <oauth_token>` + `ChatGPT-Account-Id` |
| 404 = not found | 404 = **usage limit reached** |

## Parsing the SSE Response

Since `stream: true` is mandatory, you must parse Server-Sent Events. The key event is `response.completed` which contains the full response.

### SSE Event Sequence

```
event: response.created        → response initialized
event: response.in_progress    → generation started
event: response.output_item.added → new output item
event: response.output_text.delta → text chunks (many)
event: response.output_text.done  → text finalized
event: response.output_item.done  → item finalized
event: response.completed      → FULL response object (this is what you want)
```

### Critical: The response.completed Event is Wrapped

The `response.completed` event data is **NOT** the Response object directly. It's wrapped:

```json
{
    "type": "response.completed",
    "response": {
        "id": "resp_...",
        "output": [...],
        "output_text": "...",
        "usage": {...}
    },
    "sequence_number": 147
}
```

You must unwrap: `data["response"]` to get the actual response with the `output` array.

### Parsing Example

```python
import json as _json
import httpx

async def call_codex(body: dict, headers: dict) -> dict:
    completed_data = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            "https://chatgpt.com/backend-api/codex/responses",
            json=body,
            headers=headers,
        ) as response:
            if response.status_code == 404:
                text = ""
                async for chunk in response.aiter_text():
                    text += chunk
                if "usage_limit_reached" in text:
                    raise ValueError("ChatGPT usage limit reached")
                raise ValueError(f"404: {text[:200]}")

            if response.status_code != 200:
                text = ""
                async for chunk in response.aiter_text():
                    text += chunk
                raise ValueError(f"Error ({response.status_code}): {text[:200]}")

            # Parse SSE stream
            buffer = ""
            current_event = ""
            current_data_lines = []
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")

                    if line.startswith("event: "):
                        current_event = line[7:]
                        current_data_lines = []
                    elif line.startswith("data: "):
                        current_data_lines.append(line[6:])
                    elif line == "":
                        # Blank line = event dispatch
                        if current_event == "response.completed" and current_data_lines:
                            data_str = "\n".join(current_data_lines)
                            completed_data = _json.loads(data_str)
                        current_event = ""
                        current_data_lines = []

    if not completed_data:
        raise ValueError("Stream ended without response.completed")

    # IMPORTANT: Unwrap the nested response
    response_obj = completed_data.get("response", completed_data)
    return parse_response(response_obj)


def parse_response(data: dict) -> dict:
    """Parse the Response object's output array."""
    text_parts = []
    tool_calls = []

    for item in data.get("output", []):
        item_type = item.get("type", "")

        if item_type == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text_parts.append(content.get("text", ""))

        elif item_type == "function_call":
            args_raw = item.get("arguments", "{}")
            args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            tool_calls.append({
                "call_id": item.get("call_id", ""),
                "name": item.get("name", ""),
                "arguments": args,
            })

        # item_type == "reasoning" → skip (encrypted reasoning trace)

    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "output_text": data.get("output_text", ""),  # convenience field
    }
```

### Sending Tool Results Back

When the model makes function calls, send results back as input items:

```python
# After executing the tool:
input_items.append({
    "type": "function_call_output",
    "call_id": tool_call["call_id"],   # Must match the call_id from the function_call
    "output": json.dumps(result),       # String, not dict
})

# Then make another API call with the updated input
```

## Behavioral Differences: GPT-5.4 vs Claude

| Behavior | Claude | GPT-5.4 |
|----------|--------|---------|
| Text alongside tool calls | Always includes "thinking" text | Function calls have **no** accompanying text |
| Empty response after tools | Never happens | Common — model considers work done after tool execution |
| Tool result format | `tool_result` content blocks | `function_call_output` input items |
| Reasoning traces | Not applicable | Encrypted (must pass back in multi-turn) |

**Important**: After GPT-5.4 executes tools and you send back results, it may return an empty response (no text, no more tool calls). This means it considers the task complete. Don't treat this as an error — synthesize a summary or accept the tool execution as the response.

## Error Handling

| HTTP Status | Meaning | Action |
|-------------|---------|--------|
| 200 | Success (SSE stream) | Parse events |
| 400 | Bad request (e.g. `stream: false`) | Fix request body |
| 401 | Token expired/invalid | Refresh or re-auth |
| 404 + `usage_limit_reached` | Subscription limit hit | Wait or switch to API key |
| 404 (other) | Endpoint issue | Check URL |
| 429 | Rate limited | Back off and retry |

## Token Storage

Store tokens securely (database, encrypted file). You need:
- `access_token` — for API calls (expires ~1 hour)
- `refresh_token` — for getting new access tokens (one-time use per refresh)
- `account_id` — extracted once from JWT, doesn't change
- `expires_at` — track expiration for proactive refresh

## Reference Implementations

- **Cline** (VS Code AI): [PR #8664](https://github.com/cline/cline/pull/8664) — merged Codex OAuth support
- **opencode-openai-codex-auth**: [GitHub](https://github.com/numman-ali/opencode-openai-codex-auth) — 7-step fetch flow reference
- **OpenAI Responses API docs**: [platform.openai.com/docs/api-reference/responses](https://platform.openai.com/docs/api-reference/responses)

## Quick Checklist

- [ ] Client ID: `app_EMoamEEZ73f0CkXaXp7hrann` (no secret needed)
- [ ] Endpoint: `chatgpt.com/backend-api/codex/responses` (not `api.openai.com`)
- [ ] `stream: true` always
- [ ] `store: false` always
- [ ] `include: ["reasoning.encrypted_content"]` always
- [ ] `ChatGPT-Account-Id` header from JWT claims
- [ ] Unwrap `response.completed` event: `data["response"]` contains the actual response
- [ ] Handle empty response after tool calls (GPT behavioral difference)
- [ ] No `max_output_tokens` parameter
- [ ] 404 can mean usage limit (check body for `usage_limit_reached`)
