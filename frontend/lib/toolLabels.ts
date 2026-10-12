// What a tool call reads as in the chat's work rail (2026-09-26).
//
// The backend names tools for the model ("browser_open",
// "get_recent_messages"); the owner reads what Edward *did*: 'Opened
// example.com', 'Searched “kayak weather”', 'Texted Parker'. One exact-name
// table covers every tool the backend defines (a test reads
// backend/services/**/tools and fails on a new tool without a label), a few
// keyword rules cover MCP tools (Apple's "calendar", WhatsApp's
// "whatsapp_*"), and anything else is its name in words.
//
// The argument comes from the stream's `args_summary` (and the history
// trace's), which the backend writes as `"quoted text"`, a URL, `to: Parker`
// or `id: 42` (services/graph/streaming.py _summarize_tool_args). Pure — no
// React — so it runs under node --test; the rail maps `icon` to a lucide icon.
//
// The one toolset (2026-10-11) has action tools — browser, chrome, messages,
// memory, schedule, screen, tell_owner and the file editor — whose summary is
// `"{action}: {detail}"` ("open: https://…", 'search: "Biscuit's meds"'). Each
// action has its own label (`ACTION_SPECS`); old tool names keep theirs, for
// the receipts already in history.

import { EXECUTION_TOOLS, isOutwardTool } from "./turnModel";

export type ToolIcon =
  | "alarm"
  | "archive"
  | "bell"
  | "bot"
  | "brain"
  | "calendar"
  | "calendar-plus"
  | "calendar-x"
  | "code"
  | "database"
  | "eye"
  | "file"
  | "file-pen"
  | "file-plus"
  | "file-search"
  | "files"
  | "folder"
  | "globe"
  | "hand"
  | "history"
  | "image"
  | "inbox"
  | "keyboard"
  | "layout"
  | "lightbulb"
  | "link"
  | "list-checks"
  | "mail"
  | "map-pin"
  | "messages"
  | "monitor"
  | "note"
  | "pointer"
  | "search"
  | "send"
  | "server"
  | "sparkles"
  | "speaker"
  | "tag"
  | "target"
  | "terminal"
  | "trash"
  | "user"
  | "users"
  | "wrench";

/** How `{arg}` renders in a label. */
type ArgKind = "quote" | "host" | "to" | "plain" | "file" | "look";

interface Tally {
  /** Calls with the same key fold into one receipt phrase. */
  key: string;
  one: string;
  /** `{n}` is the count. */
  many: string;
}

/** Sends name their recipients in the receipt ("texted Parker and Jordan"). */
interface SendTally {
  key: string;
  verb: string;
  /** No recipient known. */
  one: string;
  many: string;
}

interface Spec {
  icon: ToolIcon;
  /** Past tense; `{arg}` is the argument. */
  done: string;
  /** Present participle for the live header, ending in "…". */
  live: string;
  /** `done` / `live` when the call has no usable argument. */
  bare?: string;
  bareLive?: string;
  arg?: ArgKind;
  tally?: Tally | SendTally;
  /** Cut a chip to this many characters (a shell command). */
  chipMax?: number;
}

export interface ToolDescription {
  icon: ToolIcon;
  /** 'Opened example.com' */
  title: string;
  /** 'Opening example.com…' */
  live: string;
  /** When the call failed: 'Couldn't open nws.gov' — never the past tense of success. */
  failed: string;
  /** The argument when the label does not already say it. */
  chips: string[];
  /** A send, a push, a note, a hosted page: Edward reached outward. */
  outward: boolean;
}

// ---------------------------------------------------------------- tallies
const T = (key: string, one: string, many: string): Tally => ({ key, one, many });
const PAGES_OPENED = T("opened", "opened a page", "opened {n} pages");
const PAGES_READ = T("page-read", "read a page", "read {n} pages");
const WEB = T("web", "searched the web", "searched the web {n} times");
const MEMORY = T("memory", "searched memory", "searched memory");
const REMEMBERED = T("remembered", "remembered something", "remembered {n} things");
const NOTE = T("note", "left you a note", "left you {n} notes");
const PUSH = T("push", "sent you a notification", "sent you {n} notifications");
const SCHEDULED = T("scheduled", "scheduled something", "scheduled {n} things");
const DOCS_READ = T("doc-read", "read a document", "read {n} documents");
const THREADS = T("threads", "read messages", "read {n} threads");
const PUBLISHED = T("published", "published a page", "published {n} pages");
const BROWSER = T("browser", "used the browser", "used the browser");
const TYPED = T("typed", "typed into a page", "typed into a page {n} times");
const PYTHON = T("python", "ran Python", "ran Python {n} times");
const JS = T("js", "ran JavaScript", "ran JavaScript {n} times");
const SHELL = T("shell", "ran a command", "ran {n} commands");
const SQL = T("sql", "ran a query", "ran {n} queries");
const PLAN = T("plan", "made a plan", "made a plan");
const HELPERS = T("helpers", "asked a helper", "asked {n} helpers");
const LOOKED = T("looked", "looked at something", "looked at {n} things");
const DOWNLOADED = T("downloaded", "downloaded a file", "downloaded {n} files");
const FILES_EDITED = T("files", "worked on a file", "worked on {n} files");
const TEXTED: SendTally = { key: "texted", verb: "texted", one: "sent a text", many: "sent {n} texts" };
const WHATSAPPED: SendTally = { key: "whatsapp", verb: "messaged", one: "sent a WhatsApp", many: "sent {n} WhatsApps" };
const EMAILED: SendTally = { key: "emailed", verb: "emailed", one: "sent an email", many: "sent {n} emails" };
const MAIL_READ = T("mail-read", "read an email", "read {n} emails");

function S(icon: ToolIcon, done: string, live: string, opts: Omit<Spec, "icon" | "done" | "live"> = {}): Spec {
  return { icon, done, live, ...opts };
}

// ---------------------------------------------------------------- the table
const SPECS: Record<string, Spec> = {
  // Memory
  remember: S("brain", "Remembered", "Remembering…", { tally: REMEMBERED }),
  remember_update: S("brain", "Updated a memory", "Updating a memory…", { tally: REMEMBERED }),
  remember_forget: S("brain", "Let a memory go", "Letting a memory go…"),
  remember_search: S("brain", "Searched memory for {arg}", "Searching memory for {arg}…", {
    bare: "Searched memory",
    bareLive: "Searching memory…",
    tally: MEMORY,
  }),
  update_memory_confidence: S("brain", "Adjusted how sure I am of a memory", "Adjusting a memory…"),
  graph_memory_search: S("brain", "Searched connections for {arg}", "Searching connections for {arg}…", {
    bare: "Searched connections",
    bareLive: "Searching connections…",
    tally: MEMORY,
  }),
  how_do_i_know: S("history", "Traced where that came from", "Tracing where that came from…"),
  // One of his own intentions, done or dropped (2026-09-27); the chip is its
  // outcome (streaming._summarize_tool_args).
  close_intention: S("brain", "Closed an intention", "Closing an intention…"),
  forget_about: S("trash", "Looked at what to forget about {arg}", "Looking at what to forget…", {
    bare: "Looked at what to forget",
    arg: "plain",
  }),

  // Messages and people
  send_imessage: S("send", "Texted {arg}", "Texting {arg}…", { bare: "Sent a text", bareLive: "Sending a text…", arg: "to", tally: TEXTED }),
  send_sms: S("send", "Texted {arg}", "Texting {arg}…", { bare: "Sent a text", bareLive: "Sending a text…", arg: "to", tally: TEXTED }),
  // The one send since 2026-10-11 (iMessage from the owner's number); the old
  // routed send_message (Twilio/WhatsApp) read "Messaged".
  send_message: S("send", "Texted {arg}", "Texting {arg}…", {
    bare: "Sent a text",
    bareLive: "Sending a text…",
    arg: "to",
    tally: TEXTED,
  }),
  send_whatsapp: S("send", "Messaged {arg} on WhatsApp", "Messaging {arg} on WhatsApp…", {
    bare: "Sent a WhatsApp",
    bareLive: "Sending a WhatsApp…",
    arg: "to",
    tally: WHATSAPPED,
  }),
  stand_down: S("hand", "Stepped back from the chat", "Stepping back…"),
  get_recent_messages: S("messages", "Read messages with {arg}", "Reading messages with {arg}…", {
    bare: "Read recent messages",
    bareLive: "Reading recent messages…",
    arg: "plain",
    tally: THREADS,
  }),
  read_imessage_thread: S("messages", "Read the thread", "Reading the thread…", { tally: THREADS }),
  list_chat_participants: S("users", "Checked who's in the chat", "Checking who's in the chat…"),
  get_imessage_attachment: S("image", "Looked at an attachment", "Looking at an attachment…"),
  lookup_contact: S("user", "Looked up {arg}", "Looking up {arg}…", { bare: "Looked up a contact", arg: "plain" }),
  lookup_phone: S("user", "Looked up {arg}", "Looking up {arg}…", { bare: "Looked up a number", arg: "plain" }),
  find_friend_location: S("map-pin", "Checked where {arg} is", "Checking where {arg} is…", {
    bare: "Checked a friend's location",
    bareLive: "Checking a location…",
    arg: "plain",
  }),
  list_friend_locations: S("map-pin", "Checked everyone's location", "Checking locations…"),

  // Code and data
  execute_code: S("code", "Ran Python", "Running Python…", { tally: PYTHON }),
  execute_javascript: S("code", "Ran JavaScript", "Running JavaScript…", { tally: JS }),
  execute_sql: S("database", "Ran a query", "Running a query…", { tally: SQL }),
  execute_shell: S("terminal", "Ran a command", "Running a command…", { tally: SHELL }),
  list_sandbox_files: S("folder", "Listed the sandbox", "Listing the sandbox…"),
  read_sandbox_file: S("file", "Read {arg}", "Reading {arg}…", { bare: "Read a sandbox file", arg: "plain" }),
  create_persistent_db: S("database", "Created the {arg} database", "Creating a database…", { bare: "Created a database", arg: "plain" }),
  query_persistent_db: S("database", "Queried {arg}", "Querying {arg}…", { bare: "Queried a database", arg: "plain", tally: SQL }),
  list_persistent_dbs: S("database", "Listed databases", "Listing databases…"),
  delete_persistent_db: S("trash", "Deleted the {arg} database", "Deleting a database…", { bare: "Deleted a database", arg: "plain" }),

  // The web
  web_search: S("search", "Searched {arg}", "Searching {arg}…", { bare: "Searched the web", bareLive: "Searching the web…", tally: WEB }),
  webSearch: S("search", "Searched {arg}", "Searching {arg}…", { bare: "Searched the web", bareLive: "Searching the web…", tally: WEB }),
  fetch_page_content: S("globe", "Read {arg}", "Reading {arg}…", { bare: "Read a page", bareLive: "Reading a page…", arg: "host", tally: PAGES_READ }),

  // Plans
  create_plan: S("list-checks", "Made a plan", "Making a plan…", { tally: PLAN }),
  update_plan_step: S("list-checks", "Updated the plan", "Updating the plan…", { tally: PLAN }),
  edit_plan: S("list-checks", "Changed the plan", "Changing the plan…", { tally: PLAN }),
  complete_plan: S("list-checks", "Finished the plan", "Finishing the plan…", { tally: PLAN }),

  // Time
  schedule_event: S("calendar-plus", "Scheduled {arg}", "Scheduling {arg}…", { bare: "Scheduled something", bareLive: "Scheduling…", tally: SCHEDULED }),
  list_scheduled_events: S("calendar", "Checked what's scheduled", "Checking what's scheduled…"),
  cancel_scheduled_event: S("calendar-x", "Cancelled a scheduled event", "Cancelling a scheduled event…"),
  wake_me: S("alarm", "Set a time to look again", "Setting a time to look again…"),

  // Mail
  review_heartbeat: S("inbox", "Reviewed recent messages", "Reviewing recent messages…"),
  search_inbox: S("mail", "Searched your inbox", "Searching your inbox…"),
  read_email: S("mail", "Read an email", "Reading an email…"),
  delete_email: S("trash", "Deleted an email", "Deleting an email…"),
  move_to_junk: S("archive", "Moved an email to junk", "Moving an email to junk…"),
  get_email_actions: S("mail", "Checked what I've done with email", "Checking email history…"),
  get_email_sender_prefs: S("mail", "Checked your email preferences", "Checking email preferences…"),
  set_email_sender_pref: S("mail", "Set an email preference", "Setting an email preference…"),
  recommend_unsubscribes: S("mail", "Found lists to unsubscribe from", "Looking for lists to unsubscribe from…"),
  approve_unsubscribe: S("mail", "Unsubscribed", "Unsubscribing…"),
  skip_unsubscribe: S("mail", "Kept a subscription", "Keeping a subscription…"),
  search_gmail: S("mail", "Searched Gmail for {arg}", "Searching Gmail for {arg}…", { bare: "Searched Gmail", bareLive: "Searching Gmail…" }),
  read_gmail_message: S("mail", "Read an email", "Reading an email…"),
  delete_gmail_message: S("trash", "Deleted an email", "Deleting an email…"),
  archive_gmail_message: S("archive", "Archived an email", "Archiving an email…"),
  label_gmail_message: S("tag", "Labelled an email", "Labelling an email…"),
  get_gmail_unsub_info: S("mail", "Checked how to unsubscribe", "Checking how to unsubscribe…"),

  // Reaching the owner
  send_push_notification: S("bell", "Sent you a notification", "Sending you a notification…", { tally: PUSH }),
  leave_note: S("note", "Left you a note", "Leaving you a note…", { tally: NOTE }),
  suggest_idea: S("lightbulb", "Suggested an idea", "Suggesting an idea…"),

  // Documents
  save_document: S("file-plus", "Saved {arg}", "Saving {arg}…", { bare: "Saved a document", bareLive: "Saving a document…" }),
  read_document: S("file", "Read a document", "Reading a document…", { tally: DOCS_READ }),
  edit_document: S("file-pen", "Edited {arg}", "Editing {arg}…", { bare: "Edited a document", bareLive: "Editing a document…" }),
  search_documents: S("file-search", "Searched documents for {arg}", "Searching documents for {arg}…", {
    bare: "Searched documents",
    bareLive: "Searching documents…",
  }),
  list_documents: S("files", "Listed documents", "Listing documents…"),
  delete_document: S("trash", "Deleted a document", "Deleting a document…"),

  // Goals
  set_goal: S("target", "Set a goal: {arg}", "Setting a goal…", { bare: "Set a goal" }),
  update_goal: S("target", "Updated a goal", "Updating a goal…"),
  list_goals: S("target", "Checked your goals", "Checking your goals…"),

  // Pages on the web
  create_hosted_page: S("link", "Published a page", "Publishing a page…", { tally: PUBLISHED }),
  update_hosted_page: S("link", "Updated a published page", "Updating a published page…", { tally: PUBLISHED }),
  delete_hosted_page: S("trash", "Took down a page", "Taking down a page…"),
  check_hosted_slug: S("link", "Checked a page address", "Checking a page address…"),

  // The widget and the screens
  update_widget: S("layout", "Updated your widget", "Updating your widget…"),
  get_widget_state_tool: S("layout", "Checked your widget", "Checking your widget…"),
  update_widget_code: S("layout", "Rewrote your widget", "Rewriting your widget…"),
  clear_widget_code: S("layout", "Reset your widget", "Resetting your widget…"),
  show_on_screen: S("monitor", "Put something on the screen", "Putting something on the screen…"),
  clear_screen: S("monitor", "Cleared the screen", "Clearing the screen…"),
  // Kept: old chat receipts still name it (the stage replaced it on 2026-09-28).
  set_screen_layout: S("monitor", "Rearranged the screen", "Rearranging the screen…"),
  compose_screen: S("layout", "Set the scene on the screen", "Setting the scene on the screen…"),
  say_on_screen: S("speaker", "Said something on the screen", "Saying something on the screen…"),
  reset_screen_layout: S("monitor", "Reset the screen", "Resetting the screen…"),
  bring_up_screen: S("monitor", "Brought up the screen", "Bringing up the screen…"),
  look_at_screen: S("eye", "Looked at the screen", "Looking at the screen…"),
  update_display_settings: S("monitor", "Adjusted the display", "Adjusting the display…"),

  // Chrome
  browser_tabs: S("globe", "Checked the open tabs", "Checking the open tabs…", { tally: BROWSER }),
  browser_open: S("globe", "Opened {arg}", "Opening {arg}…", { bare: "Opened a page", bareLive: "Opening a page…", arg: "host", tally: PAGES_OPENED }),
  browser_use_tab: S("globe", "Switched tabs", "Switching tabs…", { tally: BROWSER }),
  browser_read: S("eye", "Read the page", "Reading the page…", { tally: BROWSER }),
  browser_find: S("search", "Looked for {arg} on the page", "Looking for {arg} on the page…", {
    bare: "Looked through the page",
    bareLive: "Looking through the page…",
    tally: BROWSER,
  }),
  browser_click: S("pointer", "Clicked on the page", "Clicking…", { tally: BROWSER }),
  browser_type: S("keyboard", "Typed into the page", "Typing…", { tally: TYPED }),
  browser_key: S("keyboard", "Pressed {arg}", "Pressing {arg}…", { bare: "Pressed a key", bareLive: "Pressing a key…", arg: "plain", tally: BROWSER }),
  browser_scroll: S("pointer", "Scrolled the page", "Scrolling…", { tally: BROWSER }),
  browser_back: S("globe", "Went back", "Going back…", { tally: BROWSER }),
  browser_wait: S("history", "Waited for the page", "Waiting for the page…", { tally: BROWSER }),
  browser_fullscreen: S("monitor", "Changed full screen", "Changing full screen…", { tally: BROWSER }),
  browser_steps: S("pointer", "Worked through the page", "Working through the page…", { tally: BROWSER }),
  browser_done: S("globe", "Finished in the browser", "Finishing in the browser…", { tally: BROWSER }),

  // Files
  save_to_storage: S("folder", "Saved {arg} to your files", "Saving {arg}…", { bare: "Saved a file", bareLive: "Saving a file…", arg: "plain" }),
  list_storage_files: S("folder", "Listed your files", "Listing your files…"),
  get_storage_file_url: S("link", "Got a file link", "Getting a file link…"),
  read_storage_file: S("file", "Read a file", "Reading a file…"),
  tag_storage_file: S("tag", "Tagged a file", "Tagging a file…"),
  delete_storage_file: S("trash", "Deleted a file", "Deleting a file…"),

  // The wishlist
  wish_for_feature: S("sparkles", "Wished for {arg}", "Adding a wish…", { bare: "Added a wish" }),
  list_wishlist: S("sparkles", "Checked my wishlist", "Checking my wishlist…"),
  update_wish: S("sparkles", "Updated a wish", "Updating a wish…"),

  // Helpers
  spawn_worker: S("bot", "Asked a helper: {arg}", "Asking a helper…", { bare: "Asked a helper", tally: HELPERS }),
  check_worker: S("bot", "Checked on a helper", "Checking on a helper…"),
  list_workers: S("bot", "Checked the helpers", "Checking the helpers…"),
  cancel_worker: S("bot", "Stopped a helper", "Stopping a helper…"),
  wait_for_workers: S("bot", "Waited for the helpers", "Waiting for the helpers…"),
  send_to_worker: S("bot", "Messaged a helper", "Messaging a helper…"),
  spawn_cc_worker: S("code", "Had a helper write code", "A helper is writing code…", { tally: HELPERS }),
  start_claude_session: S("terminal", "Started a coding session", "Starting a coding session…"),

  // Tool servers
  search_mcp_servers: S("server", "Looked for new tools: {arg}", "Looking for new tools…", { bare: "Looked for new tools" }),
  add_mcp_server: S("server", "Installed {arg}", "Installing {arg}…", { bare: "Installed a tool", bareLive: "Installing a tool…", arg: "plain" }),
  list_custom_servers: S("server", "Checked my installed tools", "Checking my installed tools…"),
  remove_mcp_server: S("server", "Removed a tool", "Removing a tool…"),
  update_mcp_server: S("server", "Updated a tool", "Updating a tool…"),
  restart_mcp_server: S("server", "Restarted a tool", "Restarting a tool…"),

  // Alexa
  announce_on_alexa: S("speaker", "Announced on Alexa", "Announcing on Alexa…"),
  set_alexa_alarm: S("alarm", "Set an Alexa alarm", "Setting an Alexa alarm…"),
  play_sound_on_alexa: S("speaker", "Played a sound on Alexa", "Playing a sound on Alexa…"),
  get_alexa_devices: S("speaker", "Checked the Alexa devices", "Checking the Alexa devices…"),

  // Apple (apple-mcp names its tools by app)
  calendar: S("calendar", "Checked your calendar", "Checking your calendar…"),
  reminders: S("list-checks", "Checked your reminders", "Checking your reminders…"),
  notes: S("note", "Checked your notes", "Checking your notes…"),
  mail: S("mail", "Checked your mail", "Checking your mail…"),
  contacts: S("user", "Looked in your contacts", "Looking in your contacts…"),
  maps: S("map-pin", "Looked at the map", "Looking at the map…"),
  messages: S("messages", "Checked your messages", "Checking your messages…"),

  // WhatsApp's send (the rest of whatsapp_* reads as words)
  whatsapp_send_message: S("send", "Messaged {arg} on WhatsApp", "Messaging {arg} on WhatsApp…", {
    bare: "Sent a WhatsApp",
    bareLive: "Sending a WhatsApp…",
    arg: "to",
    tally: WHATSAPPED,
  }),

  // ---- the one toolset (2026-10-11); action tools fall back to these
  bash: S("terminal", "Ran a command", "Running a command…", { tally: SHELL, chipMax: 60 }),
  str_replace_based_edit_tool: S("file-pen", "Worked on a file", "Working on a file…", { tally: FILES_EDITED }),
  web_fetch: S("globe", "Read {arg}", "Reading {arg}…", { bare: "Read a page", bareLive: "Reading a page…", arg: "host", tally: PAGES_READ }),
  browser: S("globe", "Used my browser", "Using my browser…", { tally: BROWSER }),
  chrome: S("globe", "Used your Chrome", "Using your Chrome…", { tally: BROWSER }),
  look: S("eye", "Looked at {arg}", "Looking at {arg}…", { bare: "Looked at something", bareLive: "Looking…", arg: "look", tally: LOOKED }),
  // send_message and messages are above (the old routed send and apple-mcp's reads share the names).
  tell_owner: S("note", "Left you a note", "Leaving you a note…", { tally: NOTE }),
  memory: S("brain", "Used memory", "Using memory…", { tally: MEMORY }),
  schedule: S("calendar", "Checked the schedule", "Checking the schedule…"),
  screen: S("monitor", "Used the screen", "Using the screen…"),
  plan: S("list-checks", "Updated the plan", "Updating the plan…", { tally: PLAN }),
};

// The action tools' labels, by the action at the head of their summary.
const IN_MINE = (verb: string, live: string, opts: Omit<Spec, "icon" | "done" | "live"> = {}, icon: ToolIcon = "globe") =>
  S(icon, `${verb} in my browser`, `${live} in my browser…`, { tally: BROWSER, ...opts });
const IN_YOURS = (verb: string, live: string, opts: Omit<Spec, "icon" | "done" | "live"> = {}, icon: ToolIcon = "globe") =>
  S(icon, `${verb} in your Chrome`, `${live} in your Chrome…`, { tally: BROWSER, ...opts });
const EDIT_FILE = S("file-pen", "Edited {arg}", "Editing {arg}…", { bare: "Edited a file", bareLive: "Editing a file…", arg: "file", tally: FILES_EDITED });

const ACTION_SPECS: Record<string, Record<string, Spec>> = {
  browser: {
    open: S("globe", "Opened {arg} in my browser", "Opening {arg} in my browser…", {
      bare: "Opened a page in my browser",
      bareLive: "Opening a page in my browser…",
      arg: "host",
      tally: PAGES_OPENED,
    }),
    read: IN_MINE("Read the page", "Reading the page", {}, "eye"),
    find: S("search", "Looked for {arg} in my browser", "Looking for {arg} in my browser…", {
      bare: "Looked through the page",
      bareLive: "Looking through the page…",
      tally: BROWSER,
    }),
    click: IN_MINE("Clicked", "Clicking", {}, "pointer"),
    type: IN_MINE("Typed", "Typing", {}, "keyboard"),
    key: IN_MINE("Pressed a key", "Pressing a key", {}, "keyboard"),
    scroll: IN_MINE("Scrolled", "Scrolling", {}, "pointer"),
    back: IN_MINE("Went back", "Going back"),
    wait: IN_MINE("Waited for the page", "Waiting for the page", {}, "history"),
    download: S("file", "Downloaded a file", "Downloading a file…", { tally: DOWNLOADED }),
    close: IN_MINE("Closed the page", "Closing the page"),
  },
  chrome: {
    tabs: S("globe", "Checked your tabs", "Checking your tabs…", { tally: BROWSER }),
    open: S("globe", "Opened {arg} in your Chrome", "Opening {arg} in your Chrome…", {
      bare: "Opened a page in your Chrome",
      bareLive: "Opening a page in your Chrome…",
      arg: "host",
      tally: PAGES_OPENED,
    }),
    use: IN_YOURS("Took over a tab", "Taking over a tab"),
    read: IN_YOURS("Read the page", "Reading the page", {}, "eye"),
    find: S("search", "Looked for {arg} in your Chrome", "Looking for {arg} in your Chrome…", {
      bare: "Looked through the page",
      bareLive: "Looking through the page…",
      tally: BROWSER,
    }),
    click: IN_YOURS("Clicked", "Clicking", {}, "pointer"),
    type: IN_YOURS("Typed", "Typing", { tally: TYPED }, "keyboard"),
    key: IN_YOURS("Pressed a key", "Pressing a key", {}, "keyboard"),
    scroll: IN_YOURS("Scrolled", "Scrolling", {}, "pointer"),
    back: IN_YOURS("Went back", "Going back"),
    wait: IN_YOURS("Waited for the page", "Waiting for the page", {}, "history"),
    fullscreen: S("monitor", "Changed full screen", "Changing full screen…", { tally: BROWSER }),
    steps: IN_YOURS("Worked through the page", "Working through the page", {}, "pointer"),
    done: S("globe", "Finished in your Chrome", "Finishing in your Chrome…", { tally: BROWSER }),
  },
  messages: {
    recent: S("messages", "Read messages with {arg}", "Reading messages with {arg}…", {
      bare: "Read recent messages",
      bareLive: "Reading recent messages…",
      arg: "plain",
      tally: THREADS,
    }),
    thread: S("messages", "Read the thread with {arg}", "Reading the thread with {arg}…", {
      bare: "Read the thread",
      bareLive: "Reading the thread…",
      arg: "plain",
      tally: THREADS,
    }),
    participants: S("users", "Checked who's in {arg}", "Checking who's in {arg}…", {
      bare: "Checked who's in the chat",
      bareLive: "Checking who's in the chat…",
      arg: "plain",
    }),
    who: S("user", "Looked up {arg}", "Looking up {arg}…", { bare: "Looked up a contact", bareLive: "Looking up a contact…", arg: "plain" }),
    stand_down: S("hand", "Stood down in {arg}", "Standing down in {arg}…", {
      bare: "Stepped back from the chat",
      bareLive: "Stepping back…",
      arg: "plain",
    }),
    // The owner's email, iCloud and Gmail (2026-10-11).
    mail_search: S("mail", "Searched your email for {arg}", "Searching your email for {arg}…", {
      bare: "Searched your email",
      bareLive: "Searching your email…",
    }),
    mail_read: S("mail", "Read an email", "Reading an email…", { tally: MAIL_READ }),
  },
  // send_message with a subject is an email from the owner's Gmail; its chip reads
  // "email: <to>". A text's "to: X" still resolves to SPECS.send_message
  // ("to" is not an action).
  send_message: {
    email: S("mail", "Emailed {arg}", "Emailing {arg}…", {
      bare: "Sent an email",
      bareLive: "Sending an email…",
      arg: "to",
      tally: EMAILED,
    }),
  },
  memory: {
    search: S("brain", "Searched memory for {arg}", "Searching memory for {arg}…", {
      bare: "Searched memory",
      bareLive: "Searching memory…",
      tally: MEMORY,
    }),
    remember: S("brain", "Remembered", "Remembering…", { tally: REMEMBERED }),
    update: S("brain", "Updated a memory", "Updating a memory…", { tally: REMEMBERED }),
    close: S("brain", "Closed an intention", "Closing an intention…"),
    why: S("history", "Traced where that came from", "Tracing where that came from…"),
    forget: S("trash", "Looked at what to forget about {arg}", "Looking at what to forget…", {
      bare: "Looked at what to forget",
      arg: "plain",
    }),
    doc_read: S("file", "Read a document", "Reading a document…", { tally: DOCS_READ }),
    doc_write: S("file-plus", "Saved {arg}", "Saving {arg}…", { bare: "Saved a document", bareLive: "Saving a document…" }),
    doc_edit: S("file-pen", "Edited {arg}", "Editing {arg}…", { bare: "Edited a document", bareLive: "Editing a document…" }),
  },
  schedule: {
    add: S("calendar-plus", "Scheduled {arg}", "Scheduling {arg}…", { bare: "Scheduled something", bareLive: "Scheduling…", tally: SCHEDULED }),
    wake: S("alarm", "Set a time to look again", "Setting a time to look again…"),
    list: S("calendar", "Checked what's scheduled", "Checking what's scheduled…"),
    cancel: S("calendar-x", "Cancelled a scheduled event", "Cancelling a scheduled event…"),
    calendar: S("calendar", "Checked your calendar", "Checking your calendar…"),
    reminders: S("list-checks", "Checked your reminders", "Checking your reminders…"),
    event_add: S("calendar-plus", "Added {arg} to your calendar", "Adding {arg} to your calendar…", { bare: "Added an event to your calendar", bareLive: "Adding an event…" }),
    event_update: S("calendar", "Changed {arg} on your calendar", "Changing {arg} on your calendar…", { bare: "Changed an event", bareLive: "Changing an event…" }),
    event_delete: S("calendar-x", "Deleted an event from your calendar", "Deleting an event from your calendar…"),
    reminder_add: S("list-checks", "Added the reminder {arg}", "Adding the reminder {arg}…", { bare: "Added a reminder", bareLive: "Adding a reminder…" }),
    reminder_complete: S("list-checks", "Checked off a reminder", "Checking off a reminder…"),
    reminder_delete: S("trash", "Deleted a reminder", "Deleting a reminder…"),
  },
  screen: {
    compose: SPECS.compose_screen,
    say: SPECS.say_on_screen,
    show: SPECS.show_on_screen,
    clear: SPECS.clear_screen,
    reset: SPECS.reset_screen_layout,
    bring_up: SPECS.bring_up_screen,
    widget: SPECS.update_widget,
    widget_code: SPECS.update_widget_code,
    widget_clear: SPECS.clear_widget_code,
    state: S("monitor", "Checked the screens", "Checking the screens…"),
  },
  tell_owner: {
    note: SPECS.tell_owner,
    urgent: S("bell", "Pinged you", "Pinging you…", { tally: PUSH }),
  },
  str_replace_based_edit_tool: {
    view: S("file", "Read {arg}", "Reading {arg}…", { bare: "Read a file", bareLive: "Reading a file…", arg: "file", tally: FILES_EDITED }),
    create: S("file-plus", "Created {arg}", "Creating {arg}…", { bare: "Created a file", bareLive: "Creating a file…", arg: "file", tally: FILES_EDITED }),
    str_replace: EDIT_FILE,
    insert: EDIT_FILE,
    undo_edit: EDIT_FILE,
  },
};

/** Tools whose summary leads with an action ("open: https://…"). */
export const ACTION_TOOLS: ReadonlySet<string> = new Set(Object.keys(ACTION_SPECS));

/** True when this action of an action tool has its own written label. */
export function hasActionLabel(name: string, action: string): boolean {
  return Boolean(ACTION_SPECS[name]?.[action]);
}

const ACTION_HEAD = /^([a-z][a-z_]*)(?::\s([\s\S]*))?$/;
const NOT_ACTIONS = new Set(["to", "id", "http", "https"]);

/** `"{action}: {detail}"` → its parts; anything that doesn't lead with one lower-case word is all detail. */
export function splitAction(raw: string | null | undefined): { action: string; detail: string } {
  const s = (raw ?? "").trim();
  const m = ACTION_HEAD.exec(s);
  if (!m || NOT_ACTIONS.has(m[1])) return { action: "", detail: s };
  return { action: m[1], detail: (m[2] ?? "").trim() };
}

/** The spec and the argument for one call: action tools read their action first. */
function resolve(name: string, argsSummary?: string | null): { spec: Spec | null; arg: string | null | undefined } {
  const actions = ACTION_SPECS[name];
  if (!actions) return { spec: specFor(name), arg: argsSummary };
  const { action, detail } = splitAction(argsSummary);
  const byAction = action ? actions[action] : undefined;
  if (byAction) return { spec: byAction, arg: detail };
  return { spec: specFor(name), arg: action ? detail : argsSummary };
}

// MCP tools whose names vary: read them by what they touch.
const KEYWORD_SPECS: [RegExp, Spec][] = [
  [/calendar/i, SPECS.calendar],
  [/reminder/i, SPECS.reminders],
  [/gmail|e?mail/i, SPECS.mail],
  [/contact/i, SPECS.contacts],
  [/\bnotes?\b|_notes?\b|^notes?_/i, SPECS.notes],
  [/\bmaps?\b|_maps?\b|^maps?_/i, SPECS.maps],
];

/** True when the tool has its own written label (not a keyword guess or its name in words). */
export function hasExplicitLabel(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(SPECS, name);
}

/** A tool's name in words: "get_alexa_devices" → "Get alexa devices". */
export function humanizeToolName(name: string): string {
  const words = (name || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-.]+/g, " ")
    .trim()
    .toLowerCase();
  if (!words) return "A tool";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function specFor(name: string): Spec | null {
  if (hasExplicitLabel(name)) return SPECS[name];
  if (name.startsWith("whatsapp_send")) return SPECS.whatsapp_send_message;
  if (name.startsWith("whatsapp_")) return null;
  for (const [re, spec] of KEYWORD_SPECS) if (re.test(name)) return spec;
  return null;
}

function fallbackTitle(name: string): string {
  if (name.startsWith("whatsapp_")) return `${humanizeToolName(name.slice("whatsapp_".length))} (WhatsApp)`;
  return humanizeToolName(name);
}

// ---------------------------------------------------------------- arguments
interface ParsedArg {
  kind: "text" | "to" | "id" | "none";
  text: string;
}

/** The backend's args_summary → the part worth showing. */
function parseArg(raw: string | null | undefined): ParsedArg {
  let s = (raw ?? "").trim();
  if (!s) return { kind: "none", text: "" };
  if (/^id:\s*/i.test(s)) return { kind: "id", text: s.replace(/^id:\s*/i, "") };
  if (/^\[\d+\]$/.test(s)) return { kind: "id", text: s.slice(1, -1) };
  let kind: ParsedArg["kind"] = "text";
  if (/^to:\s*/i.test(s)) {
    kind = "to";
    s = s.replace(/^to:\s*/i, "");
  }
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) s = s.slice(1, -1);
  s = s.replace(/\.\.\.$/, "…").trim();
  return s ? { kind, text: s } : { kind: "none", text: "" };
}

function hostOf(text: string): string {
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`;
  try {
    const host = new URL(candidate).hostname.replace(/^www\./, "");
    return host || text;
  } catch {
    return text;
  }
}

function fileName(text: string): string {
  const parts = text.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || text;
}

/** look's target in words: the screen, your Chrome tab, my browser, a photo, a host, a file. */
function lookTarget(text: string): string {
  const t = text.trim();
  const low = t.toLowerCase();
  if (low === "screen") return "the screen";
  if (low === "chrome") return "your Chrome tab";
  if (low === "browser") return "my browser";
  if (/^(attachment|media|file):/.test(low)) return "a photo";
  if (/^https?:\/\//.test(low)) return hostOf(t);
  return fileName(t);
}

function renderArg(kind: ArgKind, text: string): string {
  switch (kind) {
    case "host":
      return hostOf(text);
    case "file":
      return fileName(text);
    case "look":
      return lookTarget(text);
    case "to":
    case "plain":
      return text;
    default:
      return `“${text}”`;
  }
}

function fill(template: string, arg: string): string {
  return template.replace("{arg}", arg);
}

// ---------------------------------------------------------------- failures
// "Checking your calendar…" → "Couldn't check your calendar": the live
// phrase's gerund turned back into its base verb.
const IRREGULAR_BASE: Record<string, string> = { adding: "add", cancelling: "cancel", labelling: "label" };
const E_STEMS = new Set([
  "announc", "archiv", "chang", "clos", "creat", "delet", "leav", "mak", "messag", "mov", "rearrang", "remov",
  "rewrit", "sav", "schedul", "tak", "trac", "typ", "unsubscrib", "updat", "us", "writ",
]);

function baseVerb(word: string): string | null {
  const w = word.toLowerCase();
  if (w.length < 5 || !w.endsWith("ing")) return null;
  if (IRREGULAR_BASE[w]) return IRREGULAR_BASE[w];
  const stem = w.slice(0, -3);
  if (E_STEMS.has(stem)) return `${stem}e`;
  if (/([bdgmnprt])\1$/.test(stem)) return stem.slice(0, -1);
  return stem;
}

function failedFrom(live: string, title: string): string {
  const phrase = live.replace(/…$/, "").trim();
  const [first, ...rest] = phrase.split(" ");
  const base = baseVerb(first ?? "");
  if (!base) return `Couldn't finish: ${title}`;
  return ["Couldn't", base, ...rest].join(" ");
}

/** What one call reads as. `argsSummary` is the stream's / trace's `args_summary`. */
export function describeTool(name: string, argsSummary?: string | null): ToolDescription {
  const resolved = resolve(name, argsSummary);
  const spec = resolved.spec;
  const arg = parseArg(resolved.arg);
  const outward = isOutwardTool(name);
  const usable = arg.kind === "text" || arg.kind === "to";

  if (!spec) {
    const title = fallbackTitle(name);
    return { icon: "wrench", title, live: `${title}…`, failed: `Couldn't finish: ${title}`, chips: usable ? [arg.text] : [], outward };
  }

  const wantsArg = spec.done.includes("{arg}");
  if (wantsArg && usable) {
    const shown = renderArg(spec.arg ?? "quote", arg.text);
    const title = fill(spec.done, shown);
    const live = spec.live.includes("{arg}") ? fill(spec.live, shown) : spec.live;
    return { icon: spec.icon, title, live, failed: failedFrom(live, title), chips: [], outward };
  }
  const title = wantsArg ? spec.bare ?? spec.done.replace(/\s*\{arg\}/, "") : spec.done;
  const live = spec.live.includes("{arg}") ? spec.bareLive ?? spec.live.replace(/\s*\{arg\}/, "") : spec.live;
  let chip = arg.text;
  if (spec.chipMax && chip.length > spec.chipMax) chip = `${chip.slice(0, spec.chipMax).trimEnd()}…`;
  return { icon: spec.icon, title, live, failed: failedFrom(live, title), chips: usable && !wantsArg ? [chip] : [], outward };
}

// ---------------------------------------------------------------- a call being composed
// The stream's tool_args_delta buffer is the model's raw, partial JSON
// ({"query": "…kayaking\non sat). The pending node shows what it says, the
// way a finished call does (2026-09-26 review): the value pulled out of the
// partial JSON, unescaped, in the backend's args_summary shape so
// describeTool renders it — never braces, keys or escape sequences.

const PENDING_PAIR = /"([A-Za-z_]\w*)"\s*:\s*"((?:[^"\\]|\\.)*)/g;
const SEND_KEYS = ["to", "recipient", "phone_number", "chat_identifier", "chat_id"];
const VALUE_KEYS = ["query", "url", "title", "description", "name", "memory", "content", "message", "text", "path", "outcome",
  "target", "what", "contact", "line", "chat_id"];
const PENDING_MAX = 80;

function unescapeJsonFragment(raw: string): string {
  const safe = raw.replace(/\\(u[0-9a-fA-F]{0,3})?$/, "");
  try {
    return JSON.parse(`"${safe}"`) as string;
  } catch {
    return safe.replace(/\\n/g, " ").replace(/\\(.)/g, "$1");
  }
}

/** Partial tool-call JSON → an args_summary (`"text"` or `to: Name`), or null while there is nothing to show. */
export function pendingArgsSummary(name: string, partialJson: string | null | undefined): string | null {
  if (!partialJson || EXECUTION_TOOLS.has(name)) return null;
  const values = new Map<string, string>();
  const order: string[] = [];
  PENDING_PAIR.lastIndex = 0;
  for (let m = PENDING_PAIR.exec(partialJson); m; m = PENDING_PAIR.exec(partialJson)) {
    const v = unescapeJsonFragment(m[2]).replace(/\s+/g, " ").trim();
    if (!v || values.has(m[1])) continue;
    values.set(m[1], v);
    order.push(m[1]);
  }
  if (!order.length) return null;
  const cut = (v: string) => (v.length > PENDING_MAX ? `${v.slice(0, PENDING_MAX).trimEnd()}…` : v);
  // An email being written (a subject, or a reply to a mail id) reads as one;
  // a text keeps "to: X" below.
  if (name === "send_message" && (values.has("subject") || /^(gmail|icloud):/.test(values.get("chat_id") ?? ""))) {
    return `email: ${values.has("to") ? cut(values.get("to")!) : "a reply"}`;
  }
  if (ACTION_TOOLS.has(name) && name !== "send_message") {
    const actionKey = name === "str_replace_based_edit_tool" ? "command" : "action";
    const action = values.get(actionKey);
    const valueKey = VALUE_KEYS.find((k) => values.has(k) && k !== actionKey);
    if (name === "tell_owner") {
      const head = values.get("title") ?? values.get("message");
      return head ? `"${cut(head)}"` : null;
    }
    if (!action) return null;
    if (!valueKey) return action;
    const v = cut(values.get(valueKey)!);
    return `${action}: ${valueKey === "url" || valueKey === "path" ? v : `"${v}"`}`;
  }
  if (isOutwardTool(name) && name !== "browser_type") {
    const to = SEND_KEYS.find((k) => values.has(k));
    if (to) return `to: ${cut(values.get(to)!)}`;
  }
  const key = VALUE_KEYS.find((k) => values.has(k)) ?? order[0];
  return `"${cut(values.get(key)!)}"`;
}

// ---------------------------------------------------------------- the receipt
function lowerFirst(s: string): string {
  return s ? s.charAt(0).toLowerCase() + s.slice(1) : s;
}

function isSendTally(t: Tally | SendTally | undefined): t is SendTally {
  return Boolean(t && "verb" in t);
}

/** Calls whose receipts fold together share a key (every browser action is
 * "used the browser"). An action tool's key is its action's ("open" counts
 * pages; "click" is the browser). */
export function tallyKey(name: string, argsSummary?: string | null): string {
  const { spec } = resolve(name, argsSummary);
  const t = spec?.tally;
  if (t) return t.key;
  if (ACTION_SPECS[name]) {
    const { action } = splitAction(argsSummary);
    return action && ACTION_SPECS[name][action] ? `tool:${name}:${action}` : `tool:${name}`;
  }
  return `tool:${name}`;
}

/** Only the action of a summary (a receipt phrase for several calls names no one call's detail). */
function actionOnly(name: string, argsSummary?: string | null): string | undefined {
  if (!ACTION_SPECS[name]) return undefined;
  const { action } = splitAction(argsSummary);
  return action || undefined;
}

function joinNames(names: string[]): string {
  if (names.length === 1) return names[0];
  return `${names[0]} and ${names[1]}`;
}

/**
 * One receipt phrase for a group of calls with the same tally key: "opened 2
 * pages", "texted Parker and Jordan", "checked your calendar". `args` holds each
 * call's args_summary (its length is the count).
 */
export function receiptPhrase(name: string, args: (string | null | undefined)[]): string {
  const n = Math.max(1, args.length);
  const spec = resolve(name, args[0]).spec;
  const tally = spec?.tally;

  if (isSendTally(tally)) {
    const names: string[] = [];
    for (const a of args) {
      const p = parseArg(resolve(name, a).arg);
      if ((p.kind === "to" || p.kind === "text") && !names.includes(p.text)) names.push(p.text);
    }
    if (names.length === 0) return n === 1 ? tally.one : tally.many.replace("{n}", String(n));
    if (names.length <= 2) return `${tally.verb} ${joinNames(names)}`;
    return `${tally.verb} ${names.length} people`;
  }
  if (tally) return n === 1 ? tally.one : tally.many.replace("{n}", String(n));

  const base = lowerFirst(spec ? describeTool(name, actionOnly(name, args[0])).title : fallbackTitle(name));
  return n === 1 ? base : `${base} ×${n}`;
}

/** A receipt phrase for calls that failed: "couldn't check your calendar", "couldn't text Parker ×2". */
export function failedPhrase(name: string, args: (string | null | undefined)[]): string {
  const n = Math.max(1, args.length);
  const d = describeTool(name, isOutwardTool(name) ? args[0] : actionOnly(name, args[0]));
  const text = lowerFirst(d.failed);
  return n === 1 ? text : `${text} ×${n}`;
}
