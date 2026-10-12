import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "The Mind — Edward Docs",
  description:
    "How Edward thinks when nobody is talking to him: wakes, follow-through on promises, a silent nightly, one budgeted block of free time, a thought journal you can mark right or wrong, and opt-in noticing that speaks once.",
  alternates: { canonical: "/docs/mind" },
  openGraph: {
    title: "The Mind — Edward Docs",
    description:
      "Wakes, follow-through, the nightly, free time, the thought journal and opt-in noticing.",
    url: "/docs/mind",
  },
};

export default function MindPage() {
  return (
    <DocsContent>
      <h1>The mind</h1>
      <p className="subtitle">
        What Edward does when nobody is talking to him, and how you can tell.
      </p>

      <h2>Proactivity is noticing, not a schedule</h2>
      <p>
        An earlier Edward ran on a timetable: a morning briefing, meeting prep,
        nudges about open loops, a weekly pattern report. On the install this
        release comes from, almost none of it was read. What was valued were
        the catches: a deadline closing, two messages that disagreed, someone
        waiting only on its owner. So Edward now wakes for a reason, does what
        that reason needs, and otherwise stays quiet.
      </p>

      <h2>What wakes him</h2>
      <table>
        <thead>
          <tr>
            <th>Wake</th>
            <th>When</th>
            <th>What it may do</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><strong>Follow-through</strong></td>
            <td>A follow-up he scheduled, usually because he promised to check back</td>
            <td>
              The checking, then tell you. A promise made in an iMessage chat
              is kept in that chat.
            </td>
          </tr>
          <tr>
            <td><strong>The nightly</strong></td>
            <td>03:00, skipped when nothing happened</td>
            <td>
              Close finished intentions, note what he learned, update his
              state page; on Sundays, review the week and run memory upkeep.
              Silent: at most one note, never a push or a message.
            </td>
          </tr>
          <tr>
            <td><strong>Free time</strong></td>
            <td>13:00, once a day</td>
            <td>
              Whatever he chooses for his screen and his own projects, about
              $0.50 at most. It reaches nobody.
            </td>
          </tr>
          <tr>
            <td><strong>Speaking up</strong></td>
            <td>The noticer found something worth a word (opt-in, below)</td>
            <td>Check it, then say it once.</td>
          </tr>
          <tr>
            <td><strong>The surfaces pulse</strong></td>
            <td>Every 15 minutes, only if something changed</td>
            <td>
              Decide what the widget and the screens show. See{" "}
              <Link href="/docs/surfaces">Widget, screen &amp; hub</Link>.
            </td>
          </tr>
        </tbody>
      </table>
      <p>
        A wake is an ordinary turn with the same 14 tools as a conversation;
        what it may do is set by its policy. A wake that reaches nobody can’t
        send or push however it tries, and a refused call comes back as an
        honest error the model can read. The <code>mind</code> route runs
        Sonnet 5.5, inside the daily spend cap and a $1 cap per turn.
      </p>
      <p>
        <code>@Edward</code> pings are not wakes: they have their own runner,
        which answers each one within the limits in{" "}
        <Link href="/docs/listening">Listening &amp; pings</Link>.
      </p>

      <h2>The thought journal</h2>
      <p>
        Edward needs no approval to act, but it must be obvious when he
        chooses to. A wake that does something outward (a message, a push, a
        note) or commits to something (scheduling, standing down) writes a
        journal entry:
      </p>
      <ul>
        <li><strong>Why I looked</strong></li>
        <li><strong>What I noticed</strong></li>
        <li><strong>What I did</strong>, one line per action, “sent” only when it was</li>
        <li><strong>Why</strong></li>
      </ul>
      <p>
        Each entry asks you: <strong>Right call? Yes / No.</strong> The next
        wake reads your verdicts. Entries lead Edward’s side of the chat
        sidebar, marked when he reached someone, and you can reply inside one
        to ask why: he answers from what he actually saw and did.
      </p>

      <h2>Noticing (opt-in)</h2>
      <p>
        With Listening on, Edward stores what arrives in your messages and
        mail. Whether he reads it for what matters is{" "}
        <code>EDWARD_PROACTIVITY</code>:
      </p>
      <table>
        <thead>
          <tr>
            <th>Value</th>
            <th>What happens</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><code>off</code> (default)</td>
            <td>
              Messages are stored, pings answered, promises kept, the nightly
              runs. Nothing is noticed.
            </td>
          </tr>
          <tr>
            <td><code>shadow</code></td>
            <td>
              The noticer and the gate run and record each decision, but he
              never speaks. Try this first.
            </td>
          </tr>
          <tr>
            <td><code>notice</code></td>
            <td>The whole path.</td>
          </tr>
        </tbody>
      </table>
      <p>The path:</p>
      <ol>
        <li>
          <strong>The noticer</strong> (a small model) reads each new batch
          of messages, with your own lines from every chat, and writes down
          private noticings: a deadline, a conflict between two facts, someone
          waiting only on you, a travel time closing, a password you pasted
          somewhere. Each must cite the messages it came from.
        </li>
        <li>
          <strong>The gate</strong>, plain code, decides: silent, hold, a
          note on Today, a push, or a reply in the thread. You are already on
          it? Silent. Just arrived? Wait 15 minutes. Said it before with
          nothing new? Silent. Only travel or household items closing within
          three hours may push, and quiet hours hold the rest until morning.
        </li>
        <li>
          <strong>The speaker</strong>, a wake, checks the noticing against
          the messages and says it once, through{" "}
          <code>tell_owner</code>.
        </li>
      </ol>
      <p>
        Your own instructions bind the gate (“don’t push me about the soccer
        chat”). What he learns from being ignored only shapes his advice to
        himself; it never silences a category.
      </p>

      <h2>How he reaches you</h2>
      <p>
        Everything Edward says to you on his own goes through one door,{" "}
        <code>tell_owner</code>: quiet hours (22:00–07:00 by default) held in
        code, except care alerts and travel closing within three hours; at
        most one follow-up per subject; an offer phrased as an offer
        (“Want me to reply? Not sent.”); no secret on a lock screen. The
        channel is <code>EDWARD_OWNER_CHANNEL</code>: a push (the default),
        your iMessage self-chat, or Edward’s own Twilio number (with those
        two, an urgent word is a text). A wake never messages you any other
        way: <code>send_message</code> refuses to.
      </p>
      <p>
        <strong>The late-dose alert</strong> is code, not a model: a timed
        pet medication on the hub left unlogged 45 minutes after its slot
        pushes once. It works with every other switch off and outside the
        spend cap.
      </p>

      <h2>Rules a wake keeps</h2>
      <ul>
        <li>
          A mention must be answered, unless one of your standing instructions
          says stay out.
        </li>
        <li>
          He doesn’t start or join other people’s conversations unasked:
          messages go out from your number, so he tells you instead.
        </li>
        <li>
          A wake can’t change or delete what you said, and is never given the
          tool that forgets.
        </li>
        <li>
          A failed wake is retried only when the failure was transient, and
          never after it reached someone or committed to something.
        </li>
      </ul>

      <h2>Watching him think</h2>
      <ul>
        <li>
          <code>GET /api/mind/now</code> is his live state (what is running,
          his last and next wake); the app’s Edward reflects it.
        </li>
        <li>Mind → Thoughts lists each wake, what it did, and your verdicts.</li>
        <li>
          <code>POST /api/mind/wake</code> wakes him by hand (it answers 202
          and runs in the background).
        </li>
      </ul>

      <h2>Before you leave him unattended</h2>
      <p>
        The repository has evaluation gates that run the real model over
        recorded situations on a test database, with every outward act a dry
        run: the judgement check (a mention, a standing instruction, a
        follow-through, the silent nightly; about $1–4), a ping check, and a
        noticer check to run before <code>EDWARD_PROACTIVITY</code> goes past{" "}
        <code>off</code>. See{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/testing.md`}>testing.md</a>.
      </p>

      <hr />
      <p>
        The long form:{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/mind.md`}>mind.md</a>,{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/noticing.md`}>noticing.md</a>{" "}
        and{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/push-and-notes.md`}>
          push-and-notes.md
        </a>
        .
      </p>
    </DocsContent>
  );
}
