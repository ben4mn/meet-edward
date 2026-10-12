import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { Screenshot } from "../../../components/docs/Screenshot";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "The App — Edward Docs",
  description:
    "Edward's app: Today as the front door, Chat with a work rail that shows his reasoning and tools live, voice memos and live voice, the Edward tab, and push notifications with quiet hours.",
  alternates: { canonical: "/docs/app" },
  openGraph: {
    title: "The App — Edward Docs",
    description:
      "Today, Chat with a live work rail, voice, the Edward tab and push notifications.",
    url: "/docs/app",
  },
};

export default function AppPage() {
  return (
    <DocsContent>
      <h1>The app</h1>
      <p className="subtitle">
        Three tabs, three questions: what needs me, let’s talk, and what is
        Edward up to.
      </p>
      <p>
        Edward’s app is a progressive web app served by your Mac on port
        3000. Open it in any browser, or install it on your phone’s home
        screen (that needs an <code>https://</code> address; see{" "}
        <Link href="/docs/getting-started">Quick start</Link>). The same
        Edward, with the same memory and conversations, is behind every
        surface: the app, voice, iMessage, the widget and the screens.
      </p>

      <h2>Today</h2>
      <p>
        The front door, and where the app opens. In order:
      </p>
      <ul>
        <li>
          <strong>What needs you</strong>, each with its action on the card:
          a message thread waiting on you, an event about to start, a send
          held for your approval, a note Edward left, a late pet dose. Cards
          never flood: one per thread, one per note title.
        </li>
        <li>
          <strong>Today’s digest</strong>, and a short line from Edward that
          only the real signals support.
        </li>
        <li>
          <strong>What I did</strong>: what he did since last night, grouped
          by part of the day. What he chose to do on his own links to its
          journal entry.
        </li>
        <li>
          <strong>Your conversations.</strong>
        </li>
      </ul>
      <Screenshot
        src="/screenshots/today-desktop.png"
        alt="Edward's Today page: late pet doses to log, a call starting soon, a reply held for approval, and a list of what he did today"
        width={1600}
        height={1000}
        caption="Today: what needs you, with the action on each card, and what he did. (Invented data from the repository's UX harness.)"
      />
      <p>
        A send is never a keyboard shortcut: it goes out from your number, so
        it is always a deliberate tap, and the card says “Sent” only when the
        message was delivered.
      </p>

      <h2>Chat</h2>
      <ul>
        <li>
          <strong>The work rail.</strong> Above each answer, one timeline of
          what he did to get there, live: memory searches, each reasoning
          step by its heading, every tool call in plain words (“Opened
          example.com”, “Searched memory for …”, “Texted Parker”), code and
          its output, plans. When the answer starts, it folds into a receipt
          (“Thought for 14s · searched memory · opened 2 pages”), and a tap
          reopens it. His thinking and tool use are always visible; that is a
          design rule, not a debug option.
        </li>
        <li>
          <strong>Yours and Edward’s.</strong> The sidebar keeps your
          conversations apart from Edward’s own threads: his journal entries
          (led by what he did on his own), message threads he read, scheduled
          events.
        </li>
        <li>
          <strong>Drafts and attachments</strong> stay with their thread;
          what you type while he is answering waits in a queue, and Stop
          halts the turn in flight.
        </li>
        <li>
          <strong>Links</strong>: <code>/chat?c=&lt;id&gt;</code> is a
          conversation, <code>?prompt=</code> fills the composer without
          sending, <code>?voice=1</code> opens live voice.
        </li>
      </ul>

      <Screenshot
        src="/screenshots/chat-desktop.png"
        alt="A chat with Edward: his work rail lists a memory search, a document search, a page he couldn't read and a web search, above his answer with a cost table"
        width={1600}
        height={1000}
        caption="The work rail: every step he took, including the one that failed."
      />

      <h2>Voice</h2>
      <p>
        With <code>ELEVENLABS_API_KEY</code> (and{" "}
        <code>ELEVENLABS_VOICE_ID</code> for the voice he speaks in) and the
        Voice skill on:
      </p>
      <ul>
        <li>
          <strong>Voice memos</strong>: the mic button records a memo, posted
          as a playable attachment with its transcript, or transcribed into
          the text box.
        </li>
        <li>
          <strong>Live voice</strong>: a hands-free (or hold-to-talk)
          conversation. Edward streams captions as you speak, answers out
          loud, says a short bridge line when he needs a moment, and stops
          when you talk over him. A long task hands off (“I’ll keep working on
          that”) and carries on in the background.
        </li>
        <li>
          <strong>Mic sensitivity</strong> has three levels; the default gates
          out the room’s background noise, such as a TV.
        </li>
      </ul>
      <p>
        Spoken turns use the <code>voice</code> route (Sonnet 5.5 at low
        effort) for a fast first word. Audio goes to ElevenLabs for speech to
        text and back.
      </p>

      <h2>The Edward tab</h2>
      <p>
        What he knows, watches and does. It opens on “Edward now”: him, his
        status and the start of his own state page, then five rooms:
      </p>
      <table>
        <thead>
          <tr>
            <th>Room</th>
            <th>What is in it</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Mind</td>
            <td>His thoughts and wakes, the journal, the model, skills, MCP servers</td>
          </tr>
          <tr>
            <td>Memory</td>
            <td>
              Memories (with where each came from), documents, files,
              databases, and Dreaming: how he tidies his memory, and any “Which
              is right?” question
            </td>
          </tr>
          <tr>
            <td>Attention</td>
            <td>Listening (messages and mail), notifications, proactivity, Gmail, Alexa</td>
          </tr>
          <tr>
            <td>Work</td>
            <td>Events and reminders, workers, his wishlist, usage (the day’s spend)</td>
          </tr>
          <tr>
            <td>House</td>
            <td>Locale (your time zone), the hub’s photos</td>
          </tr>
        </tbody>
      </table>
      <Screenshot
        src="/screenshots/edward-tab-desktop.png"
        alt="The Edward tab: Edward's status, what he knows, watches and does, and the five rooms"
        width={1600}
        height={1000}
        caption="The Edward tab: what he knows, watches and does, and the five rooms."
      />
      <p>
        Every status line comes from a real signal. Edward’s animation makes
        the same promise: he looks busy only when something is running.
      </p>

      <h2>Push notifications</h2>
      <ul>
        <li>
          Web Push to the devices where you allowed it, once you set VAPID
          keys and turn on the Push Notifications skill.
        </li>
        <li>
          Everything Edward sends on his own goes through one door: quiet
          hours (22:00–07:00 by default) hold it until morning, except a late
          pet dose or travel closing within three hours; he follows up once,
          never twice; and nothing secret goes on your lock screen.
        </li>
        <li>
          Every push is logged with where its tap leads, and the tap goes
          there.
        </li>
        <li>
          Notes that can wait land on Today instead of buzzing your phone.
        </li>
      </ul>

      <h2>A menu-bar companion</h2>
      <p>
        <code>companion/EdwardBar</code> puts Edward in the menu bar of any
        Mac you use. It tells him you are at that Mac and whether you are
        active, idle or away (never what is on your screen; sharing the
        frontmost app’s name is off by default), and opens the chat. Build
        notes are in{" "}
        <a href={`${GITHUB_BLOB}/companion/EdwardBar/README.md`}>
          its README
        </a>
        .
      </p>
    </DocsContent>
  );
}
