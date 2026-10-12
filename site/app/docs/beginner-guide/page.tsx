import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Beginner Guide — Edward Docs",
  description:
    "Install Edward on your Mac step by step, with no coding experience: Terminal, Homebrew, an Anthropic API key, the setup script, your password and first conversation.",
  alternates: { canonical: "/docs/beginner-guide" },
  openGraph: {
    title: "Beginner Guide — Edward Docs",
    description:
      "Install Edward on your Mac step by step, with no coding experience required.",
    url: "/docs/beginner-guide",
  },
};

export default function BeginnerGuidePage() {
  return (
    <DocsContent>
      <h1>Beginner guide</h1>
      <p className="subtitle">
        From a Mac to a running Edward, one step at a time. No experience
        needed.
      </p>

      <h2>What you will need</h2>
      <ul>
        <li>
          <strong>A Mac with Apple silicon</strong> (an M1 or later) that can
          stay on and stay logged in. Edward runs on this Mac, so when it
          sleeps, he does too. A Mac mini makes a good always-on host. Intel
          Macs are not supported.
        </li>
        <li>
          <strong>An Anthropic API key</strong>, which is how Edward talks to
          Claude. You pay Anthropic for what he uses, and Edward stops himself
          at a daily cap ($5 by default; you can change it).
        </li>
        <li>
          <strong>About 30 minutes</strong>, most of it waiting for things to
          download and install.
        </li>
      </ul>

      <h2>Step 1: Open Terminal</h2>
      <p>
        Terminal is the Mac app for typing commands. You don’t need to know
        how it works: paste exactly what this guide shows and press Return.
      </p>
      <ol>
        <li>
          Press <code>Cmd + Space</code> to open Spotlight
        </li>
        <li>
          Type <strong>Terminal</strong> and press Return
        </li>
      </ol>

      <h2>Step 2: Install Apple’s command-line tools</h2>
      <pre>
        <code>xcode-select --install</code>
      </pre>
      <p>
        A window asks to install the tools. Click <strong>Install</strong>{" "}
        and wait for it to finish. If Terminal says they are already
        installed, move on.
      </p>

      <h2>Step 3: Install Homebrew</h2>
      <p>
        Homebrew installs the software Edward is built on (Python, PostgreSQL
        and Node). Paste this and press Return:
      </p>
      <pre>
        <code>{`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`}</code>
      </pre>
      <p>
        It asks for your Mac password. Nothing appears while you type it;
        that is normal. When it finishes, it prints <strong>Next steps</strong>{" "}
        with two or three commands that add Homebrew to your path: paste
        those too. Then check:
      </p>
      <pre>
        <code>brew --version</code>
      </pre>

      <h2>Step 4: Get an Anthropic API key</h2>
      <ol>
        <li>
          Go to{" "}
          <a href="https://console.anthropic.com/">console.anthropic.com</a>{" "}
          and sign in or create an account
        </li>
        <li>Add a payment method or credits under Billing</li>
        <li>
          Open <strong>API Keys</strong> and click <strong>Create Key</strong>
        </li>
        <li>
          Copy the key (it starts with <code>sk-ant-</code>). The setup script
          asks for it in the next steps. Treat it like a password: don’t paste
          it into a chat or an email.
        </li>
      </ol>

      <h2>Step 5: Get the Edward code</h2>
      <pre>
        <code>{`cd ~
git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward`}</code>
      </pre>
      <p>
        This puts Edward in a <code>meet-edward</code> folder in your home
        folder. Keep it there rather than on the Desktop or in Documents,
        which macOS guards from background programs like Edward’s services.
      </p>

      <h2>Step 6: Run the setup script</h2>
      <pre>
        <code>./setup.sh</code>
      </pre>
      <p>It asks three things:</p>
      <ul>
        <li>
          <strong>Your name</strong>, which Edward calls you by
        </li>
        <li>
          <strong>Your Anthropic API key</strong> from step 4
        </li>
        <li>
          <strong>Your time zone</strong>, which reminders, quiet hours and
          “today” follow
        </li>
      </ul>
      <p>
        Then it installs Python 3.12, PostgreSQL 16 and Node, builds the
        memory search extension, creates Edward’s database, downloads his
        Python and web packages and his headless browser, and builds two small
        helpers. It takes a while, and a lot of text scrolls past. It is safe
        to run again if anything stops it.
      </p>
      <p>
        At the end it prints the path of a <code>python3.12</code>. Copy it
        somewhere: you need it if you want Edward to read your messages or
        calendar (step 10).
      </p>

      <h2>Step 7: Start Edward</h2>
      <pre>
        <code>bash scripts/install-launchagents.sh</code>
      </pre>
      <p>
        This keeps Edward running: macOS starts him now and every time you log
        in, and restarts him if he stops. It also sets up a health check every
        five minutes and a nightly backup to <code>~/edward-backups</code>.
      </p>
      <p>
        The first start takes a few minutes: the backend downloads its small
        memory model and the app builds itself. To watch it, run{" "}
        <code>tail -f /tmp/edward-backend.log</code> (press{" "}
        <code>Ctrl + C</code> to stop watching).
      </p>

      <h2>Step 8: Choose your password</h2>
      <p>
        On the Mac that runs Edward, open your browser and go to{" "}
        <code>http://localhost:3000</code>. The first visit asks for a
        password and a <strong>one-time setup code</strong>. Get the code in
        Terminal:
      </p>
      <pre>
        <code>cat backend/.setup_code</code>
      </pre>
      <p>
        Type it in (capitals and dashes don’t matter) and choose your
        password. The code is deleted once the password is set. It exists so
        nobody else on your network can claim your Edward first, which is also
        why the first password can only be set on this Mac.
      </p>

      <h2>Step 9: Turn on what he may use</h2>
      <p>
        On a fresh install Edward can use none of his skills until you turn
        them on. Open the <strong>Edward</strong> tab, then:
      </p>
      <ol>
        <li>
          <strong>Mind → Skills</strong>: turn on what you want him to have.
          Shell/Bash is his own computer (a shell and a file editor), Web
          Search lets him search and read pages, iMessage lets him read and
          send messages, Push Notifications lets him reach your phone, and
          iOS Widget lets him fill the iPhone widget.
        </li>
        <li>
          <strong>House → Locale</strong>: check the time zone.
        </li>
      </ol>
      <p>
        Then go to <strong>Chat</strong> and say hello. If he answers, Edward
        is running.
      </p>

      <h2>Step 10 (optional): Let him read your Mac’s apps</h2>
      <p>
        Edward reads Messages, Mail, Calendar, Reminders and Contacts from
        their own databases, which macOS protects. Grant access only for what
        you will use:
      </p>
      <ul>
        <li>
          <strong>Full Disk Access</strong>: System Settings → Privacy &amp;
          Security → Full Disk Access → <strong>+</strong>, press{" "}
          <code>Cmd + Shift + G</code>, paste the python path from step 6, and
          turn it on.
        </li>
        <li>
          <strong>Calendars and Reminders</strong> (so he can add and change
          events when you ask): sitting at the Mac, run{" "}
          <code>bash backend/scripts/eventkit_request_access.sh</code> and
          click Allow.
        </li>
        <li>
          <strong>Automation</strong>: macOS asks the first time Edward sends
          a message. Answer it at the Mac.
        </li>
      </ul>
      <p>
        Reading your messages is still off until you turn on{" "}
        <strong>Attention → Listening → Configuration → Messages</strong> in the
        Edward tab.
        Read <Link href="/docs/listening">Listening &amp; pings</Link> first:
        with it on, anyone can write <code>@Edward</code> in a chat with you
        and he answers from your own iMessage account.
      </p>

      <hr />

      <h2>Keeping the Mac awake</h2>
      <p>
        Edward runs as your user, so the Mac must be on and you must be logged
        in. In System Settings → Energy, turn on “Prevent automatic sleeping
        when the display is off” (on a laptop, keep it plugged in). After a
        restart, log in and Edward starts by himself.
      </p>

      <h2 id="using-edward-from-your-phone">Using Edward from your phone</h2>
      <p>
        Installing the app on your phone, push notifications and the
        microphone all need an <code>https://</code> address. The usual way is
        a tunnel to port 3000 on your Mac:
      </p>
      <ul>
        <li>
          <a href="https://tailscale.com/">Tailscale</a> keeps Edward private
          to your own devices.
        </li>
        <li>
          <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/">
            Cloudflare Tunnel
          </a>{" "}
          gives him a public address. To try it:{" "}
          <code>brew install cloudflared</code>, then{" "}
          <code>cloudflared tunnel --url http://localhost:3000</code>, which
          prints a temporary <code>https://…trycloudflare.com</code> address.
          For a lasting one, set up a named tunnel and run it as a LaunchAgent.
        </li>
      </ul>
      <p>
        Then tell Edward where he lives: add{" "}
        <code>EDWARD_PUBLIC_BASE_URL=https://your-address</code> to the{" "}
        <code>.env</code> file in the <code>meet-edward</code> folder (links in
        his pushes use it), and the tunnel’s LaunchAgent label as{" "}
        <code>EDWARD_TUNNEL_LABEL</code> so the health check watches it. Run{" "}
        <code>./restart.sh</code> to apply.
      </p>
      <p>
        Open the address on your phone and sign in. On iPhone, tap Share →{" "}
        <strong>Add to Home Screen</strong>; on Android, the ⋮ menu →{" "}
        <strong>Add to Home screen</strong>.
      </p>

      <h2>Common problems</h2>

      <h3>“brew: command not found”</h3>
      <p>
        The Homebrew installer’s “Next steps” weren’t run. Close Terminal,
        open a new window and try again; if it still fails, run the commands
        Homebrew printed at the end of step 3.
      </p>

      <h3>Setup stops with an Intel or Rosetta message</h3>
      <p>
        Edward needs Apple silicon. If your Mac is Apple silicon, your
        Terminal is running under Rosetta: quit it, then in Finder →
        Applications → Utilities, select Terminal, press{" "}
        <code>Cmd + I</code> and untick “Open using Rosetta”.
      </p>

      <h3>The page says “still starting”, or won’t load</h3>
      <p>
        The first start can take several minutes. Watch{" "}
        <code>tail -f /tmp/edward-backend.log</code> and{" "}
        <code>tail -f /tmp/edward-frontend.log</code>, then try again.{" "}
        <code>./restart.sh</code> restarts both.
      </p>

      <h3>I signed in but keep landing back on the login page</h3>
      <p>
        You are probably on a plain <code>http://</code> address other than{" "}
        <code>localhost</code>. Use the Mac itself, or an{" "}
        <code>https://</code> tunnel (above). On a home network you trust,{" "}
        <code>EDWARD_COOKIE_SECURE=off</code> in <code>.env</code> also works.
      </p>

      <h3>He says he can’t read my messages or calendar</h3>
      <p>
        Full Disk Access is missing, or went to a different python. The path
        changes when Homebrew upgrades Python, so grant the new one (setup and
        the LaunchAgent installer both print it) and run{" "}
        <code>bash scripts/install-launchagents.sh</code> again.
      </p>

      <h3>Is everything working?</h3>
      <p>
        Open <code>http://localhost:3000/api/health/report</code> while signed
        in: it lists what works and what doesn’t.
      </p>

      <hr />
      <p>
        Next: <Link href="/docs/configuration">Configuration</Link> lists the
        settings, and what ships switched off. The repository’s{" "}
        <a href={`${GITHUB_BLOB}/README.md#quick-start`}>README</a> has the
        same steps in brief.
      </p>
    </DocsContent>
  );
}
