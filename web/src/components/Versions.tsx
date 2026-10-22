import { ChevronLeft, ChevronRight, Ellipsis } from "lucide-react";
import { useState } from "react";
import { post, put } from "../lib/api";
import type { ChatDetail, Message } from "../lib/types";
import { VERSIONS_WAIT } from "../lib/words";
import { CompareVersions } from "./CompareVersions";
import { BranchModal, DeleteBranchDialog } from "./DeleteBranchDialog";

export function VersionArrows({
  chatId,
  message,
  busy,
  readOnly,
  onChanged,
  onLook,
  onProblem,
}: {
  chatId: string;
  message: Message;
  busy: boolean;
  readOnly: boolean;
  onChanged: () => void;
  onLook?: (id: string) => void;
  onProblem: (problem: string | null) => void;
}) {
  const [moving, setMoving] = useState(false);
  const [menu, setMenu] = useState(false);
  const [dialog, setDialog] = useState<"name" | "delete" | null>(null);
  const [compare, setCompare] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [nameProblem, setNameProblem] = useState<string | null>(null);
  const versions = message.versions;
  if (!versions) return null;
  const what = message.role === "user" ? "Message" : "Answer";
  const held = busy && !readOnly;
  const currentName = versions.names?.[versions.index - 1];
  async function go(step: number) {
    const id = versions?.ids[versions.index - 1 + step];
    if (!id || moving) return;
    if (readOnly) {
      onLook?.(id);
      return;
    }
    setMoving(true);
    onProblem(null);
    try {
      await post(`/api/chats/${chatId}/messages/${id}/choose`);
      onChanged();
    } catch (error) {
      onProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setMoving(false);
    }
  }
  async function saveName() {
    setMoving(true);
    setNameProblem(null);
    try {
      await put(`/api/chats/${chatId}/messages/${message.id}/name`, { name: name || null });
      setDialog(null);
      onChanged();
    } catch (error) {
      setNameProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setMoving(false);
    }
  }
  const arrow = (step: number, label: string, Icon: typeof ChevronLeft, end: boolean) => (
    <button
      type="button"
      aria-label={label}
      title={held ? VERSIONS_WAIT : label}
      disabled={held || moving || end}
      onClick={() => void go(step)}
      className="act justify-center"
    >
      <Icon size={14} aria-hidden />
    </button>
  );
  return (
    <>
      <span
        role="group"
        aria-label={`${what} ${versions.index} of ${versions.count}`}
        title={held ? VERSIONS_WAIT : undefined}
        data-testid={versions.count > 1 ? "versions" : undefined}
        className="relative flex items-center gap-0.5 whitespace-nowrap"
      >
        {versions.count > 1 && (
          <>
            {arrow(
              -1,
              `Previous version of this ${what.toLowerCase()}`,
              ChevronLeft,
              versions.index <= 1,
            )}
            <span aria-hidden>
              {versions.index} of {versions.count}
              {currentName ? ` · ${currentName}` : ""}
            </span>
            {arrow(
              1,
              `Next version of this ${what.toLowerCase()}`,
              ChevronRight,
              versions.index >= versions.count,
            )}
          </>
        )}
        {(!readOnly || versions.count > 1) && (
          <button
            type="button"
            className="act"
            aria-label="Version actions"
            aria-expanded={menu}
            onClick={() => setMenu(!menu)}
          >
            <Ellipsis size={14} aria-hidden />
          </button>
        )}
        {menu && (
          <span className="absolute right-0 top-full z-10 flex flex-col rounded-plexus border border-line bg-bg p-2 shadow-lg">
            {!readOnly && (
              <button
                type="button"
                className="act"
                onClick={() => {
                  setName(currentName ?? "");
                  setDialog("name");
                  setMenu(false);
                }}
              >
                Name this version
              </button>
            )}
            {versions.ids.map(
              (id, i) =>
                id !== message.id && (
                  <button
                    type="button"
                    key={id}
                    className="act"
                    onClick={() => {
                      setCompare(id);
                      setMenu(false);
                    }}
                  >
                    Compare with version {i + 1}
                  </button>
                ),
            )}
            {!readOnly && (
              <button
                type="button"
                className="act btn-danger"
                disabled={busy}
                title={busy ? VERSIONS_WAIT : undefined}
                onClick={() => {
                  setDialog("delete");
                  setMenu(false);
                }}
              >
                {versions.count > 1 ? "Delete this version" : "Delete from here"}
              </button>
            )}
          </span>
        )}
      </span>
      {dialog === "name" && (
        <BranchModal title="Name this version" onClose={() => setDialog(null)}>
          {nameProblem && <p role="alert">{nameProblem}</p>}
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void saveName();
            }}
            className="my-4 flex flex-col gap-3"
          >
            <label>
              Version name
              <input
                aria-label="Version name"
                placeholder="Name, e.g. Shorter tone"
                maxLength={60}
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="input"
              />
            </label>
            <button type="submit" className="btn" disabled={moving}>
              Save name
            </button>
          </form>
        </BranchModal>
      )}
      {dialog === "delete" && (
        <DeleteBranchDialog
          chatId={chatId}
          messageId={message.id}
          version={versions.index}
          versionCount={versions.count}
          kind={message.role === "user" ? "message" : "answer"}
          onClose={() => setDialog(null)}
          onDeleted={() => {
            setDialog(null);
            onChanged();
          }}
        />
      )}
      {compare && (
        <CompareVersions
          chatId={chatId}
          ids={[message.id, compare]}
          readOnly={readOnly}
          busy={busy}
          onChanged={onChanged}
          onClose={() => setCompare(null)}
        />
      )}
    </>
  );
}

export function DeletedBranches({
  chatId,
  branches,
  busy,
  onChanged,
}: {
  chatId: string;
  branches: NonNullable<ChatDetail["deletedBranches"]>;
  busy: boolean;
  onChanged: () => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  async function undo(id: string) {
    setActing(true);
    try {
      await post(`/api/chats/${chatId}/messages/${id}/undo`);
      onChanged();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setActing(false);
    }
  }
  return (
    <div>
      {branches.map((branch) => (
        <p key={branch.id} role="status" className="notice notice--bar">
          {branch.problem ??
            (branch.state === "hidden"
              ? `${branch.message ?? "Version deleted."} You can restore it until ${new Date(branch.deleteAt * 1000).toLocaleDateString()}.`
              : branch.state === "restoring"
                ? "Restoring this version…"
                : "Deleting this version permanently…")}
          {branch.state === "hidden" && (
            <button
              type="button"
              className="act"
              disabled={busy || acting}
              onClick={() => void undo(branch.id)}
            >
              Undo
            </button>
          )}
        </p>
      ))}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
