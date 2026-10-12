// Edward Chat Bar Widget for Scriptable
// https://scriptable.app
//
// A small companion widget that acts as a chat input bar.
// Tap it to type a message — Edward replies, then opens the conversation.
//
// Setup:
// 1. Copy this script into the Scriptable app
// 2. Set TOKEN below (or pass as widget parameter)
// 3. Add a Small Scriptable widget below the main Edward widget
// 4. Select this script for the widget

// Your Edward's public address (the backend's EDWARD_PUBLIC_BASE_URL),
// e.g. "https://edward.yourdomain.com". Set it in the phone's copy.
const EDWARD_URL = "https://YOUR-EDWARD-HOST";
const TOKEN = "YOUR_TOKEN_HERE";

// Allow override via widget parameter
const param = args.widgetParameter;
let token = TOKEN;
if (param) token = param;

const THEME = {
  bg: "#1a1a2e",
  accent: "#00d4aa",
  muted: "#888888",
  text: "#e0e0e0",
};

function hexToColor(hex) {
  hex = hex.replace("#", "");
  return new Color(hex, 1);
}

function drawChatBubble(size) {
  const dc = new DrawContext();
  dc.size = new Size(size, size);
  dc.opaque = false;
  dc.respectScreenScale = true;

  dc.setFillColor(hexToColor(THEME.accent));

  // Main bubble body
  const bubbleW = size * 0.7;
  const bubbleH = size * 0.5;
  const bubbleX = (size - bubbleW) / 2;
  const bubbleY = size * 0.1;
  const r = size * 0.12;

  const path = new Path();
  path.addRoundedRect(new Rect(bubbleX, bubbleY, bubbleW, bubbleH), r, r);
  dc.addPath(path);
  dc.fillPath();

  // Tail
  const tailPath = new Path();
  const tailX = bubbleX + size * 0.12;
  const tailY = bubbleY + bubbleH;
  tailPath.move(new Point(tailX, tailY - 2));
  tailPath.addLine(new Point(tailX - size * 0.08, tailY + size * 0.15));
  tailPath.addLine(new Point(tailX + size * 0.12, tailY - 2));
  tailPath.closeSubpath();
  dc.addPath(tailPath);
  dc.fillPath();

  // Three dots
  dc.setFillColor(hexToColor(THEME.bg));
  const dotR = size * 0.045;
  const dotY = bubbleY + bubbleH / 2;
  const dotSpacing = size * 0.12;
  const dotCenterX = size / 2;

  for (let i = -1; i <= 1; i++) {
    const cx = dotCenterX + i * dotSpacing;
    const dotPath = new Path();
    dotPath.addEllipse(new Rect(cx - dotR, dotY - dotR, dotR * 2, dotR * 2));
    dc.addPath(dotPath);
    dc.fillPath();
  }

  return dc.getImage();
}

// --- In-app: prompt for message, send to Edward API, open conversation ---
async function promptAndSend() {
  const alert = new Alert();
  alert.title = "Ask Edward";
  alert.addTextField("What's on your mind?");
  alert.addAction("Send");
  alert.addCancelAction("Cancel");

  const choice = await alert.present();
  if (choice === -1) return;

  const message = alert.textFieldValue(0).trim();
  if (!message) return;

  // Send to Edward API
  const req = new Request(`${EDWARD_URL}/api/widget/chat`);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({ token, message });
  req.timeoutInterval = 30;

  try {
    const data = await req.loadJSON();

    if (data.conversation_id) {
      // Open the conversation in the browser
      Safari.open(`${EDWARD_URL}?c=${data.conversation_id}`);
    }
  } catch (e) {
    const err = new Alert();
    err.title = "Couldn't reach Edward";
    err.message = String(e);
    err.addAction("OK");
    await err.present();
  }
}

// --- Widget: visual chat bar ---
function buildWidget() {
  const w = new ListWidget();
  w.backgroundColor = hexToColor(THEME.bg);
  w.setPadding(12, 16, 12, 16);

  // Tap runs this script in Scriptable (shows input dialog)
  w.url = URLScheme.forRunningScript();

  const row = w.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  row.spacing = 10;

  const icon = row.addImage(drawChatBubble(40));
  icon.imageSize = new Size(22, 22);

  const label = row.addText("Ask Edward...");
  label.font = Font.mediumRoundedSystemFont(15);
  label.textColor = hexToColor(THEME.muted);
  label.lineLimit = 1;

  return w;
}

// --- Main ---
if (config.runsInWidget) {
  const widget = buildWidget();
  Script.setWidget(widget);
} else {
  await promptAndSend();
}

Script.complete();
