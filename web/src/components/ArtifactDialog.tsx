/**
 * An artifact beside the chat (workbench-rich-content-roadmap.md RC5): its
 * preview, its source to edit, its revisions and a download.
 *
 * - Markdown reads as an answer does (Markdown.tsx); SVG is a picture by RC2's
 *   rules (lib/svgArtifact.ts); a page or a component runs only when the
 *   person presses Run, in a frame of its own (ArtifactPreview.tsx).
 * - An edit is kept as a new revision; none is written over. Until the first
 *   is saved, the artifact is the answer's block and nothing is kept
 *   (artifacts.py). An edit not yet saved stays while the page is open
 *   (lib/artifacts.ts `keepDraft`), and another revision is shown only once
 *   it is saved or discarded.
 * - The owner reading someone's chat reads the artifacts of what they may
 *   read, and keeps nothing.
 */

import { Download, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api, ApiError, post } from "../lib/api";
import { lineDiff, type DiffLine } from "../lib/lineDiff";
import {
  type ArtifactView,
  type RevisionView,
  byteSize,
  digest,
  draftOf,
  fileName,
  KIND_NAMES,
  keepDraft,
  keptFor,
  MAX_SOURCE,
  runs,
  revisionOrigin,
} from "../lib/artifacts";
import { ArtifactScope, type Opened, type Scope } from "./Artifact";
import { SvgPicture } from "./ArtifactBody";
import { ArtifactPreview } from "./ArtifactPreview";
import { Markdown } from "./Markdown";

function save(text: string, name: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function when(seconds: number | null): string {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function revisionLabel(revision: RevisionView, count: number, tool?: string): string {
  const by = revision.author === "answer" ? revisionOrigin(revision.tool ?? tool) : "edited";
  const at = when(revision.createdAt);
  return `Revision ${revision.number} of ${count}, ${by}${at ? `, ${at}` : ""}`;
}

type Tab = "preview" | "source";
type ShownTab = Tab | "changes";

export default function ArtifactDialog({
  scope,
  opened,
  onClose,
}: {
  scope: Scope;
  opened: Opened;
  onClose: () => void;
}) {
  const { kind, language, original } = opened;
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const uid = useId();
  const key = useMemo(
    () => `${scope.messageId}:${kind}:${digest(original)}`,
    [scope.messageId, kind, original],
  );
  const summary = keptFor(scope.artifacts, kind, original);
  const [kept, setKept] = useState<ArtifactView | null>(null);
  const [loading, setLoading] = useState(summary !== null);
  const [sources, setSources] = useState<Map<number, string>>(() => new Map([[1, original]]));
  const [selected, setSelected] = useState(1);
  const [compare, setCompare] = useState<number | null>(null);
  const [compareProblem, setCompareProblem] = useState("");
  const [draft, setDraft] = useState<string | null>(() => draftOf(key)?.text ?? null);
  // The revision the draft began from; unknown once a newer one was found saved.
  const [base, setBase] = useState<number | null>(() => draftOf(key)?.base ?? null);
  const [tab, setTab] = useState<Tab>("preview");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const title = kept?.title ?? opened.title;

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) {
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
    }
    close.current?.focus();
    return () => {
      if (element?.open && typeof element.close === "function") element.close();
    };
  }, []);

  // What is kept of it, if anything: shown at its last revision.
  const summaryId = summary?.id;
  useEffect(() => {
    if (!summaryId) return;
    let gone = false;
    api<ArtifactView>(`/api/chats/${scope.chatId}/artifacts/${summaryId}`).then(
      (view) => {
        if (gone) return;
        setKept(view);
        setSelected(view.revisionList[view.revisionList.length - 1]?.number ?? 1);
        setLoading(false);
      },
      (error: unknown) => {
        if (gone) return;
        setProblem(
          `Its saved revisions could not be read: ${error instanceof Error ? error.message : String(error)}`,
        );
        setLoading(false);
      },
    );
    return () => {
      gone = true;
    };
  }, [scope.chatId, summaryId]);

  // A revision's source, read when it is first shown.
  const missing = !sources.has(selected) && kept !== null;
  useEffect(() => {
    if (!missing || !kept) return;
    let gone = false;
    api<RevisionView>(`/api/chats/${scope.chatId}/artifacts/${kept.id}/revisions/${selected}`).then(
      (revision) => {
        if (!gone) setSources((was) => new Map(was).set(selected, revision.source ?? ""));
      },
      (error: unknown) => {
        if (!gone) {
          setProblem(
            `Revision ${selected} could not be read: ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      },
    );
    return () => {
      gone = true;
    };
  }, [missing, kept, selected, scope.chatId]);

  const compareMissing = compare !== null && !sources.has(compare);
  useEffect(() => {
    if (!compareMissing || compare === null || !kept) return;
    let gone = false;
    api<RevisionView>(`/api/chats/${scope.chatId}/artifacts/${kept.id}/revisions/${compare}`).then(
      (revision) => {
        if (!gone) setSources((was) => new Map(was).set(compare, revision.source ?? ""));
      },
      (error: unknown) => {
        if (!gone)
          setCompareProblem(
            `Revision ${compare} could not be read: ${error instanceof Error ? error.message : String(error)}`,
          );
      },
    );
    return () => {
      gone = true;
    };
  }, [compareMissing, compare, kept, scope.chatId]);

  const revisions: RevisionView[] = kept?.revisionList ?? [
    { number: 1, author: "answer", size: byteSize(original), createdAt: 0 },
  ];
  const current = revisions.find((r) => r.number === selected) ?? revisions[0];
  const saved = sources.get(selected);
  const shown = loading ? "" : (draft ?? saved ?? "");
  const tooLarge = draft !== null && byteSize(draft) > MAX_SOURCE;
  const canSave = !scope.readOnly && !scope.writing && draft !== null && !saving && !tooLarge;

  const latest = revisions[revisions.length - 1]?.number ?? 1;
  const edit = (text: string) => {
    setCompare(null);
    const next = text === saved ? null : text;
    const from = next === null ? null : (base ?? selected);
    setDraft(next);
    setBase(from);
    keepDraft(key, next === null || from === null ? null : { text: next, base: from });
  };

  const keep = async () => {
    if (draft === null) return;
    setSaving(true);
    setProblem("");
    // Begun from a revision older than the last saved: the server says so, and does
    // not put this edit after one it never saw.
    const from = base ?? latest;
    try {
      const view = kept
        ? await post<ArtifactView>(`/api/chats/${scope.chatId}/artifacts/${kept.id}/revisions`, {
            source: draft,
            base: from,
          })
        : await post<ArtifactView>(
            `/api/chats/${scope.chatId}/messages/${scope.messageId}/artifacts`,
            { kind, title: opened.title, original, source: draft, base: from },
          );
      const last = view.revisionList[view.revisionList.length - 1]?.number ?? 1;
      setKept(view);
      setSources((was) => new Map(was).set(last, draft));
      setSelected(last);
      setDraft(null);
      setBase(null);
      keepDraft(key, null);
      scope.onKept();
    } catch (error) {
      setProblem(`It was not saved: ${error instanceof Error ? error.message : String(error)}`);
      if (error instanceof ApiError && error.status === 409) {
        // Saved elsewhere since: the chat is read again, and the next save is the
        // person's choice, after the revision they have now been told of.
        setBase(null);
        scope.onKept();
        if (kept) {
          api<ArtifactView>(`/api/chats/${scope.chatId}/artifacts/${kept.id}`).then(
            (fresh) => setKept(fresh),
            () => undefined,
          );
        }
      }
    } finally {
      setSaving(false);
    }
  };

  // Closed as Escape closes it; a browser with no dialog element closes it here.
  const shut = () => {
    const element = dialog.current;
    if (element && typeof element.close === "function") element.close();
    else onClose();
  };

  const tabs: ShownTab[] =
    compare === null ? ["preview", "source"] : ["preview", "source", "changes"];
  const activeTab: ShownTab = compare === null ? tab : "changes";
  const showTab = (name: ShownTab) => {
    if (name === "changes") return;
    setTab(name);
    setCompare(null);
  };
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.indexOf(activeTab);
    const next =
      event.key === "ArrowLeft"
        ? tabs[(index + tabs.length - 1) % tabs.length]
        : event.key === "ArrowRight"
          ? tabs[(index + 1) % tabs.length]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[tabs.length - 1]
              : null;
    if (!next) return;
    event.preventDefault();
    showTab(next);
    document.getElementById(`${uid}-${next}-tab`)?.focus();
  };

  let preview;
  // A kept artifact opens at its last revision: nothing is shown until that is read.
  if (loading || (saved === undefined && draft === null)) {
    preview = <p className="text-sm text-muted">Reading the revision…</p>;
  } else if (kind === "markdown") {
    // Its own blocks are plain: an artifact holds no artifacts of its own.
    preview = (
      <ArtifactScope.Provider value={null}>
        <Markdown text={shown} />
      </ArtifactScope.Provider>
    );
  } else if (kind === "svg") {
    preview = <SvgPicture source={shown} title={title} />;
  } else if (runs(kind)) {
    preview = (
      <ArtifactPreview
        key={`${selected}`}
        kind={kind === "html" ? "html" : "react"}
        source={shown}
        title={title}
        blockKey={key}
      />
    );
  }

  return (
    <dialog
      ref={dialog}
      className="artifact-dialog"
      aria-labelledby={`${uid}-title`}
      data-testid="artifact-dialog"
      onClose={() => {
        if (!dialog.current?.open) onClose();
      }}
    >
      <div className="artifact-dialog-head">
        <span className="artifact-dialog-name">
          <span id={`${uid}-title`} className="artifact-dialog-title">
            {title}
          </span>
          <span className="text-sm text-muted">{KIND_NAMES[kind]}</span>
        </span>
        <span className="diagram-tools">
          <button
            type="button"
            className="act"
            aria-label={`Download ${draft !== null ? "your changes" : `revision ${current?.number ?? 1}`}`}
            onClick={() => save(shown, fileName(title, kind, language))}
          >
            <Download size={14} aria-hidden="true" />
            <span className="diagram-tool-label">Download</span>
          </button>
          <button ref={close} type="button" className="act" onClick={shut}>
            <X size={16} aria-hidden="true" />
            Close
          </button>
        </span>
      </div>
      <div className="artifact-dialog-bar">
        <span role="tablist" aria-label="Show" className="artifact-tabs">
          {tabs.map((name) => (
            <button
              key={name}
              id={`${uid}-${name}-tab`}
              type="button"
              role="tab"
              className="artifact-tab"
              aria-selected={activeTab === name}
              aria-controls={`${uid}-${name}-panel`}
              tabIndex={activeTab === name ? 0 : -1}
              onClick={() => showTab(name)}
              onKeyDown={onTabKey}
            >
              {name === "preview" ? "Preview" : name === "source" ? "Source" : "Changes"}
            </button>
          ))}
        </span>
        <label className="artifact-revision">
          <span className="sr-only">Revision</span>
          <select
            value={selected}
            disabled={loading || draft !== null || revisions.length < 2}
            onChange={(event) => {
              setSelected(Number(event.target.value));
              setCompare(null);
              setCompareProblem("");
            }}
            aria-describedby={draft !== null ? `${uid}-unsaved` : undefined}
          >
            {revisions.map((revision) => (
              <option key={revision.number} value={revision.number}>
                {revisionLabel(revision, revisions.length, kept?.tool ?? scope.tool)}
              </option>
            ))}
          </select>
        </label>
        {revisions.length > 1 && (
          <label className="artifact-revision">
            <span className="sr-only">Compare with revision</span>
            <select
              value={compare ?? ""}
              disabled={loading || missing || draft !== null}
              onChange={(event) => {
                setCompare(event.target.value ? Number(event.target.value) : null);
                setCompareProblem("");
              }}
            >
              <option value="">Compare revisions</option>
              {revisions
                .filter((revision) => revision.number !== selected)
                .map((revision) => (
                  <option key={revision.number} value={revision.number}>
                    Compare with revision {revision.number}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
      {compare !== null && (
        <section
          id={`${uid}-changes-panel`}
          role="tabpanel"
          className="artifact-diff"
          aria-labelledby={`${uid}-changes-tab`}
        >
          {compareProblem ? (
            <p role="alert" className="notice notice--error">
              {compareProblem}
            </p>
          ) : compareMissing || saved === undefined ? (
            <p role="status">Reading revision {compare}…</p>
          ) : (
            <RevisionDiff
              before={sources.get(compare)!}
              after={saved}
              beforeNumber={compare}
              afterNumber={selected}
              onDownload={() => {
                save(
                  sources.get(compare)!,
                  fileName(`${title}-revision-${compare}`, kind, language),
                );
                save(saved, fileName(`${title}-revision-${selected}`, kind, language));
              }}
            />
          )}
        </section>
      )}
      {draft !== null && (
        <p id={`${uid}-unsaved`} className="artifact-unsaved text-sm">
          {tab === "preview"
            ? "This shows your changes, which are not saved yet."
            : "Your changes are not saved yet."}{" "}
          Save or discard them to look at another revision.
        </p>
      )}
      {/* Both stay: a preview runs on while its source is read or edited. */}
      <div
        id={`${uid}-preview-panel`}
        role="tabpanel"
        aria-labelledby={`${uid}-preview-tab`}
        className="artifact-dialog-body"
        hidden={tab !== "preview" || compare !== null}
      >
        {preview}
      </div>
      <div
        id={`${uid}-source-panel`}
        role="tabpanel"
        aria-labelledby={`${uid}-source-tab`}
        className="artifact-dialog-body"
        hidden={tab !== "source" || compare !== null}
      >
        {
          <div className="artifact-source">
            <textarea
              value={shown}
              onChange={(event) => edit(event.target.value)}
              readOnly={scope.readOnly || loading || saved === undefined}
              spellCheck={false}
              aria-label={`Source of ${title}`}
              className="artifact-editor"
            />
            <div className="artifact-source-tools">
              {!scope.readOnly && (
                <button
                  type="button"
                  className="btn"
                  disabled={!canSave}
                  onClick={() => void keep()}
                >
                  {saving ? "Saving…" : `Save as revision ${revisions.length + 1}`}
                </button>
              )}
              {draft !== null && (
                <button type="button" className="act" onClick={() => edit(saved ?? "")}>
                  Discard changes
                </button>
              )}
              <span className="text-sm text-muted">
                {scope.readOnly
                  ? "You are reading someone else's chat: nothing is saved from here."
                  : scope.writing
                    ? "The answer is still being written: save when it has ended."
                    : tooLarge
                      ? `An artifact keeps at most ${MAX_SOURCE / 1024} KB a revision.`
                      : "Each save is a new revision; none is written over."}
              </span>
            </div>
          </div>
        }
      </div>
      {problem && (
        <p role="alert" className="notice notice--error artifact-problem">
          {problem}
        </p>
      )}
    </dialog>
  );
}

function RevisionDiff({
  before,
  after,
  beforeNumber,
  afterNumber,
  onDownload,
}: {
  before: string;
  after: string;
  beforeNumber: number;
  afterNumber: number;
  onDownload: () => void;
}) {
  const result = useMemo(() => {
    try {
      const lines = lineDiff(before, after);
      const rows: { before?: DiffLine; after?: DiffLine }[] = [];
      for (let i = 0; i < lines.length;) {
        const line = lines[i]!;
        if (line.type === "same") {
          rows.push({ before: line, after: line });
          i++;
          continue;
        }
        const removed: DiffLine[] = [],
          added: DiffLine[] = [];
        while (i < lines.length && lines[i]!.type !== "same") {
          const changed = lines[i++]!;
          (changed.type === "removed" ? removed : added).push(changed);
        }
        for (let j = 0; j < Math.max(removed.length, added.length); j++)
          rows.push({ before: removed[j], after: added[j] });
      }
      return { rows, error: "" };
    } catch (error) {
      return { rows: [], error: error instanceof Error ? error.message : String(error) };
    }
  }, [before, after]);
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2 text-sm">
        <h3>
          Revision {beforeNumber} → Revision {afterNumber}
        </h3>
        <button type="button" className="act" onClick={onDownload}>
          <Download size={14} aria-hidden="true" /> Download both
        </button>
      </div>
      {result.error ? (
        <p role="status">{result.error}</p>
      ) : (
        <div className="artifact-diff-lines" tabIndex={0} role="region" aria-label="Compared lines">
          {result.rows.map((row, index) => (
            <div key={index} className="artifact-diff-line">
              <span className="diff-before" data-change={row.before?.type ?? "empty"}>
                {row.before?.type === "removed" ? (
                  <del>− {row.before.text || " "}</del>
                ) : row.before?.type === "same" ? (
                  `  ${row.before.text}`
                ) : null}
              </span>
              <span
                className="diff-after"
                data-change={row.after?.type ?? "empty"}
                aria-hidden={row.after?.type === "same" ? true : undefined}
              >
                {row.after?.type === "added" ? (
                  <ins>+ {row.after.text || " "}</ins>
                ) : row.after?.type === "same" ? (
                  `  ${row.after.text}`
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
