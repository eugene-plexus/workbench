import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";

export function BranchModal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) {
      if (typeof el.showModal === "function") el.showModal();
      else el.setAttribute("open", "");
    }
    close.current?.focus();
    return () => {
      if (el?.open && typeof el.close === "function") el.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      className="artifact-dialog p-4"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="flex items-center justify-between gap-4">
        <h2 id={id}>{title}</h2>
        <button type="button" ref={close} className="act" onClick={onClose}>
          Close
        </button>
      </div>
      {children}
    </dialog>
  );
}

type Summary = {
  token?: string;
  messages: number;
  answers: number;
  files: number;
  artifacts: number;
  pendingNotes: number;
  keptNotes: number;
};

export function DeleteBranchDialog({
  chatId,
  messageId,
  version,
  versionCount,
  kind = "answer",
  onClose,
  onDeleted,
}: {
  chatId: string;
  messageId: string;
  version: number;
  versionCount: number;
  kind?: "answer" | "message";
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const path = `/api/chats/${chatId}/messages/${messageId}`;
  useEffect(() => {
    let live = true;
    api<Summary>(`${path}/branch`).then(
      (next) => {
        if (live) setSummary(next);
      },
      (error: unknown) => {
        if (live) setProblem(String(error));
      },
    );
    return () => {
      live = false;
    };
  }, [path]);
  async function remove() {
    setActing(true);
    try {
      const expected = summary?.token ? `?expected=${encodeURIComponent(summary.token)}` : "";
      await api(`${path}${expected}`, { method: "DELETE" });
      onDeleted();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setActing(false);
    }
  }
  return (
    <BranchModal title="Delete this version?" onClose={onClose}>
      {summary ? (
        <p className="my-4">
          Delete {kind} {version} of {versionCount} and the {summary.messages - 1} messages after
          it? {summary.files} {summary.files === 1 ? "file" : "files"} and {summary.artifacts}{" "}
          {summary.artifacts === 1 ? "artifact" : "artifacts"} go with them. {summary.pendingNotes}{" "}
          pending {summary.pendingNotes === 1 ? "note goes" : "notes go"} too. Notes kept from them
          stay. You can Undo for 30 days. Then they are deleted permanently.
        </p>
      ) : (
        <p role="status">Counting what goes…</p>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <button type="button" className="btn" onClick={onClose} disabled={acting}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-danger"
          disabled={!summary || acting}
          onClick={() => void remove()}
        >
          {acting ? "Deleting…" : "Delete version"}
        </button>
      </div>
    </BranchModal>
  );
}
