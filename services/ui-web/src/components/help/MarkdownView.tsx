"use client";
import React from "react";

/**
 * Minimal, dependency-free Markdown renderer for the Help Center. Supports the
 * controlled subset the help content uses: ## / ### headings, paragraphs,
 * **bold**, *italic*, `code`, <kbd>, [links](url), > blockquotes, ordered and
 * unordered lists, and --- rules. Content is authored in-repo (not user input),
 * so the supported subset is kept in sync with the authored articles and there is
 * no untrusted-HTML surface.
 */

let keySeq = 0;
const nextKey = () => `md-${keySeq++}`;

/** Inline: bold, italic, inline code, <kbd>text</kbd>, links. Order matters —
 * code and kbd are matched first so their contents aren't re-formatted. */
function renderInline(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const pattern =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(<kbd>[^<]+<\/kbd>)|(\[[^\]]+\]\([^)]+\))|(\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        <code key={nextKey()} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(<strong key={nextKey()}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("<kbd>")) {
      out.push(
        <kbd
          key={nextKey()}
          className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[0.8em] shadow-sm"
        >
          {tok.slice(5, -6)}
        </kbd>,
      );
    } else if (tok.startsWith("[")) {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok);
      if (mm) {
        const href = mm[2];
        const internal = href.startsWith("/");
        out.push(
          <a
            key={nextKey()}
            href={href}
            className="text-primary underline underline-offset-2"
            {...(internal ? {} : { target: "_blank", rel: "noreferrer" })}
          >
            {mm[1]}
          </a>,
        );
      } else {
        out.push(tok);
      }
    } else if (tok.startsWith("*")) {
      out.push(<em key={nextKey()}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

interface Block {
  type: "h2" | "h3" | "p" | "ul" | "ol" | "quote" | "hr";
  lines: string[];
}

function parseBlocks(md: string): Block[] {
  const rawLines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  const isUl = (l: string) => /^\s*[-*]\s+/.test(l);
  const isOl = (l: string) => /^\s*\d+\.\s+/.test(l);
  while (i < rawLines.length) {
    const line = rawLines[i];
    if (line.trim() === "") { i++; continue; }
    if (line.startsWith("### ")) { blocks.push({ type: "h3", lines: [line.slice(4)] }); i++; continue; }
    if (line.startsWith("## ")) { blocks.push({ type: "h2", lines: [line.slice(3)] }); i++; continue; }
    if (/^---+\s*$/.test(line)) { blocks.push({ type: "hr", lines: [] }); i++; continue; }
    if (line.startsWith(">")) {
      const lines: string[] = [];
      while (i < rawLines.length && rawLines[i].startsWith(">")) {
        lines.push(rawLines[i].replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({ type: "quote", lines });
      continue;
    }
    if (isUl(line) || isOl(line)) {
      const ordered = isOl(line);
      const items: string[] = [];
      while (i < rawLines.length) {
        const l = rawLines[i];
        if (ordered ? isOl(l) : isUl(l)) {
          items.push(l.replace(/^\s*(?:[-*]|\d+\.)\s+/, ""));
          i++;
        } else if (l.trim() !== "" && /^\s+\S/.test(l) && items.length > 0) {
          // indented continuation line — fold into the current list item
          items[items.length - 1] += " " + l.trim();
          i++;
        } else {
          break; // blank / structural / opposite marker ends the list
        }
      }
      blocks.push({ type: ordered ? "ol" : "ul", lines: items });
      continue;
    }
    // paragraph: accumulate until blank / structural line
    const lines: string[] = [];
    while (
      i < rawLines.length &&
      rawLines[i].trim() !== "" &&
      !rawLines[i].startsWith("#") &&
      !rawLines[i].startsWith(">") &&
      !/^---+\s*$/.test(rawLines[i]) &&
      !isUl(rawLines[i]) &&
      !isOl(rawLines[i])
    ) {
      lines.push(rawLines[i]);
      i++;
    }
    blocks.push({ type: "p", lines });
  }
  return blocks;
}

export function MarkdownView({ children }: { children: string }) {
  const blocks = parseBlocks(children.trim());
  return (
    <div className="space-y-3 text-sm leading-relaxed text-foreground/90">
      {blocks.map((b) => {
        const key = nextKey();
        switch (b.type) {
          case "h2":
            return <h2 key={key} className="mt-6 text-lg font-semibold text-foreground">{renderInline(b.lines[0])}</h2>;
          case "h3":
            return <h3 key={key} className="mt-4 text-base font-semibold text-foreground">{renderInline(b.lines[0])}</h3>;
          case "hr":
            return <hr key={key} className="my-4 border-border" />;
          case "quote":
            return (
              <blockquote key={key} className="rounded-r border-l-4 border-primary/40 bg-muted/40 py-2 pl-4 pr-3 text-muted-foreground">
                {b.lines.map((l) => <p key={nextKey()}>{renderInline(l)}</p>)}
              </blockquote>
            );
          case "ul":
            return (
              <ul key={key} className="ml-5 list-disc space-y-1">
                {b.lines.map((l) => <li key={nextKey()}>{renderInline(l)}</li>)}
              </ul>
            );
          case "ol":
            return (
              <ol key={key} className="ml-5 list-decimal space-y-1">
                {b.lines.map((l) => <li key={nextKey()}>{renderInline(l)}</li>)}
              </ol>
            );
          default:
            return <p key={key}>{renderInline(b.lines.join(" "))}</p>;
        }
      })}
    </div>
  );
}
