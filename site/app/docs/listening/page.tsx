import type { Metadata } from "next";
import Link from "next/link";
import { DocsContent } from "../../../components/docs/DocsContent";
import { GITHUB_BLOB } from "../../../lib/docsNav";

export const metadata: Metadata = {
  title: "Listening & Pings — Edward Docs",
  description:
    "Edward reads iMessage, Mail.app and Gmail when you let him, and answers @Edward in any iMessage chat from your own account: who can ping him, what friends' pings may do, and how photos, PDFs and voice messages are understood.",
  alternates: { canonical: "/docs/listening" },
  openGraph: {
    title: "Listening & Pings — Edward Docs",
    description:
      "Reading iMessage, Mail and Gmail, and answering @Edward in any chat, with friends' limits.",
    url: "/docs/listening",
  },
};

export default function ListeningPage() {
  return (
    <DocsContent>
      <h1>Listening &amp; pings</h1>
      <p className="subtitle">
        What he reads, who can ask him things, and what he may do for them.
      </p>

      <div className="note caution">
        <p>
          <strong>Edward reads and sends iMessages as you.</strong> He uses
          the Apple ID that Messages on the host Mac is signed into, and his
          messages go out from your own account, signed as Edward. With
          Messages turned on, anyone can write <code>@Edward</code> in a chat
          with you and he answers there. Decide that you want this before you
          turn it on.
        </p>
      </div>

      <h2>Listening</h2>
      <p>
        Three listeners store what arrives, and do nothing else. Each is off
        until you turn it on in the Edward tab: Messages and Email under{" "}
        <strong>Attention → Listening → Configuration</strong>, Gmail under{" "}
        <strong>Attention → Gmail → Watch my inbox</strong>.
      </p>
      <table>
        <thead>
          <tr>
            <th>Listener</th>
            <th>How</th>
            <th>Needs</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>iMessage</td>
            <td>
              Reads <code>~/Library/Messages/chat.db</code> every 10 seconds
            </td>
            <td>
              Full Disk Access; Messages signed into your Apple ID;{" "}
              <code>EDWARD_OWNER_HANDLES</code>
            </td>
          </tr>
          <tr>
            <td>Mail.app</td>
            <td>Reads Mail’s own index for unread inbox mail</td>
            <td>Full Disk Access; Mail.app kept running</td>
          </tr>
          <tr>
            <td>Gmail</td>
            <td>Google’s API, with “Watch my inbox” on</td>
            <td>
              An OAuth client of your own (
              <a href={`${GITHUB_BLOB}/docs/gmail-setup.md`}>gmail-setup.md</a>)
            </td>
          </tr>
        </tbody>
      </table>
      <p>
        What is stored feeds the mind: Edward can read it when you ask (“what
        did Rowan say about Saturday?”), and, if you turn noticing on, he
        reads each batch for what matters (see{" "}
        <Link href="/docs/mind">The mind</Link>). A message thread waiting on
        you shows on Today as one card per thread, and “Done” clears it.
      </p>
      <p>
        Mail can never trigger him: anyone can type <code>@edward</code> in a
        subject line, so only an iMessage can ask Edward anything.
      </p>

      <h2>Pings</h2>
      <p>A ping is an iMessage that asks Edward something:</p>
      <ul>
        <li>
          <code>@Edward</code> from anyone in any chat (yours included)
        </li>
        <li>anything you write in your own self-chat</li>
        <li>a reply to one of Edward’s messages</li>
      </ul>
      <p>
        Each ping gets its own turn, with the chat’s recent lines, its people,
        the photos and PDFs in it, and the links in the message, and his
        answer goes back into that chat. Pings are recorded the moment the
        message is stored, and answered by their own runner, so they are
        never lost in a busy batch. A ping closes when his reply is confirmed
        delivered (read back from Messages), or when he records that he chose
        not to answer. If a send went out and something failed after it, he
        says he was cut off rather than answering twice.
      </p>
      <p>
        Your own pings carry your authority: what you tell him in your
        self-chat or in your own <code>@Edward</code> is remembered as yours.
        A promise he makes in a chat (“I’ll check at five”) is kept in that
        chat.
      </p>

      <h3>Friends’ pings</h3>
      <p>
        A friend’s <code>@Edward</code> is someone else’s text driving a turn
        on your Mac, so it gets less:
      </p>
      <ul>
        <li>
          <strong>No shell, file editor, Chrome or screens</strong>, and{" "}
          <code>look</code> only at what they sent or a link. Those act as you
          on your machine. <code>EDWARD_FRIENDS_FULL_TOOLSET=on</code> gives
          friends everything, at the risk that a message (or a page it points
          him at) drives a shell running as you.
        </li>
        <li>
          <strong>No new conversations</strong>: anything he sends goes into
          that chat, or one that addressed him in the last 24 hours. He never
          writes to someone who asked him nothing, and leaves no task or
          reminder behind.
        </li>
        <li>
          <strong>Limits</strong>: six turns an hour per chat, more than three
          friends’ pings in five minutes stops him there (and tells you), and
          friends’ turns spend Edward’s own share of the daily cap, never your
          reserve.
        </li>
        <li>
          <strong>Unknown numbers are held</strong> for you, with a note at
          most once a day per sender, unless the chat is a group with someone
          from your contacts in it.
        </li>
        <li>
          <strong>Your standing instructions come along</strong>: if you told
          him to stay out of a chat, he stands down there and tells you.
        </li>
      </ul>
      <p>
        What a friend’s ping can still do is read what Edward reads (your
        chats, memories and calendar) and answer from it. His prompt asks for
        discretion, but a prompt is not a wall: think about who can reach him
        before you turn Messages on.
      </p>

      <h3>Switches</h3>
      <ul>
        <li>
          <code>EDWARD_PINGS=off</code> stops the runner; pings are still
          recorded, and the mind answers mentions instead. To stop answering
          altogether, turn Messages off in Listening.
        </li>
        <li>
          <code>EDWARD_PING_FRIEND_TURNS_PER_HOUR</code> (6),{" "}
          <code>EDWARD_PING_CONCURRENCY</code> (2), and the iMessage skill,
          which gates reading and sending.
        </li>
        <li>
          <code>GET /api/health/report</code> counts how every ping ended, by
          who asked, and how fast he answered.
        </li>
      </ul>

      <h2>Photos, PDFs, voice messages and video</h2>
      <p>
        Attachments in a chat are understood, not skipped:
      </p>
      <ul>
        <li>
          Photos (HEIC, stickers, GIF frames) are converted with macOS’s{" "}
          <code>sips</code> and seen as pictures; a ping turn gets up to six
          images and one PDF inline.
        </li>
        <li>PDFs are read whole (up to 15 MB and 100 pages).</li>
        <li>
          Voice messages and videos are heard through ElevenLabs’ Scribe
          (once voice is set up) and seen through a small Swift helper built
          by <code>setup.sh</code> (frames from a video).
        </li>
        <li>
          Links are read first, from public addresses only, and never a
          sign-in or confirmation link, or one from an unknown number.
        </li>
      </ul>
      <p>
        Images leave saved history as a line with their reference, so old
        conversations stay small. <code>EDWARD_MEDIA=off</code> brings back
        plain placeholders.
      </p>

      <h2>Other ways in</h2>
      <ul>
        <li>
          <strong>Twilio</strong> gives Edward a number of his own for SMS and
          WhatsApp. Webhooks are refused without a valid Twilio signature, and
          only your own numbers get an answer; anyone else’s text is held,
          unanswered.
        </li>
        <li>
          <strong>The WhatsApp bridge</strong> (whatsapp-mcp) reads and sends
          WhatsApp as you, once you run and pair it.
        </li>
        <li>
          <strong>Alexa</strong> can ask Edward through a custom skill, but
          inbound requests are not signature-verified yet: see{" "}
          <Link href="/docs/guard-rails">Guard rails</Link>.
        </li>
      </ul>
      <p>
        Want Edward on an Apple ID of his own, so people text him rather than
        you? That is a project;{" "}
        <Link href="/docs/adapting">Adapting to your Mac</Link> has a prompt
        for it.
      </p>

      <hr />
      <p>
        The long form:{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/heartbeat.md`}>heartbeat.md</a>,{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/pings.md`}>pings.md</a> and{" "}
        <a href={`${GITHUB_BLOB}/docs/architecture/media.md`}>media.md</a>.
      </p>
    </DocsContent>
  );
}
