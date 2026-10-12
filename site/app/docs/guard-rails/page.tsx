import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Guard Rails — Edward Docs",
  description:
    "What protects you when an AI can read your messages and act as you: the one-time setup code, the daily spend cap, the egress secret scan, taint and approvals, the shell's sandbox and its limits, friends' pings, Twilio and the known Alexa gap.",
  alternates: { canonical: "/docs/guard-rails" },
  openGraph: {
    title: "Guard Rails — Edward Docs",
    description:
      "Spend caps, the secret scan, approvals, the sandbox and its limits, friends' pings, and known gaps.",
    url: "/docs/guard-rails",
  },
};

export default function GuardRailsPage() {
  return (
    <DocsContent>
      <h1>Guard rails</h1>
      <p className="subtitle">
        One process reads untrusted text and can act as you. Here is what
        stands in between, and where it stops.
      </p>

      <h2>Before a password exists</h2>
      <ul>
        <li>
          Until you choose a password, the API answers nothing but the sign-in
          pages.
        </li>
        <li>
          The first password needs a <strong>one-time setup code</strong> the
          backend writes to <code>backend/.setup_code</code>, readable only by
          your macOS user and deleted once the password is set. The request
          must also come from a page at <code>localhost</code> on the host
          Mac, but a device on your network can forge those headers: the code
          is what keeps someone else from claiming your Edward first.
        </li>
        <li>
          Wrong codes are limited (10 per client in 15 minutes); sign-in
          allows 5 failed tries per client in 15 minutes, and 100 across all
          clients.
        </li>
        <li>
          Sessions last 30 days and renew past half their life. The cookie is{" "}
          <code>Secure</code>: serve Edward over HTTPS.
        </li>
      </ul>

      <h2>Money</h2>
      <ul>
        <li>
          <strong>A daily spend cap</strong> ($5 over your local day by
          default). Background work stops first (at 60 %), then Edward’s own
          turns (at the cap); your own turns may run a $3 reserve past it.
          When the cap is reached he says so in a sentence, not an error.
        </li>
        <li>
          <strong>Per-turn caps</strong>: $3 for a turn you asked for, $1
          for anything else, and 60 tool rounds at most.
        </li>
        <li>Every model call is priced and logged, and shown in the app.</li>
      </ul>

      <h2>What leaves</h2>
      <ul>
        <li>
          <strong>The egress secret scan</strong> is always on. Every message
          and file he sends, every shell command, every push, every URL he
          fetches or looks at, every search, everything typed in a browser
          and every argument to a custom MCP tool is scanned for keys, tokens,
          JWTs and private keys. A hit is refused, with only the kind logged.
          The exception is Anthropic’s server-side search and fetch (the
          default on Claude): their queries are checked only after the fact,
          and logged.
        </li>
        <li>
          <strong>Redaction on the way in</strong>: shell output, files he
          views and every local tool’s result pass through the same scanner
          before the model sees them (the server-side search and fetch results
          can’t be).
        </li>
        <li>
          <strong>One send.</strong> <code>send_message</code> is the only way
          he messages anyone: one recipient, a 10-minute dedupe, and a{" "}
          <strong>recipient rule</strong>. He may write to someone only if you
          asked, it is the chat the turn was asked in, they addressed him in
          the last 24 hours, or it is a live conversation; anyone else is your
          call. The app says “Sent” only when the message was delivered.
        </li>
        <li>
          <strong>Your Chrome</strong> (if you turned the hands on): typing
          outside a search box or clicking send, post, delete and the like
          needs you to have asked in that turn; payments are refused in every
          turn.
        </li>
      </ul>

      <h2>Taint and approvals</h2>
      <p>
        A turn is marked tainted when it starts from untrusted content (a
        message, a mail, a wake’s pack) or reads some (a web page, your
        messages, a calendar invitation). The approval gate that can hold
        such a turn’s outward calls is <strong>off by default</strong>; the
        recipient and Chrome rules above do its job in code. Set{" "}
        <code>EDWARD_TAINT_APPROVALS=shadow</code> to log what it would have
        held, or <code>on</code> to hold each one for your Approve / Deny on
        Today. An approval runs the stored call once, and never past the
        secret scan.
      </p>

      <h2>His computer</h2>
      <p>
        His shell runs under a macOS Seatbelt sandbox: it can’t read his own
        credentials (the session key, the setup code, <code>.env</code>),
        write the code he runs from or what production depends on, reach his
        own API or databases, or launch and script other apps. Details in{" "}
        <Link href="/docs/computer">His computer</Link>.
      </p>
      <div className="note caution">
        <p>
          <strong>It is not a containment boundary.</strong> The shell runs as
          your macOS user and can write your shell startup files, your
          LaunchAgents and Homebrew, from which programs later run as you,
          outside the sandbox. A prompt injection that reaches the shell (a
          web page, a message, a file he was pointed at) therefore has a way
          to run code as you. The rails make that deliberate work rather than
          one mistaken command. For strong isolation, run Edward as a separate
          macOS user (prompt G in{" "}
          <Link href="/docs/adapting">Adapting to your Mac</Link>).
        </p>
      </div>

      <h2>Other people</h2>
      <ul>
        <li>
          <strong>Friends’ pings stay off your machine</strong>: no shell,
          editor, Chrome or screens unless{" "}
          <code>EDWARD_FRIENDS_FULL_TOOLSET=on</code>. They can still read what
          Edward reads (your chats, memories, calendar) and answer from it;
          his prompt asks for discretion, which is a prompt, not a wall. See{" "}
          <Link href="/docs/listening">Listening &amp; pings</Link>.
        </li>
        <li>
          <strong>Twilio</strong> webhooks are refused without a valid Twilio
          signature, and then only your own numbers get a turn. Anyone else’s
          text is held, unanswered, with no push.
        </li>
        <li>
          <strong>The household hub’s voice</strong> runs as someone in your
          home, not as you: it follows the recipient rule and never texts you.
        </li>
        <li>
          <strong>A wake never messages you with <code>send_message</code></strong>;
          it reaches you only through <code>tell_owner</code>. And a wake can’t
          change or delete what you said.
        </li>
      </ul>

      <h2>Known gaps</h2>
      <ul>
        <li>
          <strong>Alexa</strong>: inbound requests are not signature-verified.
          Only the skill id is checked, and an Alexa turn runs as you, so
          anyone who learns the id and your URL can run a turn as you. Leave{" "}
          <code>ALEXA_SKILL_ID</code> unset unless you use the skill (the
          route then answers 503).{" "}
          <Link href="/docs/adapting">Adapting to your Mac</Link> has a prompt
          (E) that adds Amazon’s verification.
        </li>
        <li>
          <strong>The widget token is owner-equivalent</strong>: it can chat
          as you. Keep it on your own devices and regenerate it if it leaks.
        </li>
        <li>
          <strong>Postgres trusts local connections</strong> (Homebrew’s
          default), so any program running as you outside the sandbox can
          reach it. The shell itself is refused.
        </li>
        <li>
          <strong>Custom MCP servers’</strong> environment values are stored
          in plain text.
        </li>
      </ul>

      <h2>Emergency switches</h2>
      <p>
        <code>EDWARD_EGRESS_SECRET_SCAN=off</code> and{" "}
        <code>EDWARD_COMPUTER_SANDBOX=off</code> exist for emergencies only;
        the second runs his shell unsandboxed and logs every command. To stop
        Edward reaching anyone: turn Messages off in Listening, unset{" "}
        <code>TWILIO_AUTH_TOKEN</code>, and turn the iMessage and Push
        Notifications skills off.
      </p>

      <hr />
      <p>
        The long form:{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/trust-boundary.md`}>
          trust-boundary.md
        </a>
        ,{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/auth-and-pwa.md`}>
          auth-and-pwa.md
        </a>{" "}
        and{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/computer.md`}>computer.md</a>
        .
      </p>
    </DocsContent>
  );
}
