import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Platform Support — Edward Docs",
  description:
    "Edward runs on macOS with Apple silicon only. Why Intel Macs, Linux and Windows are not supported, what needs macOS 14, and where a port would start.",
  alternates: { canonical: "/docs/platform-support" },
  openGraph: {
    title: "Platform Support — Edward Docs",
    description:
      "macOS on Apple silicon only: why, what needs macOS 14, and where a port would start.",
    url: "/docs/platform-support",
  },
};

export default function PlatformSupportPage() {
  return (
    <DocsContent>
      <h1>Platform support</h1>
      <p className="subtitle">macOS on Apple silicon, and nothing else, for now.</p>

      <table>
        <thead>
          <tr>
            <th>Platform</th>
            <th>Supported</th>
            <th>What happens</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>macOS 14 or later, Apple silicon</td>
            <td>Yes</td>
            <td>Everything works.</td>
          </tr>
          <tr>
            <td>An older macOS, Apple silicon</td>
            <td>Without calendar changes</td>
            <td>
              The EventKit helper needs macOS 14, so <code>setup.sh</code>{" "}
              warns and Edward runs without it: he can read Calendar and
              Reminders but not change them.
            </td>
          </tr>
          <tr>
            <td>Intel Macs, or a Rosetta (x86_64) shell</td>
            <td>No</td>
            <td><code>setup.sh</code> stops before installing anything.</td>
          </tr>
          <tr>
            <td>Linux, Windows (WSL)</td>
            <td>No</td>
            <td><code>setup.sh</code> stops on anything but macOS.</td>
          </tr>
          <tr>
            <td>Docker</td>
            <td>No</td>
            <td>The backend runs natively on macOS, never in a container.</td>
          </tr>
        </tbody>
      </table>

      <div className="note">
        <p>
          The March 2026 release described Linux and WSL as mostly working.
          This release does not: Edward’s own computer, his listeners and his
          follow-through all lean on macOS now.
        </p>
      </div>

      <h2>Why Apple silicon</h2>
      <p>
        Edward’s memory search uses a small local embedding model through
        sentence-transformers, which pulls in PyTorch. The pinned PyTorch (in{" "}
        <code>backend/constraints.txt</code>) publishes no x86_64 macOS wheel,
        so <code>setup.sh</code> stops on an Intel Mac, in a Rosetta shell, or
        with an x86_64 Python. That pin, not the Swift helpers, is what an
        Intel port would have to change.
      </p>

      <h2>Why macOS</h2>
      <ul>
        <li>
          <strong>His shell is sandboxed by Seatbelt</strong> (
          <code>sandbox-exec</code>). Without it the <code>bash</code> tool
          fails closed rather than running unsandboxed.
        </li>
        <li>
          <strong>He reads Apple’s own databases</strong>: Messages’{" "}
          <code>chat.db</code>, Mail’s Envelope Index, the Calendar and
          Reminders stores, Contacts. He sends iMessages through AppleScript
          and changes your calendar through EventKit.
        </li>
        <li>
          <strong>launchd keeps him running</strong>, and the macOS privacy
          grants (Full Disk Access, Automation, Calendars) decide what he may
          read.
        </li>
        <li>
          <strong>His media handling uses macOS tools</strong> (
          <code>sips</code>, <code>afconvert</code>, <code>avconvert</code>)
          and a Swift helper, not ffmpeg.
        </li>
      </ul>

      <h2>What a port would take</h2>
      <p>
        A port is a project, not a setting. The macOS seams are marked{" "}
        <code>ADAPT:</code> in the code and collected in{" "}
        <Link href="/docs/adapting">Adapting to your Mac</Link>, which has
        prompts for an AI assistant for the two pieces a Linux port would
        start with:
      </p>
      <ol>
        <li>
          <strong>A sandbox for the <code>bash</code> tool first</strong>{" "}
          (bubblewrap or firejail), rendered from the same rails table as the
          macOS profile and failing closed in the same way.
        </li>
        <li>
          <strong>systemd units</strong> in place of the LaunchAgents, with
          the same options as <code>scripts/install-launchagents.sh</code>.
        </li>
      </ol>
      <p>
        Everything that reads an Apple app (iMessage, Mail, Calendar,
        Reminders, Contacts, the screen) already degrades with an honest
        error when it can’t, so a port could leave those off.
      </p>

      <h2>Where the other pieces run</h2>
      <ul>
        <li>
          <strong>Your phone</strong>: the app is a PWA, so any modern phone
          browser works, over HTTPS (see{" "}
          <Link href="/docs/getting-started">Quick start</Link>).
        </li>
        <li>
          <strong>The household hub</strong>: any browser in kiosk mode on{" "}
          <code>/hub</code>; the reference install used a Raspberry Pi with a
          7&Prime; touchscreen (
          <a href={`${GITHUB_BLOB}/docs/pi-hub-setup.md`}>pi-hub-setup.md</a>
          ).
        </li>
        <li>
          <strong>The iPhone widget</strong>: the Scriptable app.
        </li>
        <li>
          <strong>Hands in your browser</strong>: Google Chrome with the
          unpacked Edward extension, on the same Mac.
        </li>
      </ul>
    </DocsContent>
  );
}
