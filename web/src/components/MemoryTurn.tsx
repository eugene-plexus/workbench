/**
 * What one turn was sent of the person's notes, in a chat's Memory drawer. The pinned notes go
 * in every turn; the rest are chosen again for each message. This says which were chosen for
 * the turn the person is looking at, how, and how much of the room they took. A chat with
 * several answers lets the person pick which turn to read.
 */

import { CircleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api } from "../lib/api";
import { failureWords, kindLabel, plural, tokenWords } from "../lib/memory";
import type { ChatMemory, ChatMemoryReadOnly, MemoryServerTurn, MemoryTurn } from "../lib/types";

const VIA: Record<MemoryTurn["notes"][number]["via"], string> = {
  words: "Found by words",
  meaning: "Found by meaning",
  both: "Found by meaning and words",
  recent: "Newest first",
};

/** How the turn's notes were chosen, in a sentence. */
export function howWords(turn: MemoryTurn): string {
  if (turn.how === "all") return turn.why ?? "Every note that fits was used.";
  if (turn.how === "words") {
    return turn.why ?? `Chosen from ${plural(turn.from, "note")} by words.`;
  }
  return `Chosen from ${plural(turn.from, "note")} by meaning and words.`;
}

/** The owner reading someone else's chat: how many notes the turn was sent, never which. */
export function ReadOnlyTurn({ view }: { view: ChatMemoryReadOnly }) {
  const turn = view.turn;
  if (!turn) return null;
  return (
    <p data-testid="memory-turn-counts" className="help">
      The latest answer was sent {plural(turn.notes, "note")} chosen for it
      {turn.budget ? `. ${tokenWords(turn.tokens ?? 0, turn.budget)}.` : "."}
      {turn.pastChats ? ` It was also sent ${plural(turn.pastChats, "past exchange")}.` : ""}
      {turn.memoryServer
        ? ` It was also sent ${plural(turn.memoryServer, "memory", "memories")} from the person's memory server.`
        : ""}
    </p>
  );
}

export function ThisTurn({ chatId, view }: { chatId: string; view: ChatMemory }) {
  const uid = useId();
  const turns = view.turns ?? [];
  const latest = view.turn ?? null;
  // The turn picked from the list, read from the server; the latest answer's comes with the view.
  const [picked, setPicked] = useState<MemoryTurn | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  // A new answer is a new latest turn: the one that was picked is let go.
  useEffect(() => {
    setPicked(null);
  }, [latest?.messageId]);

  async function pick(id: string) {
    setProblem(null);
    if (id === latest?.messageId) {
      setPicked(null);
      return;
    }
    try {
      const got = await api<ChatMemory>(
        `/api/chats/${encodeURIComponent(chatId)}/memory?message=${encodeURIComponent(id)}`,
      );
      setPicked(got.turn ?? null);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  const chosen = picked ?? latest;
  // A turn sent past chats and no notes keeps no notes' record (MV2).
  const turn = chosen && Array.isArray(chosen.notes) ? chosen : null;
  // A person with no notes may still have been sent their memory server's results (MV5).
  if (!turn && view.relevantBudget == null && !chosen?.memoryServer) return null;
  return (
    <section
      aria-labelledby={`${uid}-turn`}
      data-testid="memory-this-turn"
      className="flex flex-col gap-1"
    >
      <h3 id={`${uid}-turn`} className="eyebrow text-muted">
        This turn
      </h3>
      {view.relevantBudget != null && (
        <p data-testid="memory-turn-budget" className="help">
          Notes that bear on each message are chosen again for it, up to{" "}
          {view.relevantBudget.toLocaleString()} tokens a turn.{" "}
          {view.searchTool
            ? "This model can search your notes."
            : "This model cannot search your notes."}
        </p>
      )}
      {turns.length > 1 && (
        <div className="field">
          <label htmlFor={`${uid}-pick`} className="field-label">
            Turn
          </label>
          <select
            id={`${uid}-pick`}
            data-testid="memory-turn-pick"
            value={chosen?.messageId ?? ""}
            onChange={(event) => void pick(event.target.value)}
            className="control w-56 max-w-full"
          >
            {turns.map((id, index) => (
              <option key={id} value={id}>
                {index === turns.length - 1
                  ? `Answer ${index + 1} (latest)`
                  : `Answer ${index + 1}`}
              </option>
            ))}
          </select>
        </div>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {turn ? (
        <>
          <p data-testid="memory-turn-summary" className="help">
            {turn.notes.length === 0
              ? "No notes were chosen for this turn."
              : `${plural(turn.notes.length, "note")} chosen.`}{" "}
            {tokenWords(turn.tokens, turn.budget)}.
            {turn.didNotFit > 0 ? ` ${turn.didNotFit.toLocaleString()} more did not fit.` : ""}
          </p>
          <p data-testid="memory-turn-how" className="help">
            {howWords(turn)}
          </p>
          {turn.notes.length > 0 && (
            <ul data-testid="memory-turn-notes" className="divide-y divide-line text-sm">
              {turn.notes.map((one) => (
                <li key={one.id} className="flex flex-col gap-1 py-2">
                  <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
                    <span dir="auto" className="min-w-0 flex-1 basis-56 wrap-anywhere">
                      {one.text}
                    </span>
                    <span className="flex flex-wrap gap-1.5">
                      <span className="badge">{kindLabel(one.kind)}</span>
                      {one.tags.map((tag) => (
                        <span key={tag} className="badge">
                          {tag}
                        </span>
                      ))}
                    </span>
                  </div>
                  <span className="text-xs text-muted">
                    {VIA[one.via]}
                    {one.passages.length > 0
                      ? `. ${plural(one.passages.length, "part")} of its details sent.`
                      : "."}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="help">No answer in this chat has been sent notes yet.</p>
      )}
      {chosen?.memoryServer && <ServerTurn server={chosen.memoryServer} />}
    </section>
  );
}

/** What the person's own memory server gave the turn (MV5), or why it gave nothing. */
function ServerTurn({ server }: { server: MemoryServerTurn }) {
  const name = server.name || "Your memory server";
  return (
    <div data-testid="memory-turn-server" className="flex flex-col gap-1">
      <h4 className="text-sm font-medium">From {name}</h4>
      {server.why ? (
        <p className="help">{server.why}</p>
      ) : (
        <p className="help">
          {server.memories.length === 0
            ? "It returned nothing for this turn."
            : `${plural(server.memories.length, "memory", "memories")} sent.`}
          {server.dropped
            ? ` Left out for looking like a password or key: ${plural(server.dropped, "result")}.`
            : ""}
          {server.didNotFit ? ` ${server.didNotFit.toLocaleString()} more did not fit.` : ""}
        </p>
      )}
      {server.memories.length > 0 && (
        <ul className="divide-y divide-line text-sm">
          {server.memories.map((one, index) => (
            <li key={index} dir="auto" className="py-2 wrap-anywhere">
              {one.text}
              {one.date && <span className="text-xs text-muted"> ({one.date})</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
