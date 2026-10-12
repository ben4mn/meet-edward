// Edward iOS Widget for Scriptable
// https://scriptable.app
//
// Setup:
// 1. Copy this script into the Scriptable app
// 2. Set EDWARD_URL and TOKEN below (or pass as widget parameter: "url|token").
//    The token is GET /api/widget/token (signed in). Set it in the phone's
//    copy only; the repo keeps the placeholder.
// 3. Add a Scriptable widget to your home screen and select this script

// Your Edward's public address (the backend's EDWARD_PUBLIC_BASE_URL),
// e.g. "https://edward.yourdomain.com". Set it in the phone's copy.
const EDWARD_URL = "https://YOUR-EDWARD-HOST";
const TOKEN = "YOUR_TOKEN_HERE";

// Allow override via widget parameter: "url|token"
const param = args.widgetParameter;
let url = EDWARD_URL;
let token = TOKEN;
if (param && param.includes("|")) {
  const parts = param.split("|");
  url = parts[0];
  token = parts[1];
}

// Detect widget size
const widgetFamily = config.widgetFamily || "medium";
const maxSections = { small: 2, medium: 4, large: 10 }[widgetFamily] || 4;

// ============================================================================
// FETCH DATA FROM API
// ============================================================================

async function fetchWidgetData() {
  const req = new Request(`${url}/api/widget?token=${token}`);
  req.timeoutInterval = 10;
  try {
    return await req.loadJSON();
  } catch (e) {
    return null;
  }
}

// ============================================================================
// ERROR / FALLBACK WIDGET
// ============================================================================

function createErrorWidget(message) {
  const w = new ListWidget();
  w.backgroundColor = new Color("1a1a2e");
  w.setPadding(16, 16, 16, 16);

  const title = w.addText("Edward");
  title.font = Font.boldSystemFont(16);
  title.textColor = new Color("e0e0e0");

  w.addSpacer(4);

  const msg = w.addText(message || "Could not connect");
  msg.font = Font.systemFont(12);
  msg.textColor = new Color("888888");

  return w;
}

// ============================================================================
// RAW CODE EXECUTION (when API returns a "script" field)
// ============================================================================

async function executeWidgetCode(code) {
  // The code is expected to create a ListWidget and call Script.setWidget / Script.complete.
  // We wrap it in an AsyncFunction so the code can use `await`.
  // We inject `widgetFamily` and `config` so the code can adapt to widget size.
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const fn = new AsyncFunction("widgetFamily", "config", "args", code);
    await fn(widgetFamily, config, args);
    return true;
  } catch (e) {
    // Code failed — show fallback with error
    const w = createErrorWidget(`Code error: ${e.message}`);
    Script.setWidget(w);
    Script.complete();
    return false;
  }
}

// ============================================================================
// STRUCTURED DATA RENDERER (when API returns sections without a script)
// ============================================================================

const DEFAULT_THEME = {
  backgroundColor: "#1a1a2e",
  textColor: "#e0e0e0",
  secondaryTextColor: "#888888",
  accentColor: "#00d4aa",
};

const ICON_MAP = {
  calendar: "\uD83D\uDCC5",
  star: "\u2B50",
  bell: "\uD83D\uDD14",
  clock: "\u23F0",
  check: "\u2705",
  pin: "\uD83D\uDCCC",
  heart: "\u2764\uFE0F",
  fire: "\uD83D\uDD25",
  brain: "\uD83E\uDDE0",
  rocket: "\uD83D\uDE80",
  memo: "\uD83D\uDCDD",
  chart: "\uD83D\uDCCA",
  flag: "\uD83C\uDFF3\uFE0F",
  bolt: "\u26A1",
  target: "\uD83C\uDFAF",
};

function getIcon(icon) {
  if (!icon) return "";
  return ICON_MAP[icon] || icon;
}

function hexToColor(hex) {
  if (!hex) return null;
  hex = hex.replace("#", "");
  return new Color(hex, 1);
}

function renderHeader(stack, section, theme) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();

  const icon = getIcon(section.icon);
  if (icon) {
    const iconText = row.addText(icon + " ");
    iconText.font = Font.systemFont(12);
  }

  const title = row.addText(section.title || "");
  title.font = Font.boldSystemFont(13);
  title.textColor = hexToColor(theme.accentColor);
}

function renderText(stack, section, theme) {
  const text = stack.addText(section.content || "");
  text.font = Font.systemFont(12);
  text.textColor = hexToColor(theme.textColor);
  text.lineLimit = 3;
}

function renderList(stack, section, theme) {
  const items = section.items || [];
  for (const item of items.slice(0, 3)) {
    const row = stack.addStack();
    row.layoutHorizontally();
    row.centerAlignContent();
    row.spacing = 4;

    const icon = getIcon(item.icon);
    if (icon) {
      const iconText = row.addText(icon);
      iconText.font = Font.systemFont(11);
    }

    const label = row.addText(item.label || "");
    label.font = Font.systemFont(12);
    label.textColor = hexToColor(theme.textColor);
    label.lineLimit = 1;

    row.addSpacer();

    if (item.detail) {
      const detail = row.addText(item.detail);
      detail.font = Font.systemFont(10);
      detail.textColor = hexToColor(theme.secondaryTextColor);
      detail.lineLimit = 1;
    }

    stack.addSpacer(2);
  }
}

function renderStat(stack, section, theme) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  row.spacing = 6;

  const icon = getIcon(section.icon);
  if (icon) {
    const iconText = row.addText(icon);
    iconText.font = Font.systemFont(12);
  }

  const label = row.addText(section.label || "");
  label.font = Font.systemFont(12);
  label.textColor = hexToColor(theme.secondaryTextColor);

  row.addSpacer();

  const value = row.addText(String(section.value || ""));
  value.font = Font.boldSystemFont(14);
  value.textColor = hexToColor(theme.accentColor);
}

function renderStatsRow(stack, section, theme) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();

  const stats = section.stats || [];
  for (let i = 0; i < stats.length; i++) {
    if (i > 0) {
      row.addSpacer();
    }

    const col = row.addStack();
    col.layoutVertically();
    col.centerAlignContent();

    const value = col.addText(String(stats[i].value || "0"));
    value.font = Font.boldSystemFont(16);
    value.textColor = hexToColor(theme.accentColor);
    value.centerAlignText();

    const label = col.addText(stats[i].label || "");
    label.font = Font.systemFont(10);
    label.textColor = hexToColor(theme.secondaryTextColor);
    label.centerAlignText();
  }
}

function renderProgress(stack, section, theme) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  row.spacing = 8;

  const label = row.addText(section.label || "");
  label.font = Font.systemFont(12);
  label.textColor = hexToColor(theme.textColor);
  label.lineLimit = 1;

  row.addSpacer();

  const pct = Math.round((section.value || 0) * 100);
  const pctText = row.addText(`${pct}%`);
  pctText.font = Font.boldSystemFont(12);
  pctText.textColor = hexToColor(theme.accentColor);

  const barWidth = 200;
  const barHeight = 4;
  const dc = new DrawContext();
  dc.size = new Size(barWidth, barHeight);
  dc.opaque = false;
  dc.respectScreenScale = true;

  dc.setFillColor(hexToColor(theme.secondaryTextColor));
  dc.fillRect(new Rect(0, 0, barWidth, barHeight));

  const fillWidth = Math.round(barWidth * (section.value || 0));
  dc.setFillColor(hexToColor(theme.accentColor));
  dc.fillRect(new Rect(0, 0, fillWidth, barHeight));

  const barImg = stack.addImage(dc.getImage());
  barImg.imageSize = new Size(barWidth / 2, barHeight);
}

function renderCountdown(stack, section, theme) {
  const row = stack.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  row.spacing = 8;

  const label = row.addText(section.label || "");
  label.font = Font.systemFont(12);
  label.textColor = hexToColor(theme.textColor);
  label.lineLimit = 1;

  row.addSpacer();

  const target = new Date(section.target_date + "T00:00:00");
  const now = new Date();
  const diffMs = target - now;
  const days = Math.ceil(diffMs / (1000 * 60 * 60 * 24));

  let countText;
  if (days < 0) {
    countText = `${Math.abs(days)}d ago`;
  } else if (days === 0) {
    countText = "Today!";
  } else if (days === 1) {
    countText = "Tomorrow";
  } else {
    countText = `${days}d`;
  }

  const ct = row.addText(countText);
  ct.font = Font.boldSystemFont(14);
  ct.textColor = hexToColor(theme.accentColor);
}

function renderDivider(stack, section, theme) {
  const dc = new DrawContext();
  dc.size = new Size(300, 1);
  dc.opaque = false;
  dc.respectScreenScale = true;
  dc.setFillColor(hexToColor(theme.secondaryTextColor));
  dc.fillRect(new Rect(0, 0, 300, 1));

  const img = stack.addImage(dc.getImage());
  img.imageSize = new Size(300, 1);
}

function renderSection(stack, section, theme) {
  switch (section.type) {
    case "header":
      renderHeader(stack, section, theme);
      break;
    case "text":
      renderText(stack, section, theme);
      break;
    case "list":
      renderList(stack, section, theme);
      break;
    case "stat":
      renderStat(stack, section, theme);
      break;
    case "stats_row":
      renderStatsRow(stack, section, theme);
      break;
    case "progress":
      renderProgress(stack, section, theme);
      break;
    case "countdown":
      renderCountdown(stack, section, theme);
      break;
    case "divider":
      renderDivider(stack, section, theme);
      break;
    case "spacer":
      stack.addSpacer(6);
      break;
  }
}

function buildStructuredWidget(data) {
  const theme = Object.assign({}, DEFAULT_THEME, data.theme || {});
  const w = new ListWidget();
  w.backgroundColor = hexToColor(theme.backgroundColor);
  w.setPadding(12, 14, 12, 14);

  const title = w.addText(data.title || "Edward");
  title.font = Font.boldSystemFont(widgetFamily === "small" ? 14 : 16);
  title.textColor = hexToColor(theme.textColor);

  if (data.subtitle && widgetFamily !== "small") {
    const subtitle = w.addText(data.subtitle);
    subtitle.font = Font.systemFont(11);
    subtitle.textColor = hexToColor(theme.secondaryTextColor);
  }

  w.addSpacer(6);

  const sections = (data.sections || []).slice(0, maxSections);
  for (let i = 0; i < sections.length; i++) {
    renderSection(w, sections[i], theme);
    if (i < sections.length - 1) {
      w.addSpacer(4);
    }
  }

  w.addSpacer();

  const refreshDate = new Date();
  refreshDate.setMinutes(refreshDate.getMinutes() + 15);
  w.refreshAfterDate = refreshDate;
  // Tap → the hub's pet board (Biscuit's doses are the first thing on the widget).
  w.url = `${url}/hub/pets`;

  return w;
}

// ============================================================================
// MAIN
// ============================================================================

const data = await fetchWidgetData();

if (!data) {
  // Network error — show fallback
  const w = createErrorWidget("Could not connect");
  Script.setWidget(w);
  if (config.runsInWidget) {
    // no preview needed
  } else {
    w.presentMedium();
  }
  Script.complete();
} else if (data.script) {
  // Raw code mode — execute Edward's custom JS
  await executeWidgetCode(data.script);
} else {
  // Structured data mode — render from sections
  const widget = buildStructuredWidget(data);
  Script.setWidget(widget);
  if (!config.runsInWidget) {
    if (widgetFamily === "small") {
      widget.presentSmall();
    } else if (widgetFamily === "large") {
      widget.presentLarge();
    } else {
      widget.presentMedium();
    }
  }
  Script.complete();
}
