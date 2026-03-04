/**
 * WhatsApp Bridge for Edward
 *
 * Maintains a persistent Baileys WebSocket connection and:
 * 1. Pushes @edward mentions to the backend via webhook (real-time)
 * 2. Exposes a REST API for sending messages, reading chats, etc.
 *
 * Environment variables:
 *   WHATSAPP_BRIDGE_PORT    — HTTP server port (default: 3100)
 *   EDWARD_WEBHOOK_URL      — Backend webhook URL (default: http://localhost:8000/api/webhook/whatsapp)
 *   WHATSAPP_AUTH_DIR       — Baileys auth state directory (default: ~/.whatsapp-mcp/auth)
 *   MENTION_PATTERN         — Regex pattern for mention detection (default: @edward)
 */

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const express = require("express");
const path = require("path");
const os = require("os");
const pino = require("pino");
const qrcode = require("qrcode-terminal");

const PORT = parseInt(process.env.WHATSAPP_BRIDGE_PORT || "3100", 10);
const WEBHOOK_URL = process.env.EDWARD_WEBHOOK_URL || "http://localhost:8000/api/webhook/whatsapp";
const AUTH_DIR = process.env.WHATSAPP_AUTH_DIR || path.join(os.homedir(), ".whatsapp-mcp", "auth");
const MENTION_RE = new RegExp(process.env.MENTION_PATTERN || "@edward", "i");

const logger = pino({ level: "warn" });

// ─── State ───────────────────────────────────────────────────────────────────

let sock = null;
let connected = false;
let userInfo = null; // { id, name }
let contactNames = {}; // jid → display name
let reconnectTimer = null;

// Track message IDs sent by Edward (via /send endpoint) so we can skip
// only those in the event listener, not ALL fromMe messages (since the
// user's own WhatsApp messages also appear as fromMe).
const sentByBridge = new Set();
// Auto-expire sentByBridge entries after 60s to prevent unbounded growth
function trackSentMessage(msgId) {
  sentByBridge.add(msgId);
  setTimeout(() => sentByBridge.delete(msgId), 60_000);
}

// In-memory message buffer: chatId → array of messages (newest last).
// Baileys 6.x doesn't export makeInMemoryStore and fetchMessageHistory is
// async/event-based, so we capture messages ourselves from messages.upsert.
const messageBuffer = {};  // { [chatId]: WAMessage[] }
const MAX_MESSAGES_PER_CHAT = 100;

function bufferMessage(chatId, msg) {
  if (!messageBuffer[chatId]) messageBuffer[chatId] = [];
  const buf = messageBuffer[chatId];
  // Deduplicate by message ID
  if (msg.key?.id && buf.some((m) => m.key?.id === msg.key.id)) return;
  buf.push(msg);
  // Trim to limit
  if (buf.length > MAX_MESSAGES_PER_CHAT) {
    messageBuffer[chatId] = buf.slice(-MAX_MESSAGES_PER_CHAT);
  }
}

// Extract text content from any Baileys message type
function extractText(msg) {
  const m = msg.message;
  if (!m) return "";
  return (
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.videoMessage?.caption ||
    m.documentMessage?.caption ||
    m.buttonsResponseMessage?.selectedDisplayText ||
    m.listResponseMessage?.title ||
    m.templateButtonReplyMessage?.selectedDisplayText ||
    ""
  );
}

// ─── Baileys Connection ──────────────────────────────────────────────────────

async function startBaileys() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    syncFullHistory: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("\n[WhatsApp Bridge] Scan this QR code with WhatsApp:\n");
      qrcode.generate(qr, { small: true });
      console.log("");
    }
    if (connection === "open") {
      connected = true;
      userInfo = {
        id: sock.user?.id || "",
        name: sock.user?.name || "",
      };
      console.log(`[WhatsApp Bridge] Connected as ${userInfo.name} (${userInfo.id})`);
    }
    if (connection === "close") {
      connected = false;
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log(`[WhatsApp Bridge] Disconnected (status=${statusCode}), reconnect=${shouldReconnect}`);
      if (shouldReconnect) {
        clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => startBaileys(), 5000);
      } else {
        console.log("[WhatsApp Bridge] Logged out — delete auth dir and restart to re-scan QR");
      }
    }
  });

  // Cache contact names
  sock.ev.on("contacts.update", (updates) => {
    for (const contact of updates) {
      if (contact.id && contact.notify) {
        contactNames[contact.id] = contact.notify;
      }
    }
  });
  sock.ev.on("contacts.upsert", (contacts) => {
    for (const contact of contacts) {
      if (contact.id) {
        contactNames[contact.id] = contact.notify || contact.name || contact.id;
      }
    }
  });

  // ─── Real-time mention detection ────────────────────────────────────────
  sock.ev.on("messages.upsert", async (upsert) => {
    const messages = upsert.messages || upsert;
    const type = upsert.type || "notify";

    // Buffer ALL messages (including history sync) for chat history retrieval
    for (const msg of messages) {
      const cid = msg.key?.remoteJid;
      if (cid) bufferMessage(cid, msg);
    }

    if (type !== "notify") return; // Only detect mentions in new messages

    for (const msg of messages) {
      const chatId = msg.key?.remoteJid;
      const text = extractText(msg);

      // Skip messages that Edward sent via the /send endpoint.
      // Do NOT skip all fromMe — the user's own messages are also fromMe
      // since the bridge is logged in as the user's WhatsApp account.
      if (msg.key.id && sentByBridge.has(msg.key.id)) {
        sentByBridge.delete(msg.key.id);
        continue;
      }

      if (!MENTION_RE.test(text)) continue;

      const isGroup = chatId.endsWith("@g.us");
      const sender = isGroup ? (msg.key.participant || chatId) : chatId;
      const senderName = msg.pushName || contactNames[sender] || "";
      const chatName = isGroup
        ? contactNames[chatId] || chatId
        : senderName || chatId;

      const payload = {
        chat_id: chatId,
        chat_name: chatName,
        sender,
        sender_name: senderName,
        text,
        timestamp: msg.messageTimestamp,
        message_id: msg.key.id,
        is_group: isGroup,
        is_from_me: msg.key.fromMe || false,
      };

      console.log(`[WhatsApp Bridge] @edward detected in ${chatName} from ${senderName}: "${text.slice(0, 80)}"`);

      try {
        const resp = await fetch(WEBHOOK_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          console.error(`[WhatsApp Bridge] Webhook POST failed: ${resp.status} ${resp.statusText}`);
        }
      } catch (err) {
        console.error(`[WhatsApp Bridge] Webhook POST error: ${err.message}`);
      }
    }
  });
}

// ─── Helper: resolve group metadata ──────────────────────────────────────────

async function getGroupName(jid) {
  try {
    const meta = await sock.groupMetadata(jid);
    return meta.subject || jid;
  } catch {
    return contactNames[jid] || jid;
  }
}

// ─── REST API ────────────────────────────────────────────────────────────────

const app = express();
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/status", (_req, res) => {
  res.json({ connected, user: userInfo });
});

app.get("/chats", async (req, res) => {
  if (!connected) return res.status(503).json({ error: "Not connected" });
  try {
    const limit = parseInt(req.query.limit || "30", 10);
    // Derive chat list from our message buffer (sorted by most recent message)
    const chatIds = Object.keys(messageBuffer);
    const chats = chatIds.map((id) => {
      const msgs = messageBuffer[id];
      const last = msgs[msgs.length - 1];
      return {
        id,
        name: contactNames[id] || id,
        lastMessage: last?.messageTimestamp || null,
      };
    });
    // Sort by most recent first
    chats.sort((a, b) => (b.lastMessage || 0) - (a.lastMessage || 0));
    res.json(chats.slice(0, limit));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/chats/:id/messages", async (req, res) => {
  if (!connected) return res.status(503).json({ error: "Not connected" });
  try {
    const chatId = req.params.id;
    const limit = parseInt(req.query.limit || "15", 10);

    // Read from our in-memory buffer (populated from messages.upsert events)
    const buffered = messageBuffer[chatId] || [];
    const messages = buffered.slice(-limit);

    const result = messages.map((m) => ({
      id: m.key?.id,
      from: m.key?.participant || m.key?.remoteJid,
      fromMe: m.key?.fromMe || false,
      sender_name: m.pushName || contactNames[m.key?.participant || m.key?.remoteJid] || "",
      text: extractText(m),
      timestamp: m.messageTimestamp,
    }));
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Debug: raw buffered messages (for diagnosing text extraction issues)
app.get("/chats/:id/messages/raw", async (req, res) => {
  const chatId = req.params.id;
  const limit = parseInt(req.query.limit || "5", 10);
  const buffered = messageBuffer[chatId] || [];
  const messages = buffered.slice(-limit);
  // Return a simplified view of the raw message structure
  const result = messages.map((m) => ({
    key: m.key,
    pushName: m.pushName,
    messageTimestamp: m.messageTimestamp,
    messageKeys: m.message ? Object.keys(m.message) : [],
    message: m.message,
  }));
  res.json(result);
});

app.get("/contacts", async (_req, res) => {
  if (!connected) return res.status(503).json({ error: "Not connected" });
  const result = Object.entries(contactNames).map(([id, name]) => ({ id, name }));
  res.json(result);
});

app.get("/groups", async (_req, res) => {
  if (!connected) return res.status(503).json({ error: "Not connected" });
  try {
    const groups = await sock.groupFetchAllParticipating();
    const result = Object.values(groups).map((g) => ({
      id: g.id,
      name: g.subject || g.id,
      participants: (g.participants || []).length,
    }));
    // Cache group names
    for (const g of result) {
      contactNames[g.id] = g.name;
    }
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/send", async (req, res) => {
  if (!connected) return res.status(503).json({ error: "Not connected" });
  const { chat_id, message } = req.body;
  if (!chat_id || !message) {
    return res.status(400).json({ error: "chat_id and message required" });
  }
  console.log(`[WhatsApp Bridge] /send to ${chat_id}: "${message.slice(0, 80)}"`);
  try {
    const sent = await sock.sendMessage(chat_id, { text: message });
    const msgId = sent?.key?.id;
    // Track this ID so the event listener skips it (it's Edward's reply, not user)
    if (msgId) trackSentMessage(msgId);
    // Also buffer the sent message so it appears in chat history
    if (sent) bufferMessage(chat_id, sent);
    console.log(`[WhatsApp Bridge] /send success (id: ${msgId})`);
    res.json({ success: true, message_id: msgId });
  } catch (err) {
    console.error(`[WhatsApp Bridge] /send error:`, err);
    res.status(500).json({ error: err.message });
  }
});

// ─── Start ───────────────────────────────────────────────────────────────────

async function main() {
  console.log(`[WhatsApp Bridge] Starting on port ${PORT}`);
  console.log(`[WhatsApp Bridge] Auth dir: ${AUTH_DIR}`);
  console.log(`[WhatsApp Bridge] Webhook URL: ${WEBHOOK_URL}`);
  console.log(`[WhatsApp Bridge] Mention pattern: ${MENTION_RE}`);

  await startBaileys();

  app.listen(PORT, () => {
    console.log(`[WhatsApp Bridge] REST API listening on http://localhost:${PORT}`);
  });
}

main().catch((err) => {
  console.error("[WhatsApp Bridge] Fatal error:", err);
  process.exit(1);
});
