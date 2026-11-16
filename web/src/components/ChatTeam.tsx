/**
 * The team collections a chat searches (row 47), in its settings: each collection the person
 * can read, chosen or not, kept at once. The boxes show what the server kept, never what was
 * asked for. A project's own documents, and what its owner chose for it, are searched too, and
 * said under the boxes.
 */

import { CircleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { problemWords } from "../lib/projects";
import {
  type ChatCollections,
  chatCollections,
  projectCollections,
  putChatCollections,
  putProjectCollections,
} from "../lib/team";

/** What the server said, with a list where it gave none. */
function shape(got: Partial<ChatCollections> | null | undefined): ChatCollections {
  return {
    chosen: Array.isArray(got?.chosen) ? got.chosen : [],
    available: Array.isArray(got?.available) ? got.available : [],
    searched: Array.isArray(got?.searched) ? got.searched : [],
    held: Array.isArray(got?.held) ? got.held : [],
  };
}

export function ChatTeam({ chatId, projectId }: { chatId?: string; projectId?: string }) {
  const [data, setData] = useState<ChatCollections | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const read = chatId
      ? chatCollections(chatId)
      : projectId
        ? projectCollections(projectId)
        : null;
    read
      ?.then((got) => {
        setData(shape(got));
        setProblem(null);
      })
      .catch((failure) => setProblem(problemWords(failure)));
  }, [chatId, projectId]);

  if (!data || (data.available.length === 0 && data.chosen.length === 0)) return null;
  const chosen = new Set(data.chosen.map((c) => c.id));

  async function toggle(id: string) {
    const next = chosen.has(id) ? [...chosen].filter((c) => c !== id) : [...chosen, id];
    setBusy(true);
    try {
      setData(
        shape(
          chatId
            ? await putChatCollections(chatId, next)
            : await putProjectCollections(projectId as string, next),
        ),
      );
      setProblem(null);
    } catch (failure) {
      setProblem(problemWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const extra = (data.searched ?? []).filter((c) => !chosen.has(c.id));
  const sent = new Set((data.searched ?? []).map((c) => c.id));
  const why = new Map((data.held ?? []).map((c) => [c.id, c.why]));
  // What the next answer is sent, never only what was chosen (settings never lie).
  const stateWords = (id: string) =>
    !chosen.has(id) ? "Not chosen" : sent.has(id) ? "Searched" : (why.get(id) ?? "Not sent");
  return (
    <fieldset className="flex min-w-0 flex-col gap-2" data-testid="chat-team">
      <legend className="eyebrow mb-3 text-muted">Team collections</legend>
      {data.available.map((collection) => (
        <label key={collection.id} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={chosen.has(collection.id)}
            disabled={busy}
            onChange={() => void toggle(collection.id)}
            className="accent-accent"
          />
          {collection.name}: {stateWords(collection.id)}
        </label>
      ))}
      <p className="help">
        {chatId
          ? "A chosen collection is searched in every answer of this chat."
          : "A chosen collection is searched in every chat in this project, for each person who can read it."}
        {extra.length > 0 && ` Also searched here: ${extra.map((c) => c.name).join(", ")}.`}
      </p>
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
    </fieldset>
  );
}
