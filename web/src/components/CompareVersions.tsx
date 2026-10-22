import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import type { ChatDetail, Message } from "../lib/types";
import { Markdown } from "./Markdown";
import { BranchModal } from "./DeleteBranchDialog";

export function CompareVersions({
  chatId,
  ids,
  readOnly,
  busy,
  onChanged,
  onClose,
}: {
  chatId: string;
  ids: [string, string];
  readOnly: boolean;
  busy: boolean;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [paths, setPaths] = useState<[ChatDetail, ChatDetail] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [selected, setSelected] = useState(0);
  const [choosing, setChoosing] = useState(false);
  const [a, b] = ids;
  useEffect(() => {
    let live = true;
    Promise.all(
      [a, b].map((id) => api<ChatDetail>(`/api/chats/${chatId}?via=${encodeURIComponent(id)}`)),
    ).then(
      (next) => {
        if (live) setPaths([next[0]!, next[1]!]);
      },
      (error: unknown) => {
        if (live) setProblem(error instanceof Error ? error.message : String(error));
      },
    );
    return () => {
      live = false;
    };
  }, [chatId, a, b]);
  let shared = 0;
  if (paths)
    while (
      shared < Math.min(paths[0].messages.length, paths[1].messages.length) &&
      paths[0].messages[shared]!.id === paths[1].messages[shared]!.id
    )
      shared++;
  const show = (message: Message) => (
    <div key={message.id} className="my-3">
      <p className="text-xs text-muted">{message.role === "user" ? "You" : "Answer"}</p>
      {message.redacted ? (
        <p>This message is hidden from the owner's view.</p>
      ) : (
        <>
          <Markdown text={message.content} />
          {message.files?.map((file) => (
            <p key={file.id}>{file.name}</p>
          ))}
        </>
      )}
    </div>
  );
  async function choose(id: string) {
    setChoosing(true);
    try {
      await post(`/api/chats/${chatId}/messages/${id}/choose`);
      onChanged();
      onClose();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setChoosing(false);
    }
  }
  return (
    <BranchModal title="Compare versions" onClose={onClose}>
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      {!paths && !problem && <p role="status">Loading versions…</p>}
      {paths && (
        <>
          <div>{paths[0].messages.slice(0, shared).map(show)}</div>
          <div className="flex gap-2 md:hidden" role="group" aria-label="Version to read">
            {paths.map((_, i) => (
              <button
                type="button"
                key={i}
                className="btn"
                aria-pressed={selected === i}
                onClick={() => setSelected(i)}
              >
                Version {i + 1}
              </button>
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {paths.map((path, i) => {
              const root = path.messages.find((m) => m.id === ids[i]);
              const index = root?.versions?.index ?? i + 1;
              const name = root?.versions?.names?.[index - 1];
              return (
                <section
                  key={ids[i]}
                  className={`${selected === i ? "block" : "hidden"} min-w-0 md:block`}
                >
                  <h3>
                    {[`${root?.role === "user" ? "Message" : "Answer"} ${index}`, name, root?.model]
                      .filter(Boolean)
                      .join(" · ")}
                  </h3>
                  {path.messages.slice(shared).map(show)}
                  {!readOnly && (
                    <button
                      type="button"
                      className="btn"
                      disabled={busy || choosing}
                      onClick={() => void choose(ids[i]!)}
                    >
                      Choose this one
                    </button>
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </BranchModal>
  );
}
