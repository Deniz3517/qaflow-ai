/**
 * MarkdownView — minimal Markdown renderer.
 *
 * Renders the subset of Markdown the validation report uses:
 *   - # / ## / ### headings
 *   - paragraphs
 *   - fenced ``` code blocks
 *   - unordered lists (- / *)
 *   - GitHub-style task lists (- [ ] / - [x])
 *   - GFM tables (| col | col |)
 *   - **bold** and `code` inline
 *
 * This is a deliberate ~120 line implementation rather than pulling in
 * react-markdown — the validation report's needs are bounded.
 */

import { useMemo } from "react";


export default function MarkdownView({ source }: { source: string }) {
  const blocks = useMemo(() => parseBlocks(source), [source]);
  return (
    <div className="prose-qa space-y-3 text-sm leading-relaxed text-slate-200">
      {blocks.map((b, i) => renderBlock(b, i))}
    </div>
  );
}


type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "para"; text: string }
  | { kind: "ul"; items: { text: string; checked: boolean | null }[] }
  | { kind: "code"; text: string; lang?: string }
  | { kind: "table"; head: string[]; rows: string[][] };


function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Blank → skip
    if (line.trim() === "") { i++; continue; }

    // Fenced code
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim() || undefined;
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // consume closing fence
      out.push({ kind: "code", text: buf.join("\n"), lang });
      continue;
    }

    // Heading
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      out.push({
        kind: "heading",
        level: h[1].length as 1 | 2 | 3,
        text: h[2].trim(),
      });
      i++; continue;
    }

    // Table — header row + separator + body rows
    if (line.includes("|") && i + 1 < lines.length && /^[\s|:\-]+$/.test(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitRow(lines[i]));
        i++;
      }
      out.push({ kind: "table", head, rows });
      continue;
    }

    // List
    if (/^\s*[-*]\s/.test(line)) {
      const items: { text: string; checked: boolean | null }[] = [];
      while (i < lines.length && /^\s*[-*]\s/.test(lines[i])) {
        const m = /^\s*[-*]\s+(?:\[([ xX])\]\s+)?(.*)$/.exec(lines[i]);
        if (m) {
          const checked = m[1] == null ? null : m[1].toLowerCase() === "x";
          items.push({ text: m[2], checked });
        }
        i++;
      }
      out.push({ kind: "ul", items });
      continue;
    }

    // Paragraph (greedy until blank or block-starting line)
    const buf: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== "" &&
           !lines[i].startsWith("```") &&
           !/^(#{1,3})\s+/.test(lines[i]) &&
           !/^\s*[-*]\s/.test(lines[i]) &&
           !(lines[i].includes("|") && i + 1 < lines.length && /^[\s|:\-]+$/.test(lines[i + 1] || ""))) {
      buf.push(lines[i]);
      i++;
    }
    out.push({ kind: "para", text: buf.join(" ").trim() });
  }
  return out;
}


function splitRow(line: string): string[] {
  // Trim leading/trailing pipe, split on |, trim each cell.
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map(s => s.trim());
}


function renderBlock(b: Block, key: number): React.ReactNode {
  if (b.kind === "heading") {
    const cls =
      b.level === 1 ? "text-xl font-bold text-emerald-200 mt-4"
        : b.level === 2 ? "text-base font-semibold text-slate-100 mt-3 border-b border-slate-700 pb-1"
          : "text-sm font-semibold text-slate-200 mt-2";
    return <div key={key} className={cls}>{renderInline(b.text)}</div>;
  }
  if (b.kind === "para") {
    return <p key={key} className="text-slate-300">{renderInline(b.text)}</p>;
  }
  if (b.kind === "ul") {
    return (
      <ul key={key} className="space-y-0.5">
        {b.items.map((it, j) => (
          <li key={j} className="flex items-start gap-2">
            {it.checked !== null && (
              <input
                type="checkbox"
                checked={!!it.checked}
                disabled
                className="mt-1 accent-emerald-500"
              />
            )}
            {it.checked === null && <span className="mt-1 text-slate-500">•</span>}
            <span className="text-slate-300">{renderInline(it.text)}</span>
          </li>
        ))}
      </ul>
    );
  }
  if (b.kind === "code") {
    return (
      <pre key={key} className="overflow-auto rounded bg-slate-950/60 p-3 text-xs text-emerald-200">
        <code>{b.text}</code>
      </pre>
    );
  }
  if (b.kind === "table") {
    return (
      <div key={key} className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              {b.head.map((c, j) => (
                <th key={j} className="border-b border-slate-700 px-2 py-1 text-left font-semibold text-slate-200">
                  {renderInline(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {b.rows.map((row, j) => (
              <tr key={j} className="hover:bg-slate-800/30">
                {row.map((cell, k) => (
                  <td key={k} className="border-b border-slate-800 px-2 py-1 text-slate-300">
                    {renderInline(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return null;
}


// Render inline markdown: **bold**, *italic*, `code`, [text](url).
function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  // Combined regex; first match wins per iteration.
  const rx = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)]+)\))/g;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(text)) !== null) {
    if (m.index > i) parts.push(text.slice(i, m.index));
    if (m[1]) parts.push(<strong key={key++} className="text-slate-100">{m[2]}</strong>);
    else if (m[3]) parts.push(<code key={key++} className="rounded bg-slate-800 px-1 py-0.5 text-emerald-200">{m[4]}</code>);
    else if (m[5]) parts.push(<a key={key++} href={m[7]} className="text-emerald-300 underline" target="_blank" rel="noreferrer">{m[6]}</a>);
    i = m.index + m[0].length;
  }
  if (i < text.length) parts.push(text.slice(i));
  return parts;
}
