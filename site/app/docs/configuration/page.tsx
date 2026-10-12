import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Configuration — Edward Docs",
  description:
    "Edward's settings: who you are, where he runs, the Claude routes and the daily spend cap, and every feature that ships switched off, with the switch that turns it on.",
  alternates: { canonical: "/docs/configuration" },
  openGraph: {
    title: "Configuration — Edward Docs",
    description:
      "Edward's settings: you, this Mac, the Claude routes and spend cap, and what ships switched off.",
    url: "/docs/configuration",
  },
};

export default function ConfigurationPage() {
  return (
    <DocsContent>
      <h1>Configuration</h1>
      <p className="subtitle">
        Who you are, where he runs, what he may spend, and what ships switched
        off.
      </p>

      <h2>Where settings live</h2>
      <p>
        Everything is in <code>.env</code> at the top of your checkout.{" "}
        <code>./setup.sh</code> creates it from{" "}
        <a href={`${GITHUB_BLOB}/.env.example`}>
          <code>.env.example</code>
        </a>
        , which describes every setting with its default. The backend loads
        the file itself (a variable already set in its environment wins), and
        the start scripts never export it. Most values are read on every call;
        to be sure a change applies, run <code>./restart.sh backend</code>.
        Never commit <code>.env</code>.
      </p>

      <h2>The essentials</h2>
      <p>
        <code>./setup.sh</code> asks for these three:
      </p>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>What it is</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>EDWARD_OWNER_NAME</code></td>
            <td>What Edward calls you. Unset, he says “the owner”.</td>
          </tr>
          <tr>
            <td><code>ANTHROPIC_API_KEY</code></td>
            <td>
              Your key from{" "}
              <a href="https://console.anthropic.com/">console.anthropic.com</a>.
              Every model call uses it.
            </td>
          </tr>
          <tr>
            <td><code>TIMEZONE</code></td>
            <td>
              Your IANA zone (<code>America/New_York</code>), until the app’s
              own setting (Edward tab → House → Locale) is loaded.
            </td>
          </tr>
        </tbody>
      </table>

      <h2>About you</h2>
      <p>
        Read by <code>backend/services/owner.py</code>. The code never names
        its owner any other way.
      </p>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>What it is</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <code>EDWARD_OWNER_FULL_NAME</code>,{" "}
              <code>EDWARD_OWNER_ALIASES</code>
            </td>
            <td>Other names you go by, so he recognises you in messages.</td>
          </tr>
          <tr>
            <td><code>EDWARD_OWNER_HANDLES</code></td>
            <td>
              Your phone numbers, emails and Apple ID: how he recognises your
              iMessages and your self-chat. Set it if he reads your messages.
            </td>
          </tr>
          <tr>
            <td><code>EDWARD_OWNER_EMAILS</code></td>
            <td>Your email addresses: mail from these is yours, and he never emails you.</td>
          </tr>
          <tr>
            <td><code>EDWARD_HOUSEHOLD</code></td>
            <td>The people at the household hub, comma-separated, you first.</td>
          </tr>
          <tr>
            <td><code>EDWARD_SENSITIVE_NAMES</code></td>
            <td>
              Pets, family and anyone else whose mentions his memory upkeep
              must never retire on its own.
            </td>
          </tr>
        </tbody>
      </table>

      <h2>This Mac</h2>
      <p>
        Read by <code>backend/services/host_config.py</code>.
      </p>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Default</th>
            <th>What it is</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>EDWARD_PUBLIC_BASE_URL</code></td>
            <td><code>http://localhost:3000</code></td>
            <td>Where you open Edward (a tunnel’s https address). Links in pushes and texts use it.</td>
          </tr>
          <tr>
            <td><code>EDWARD_TUNNEL_LABEL</code></td>
            <td>unset</td>
            <td>Your tunnel’s LaunchAgent label, so the health check watches and restarts it.</td>
          </tr>
          <tr>
            <td><code>EDWARD_BACKEND_HOST</code></td>
            <td><code>0.0.0.0</code></td>
            <td>The backend listens on every interface so a hub on your network can reach it; <code>127.0.0.1</code> keeps it to this Mac.</td>
          </tr>
          <tr>
            <td><code>EDWARD_COOKIE_SECURE</code></td>
            <td><code>on</code></td>
            <td>The session cookie assumes HTTPS (or <code>http://localhost</code>). <code>off</code> only on a plain-HTTP network you trust.</td>
          </tr>
          <tr>
            <td><code>EDWARD_LAUNCHD_PREFIX</code></td>
            <td><code>com.meet-edward</code></td>
            <td>The LaunchAgents’ label prefix.</td>
          </tr>
          <tr>
            <td><code>EDWARD_BACKUP_DIR</code></td>
            <td><code>~/edward-backups</code></td>
            <td>Where the nightly backup goes. A <code>/Volumes/…</code> path is a removable drive (skipped while unplugged; needs Full Disk Access).</td>
          </tr>
          <tr>
            <td><code>NEXT_PUBLIC_BASE_URL</code></td>
            <td>the base URL</td>
            <td>The app’s public origin for link previews, baked into the frontend build: after changing it, run <code>bash frontend/deploy-build.sh</code> and restart the frontend.</td>
          </tr>
        </tbody>
      </table>

      <h2>The model and the money</h2>
      <p>
        <code>EDWARD_LLM_PROVIDER=anthropic</code> is the default: every call
        goes to the Claude API, with one route per purpose.
      </p>
      <table>
        <thead>
          <tr>
            <th>Route</th>
            <th>Used for</th>
            <th>Model, effort</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>chat</code></td>
            <td>your conversations, ping replies, scheduled events</td>
            <td>Sonnet 5.5, medium</td>
          </tr>
          <tr>
            <td><code>mind</code></td>
            <td>every wake: follow-through, the nightly, free time, speaking up</td>
            <td>Sonnet 5.5, medium</td>
          </tr>
          <tr>
            <td><code>pulse</code></td>
            <td>deciding what the widget and screens show</td>
            <td>Sonnet 5.5, medium</td>
          </tr>
          <tr>
            <td><code>background</code></td>
            <td>memory extraction and upkeep, the noticer, photo descriptions</td>
            <td>Haiku 5.5, high</td>
          </tr>
          <tr>
            <td><code>voice</code></td>
            <td>spoken turns</td>
            <td>Sonnet 5.5, low</td>
          </tr>
          <tr>
            <td><code>judge</code></td>
            <td>evaluations</td>
            <td>Opus 5.5, high</td>
          </tr>
        </tbody>
      </table>
      <p>
        Override any of them per purpose with{" "}
        <code>EDWARD_CLAUDE_&lt;PURPOSE&gt;_MODEL</code>,{" "}
        <code>_EFFORT</code>, <code>_MAX_TOKENS</code>,{" "}
        <code>_THINKING_DISPLAY</code> and <code>_CACHE_TTL</code> (for
        example <code>EDWARD_CLAUDE_CHAT_MODEL</code>).
      </p>
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            <th>Default</th>
            <th>What it bounds</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>EDWARD_DAILY_SPEND_CAP_USD</code></td>
            <td><code>5</code></td>
            <td>
              Dollars over your local day. Background work stops at 60 % of it
              (<code>EDWARD_BACKGROUND_SPEND_SHARE</code>), Edward’s own turns
              at the cap, and your own turns may run{" "}
              <code>EDWARD_INTERACTIVE_RESERVE_USD</code> (3) past it.
            </td>
          </tr>
          <tr>
            <td><code>EDWARD_TURN_MAX_USD</code></td>
            <td><code>3.00</code></td>
            <td>One turn you asked for.</td>
          </tr>
          <tr>
            <td><code>EDWARD_AUTONOMOUS_TURN_MAX_USD</code></td>
            <td><code>1.00</code></td>
            <td>One turn you didn’t (a wake, a ping from a friend, an event).</td>
          </tr>
          <tr>
            <td>
              <code>EDWARD_CREDITS_START_USD</code>,{" "}
              <code>EDWARD_CREDITS_START_DATE</code>
            </td>
            <td>unset</td>
            <td>Set both to count prepaid credits down on the Fuel card (console.anthropic.com stays the authority).</td>
          </tr>
        </tbody>
      </table>
      <p>
        Every call is priced and shown in the app (Edward tab → Work → Usage)
        and at <code>GET /api/health/spend</code>.
      </p>
      <p>
        <strong>The Codex fallback</strong>:{" "}
        <code>EDWARD_LLM_PROVIDER=codex</code> runs GPT through the Codex
        CLI’s ChatGPT login. That is not an official API for third-party apps,
        may break at any time and may conflict with OpenAI’s terms; use it at
        your own risk. <code>OPENAI_API_KEY</code> switches it to the public
        API.
      </p>

      <h2 id="off-by-default">Off by default, and the switch for each</h2>
      <p>
        A fresh install reads nothing private and reaches no one. Each feature
        below stays off, or does nothing, until you turn it on.
      </p>
      <table>
        <thead>
          <tr>
            <th>What</th>
            <th>How to turn it on</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Every skill</td>
            <td>Edward tab → Mind → Skills (Shell/Bash, Web Search, iMessage, Push Notifications, iOS Widget, Voice…)</td>
          </tr>
          <tr>
            <td>Reading your iMessages, and <code>@Edward</code> pings</td>
            <td>
              Edward tab → Attention → Listening → Configuration → Messages;
              Full Disk Access;
              Messages on this Mac signed into your Apple ID;{" "}
              <code>EDWARD_OWNER_HANDLES</code>. Pings then follow{" "}
              <code>EDWARD_PINGS</code> (on).{" "}
              <Link href="/docs/listening">Read this first</Link>.
            </td>
          </tr>
          <tr>
            <td>Mail.app</td>
            <td>Listening → Configuration → Email; Full Disk Access; keep Mail.app running</td>
          </tr>
          <tr>
            <td>Gmail</td>
            <td>
              An OAuth client (<code>GMAIL_CLIENT_ID</code>,{" "}
              <code>GMAIL_CLIENT_SECRET</code>;{" "}
              <a href={`${GITHUB_BLOB}/docs/gmail-setup.md`}>gmail-setup.md</a>),
              then Attention → Gmail → Watch my inbox
            </td>
          </tr>
          <tr>
            <td>Noticing (speaking up unasked)</td>
            <td>
              <code>EDWARD_PROACTIVITY=shadow</code> (decides, never speaks),
              then <code>notice</code>
            </td>
          </tr>
          <tr>
            <td>Hands in your Chrome</td>
            <td>
              Load <code>chrome-extension/</code> unpacked in Chrome, then{" "}
              <code>EDWARD_CHROME_HANDS=on</code>
            </td>
          </tr>
          <tr>
            <td>His shell, editor, your Chrome and the screens in friends’ pings</td>
            <td>
              <code>EDWARD_FRIENDS_FULL_TOOLSET=on</code>, at a risk: a
              friend’s message then drives a shell that runs as you
            </td>
          </tr>
          <tr>
            <td>Approvals for tainted turns</td>
            <td>
              <code>EDWARD_TAINT_APPROVALS=shadow</code> (log only) or{" "}
              <code>on</code>
            </td>
          </tr>
          <tr>
            <td>Voice</td>
            <td>
              <code>ELEVENLABS_API_KEY</code>, <code>ELEVENLABS_VOICE_ID</code>
            </td>
          </tr>
          <tr>
            <td>Push notifications</td>
            <td>
              <code>npx web-push generate-vapid-keys</code> into{" "}
              <code>VAPID_PUBLIC_KEY</code> / <code>VAPID_PRIVATE_KEY</code>,
              then allow notifications in the app (HTTPS)
            </td>
          </tr>
          <tr>
            <td>The hub’s pets and care</td>
            <td>
              Copy <code>backend/hub_seed.example.json</code> to{" "}
              <code>backend/hub_seed.json</code>, describe your own, set{" "}
              <code>EDWARD_HUB_SEED=hub_seed.json</code>
            </td>
          </tr>
          <tr>
            <td>The hub’s people</td>
            <td><code>EDWARD_HOUSEHOLD</code> (just you until set)</td>
          </tr>
          <tr>
            <td>The household photo door (a photos-only login)</td>
            <td>
              <code>PHOTO_DOOR_PASSWORD_HASH</code>: a bcrypt hash, in single
              quotes (<code>.env.example</code> has the command)
            </td>
          </tr>
          <tr>
            <td>Twilio SMS and WhatsApp (a number of Edward’s own)</td>
            <td>
              <code>TWILIO_*</code> and <code>EDWARD_OWNER_HANDLES</code>.
              Webhooks are refused without <code>TWILIO_AUTH_TOKEN</code>, and
              only your own numbers get an answer;{" "}
              <code>EDWARD_TWILIO_ANSWER_ANYONE=on</code> answers everyone, at
              a risk
            </td>
          </tr>
          <tr>
            <td>The WhatsApp bridge</td>
            <td>
              Run the <code>whatsapp-mcp</code> Go bridge yourself, then{" "}
              <code>MCP_WHATSAPP_ENABLED=true</code>
            </td>
          </tr>
          <tr>
            <td>apple-mcp (a fallback for reading your calendar)</td>
            <td>
              Install bun, clone apple-mcp, <code>MCP_APPLE_ENABLED=true</code>
            </td>
          </tr>
          <tr>
            <td>Alexa and Home Assistant</td>
            <td>
              <code>ALEXA_SKILL_ID</code>, <code>HOME_ASSISTANT_URL</code>,{" "}
              <code>HOME_ASSISTANT_TOKEN</code>. Inbound Alexa requests are not
              signature-checked yet: leave <code>ALEXA_SKILL_ID</code> unset
              unless you use the skill
            </td>
          </tr>
          <tr>
            <td>Brave Search</td>
            <td>
              <code>BRAVE_SEARCH_API_KEY</code>, only needed with the Claude
              server-side web tools off (<code>EDWARD_SERVER_WEB_TOOLS=off</code>)
              or on the Codex fallback
            </td>
          </tr>
          <tr>
            <td>The knowledge graph</td>
            <td>
              <code>GRAPHITI_ENABLED=true</code> (queues every new memory){" "}
              <strong>and</strong> <code>GRAPHITI_INGEST_ENABLED=true</code>{" "}
              (fills the graph from the queue), then{" "}
              <code>bash scripts/install-launchagents.sh --with-graph</code>.
              Each memory costs a few background model calls
            </td>
          </tr>
          <tr>
            <td>Hosted pages</td>
            <td>
              <code>HTML_HOSTING_URL</code> and{" "}
              <code>HTML_HOSTING_API_KEY</code>. There is no default host
            </td>
          </tr>
          <tr>
            <td>A daily cap on his own pushes</td>
            <td><code>EDWARD_PUSH_BUDGET=&lt;n&gt;</code> (none by default)</td>
          </tr>
          <tr>
            <td>Deep health checks for a hub kiosk and for mail</td>
            <td>
              <code>EDWARD_HEALTH_HUB_KIOSK=1</code>,{" "}
              <code>EDWARD_HEALTH_EMAIL=1</code>
            </td>
          </tr>
          <tr>
            <td>The Codex fallback</td>
            <td>
              <code>EDWARD_LLM_PROVIDER=codex</code> (unofficial, at your own
              risk)
            </td>
          </tr>
        </tbody>
      </table>

      <h2>On by default</h2>
      <p>Know these before you leave Edward running:</p>
      <ul>
        <li>
          <strong>The daily spend cap</strong>, $5 over your local day.
        </li>
        <li>
          <strong>Quiet hours</strong>, 22:00–07:00 (
          <code>EDWARD_QUIET_HOURS=22-7</code>; equal hours mean none).
          Pushes and spoken lines wait for morning.
        </li>
        <li>
          <strong>Two scheduled wakes a day</strong>: a silent nightly at
          03:00, skipped when nothing happened, and one free-time run at 13:00
          (about $0.50 at most) that reaches nobody.
        </li>
        <li>
          <strong>The surfaces pulse</strong>: a check every 15 minutes that
          runs a model turn only when the board, the calendar, a screen or the
          widget changed.
        </li>
        <li>
          <strong>The nightly backup</strong> at 03:30 to{" "}
          <code>~/edward-backups</code>, on the same disk, unless you install
          with <code>--without-backup</code> or point{" "}
          <code>EDWARD_BACKUP_DIR</code> elsewhere. Keep a copy off the Mac
          too.
        </li>
        <li>
          <strong>The late-dose alert</strong> (<code>EDWARD_CARE_WATCH=on</code>),
          which has nothing to watch until the hub has timed care items.
        </li>
        <li>
          <strong>The egress secret scan</strong> and{" "}
          <strong>the shell sandbox</strong>. Their <code>off</code> values are
          emergency switches only.
        </li>
      </ul>

      <h2>Everything else</h2>
      <p>
        The full annotated list is{" "}
        <a href={`${GITHUB_BLOB}/.env.example`}>
          <code>.env.example</code>
        </a>
        ; the environment section of{" "}
        <a href={`${GITHUB_BLOB}/CLAUDE.md`}>CLAUDE.md</a> has the main ones
        with their defaults on one screen. Nothing reads{" "}
        <code>GITHUB_TOKEN</code>, <code>USPS_API_USER_ID</code> or the
        LangSmith variables any more.
      </p>
      <p>
        The iPhone widget has its own token, set up in{" "}
        <Link href="/docs/surfaces">Widget, screen &amp; hub</Link>.
      </p>
    </DocsContent>
  );
}
