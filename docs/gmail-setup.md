# Connecting Gmail

Edward connects to one Gmail account through Google's own OAuth (`backend/services/gmail_service.py`, `backend/routers/gmail.py`). Once connected, he can search and read it in any turn, send from it when you ask, and optionally watch the inbox. You need a one-time setup in the Google Cloud Console to get an OAuth client of your own. Edward ships without one.

Mail.app accounts (iCloud and others) are read differently, from Mail.app's own index on the Mac, which needs Full Disk Access. See [ADAPTING.md](ADAPTING.md#mail).

## How connecting works

1. **Connect Gmail** in Settings → Gmail calls the signed-in `GET /api/gmail/oauth/url`. That call mints a random `state` and a PKCE verifier, keeps them in the backend's memory for 10 minutes, and sends Google the S256 challenge.
2. Google sends the browser back to the public `GET /api/gmail/oauth/callback`. The callback accepts only a state minted that way, unused and under 10 minutes old, and exchanges the code with that state's own verifier. Anything else answers 400 and stores nothing. Both `state` and `code` are redacted from the access log.
3. Edward reads the account's address. He refuses it, storing nothing, when `GMAIL_ACCOUNT` is set and the address differs, or when a different account is already connected (disconnect that one first).

A backend restart forgets connects that were started but not finished. Press Connect again.

## Step 1: Create a Google Cloud project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Open the project picker (top left) → **New Project**, name it `Edward` → **Create**.
3. Make sure the new project is the selected one.

## Step 2: Enable the Gmail API

1. **APIs & Services** → **Library**
2. Search for "Gmail API", open it and click **Enable**.

## Step 3: Configure the consent screen

The console calls this **Google Auth Platform**. In older consoles it is **APIs & Services → OAuth consent screen**.

1. **Branding**: app name `Edward`. For the support email and developer contact, use your own address.
2. **Audience**: user type **External** (a personal @gmail.com has no Internal option). Under **Test users**, add the Gmail address you will connect, e.g. `you@example.com`.
3. **Data Access** → **Add or Remove Scopes** → add:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.modify`

   These two cover everything Edward does, sending included: `users.messages.send`, `threads.get` and `messages.get`/`list` all accept `gmail.modify`. You don't need the `gmail.send` scope.
4. **Audience** → **Publishing status** → **Publish app**, so that it reads **In production**.
   - **Important:** in **Testing**, Google expires the refresh token every 7 days and Gmail silently disconnects. An unverified app in production still works for your own account (up to 100 users). Google shows "Google hasn't verified this app": choose **Advanced** → **Go to Edward**. Personal use needs no verification.

## Step 4: Create the OAuth client

1. **Clients** (older consoles: **APIs & Services** → **Credentials** → **+ Create credentials** → **OAuth client ID**)
2. Application type: **Web application**, not Desktop, because the callback is a web redirect.
3. Name: `Edward`
4. Under **Authorized redirect URIs**, add the address you open Edward at, followed by `/api/gmail/oauth/callback`. Use the frontend's address: the frontend forwards `/api` to the backend.
   - Behind a tunnel or proxy: `https://edward.example.com/api/gmail/oauth/callback`
   - On the Mac itself: `http://localhost:3000/api/gmail/oauth/callback` (Google allows plain HTTP for localhost)
   - You can add both. Connect from the matching address.
5. Click **Create**, then copy the **Client ID** and **Client Secret**, or use **Download JSON**.

## Step 5: Configure Edward

Add to `.env` in the checkout:

```bash
GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=GOCSPX-your-client-secret

# Must match an authorized redirect URI exactly. The default is
# http://localhost:3000/api/gmail/oauth/callback; set it when you connect
# through a public URL:
GMAIL_REDIRECT_URI=https://edward.example.com/api/gmail/oauth/callback

# Or, instead of the two values above: the JSON the console's "Download JSON"
# saved. Keep it private (chmod 600). The two variables win when both are set.
# GMAIL_CLIENT_SECRETS_FILE=~/.config/edward/gmail-oauth-client.json

# Optional, recommended: the only account Edward may connect (compared without
# case). Any other account is refused and nothing is stored. It also counts as
# one of your own addresses, alongside EDWARD_OWNER_EMAILS.
GMAIL_ACCOUNT=you@example.com
```

## Step 6: Connect

1. Restart the backend so it reads the client: `./restart.sh backend`. With the LaunchAgents installed, that runs `launchctl kickstart -k gui/$UID/<prefix>.backend` (default prefix `com.meet-edward`).
2. Open **Settings** → **Gmail** → **Connect Gmail**, and finish on Google's side within 10 minutes.
3. Authorize with the account in `GMAIL_ACCOUNT`.
4. You are sent back to Settings with Gmail connected. A refusal shows one line saying why: wrong account, already connected as someone else, or an expired or reused sign-in.

To switch accounts, **Disconnect** first. A connected account is only ever replaced by itself.

## What Edward does with it

- **Reads it in any turn.** `messages(action="mail_search", query=…, account="gmail"|"icloud"|"both")` searches Gmail, using Gmail's own search syntax, together with Mail.app's mail, newest first. `messages(action="mail_read", id="gmail:<id>" | "gmail:thread:<id>")` reads one message or a whole thread ([architecture/tool-loop.md](architecture/tool-loop.md)).
- **Sends from it when asked.** `send_message(to=<address>, subject=…, message=…)` sends an email from this account, and `chat_id="gmail:<id>"` replies in that thread. Email goes through the same guards as a text: the recipient rule, the 10-minute dedupe, the egress secret scan, and never to you from a turn you are not part of. Edward says "Sent" only when Gmail has labelled the message SENT. With no Gmail connected, an email send fails honestly and says so.
- **Watches the inbox, if you turn it on.** **Watch my inbox** in Settings → Gmail starts the listener (`heartbeat_config.gmail_enabled`; it checks every `gmail_poll_seconds`, default 120 s, which you can change in the same panel). New inbox mail is stored as an event; mail more than 24 h old is skipped, and an expired history mark re-seeds itself. What Edward does with new mail follows `EDWARD_PROACTIVITY`. At the default `off`, it is stored and searchable, but he does not bring it up unasked ([architecture/noticing.md](architecture/noticing.md)).

## Troubleshooting

- **"Access blocked" on the consent screen.** Add your Gmail address as a test user (Step 3.2).
- **`redirect_uri_mismatch`.** `GMAIL_REDIRECT_URI` and the redirect URI in the console must match exactly: scheme, host, port and path, with no trailing slash. Connect from that same address.
- **"I don't recognise this sign-in" or "expired".** The connect was started more than 10 minutes ago, was already used, or was started before a backend restart. Press Connect again.
- **"I'm set up to connect only …" or "already connected as …".** Sign in with the `GMAIL_ACCOUNT` address, or disconnect the current account first.
- **Disconnected after about a week.** The app is still in **Testing**. Publish it (Step 3.4) and reconnect.
- **Token refresh fails.** Disconnect and reconnect Gmail in Settings. When the sign-in has lapsed, the `email` section of `GET /api/health/report` warns "Gmail's sign-in has lapsed", and Edward says the same when he tries to read or send.
- **Connect does nothing after editing `.env`.** The client id and secret are read when the backend starts. Restart it.
