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

import makeWASocket, { useMultiFileAuthState, DisconnectReason, Browsers, fetchLatestBaileysVersion } from "baileys";
import express from "express";
import path from "path";
import os from "os";
import pino from "pino";
import qrcode from "qrcode-terminal";

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

// ─── LID-to-JID mapping ──────────────────────────────────────────────────────
// Baileys 7.x uses LID (Linked Device ID) as the default addressing mode.
// Messages sent to @lid targets may not replicate properly to all devices.
// We maintain a cache mapping @lid → @s.whatsapp.net for reliable sending.
const lidToJid = {};    // normalized @lid → @s.whatsapp.net
let selfJid = null;     // user's own normalized @s.whatsapp.net JID
let selfLid = null;     // user's own @lid JID (if known)

/**
 * Strip the :device suffix from a JID and normalize @c.us → @s.whatsapp.net.
 * e.g. "6598587940:4@s.whatsapp.net" → "6598587940@s.whatsapp.net"
 */
function normalizeJid(jid) {
  if (!jid) return "";
  const atIdx = jid.indexOf("@");
  if (atIdx < 0) return jid;
  const userPart = jid.slice(0, atIdx).split(":")[0]; // strip :device
  let server = jid.slice(atIdx + 1);
  if (server === "c.us") server = "s.whatsapp.net";
  return `${userPart}@${server}`;
}

/**
 * Resolve an @lid JID to @s.whatsapp.net using the cache.
 * Returns the original JID unchanged if it's not @lid or no mapping exists.
 */
function resolveLid(jid) {
  if (!jid || !jid.endsWith("@lid")) return jid;

  const normalized = normalizeJid(jid);

  // Check cache
  if (lidToJid[normalized]) return lidToJid[normalized];

  // Self-chat detection via selfLid
  if (selfLid && normalized === normalizeJid(selfLid) && selfJid) return selfJid;

  console.log(`[WhatsApp Bridge] WARNING: No JID mapping for LID ${jid}`);
  return jid;
}

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
// Baileys 7.x doesn't export makeInMemoryStore, so we capture messages
// ourselves from messages.upsert.
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
  console.log(`[WhatsApp Bridge] Using WA version: ${version}`);

  sock = makeWASocket({
    version,
    auth: state,
    logger,
    browser: Browsers.ubuntu("Chrome"),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    getMessage: async (key) => {
      // Provide stored messages for retry/decrypt requests
      const msgs = messageBuffer[key.remoteJid] || [];
      const msg = msgs.find((m) => m.key?.id === key.id);
      return msg?.message || undefined;
    },
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
        lid: sock.user?.lid || "",
      };

      // Build self-JID mapping for @lid resolution
      if (sock.user?.id) {
        selfJid = normalizeJid(sock.user.id);
      }
      if (sock.user?.lid) {
        selfLid = sock.user.lid;
        if (selfJid) {
          lidToJid[normalizeJid(selfLid)] = selfJid;
          console.log(`[WhatsApp Bridge] Self LID mapping: ${selfLid} → ${selfJid}`);
        }
      }

      console.log(`[WhatsApp Bridge] Connected as ${userInfo.name} (${userInfo.id}${userInfo.lid ? `, lid: ${userInfo.lid}` : ""})`);
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

  // Cache contact names + LID-to-JID mappings
  sock.ev.on("contacts.update", (updates) => {
    for (const contact of updates) {
      if (contact.id && contact.notify) {
        contactNames[contact.id] = contact.notify;
      }
      // Capture LID → JID mapping if both fields present
      if (contact.lid && contact.jid) {
        lidToJid[normalizeJid(contact.lid)] = normalizeJid(contact.jid);
      }
      if (contact.id?.endsWith("@lid") && contact.jid) {
        lidToJid[normalizeJid(contact.id)] = normalizeJid(contact.jid);
      }
    }
  });
  sock.ev.on("contacts.upsert", (contacts) => {
    for (const contact of contacts) {
      if (contact.id) {
        contactNames[contact.id] = contact.notify || contact.name || contact.id;
      }
      // Capture LID → JID mapping if both fields present
      if (contact.lid && contact.jid) {
        lidToJid[normalizeJid(contact.lid)] = normalizeJid(contact.jid);
      }
      if (contact.id?.endsWith("@lid") && contact.jid) {
        lidToJid[normalizeJid(contact.id)] = normalizeJid(contact.jid);
      }
    }
  });

  // Explicit LID → JID mappings from phone number share events
  sock.ev.on("chats.phoneNumberShare", (update) => {
    if (update.lid && update.jid) {
      lidToJid[normalizeJid(update.lid)] = normalizeJid(update.jid);
      console.log(`[WhatsApp Bridge] Phone number share: ${update.lid} → ${update.jid}`);
    }
  });

  // ─── Real-time mention detection ────────────────────────────────────────
  sock.ev.on("messages.upsert", async (upsert) => {
    const messages = upsert.messages || upsert;
    const type = upsert.type || "notify";

    // Buffer ALL messages (including history sync) for chat history retrieval
    for (const msg of messages) {
      const cid = msg.key?.remoteJid;
      if (cid) {
        bufferMessage(cid, msg);
        // Also buffer under resolved JID so lookups work with either format
        const resolvedCid = resolveLid(cid);
        if (resolvedCid !== cid) bufferMessage(resolvedCid, msg);
      }
    }

    if (type !== "notify") return; // Only detect mentions in new messages

    for (const msg of messages) {
      const rawChatId = msg.key?.remoteJid;
      const chatId = resolveLid(rawChatId); // Convert @lid → @s.whatsapp.net if possible
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
      // Include original @lid for debugging if it was resolved
      if (rawChatId !== chatId) {
        payload.original_chat_id = rawChatId;
      }

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
  res.json({
    connected,
    user: userInfo,
    self_jid: selfJid,
    self_lid: selfLid,
    lid_mappings_count: Object.keys(lidToJid).length,
  });
});

app.get("/resolve-lid/:lid", (_req, res) => {
  const lid = decodeURIComponent(_req.params.lid);
  const resolved = resolveLid(lid);
  res.json({ original: lid, resolved, was_resolved: resolved !== lid });
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

    // Try both original and resolved JID for buffer lookup
    let buffered = messageBuffer[chatId] || [];
    if (buffered.length === 0) {
      const resolved = resolveLid(chatId);
      if (resolved !== chatId) buffered = messageBuffer[resolved] || [];
    }
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

  // Resolve @lid to @s.whatsapp.net before sending
  const resolvedId = resolveLid(chat_id);
  if (resolvedId !== chat_id) {
    console.log(`[WhatsApp Bridge] /send LID resolved: ${chat_id} → ${resolvedId}`);
  }

  console.log(`[WhatsApp Bridge] /send to ${resolvedId}: "${message.slice(0, 80)}"`);
  try {
    const sent = await sock.sendMessage(resolvedId, { text: message });
    const msgId = sent?.key?.id;
    // Track this ID so the event listener skips it (it's Edward's reply, not user)
    if (msgId) trackSentMessage(msgId);
    // Buffer under both IDs so chat history lookups work with either format
    if (sent) {
      bufferMessage(resolvedId, sent);
      if (resolvedId !== chat_id) bufferMessage(chat_id, sent);
    }
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
