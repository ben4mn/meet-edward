// Edward Lock Screen Widget for Scriptable
// https://scriptable.app
//
// An animated Edward robot for your lock screen.
// Each widget refresh (~15 min), Edward gets a new pose.
// Supports both circular (accessoryCircular) and rectangular (accessoryRectangular).
//
// Setup:
// 1. Copy this script into the Scriptable app
// 2. Long-press lock screen → Customize → Add Lock Screen Widget
// 3. Choose Scriptable, then select this script
// 4. Works with both circular and rectangular slots

// Your Edward's public address (the backend's EDWARD_PUBLIC_BASE_URL),
// e.g. "https://edward.yourdomain.com". Set it in the phone's copy.
const EDWARD_URL = "https://YOUR-EDWARD-HOST";

// Detect lock screen widget family
const family = config.widgetFamily || "accessoryRectangular";
const isRect = family === "accessoryRectangular";

// Canvas dimensions (logical points, scaled up for rendering)
const W = isRect ? 160 : 76;
const H = isRect ? 76 : 76;
const SCALE = 3;

// Randomize pose each refresh
const seed = Math.floor(Date.now() / 1000);
function rand(min, max) {
  const x = Math.sin(seed * 9301 + min * 49297 + max * 233) * 10000;
  return min + (x - Math.floor(x)) * (max - min);
}

const POSES = ["neutral", "happy", "thinking", "wink", "surprised"];
const pose = POSES[Math.floor(rand(0, POSES.length))];
const antennaAngle = rand(-20, 20);

// For rectangular, Edward wanders horizontally
const rectEdwardX = isRect ? rand(10, W - 55) : 0;

const white = Color.white();

// ── Draw Edward's face (screen-only, no body circle) ──
// Designed to be readable at lock screen sizes.
function drawEdwardFace(dc, x, y, w, h) {
  const cx = x + w / 2;

  // ── Screen (the face) ──
  // Rounded rectangle with bright outline, dark fill
  const screenR = Math.min(w, h) * 0.18;

  // Outer bezel
  dc.setFillColor(white);
  const bezelPath = new Path();
  bezelPath.addRoundedRect(new Rect(x, y, w, h), screenR, screenR);
  dc.addPath(bezelPath);
  dc.fillPath();

  // Inner dark screen
  const pad = Math.min(w, h) * 0.08;
  dc.setFillColor(new Color("#000000", 0.7));
  const screenPath = new Path();
  screenPath.addRoundedRect(
    new Rect(x + pad, y + pad, w - pad * 2, h - pad * 2),
    screenR * 0.7,
    screenR * 0.7
  );
  dc.addPath(screenPath);
  dc.fillPath();

  // ── Eyes ──
  const eyeR = w * 0.09;
  const eyeY = y + h * 0.42;
  const eyeSpacing = w * 0.18;

  dc.setFillColor(white);

  if (pose === "wink") {
    // Left eye normal
    const lp = new Path();
    lp.addEllipse(new Rect(cx - eyeSpacing - eyeR, eyeY - eyeR, eyeR * 2, eyeR * 2));
    dc.addPath(lp);
    dc.fillPath();
    // Right eye wink dash
    dc.setStrokeColor(white);
    dc.setLineWidth(w * 0.04);
    const wp = new Path();
    wp.move(new Point(cx + eyeSpacing - eyeR * 1.3, eyeY));
    wp.addLine(new Point(cx + eyeSpacing + eyeR * 1.3, eyeY));
    dc.addPath(wp);
    dc.strokePath();
  } else if (pose === "surprised") {
    const bigR = eyeR * 1.3;
    for (const side of [-1, 1]) {
      const ep = new Path();
      ep.addEllipse(new Rect(cx + side * eyeSpacing - bigR, eyeY - bigR, bigR * 2, bigR * 2));
      dc.addPath(ep);
      dc.fillPath();
    }
  } else {
    for (const side of [-1, 1]) {
      const ep = new Path();
      ep.addEllipse(new Rect(cx + side * eyeSpacing - eyeR, eyeY - eyeR, eyeR * 2, eyeR * 2));
      dc.addPath(ep);
      dc.fillPath();
    }
  }

  // ── Mouth ──
  const mouthY = y + h * 0.68;
  dc.setFillColor(white);
  dc.setStrokeColor(white);
  dc.setLineWidth(w * 0.035);

  if (pose === "happy") {
    const sp = new Path();
    sp.move(new Point(cx - w * 0.12, mouthY));
    sp.addQuadCurve(
      new Point(cx + w * 0.12, mouthY),
      new Point(cx, mouthY + h * 0.1)
    );
    dc.addPath(sp);
    dc.strokePath();
  } else if (pose === "surprised") {
    const oR = w * 0.05;
    dc.setLineWidth(w * 0.03);
    const op = new Path();
    op.addEllipse(new Rect(cx - oR, mouthY - oR, oR * 2, oR * 2));
    dc.addPath(op);
    dc.strokePath();
  } else if (pose === "thinking") {
    // Offset to the right
    const mp = new Path();
    mp.addRoundedRect(new Rect(cx, mouthY - h * 0.015, w * 0.15, h * 0.03), 2, 2);
    dc.addPath(mp);
    dc.fillPath();
  } else {
    // Neutral
    const mp = new Path();
    mp.addRoundedRect(new Rect(cx - w * 0.1, mouthY - h * 0.015, w * 0.2, h * 0.03), 2, 2);
    dc.addPath(mp);
    dc.fillPath();
  }

  // ── Antenna ──
  const antennaBaseY = y;
  const antennaTopY = y - h * 0.22;
  const antennaBallR = w * 0.06;
  const angleRad = (antennaAngle * Math.PI) / 180;
  const stickLen = antennaBaseY - antennaTopY;
  const topX = cx + Math.sin(angleRad) * stickLen;

  dc.setStrokeColor(white);
  dc.setLineWidth(w * 0.05);
  const stickPath = new Path();
  stickPath.move(new Point(cx, antennaBaseY));
  stickPath.addLine(new Point(topX, antennaTopY));
  dc.addPath(stickPath);
  dc.strokePath();

  // Antenna ball
  dc.setFillColor(white);
  const ballPath = new Path();
  ballPath.addEllipse(new Rect(
    topX - antennaBallR,
    antennaTopY - antennaBallR,
    antennaBallR * 2,
    antennaBallR * 2
  ));
  dc.addPath(ballPath);
  dc.fillPath();
}

// ── Draw thought bubbles (rectangular widget, thinking pose only) ──
function drawThoughtBubbles(dc, edwardRight, edwardCY) {
  const dimWhite = new Color("#ffffff", 0.6);
  dc.setFillColor(dimWhite);

  const d1 = new Path();
  d1.addEllipse(new Rect(edwardRight + 4, edwardCY - 8, 5, 5));
  dc.addPath(d1);
  dc.fillPath();

  const d2 = new Path();
  d2.addEllipse(new Rect(edwardRight + 12, edwardCY - 18, 8, 8));
  dc.addPath(d2);
  dc.fillPath();

  const d3 = new Path();
  d3.addEllipse(new Rect(edwardRight + 22, edwardCY - 30, 14, 14));
  dc.addPath(d3);
  dc.fillPath();
}

function buildWidget() {
  const w = new ListWidget();
  w.url = EDWARD_URL;
  w.backgroundColor = Color.clear();
  w.setPadding(0, 0, 0, 0);

  const dc = new DrawContext();
  dc.size = new Size(W * SCALE, H * SCALE);
  dc.opaque = false;
  dc.respectScreenScale = true;

  if (isRect) {
    // Rectangular: Edward face + optional thought bubbles
    const faceW = 36 * SCALE;
    const faceH = 32 * SCALE;
    const faceX = rectEdwardX * SCALE;
    const faceY = (H * SCALE - faceH) / 2 + 6 * SCALE; // offset down to leave room for antenna

    drawEdwardFace(dc, faceX, faceY, faceW, faceH);

    if (pose === "thinking") {
      drawThoughtBubbles(dc, faceX + faceW, faceY + faceH / 2);
    }
  } else {
    // Circular: face fills the circle, centered with antenna room
    const faceW = 52 * SCALE;
    const faceH = 44 * SCALE;
    const faceX = (W * SCALE - faceW) / 2;
    const faceY = (H * SCALE - faceH) / 2 + 8 * SCALE; // push down for antenna

    drawEdwardFace(dc, faceX, faceY, faceW, faceH);
  }

  const img = w.addImage(dc.getImage());
  img.centerAlignImage();

  // Refresh every ~15 min for a new pose
  const refresh = new Date();
  refresh.setMinutes(refresh.getMinutes() + 15);
  w.refreshAfterDate = refresh;

  return w;
}

const widget = buildWidget();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  widget.presentSmall();
}

Script.complete();
