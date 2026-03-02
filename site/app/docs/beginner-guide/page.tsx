import type { Metadata } from "next";
import { DocsContent } from "../../../components/docs/DocsContent";

export const metadata: Metadata = {
  title: "Beginner Guide — Edward Docs",
  description: "Step-by-step walkthrough to install and run Edward from scratch — no coding experience required. Covers Terminal basics, Homebrew, Python, Node.js, and first launch.",
  alternates: { canonical: "/docs/beginner-guide" },
  openGraph: {
    title: "Beginner Guide — Edward Docs",
    description: "Step-by-step walkthrough to install and run Edward from scratch — no coding experience required.",
    url: "/docs/beginner-guide",
  },
};

export default function BeginnerGuidePage() {
  return (
    <DocsContent>
      <h1>Beginner Guide</h1>
      <p className="subtitle">
        A complete walkthrough from zero to a running Edward — no experience
        required.
      </p>

      <h2>What You Will Need</h2>
      <ul>
        <li>
          <strong>A Mac</strong> — Edward relies on macOS for iMessage, Apple
          Calendar, and other integrations. Windows and Linux are not supported.
        </li>
        <li>
          <strong>An internet connection</strong> — to download software and
          talk to the Claude AI API.
        </li>
        <li>
          <strong>About 20 minutes</strong> — most of that is waiting for things
          to install.
        </li>
        <li>
          <strong>An Anthropic API key</strong> — this is how Edward connects to
          Claude. We&apos;ll walk you through getting one in Step 6. It costs a
          few cents per conversation (typically under $0.10/day for casual use).
        </li>
      </ul>

      <h2>Step 1: Open Terminal</h2>
      <p>
        Terminal is a built-in app on your Mac that lets you type commands. You
        don&apos;t need to know how it works in detail — just type (or paste)
        exactly what this guide shows you.
      </p>
      <p>To open it:</p>
      <ol>
        <li>
          Press <code>Cmd + Space</code> to open Spotlight search
        </li>
        <li>
          Type <strong>Terminal</strong> and press Enter
        </li>
      </ol>
      <p>
        A window with a blinking cursor will appear. This is where you&apos;ll
        paste commands for the rest of this guide.
      </p>

      <h2>Step 2: Install Homebrew</h2>
      <p>
        Homebrew is a package manager for macOS — it makes installing developer
        tools easy. Paste this command into Terminal and press Enter:
      </p>
      <pre>
        <code>{`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`}</code>
      </pre>
      <p>
        It will ask for your Mac password (the one you use to log in). When you
        type it, nothing will appear on screen — that&apos;s normal. Just type
        it and press Enter.
      </p>
      <p>
        This can take 5–10 minutes. When it finishes, it may show instructions
        to add Homebrew to your PATH. If so, copy and paste those commands too.
      </p>
      <p>Verify it worked:</p>
      <pre>
        <code>brew --version</code>
      </pre>
      <p>
        You should see something like <code>Homebrew 4.x.x</code>.
      </p>

      <h2>Step 3: Install Git, Node.js, and Python</h2>
      <p>Edward needs these three tools. Install them all at once:</p>
      <pre>
        <code>brew install git node python@3.11</code>
      </pre>
      <p>This takes a few minutes. When it finishes, verify each one:</p>
      <pre>
        <code>{`git --version      # Should show git version 2.x+
node --version     # Should show v18+ or v20+
python3 --version  # Should show Python 3.11+`}</code>
      </pre>

      <h2>Step 4: Get the Edward Code</h2>
      <p>
        &quot;Cloning&quot; a repository means downloading a copy of the code to
        your computer. Run these commands to clone Edward to your Desktop:
      </p>
      <pre>
        <code>{`cd ~/Desktop
git clone https://github.com/ben4mn/meet-edward.git
cd meet-edward`}</code>
      </pre>
      <p>
        You should now have a <code>meet-edward</code> folder on your Desktop.
      </p>

      <h2>Step 5: Run the Setup Script</h2>
      <p>
        Edward comes with a setup script that installs everything it needs —
        PostgreSQL (the database), Python packages, and frontend dependencies.
      </p>
      <pre>
        <code>./setup.sh</code>
      </pre>
      <p>
        This will take a few minutes. You&apos;ll see a lot of output scrolling
        by — that&apos;s normal. Wait until you see a message indicating setup
        is complete.
      </p>
      <p>
        <strong>If you see &quot;Permission denied&quot;:</strong>
      </p>
      <pre>
        <code>{`chmod +x setup.sh
./setup.sh`}</code>
      </pre>

      <h2>Step 6: Get an Anthropic API Key</h2>
      <p>
        Edward uses Claude (made by Anthropic) as his brain. You need an API key
        to connect them.
      </p>
      <ol>
        <li>
          Go to{" "}
          <a
            href="https://console.anthropic.com"
            target="_blank"
            rel="noopener noreferrer"
          >
            console.anthropic.com
          </a>
        </li>
        <li>Create an account or sign in</li>
        <li>
          Go to <strong>API Keys</strong> in the left sidebar
        </li>
        <li>
          Click <strong>Create Key</strong>
        </li>
        <li>
          Copy the key — it starts with <code>sk-ant-</code>
        </li>
      </ol>
      <p>
        You&apos;ll need to add a payment method. Typical personal usage costs a
        few cents per conversation — well under $5/month for most people.
      </p>

      <h2>Step 7: Add Your API Key</h2>
      <p>
        Edward stores its settings in a file called <code>.env</code> (short for
        &quot;environment&quot;). Open it in TextEdit:
      </p>
      <pre>
        <code>open -e backend/.env</code>
      </pre>
      <p>
        Find the line that says <code>ANTHROPIC_API_KEY=</code> and paste your
        key after the equals sign:
      </p>
      <pre>
        <code>ANTHROPIC_API_KEY=sk-ant-your-key-here</code>
      </pre>
      <p>Save the file (Cmd + S) and close TextEdit.</p>

      <h2>Step 8: Start Edward</h2>
      <p>Back in Terminal, make sure you&apos;re in the meet-edward folder, then run:</p>
      <pre>
        <code>./restart.sh</code>
      </pre>
      <p>
        You should see output indicating both the backend and frontend are
        starting. Wait about 10 seconds for everything to initialize.
      </p>

      <h2>Step 9: Open in Browser</h2>
      <p>
        Open your web browser (Safari, Chrome, etc.) and go to:
      </p>
      <pre>
        <code>http://localhost:3000</code>
      </pre>
      <p>
        <code>localhost</code> means &quot;this computer&quot; — Edward is
        running right here on your Mac, not on someone else&apos;s server.
      </p>
      <ol>
        <li>
          You&apos;ll be asked to <strong>set a password</strong> — this
          protects your Edward instance
        </li>
        <li>
          After logging in, type a message like{" "}
          <strong>&quot;Hi, who are you?&quot;</strong>
        </li>
        <li>
          Edward should respond. If he does, you&apos;re all set!
        </li>
      </ol>

      <hr />

      <h2>Why Does My Mac Need to Stay On?</h2>
      <p>
        Edward runs on your Mac. When your Mac sleeps or shuts down, Edward
        stops too. This is normal — he&apos;ll pick up right where he left off
        when you restart.
      </p>
      <p>To start Edward again after a reboot:</p>
      <pre>
        <code>{`cd ~/Desktop/meet-edward
./restart.sh`}</code>
      </pre>

      <h2>Accessing Edward from Your Phone</h2>
      <p>
        If your phone is on the same Wi-Fi network as your Mac, you can access
        Edward from your phone&apos;s browser.
      </p>
      <p>First, find your Mac&apos;s local IP address:</p>
      <pre>
        <code>ipconfig getifaddr en0</code>
      </pre>
      <p>
        This will print something like <code>192.168.1.42</code>. On your
        phone, open a browser and go to:
      </p>
      <pre>
        <code>http://192.168.1.42:3000</code>
      </pre>
      <p>(Use the actual number your Mac showed, not the example above.)</p>

      <h2>Save as Home Screen App (PWA)</h2>
      <p>
        Edward works as a Progressive Web App — you can save it to your
        phone&apos;s home screen for an app-like experience.
      </p>
      <h3>iPhone (Safari)</h3>
      <ol>
        <li>Open Edward in Safari</li>
        <li>
          Tap the <strong>Share</strong> button (square with arrow)
        </li>
        <li>
          Scroll down and tap <strong>Add to Home Screen</strong>
        </li>
      </ol>
      <h3>Android (Chrome)</h3>
      <ol>
        <li>Open Edward in Chrome</li>
        <li>
          Tap the <strong>three-dot menu</strong> (top right)
        </li>
        <li>
          Tap <strong>Add to Home screen</strong>
        </li>
      </ol>

      <h2>Accessing Edward from Anywhere</h2>
      <p>
        By default, Edward is only accessible on your local network. To access
        him from anywhere (outside your home), you can use a tunnel service.
      </p>
      <h3>Cloudflare Tunnel (recommended, free)</h3>
      <pre>
        <code>{`brew install cloudflared
cloudflared tunnel --url http://localhost:3000`}</code>
      </pre>
      <p>
        This gives you a public URL like{" "}
        <code>https://random-name.trycloudflare.com</code> that works from
        anywhere. The tunnel runs as long as your Terminal window is open.
      </p>
      <h3>ngrok (alternative)</h3>
      <pre>
        <code>{`brew install ngrok
ngrok http 3000`}</code>
      </pre>

      <h2>Common Issues</h2>

      <h3>&quot;brew: command not found&quot;</h3>
      <p>
        Homebrew wasn&apos;t added to your PATH. Close Terminal, open a new one,
        and try again. If it still doesn&apos;t work, re-run the Homebrew
        install command from Step 2.
      </p>

      <h3>&quot;Permission denied&quot;</h3>
      <p>The script needs to be marked as executable:</p>
      <pre>
        <code>{`chmod +x setup.sh restart.sh
./setup.sh`}</code>
      </pre>

      <h3>Port already in use</h3>
      <p>
        Something else is using port 3000 or 8000. Find and stop it:
      </p>
      <pre>
        <code>{`lsof -i :3000
lsof -i :8000`}</code>
      </pre>
      <p>
        The output will show a PID number. Stop the process with{" "}
        <code>kill &lt;PID&gt;</code>.
      </p>

      <h3>API key errors</h3>
      <p>
        Double-check that your <code>backend/.env</code> file has the correct
        key with no extra spaces or quotes. The line should look exactly like:
      </p>
      <pre>
        <code>ANTHROPIC_API_KEY=sk-ant-your-actual-key</code>
      </pre>

      <h3>PostgreSQL not running</h3>
      <p>If Edward can&apos;t connect to the database:</p>
      <pre>
        <code>{`brew services list          # Check if postgresql is running
brew services start postgresql@16  # Start it`}</code>
      </pre>

      <h3>Checking logs</h3>
      <p>If something isn&apos;t working, check the logs for error messages:</p>
      <pre>
        <code>{`cat /tmp/edward-backend.log
cat /tmp/edward-frontend.log`}</code>
      </pre>

      <hr />
      <p>
        Once Edward is running, head to the{" "}
        <a href="/docs/configuration">Configuration</a> guide to customize
        your setup.
      </p>
    </DocsContent>
  );
}
