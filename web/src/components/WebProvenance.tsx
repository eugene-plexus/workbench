import { useState } from "react";
import { api, patch, post } from "../lib/api";
import { failureWords } from "../lib/memory";
import type { NoteDocument } from "../lib/types";

type WebTool = { serverId: string; tool: string; name: string };
function pageUrl(source?: string | null) {
  try {
    const url = new URL(source ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url : null;
  } catch {
    return null;
  }
}
export function WebProvenance({ noteId, link }: { noteId: string; link: NoteDocument }) {
  const [tools, setTools] = useState<WebTool[] | null>(null);
  const [choice, setChoice] = useState(0);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [checkedHash, setCheckedHash] = useState<string | null>(null);
  const day = new Date(link.linkedAt * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
  const url = pageUrl(link.source);
  async function open() {
    setBusy(true);
    setProblem(null);
    try {
      const found = await api<{ tools: WebTool[] }>("/api/memory/web-tools");
      setTools(found.tools);
      setChoice(0);
    } catch (error) {
      setProblem(failureWords(error));
    } finally {
      setBusy(false);
    }
  }
  async function check() {
    const selected = tools?.[choice];
    if (!selected) return;
    setBusy(true);
    setProblem(null);
    try {
      const found = await post<{ changed: boolean; canCompare: boolean; sha256: string }>(
        `/api/memory/notes/${encodeURIComponent(noteId)}/documents/${encodeURIComponent(link.docId)}/check`,
        { serverId: selected.serverId, tool: selected.tool },
      );
      setCheckedHash(found.changed || !found.canCompare ? found.sha256 : null);
      setResult(
        !found.canCompare
          ? "The original answer supplied no page text or hash, so this page cannot be compared."
          : found.changed
            ? `Still true? '${link.name}' changed on the web since ${day}.`
            : "The page has the same words.",
      );
      setTools(null);
    } catch (error) {
      setProblem(failureWords(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-2">
      <p>
        {link.scope === "web"
          ? `From a web page: ${link.name}${url ? ` (${url.hostname})` : ""}, read ${day}`
          : `From a tool result: ${link.name}, read ${day}`}
      </p>
      {url && (
        <div className="flex gap-2">
          <a
            data-testid="provenance-open"
            className="link"
            href={url.href}
            target="_blank"
            rel="noreferrer"
          >
            Open
          </a>
          <button
            type="button"
            data-testid="provenance-check"
            className="btn"
            disabled={busy}
            onClick={() => void open()}
          >
            Check again
          </button>
        </div>
      )}
      {tools && (
        <div className="flex flex-col gap-2">
          {tools.length ? (
            <>
              <label>
                Web page tool
                <select
                  data-testid="provenance-tool"
                  className="control"
                  value={choice}
                  onChange={(e) => setChoice(Number(e.target.value))}
                >
                  {tools.map((tool, index) => (
                    <option key={`${tool.serverId}/${tool.tool}`} value={index}>
                      {tool.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                data-testid="provenance-compare"
                className="btn"
                disabled={busy}
                onClick={() => void check()}
              >
                Read page and compare
              </button>
            </>
          ) : (
            <p>No web page tool is connected. Add one in Tools.</p>
          )}
          <button
            type="button"
            data-testid="provenance-cancel"
            className="btn"
            onClick={() => setTools(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {result && <p role="status">{result}</p>}
      {checkedHash && (
        <button
          type="button"
          data-testid="provenance-still-true"
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setProblem(null);
            try {
              await patch(
                `/api/memory/notes/${encodeURIComponent(noteId)}/documents/${encodeURIComponent(link.docId)}/check`,
                { sha256: checkedHash },
              );
              setCheckedHash(null);
              setResult("You kept this note with the page as it is now.");
            } catch (error) {
              setProblem(failureWords(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          Still true
        </button>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
