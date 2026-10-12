// A markdown table still being written (2026-09-26 review).
//
// While Edward streams, a table's header row arrives before its separator
// row, and until the separator is complete (one "---" cell per header cell)
// GFM renders the header as literal pipes: "| Week | Miles | Long". A
// stopped answer kept them for good. The chat holds such a trailing table
// back while the turn is live and drops it once the owner stopped the turn. Pure,
// so it runs under node --test.

const SEPARATOR_CELL = /^\s*:?-{1,}:?\s*$/;

function cells(line: string): string[] {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|")) t = t.slice(0, -1);
  return t.split("|");
}

/** `content` without a trailing table whose separator row is missing or incomplete. */
export function withoutUnfinishedTable(content: string): string {
  if (!content) return content;
  const trimmed = content.replace(/\s+$/, "");
  const cut = trimmed.lastIndexOf("\n\n");
  const block = cut < 0 ? trimmed : trimmed.slice(cut + 2);
  const lines = block.split("\n");
  if (!lines.length || !lines.every((l) => l.trim().startsWith("|"))) return content;
  const header = cells(lines[0]).length;
  const sep = lines[1];
  const sepCells = sep === undefined ? [] : cells(sep);
  const complete =
    sep !== undefined &&
    sep.trim().endsWith("|") === lines[0].trim().endsWith("|") &&
    sepCells.length === header &&
    sepCells.every((c) => SEPARATOR_CELL.test(c) && c.includes("---"));
  if (complete) return content;
  return cut < 0 ? "" : trimmed.slice(0, cut).replace(/\s+$/, "");
}
