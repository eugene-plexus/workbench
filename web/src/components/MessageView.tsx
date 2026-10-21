import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileText,
  Globe,
  Music,
  Pencil,
  RotateCcw,
  Scissors,
  TriangleAlert,
} from "lucide-react";
import {
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useDisplay } from "../lib/display";
import { fileUrl, post } from "../lib/api";
import { stableCitations } from "../lib/citations";
import {
  about,
  isDocumentSource,
  isPassageSource,
  isWebSource,
  readingMeta,
  sourceTags,
  sourceWhere,
} from "../lib/documents";
import type { AttachedFile, Message, Progress } from "../lib/types";
import {
  answerParts,
  DRAFT_HINT,
  DRAFT_LABEL,
  EDIT_KEYS,
  EDIT_NOTE,
  workingWords,
  redactedNote,
  reasoningParts,
  SEARCHED_MARK,
  splitWorking,
  statusWords,
  VERSIONS_WAIT,
} from "../lib/words";
import { Markdown } from "./Markdown";
import { ArtifactScope, type Scope } from "./Artifact";
import { CopyButton } from "./CopyButton";
import { useDocumentText } from "./DocumentText";
import { type RememberProps, useRemember } from "./RememberThis";
import { NothingMatched, PassageList, SearchNotice, WorkbenchReply } from "./SourcePassages";
import { ToolCalls } from "./ToolCalls";
import { WorkingScene } from "./WorkingScene";
import { useClock } from "../lib/useClock";

export function MessageView({
  chatId,
  message,
  progress,
  busy,
  readOnly,
  runArtifacts = true,
  onChanged,
  onLook,
  memory,
  documents,
}: {
  chatId: string;
  message: Message;
  progress: Progress | null;
  busy: boolean;
  readOnly: boolean;
  runArtifacts?: boolean;
  onChanged: () => void;
  /** The owner's read-only view: show another version without choosing it. */
  onLook?: (id: string) => void;
  /** Remember this, in the actions row: absent where notes cannot be kept
   * (the owner's read-only view, a chat whose owner turned Memory off). */
  memory?: RememberProps;
  /** Open the Documents screen, from a search that went wrong; absent where the
   * person cannot (the owner's read-only view). */
  documents?: { onManage: () => void };
}) {
  const { speakers } = useDisplay();
  const [editing, setEditing] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  // Ticks while an answer runs, for how long it has been thinking.
  const now = useClock(message.role === "assistant" && message.status === "running");
  // What Remember this would keep: the person's words, or the answer (not the
  // draft written before a search). Not while an answer is still being written
  // or the person is editing their own words.
  const remember = useRemember({
    chatId,
    message,
    text: message.role === "user" ? message.content : answerParts(message).answer,
    memory:
      readOnly || message.redacted || message.status === "running" || editing !== null
        ? undefined
        : memory,
  });
  // What the answer's blocks need to be artifacts (Artifact.tsx): the chat is read
  // again when one is kept, by the latest `onChanged`.
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  });
  const artifacts = message.artifacts;
  const writing = message.status === "running";
  const artifactScope = useMemo<Scope>(
    () => ({
      chatId,
      messageId: message.id,
      artifacts: artifacts ?? [],
      readOnly,
      writing,
      runArtifacts,
      onKept: () => changed.current(),
    }),
    [chatId, message.id, artifacts, readOnly, writing, runArtifacts],
  );
  const arrows = (
    <VersionArrows
      chatId={chatId}
      message={message}
      busy={busy}
      readOnly={readOnly}
      onChanged={onChanged}
      onLook={onLook}
      onProblem={setProblem}
    />
  );
  // A version or retry that was refused, with the sentence the server wrote.
  const refused = problem && (
    <p role="alert" className="notice notice--error max-w-measure">
      <CircleAlert size={16} aria-hidden />
      {problem}
    </p>
  );

  if (message.redacted) {
    return (
      <div className="flex flex-col gap-1">
        <h2 className="sr-only">
          {message.role === "user" ? "You" : `Answer from ${message.model || "Assistant"}`}
        </h2>
        <p
          data-testid="redacted-message"
          className="max-w-measure rounded-plexus border border-line px-3 py-2 text-sm text-muted wrap-anywhere"
        >
          {redactedNote(message.redacted.site)}
        </p>
        {/* Every branch stays reachable, hidden or not (V5). */}
        <div className="flex text-xs text-muted">{arrows}</div>
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div
        className="msg flex flex-col items-end gap-1 [&:not(:first-child)]:mt-4"
        data-testid="user-message"
      >
        <h2 className="sr-only">You</h2>
        {speakers && (
          <p className="speaker-label text-accent" aria-hidden="true">
            You
          </p>
        )}
        {message.files && message.files.length > 0 && (
          // A document says how it was read, and lets the person see the text;
          // an image or a sound is its name (or itself).
          <div className="flex w-full flex-col items-end gap-1" data-testid="message-files">
            <div className="flex flex-wrap justify-end gap-2">
              {message.files
                .filter((file) => !file.reading)
                .map((file) => (
                  <Attachment key={file.id} file={file} />
                ))}
            </div>
            {message.files
              .filter((file) => file.reading)
              .map((file) => (
                <DocumentFile key={file.id} file={file} />
              ))}
          </div>
        )}
        {editing !== null ? (
          <form
            className="flex w-full max-w-measure flex-col gap-2 text-read"
            onSubmit={async (e) => {
              e.preventDefault();
              if (acting) return;
              setActing(true);
              setProblem(null);
              try {
                await post(`/api/chats/${chatId}/messages/${message.id}/edit`, {
                  content: editing,
                });
                setEditing(null);
                onChanged();
              } catch (error) {
                setProblem(error instanceof Error ? error.message : String(error));
              } finally {
                setActing(false);
              }
            }}
          >
            <textarea
              aria-label="Edit your message"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              rows={4}
              disabled={acting}
              autoFocus
              dir="auto"
              onKeyDown={(e) => {
                if (e.key === "Escape" && !acting) setEditing(null);
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
              className="control text-read leading-[1.55]"
            />
            <p className="text-xs text-muted" data-testid="edit-note">
              {EDIT_NOTE} {EDIT_KEYS}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={acting}
                onClick={() => setEditing(null)}
                className="btn btn-quiet"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={acting || (!editing.trim() && message.attachments.length === 0)}
                className="btn btn-primary"
              >
                Save and ask again
              </button>
            </div>
          </form>
        ) : (
          message.content && (
            // The person's words: a quiet steel-blue tint with a hairline (so
            // it shows in light, and in forced colours, where a fill is
            // dropped), at the answer's own reading size and measure.
            <div
              dir="auto"
              className="max-w-[85%] whitespace-pre-wrap rounded-plexus border border-tint-line bg-tint px-4 py-3 text-read leading-[1.55] wrap-anywhere sm:max-w-measure"
            >
              {message.content}
            </div>
          )
        )}
        {refused}
        {/* Right-aligned under the bubble: what stays (the versions and the
            time) hugs its edge, and the actions that come and go sit to the
            left of them, so nothing moves when they appear. */}
        <div className="-mr-2 flex min-h-7 flex-wrap items-center justify-end gap-1 text-xs text-muted">
          <span className="msg-actions">
            {editing === null && message.content && (
              <CopyButton text={message.content} label="your message" />
            )}
            {!readOnly && editing === null && !busy && (
              <button
                type="button"
                aria-label="Edit your message"
                onClick={() => setEditing(message.content)}
                className="act"
              >
                <Pencil size={14} aria-hidden /> Edit
              </button>
            )}
            {remember.action}
          </span>
          {arrows}
          <MessageTime message={message} className="pr-2" />
        </div>
        {remember.panel}
      </div>
    );
  }

  const { draft, answer } = answerParts(message);
  const thoughts = reasoningParts(message);
  const running = message.status === "running";
  const waiting = running && !answer;
  const status = statusWords(message);
  const hasTools = Boolean(message.toolRounds?.length);
  const draftLabel = hasTools ? "Written before using tools" : DRAFT_LABEL;
  const draftHint = hasTools
    ? "The model wrote this before it received the tool results."
    : DRAFT_HINT;
  const look = statusLook(message);
  const canCopy = !running && Boolean(answer);
  // What a search found and the answer linked, and the files that were sent
  // with the message. They are listed apart: a file in the list was sent to the
  // model, which says nothing of whether the answer used it.
  const web = message.sources.filter(isWebSource);
  const files = message.sources.filter(isDocumentSource);
  // What searching the person's saved documents came to, and the passages sent: apart
  // from the files, which were attached to this message and read whole.
  const passages = message.sources.filter(isPassageSource);
  const search = message.retrieval ?? null;
  const nothing = search?.status === "nothing_matched";
  const ownReply = message.finish === "documents_only";
  // A search that did not run or failed is said in its own words; a chat that answers
  // only from the documents says the same in Workbench's reply, so not twice.
  const notice =
    search && !ownReply && ["skipped", "failed", "no_documents"].includes(search.status)
      ? search
      : null;
  // The working line says "Thinking" once to a screen reader; what ticks (the
  // seconds, the count, the speed) is shown and not spoken.
  const working = waiting ? splitWorking(workingWords(progress, message, now)) : null;
  return (
    <article className="msg flex flex-col gap-3" data-testid="answer" data-status={message.status}>
      <h2 className="sr-only">Answer from {message.model || "Assistant"}</h2>
      {speakers && (
        <p className="speaker-label text-heading" aria-hidden="true">
          {message.model || "Assistant"}
        </p>
      )}
      {(message.reasoning || (message.reasoningTokens ?? 0) > 0) && (
        <Fold
          label="Reasoning"
          metrics={
            <span className="inline-flex flex-wrap gap-x-2 tabular-nums">
              <span
                title={
                  message.reasoningTokens == null
                    ? "Estimated from reasoning text; the model did not report a reasoning token count."
                    : "Reasoning tokens reported by the model."
                }
              >
                {message.reasoningTokens == null ? "~" : ""}
                {(
                  message.reasoningTokens ?? Math.ceil(message.reasoning.length / 4)
                ).toLocaleString()}{" "}
                tokens
                {message.reasoningTokens == null ? " (estimated)" : ""}
              </span>
              {message.reasoningSeconds != null && (
                <span title="Observed time streaming reasoning, excluding tool waits.">
                  {message.reasoningSeconds.toFixed(1)}s
                </span>
              )}
            </span>
          }
          boxLabel="Reasoning text"
          follow={running}
        >
          {!message.reasoning && <p>The model did not share its reasoning text.</p>}
          {thoughts.map((thought, i) => (
            <div key={i}>
              {i > 0 && (
                <p className="mt-1 border-t border-line pt-1 text-xs" data-testid="searched-mark">
                  {hasTools ? "Used tools" : SEARCHED_MARK}
                </p>
              )}
              <div dir="auto" className="py-1 wrap-anywhere">
                <Markdown text={thought} />
              </div>
            </div>
          ))}
        </Fold>
      )}
      {draft && (
        <Fold label={draftLabel} boxLabel={draftLabel} title={draftHint} testId="draft">
          <p className="pb-1 text-xs">{draftHint}</p>
          <Markdown text={draft} quiet />
        </Fold>
      )}
      <ArtifactScope.Provider value={artifactScope}>
        <ToolCalls chatId={chatId} message={message} readOnly={readOnly} onChanged={onChanged} />
      </ArtifactScope.Provider>
      {working && (
        // Eugene works while the model does; waiting on the person's
        // approval is not work, so he steps away then.
        <WorkingScene active={!(progress?.stage === "tool" && progress.phase === "approval")}>
          <p aria-live="polite" className="text-sm" data-testid="progress">
            <span className="text-fg">{working.say}</span>
            {working.tail && (
              <span aria-hidden="true" className="tabular-nums text-muted">
                {working.tail}
              </span>
            )}
          </p>
        </WorkingScene>
      )}
      {answer &&
        (ownReply ? (
          <WorkbenchReply
            text={answer}
            mine={!readOnly}
            asking={acting}
            askAnyway={
              readOnly
                ? undefined
                : async () => {
                    if (acting) return;
                    setActing(true);
                    setProblem(null);
                    try {
                      await post(`/api/chats/${chatId}/messages/${message.id}/retry`, {
                        askAnyway: true,
                      });
                      onChanged();
                    } catch (error) {
                      setProblem(error instanceof Error ? error.message : String(error));
                    } finally {
                      setActing(false);
                    }
                  }
            }
          />
        ) : (
          <ArtifactScope.Provider value={artifactScope}>
            <Markdown text={answer} citations={stableCitations(message)} />
          </ArtifactScope.Provider>
        ))}
      {running && answer && (
        // Once the first words are on the page the line is not gone: it moves
        // below them, with the count and the speed, until the answer ends. The
        // growing answer is not announced, and neither is this.
        <p data-testid="writing" className="flex items-center gap-2 text-sm text-muted">
          <span aria-hidden="true" className="working-dots">
            <span />
            <span />
            <span />
          </span>
          <span className="tabular-nums">{workingWords(progress, message, now)}</span>
        </p>
      )}
      {notice && <SearchNotice record={notice} onOpenDocuments={documents?.onManage} />}
      {(message.sources.length > 0 || message.searches > 0 || nothing) && (
        <div className="mt-1 max-w-measure border-t border-line pt-3 text-sm" data-testid="sources">
          <h3 className="eyebrow flex items-center gap-2 text-muted">
            {web.length === 0 && message.searches === 0 ? (
              <FileText size={14} aria-hidden className="shrink-0" />
            ) : (
              <Globe size={14} aria-hidden className="shrink-0" />
            )}
            Sources
            {message.searches ? (
              <span className="font-normal normal-case tracking-normal">
                {` (${message.searches} search${message.searches === 1 ? "" : "es"})`}
              </span>
            ) : null}
          </h3>
          {/* A search ran and the answer linked none of what it found: said,
              so a searched answer never looks like an unsearched one. */}
          {web.length === 0 && message.searches > 0 && (
            <p className="mt-2 text-muted">
              The answer does not link to any page the search found.
            </p>
          )}
          {web.length > 0 && (
            <ol className="mt-2 list-decimal space-y-1 pl-5 marker:text-muted wrap-anywhere">
              {web.map((source, i) => (
                // A citation's number in the answer moves here (lib/citations.ts).
                <li key={source.url} data-source-n={i + 1}>
                  <a
                    href={safe(source.url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="link"
                  >
                    {source.title}
                  </a>
                  {siteOf(source.url) && (
                    <span className="text-muted" data-testid="source-site">
                      {" "}
                      · {siteOf(source.url)}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          )}
          {passages.length > 0 && (
            <div className={web.length > 0 ? "mt-3" : "mt-2"}>
              <PassageList passages={passages} record={search} />
            </div>
          )}
          {nothing && search && (
            <div className={web.length > 0 || passages.length > 0 ? "mt-3" : "mt-2"}>
              <NothingMatched record={search} />
            </div>
          )}
          {files.length > 0 && (
            <div
              data-testid="document-sources"
              className={web.length > 0 || passages.length > 0 ? "mt-3" : "mt-2"}
            >
              <p className="text-muted">Files sent to the model</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 marker:text-muted wrap-anywhere">
                {files.map((source) => (
                  <li key={source.doc_id} data-testid="document-source">
                    <span>{source.name}</span>
                    <span>, {sourceWhere(source)}</span>
                    {sourceTags(source).map((tag) => (
                      <span key={tag} className="text-muted">
                        {" "}
                        · {tag}
                      </span>
                    ))}
                    {source.tokens != null && (
                      <span className="text-muted"> · {about(source.tokens)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(message.searchSuggestions ?? []).map((html, i) => (
            <SearchSuggestions key={i} html={html} />
          ))}
        </div>
      )}
      {status && (
        <p
          role={message.status === "failed" ? "alert" : undefined}
          data-testid="answer-status"
          className={look.className}
        >
          {look.glyph}
          {status}
        </p>
      )}
      <div className="-mt-1 flex min-h-7 flex-wrap items-center gap-1 text-xs text-muted">
        {!running && <MessageTime message={message} className="pr-2" />}
        {(canCopy || !readOnly) && (
          <span className="msg-actions">
            {canCopy && <CopyButton text={answer} label="this answer" />}
            {/* On any answer (V6); while one runs, it is stopped and kept (V2). */}
            {!readOnly && (
              <button
                type="button"
                data-testid="try-again"
                aria-label="Try again: write a new version of this answer"
                disabled={acting}
                title={
                  running
                    ? "Stops this answer, keeps it as a version, and writes another."
                    : busy
                      ? "Stops the answer being written, keeps it, and tries this one again."
                      : undefined
                }
                onClick={async () => {
                  if (acting) return;
                  setActing(true);
                  setProblem(null);
                  try {
                    await post(`/api/chats/${chatId}/messages/${message.id}/retry`);
                    onChanged();
                  } catch (error) {
                    setProblem(error instanceof Error ? error.message : String(error));
                  } finally {
                    setActing(false);
                  }
                }}
                className="act"
              >
                <RotateCcw size={14} aria-hidden /> Try again
              </button>
            )}
            {remember.action}
          </span>
        )}
        {arrows}
        {message.model && (!running || (message.versions?.count ?? 1) > 1) && (
          <span className="min-w-0 pl-2 wrap-anywhere">{message.model}</span>
        )}
      </div>
      {refused}
      {remember.panel}
    </article>
  );
}

/** How a note under an answer looks: a failure is an error box, an answer cut
 * short (a length limit, a repeat, a restart) is an amber box with the glyph of
 * what happened, and a stop the person asked for is a plain line. Each has its
 * words; the box and the glyph are for the eye that scans. */
function statusLook(message: Message): { className: string; glyph: ReactNode } {
  if (message.status === "failed") {
    return {
      className: "notice notice--error max-w-measure",
      glyph: <CircleAlert size={16} aria-hidden />,
    };
  }
  if (message.status === "interrupted" || message.finish === "repetition_detected") {
    return {
      className: "notice notice--warn max-w-measure",
      glyph: <TriangleAlert size={16} aria-hidden />,
    };
  }
  if (message.status !== "stopped" && message.finish === "length") {
    return {
      className: "notice notice--warn max-w-measure",
      glyph: <Scissors size={16} aria-hidden />,
    };
  }
  return { className: "text-sm text-muted", glyph: null };
}

/** Reasoning or a draft, folded: a quiet row that opens to a scrolling box. */
function Fold({
  label,
  metrics,
  boxLabel,
  title,
  testId,
  follow = false,
  children,
}: {
  label: string;
  metrics?: ReactNode;
  boxLabel: string;
  title?: string;
  testId?: string;
  /** Follow the bottom while text is arriving (the reasoning of a running answer). */
  follow?: boolean;
  children: ReactNode;
}) {
  return (
    <details className="group/r max-w-measure text-sm text-muted" data-testid={testId}>
      <summary
        title={title}
        className="disclosure flex w-fit max-w-full list-none flex-wrap items-center gap-1.5 rounded-plexus pl-1 pr-2 hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
      >
        <ChevronRight
          size={14}
          aria-hidden
          className="transition-transform group-open/r:rotate-90"
        />
        {label}
        {metrics}
      </summary>
      <FoldText label={boxLabel} follow={follow}>
        {children}
      </FoldText>
    </details>
  );
}

/**
 * The scrolling box inside a fold. While text arrives it follows the bottom
 * until the reader scrolls up (the rule the thread keeps), so thinking can be
 * watched without scrolling it. It is a keyboard stop only while it overflows:
 * a box that does not scroll has nothing for the arrow keys to do.
 */
function FoldText({
  label,
  follow,
  children,
}: {
  label: string;
  follow: boolean;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const following = useRef(follow);
  const [scrolls, setScrolls] = useState(false);

  useLayoutEffect(() => {
    following.current = follow;
    const el = box.current;
    if (!el) return;
    if (follow && pinned.current) el.scrollTop = el.scrollHeight;
    setScrolls(el.scrollHeight > el.clientHeight + 1);
    // Whenever the text it holds changes: each piece that arrives is a new one.
  }, [follow, children]);

  useEffect(() => {
    const el = box.current;
    // Opening the fold, or a resize, changes whether it overflows; jsdom has no
    // ResizeObserver.
    if (!el || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => {
      if (following.current && pinned.current) el.scrollTop = el.scrollHeight;
      setScrolls(el.scrollHeight > el.clientHeight + 1);
    });
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  return (
    <div
      ref={box}
      role="group"
      aria-label={label}
      tabIndex={scrolls ? 0 : undefined}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      className="reasoning-body mt-1"
    >
      {children}
    </div>
  );
}

/** `‹ 2 of 3 ›`: the versions of a message, or the tries of an answer.
 * Moving chooses that version for every tab; the owner's arrows only look. */
function VersionArrows({
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
  const versions = message.versions;
  if (!versions || versions.count < 2) return null;
  const what = message.role === "user" ? "Message" : "Answer";
  // Looking changes nothing, so a running answer holds only the person's.
  const held = busy && !readOnly;

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
    <span
      role="group"
      aria-label={`${what} ${versions.index} of ${versions.count}`}
      title={held ? VERSIONS_WAIT : undefined}
      data-testid="versions"
      className="flex items-center gap-0.5 whitespace-nowrap"
    >
      {arrow(
        -1,
        `Previous version of this ${what.toLowerCase()}`,
        ChevronLeft,
        versions.index <= 1,
      )}
      <span aria-hidden>
        {versions.index} of {versions.count}
      </span>
      {arrow(
        1,
        `Next version of this ${what.toLowerCase()}`,
        ChevronRight,
        versions.index >= versions.count,
      )}
    </span>
  );
}

/** The time alone today; with its date on any earlier day, so an old chat
 * does not read as if it were written this morning. */
export function shortTime(date: Date, now = new Date()): string {
  // 7:12 PM, not 07:12 PM: a leading zero is noise at this size.
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  const day = date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${day}, ${time}`;
}

function MessageTime({ message, className }: { message: Message; className?: string }) {
  const date = new Date(message.createdAt * 1000);
  return (
    <time
      dateTime={date.toISOString()}
      title={date.toLocaleString()}
      className={`tabular-nums ${className ?? ""}`}
    >
      {shortTime(date)}
    </time>
  );
}

/** The site a source is on, without `www.`, so a reader sees where it leads. */
export function siteOf(url: string): string | null {
  if (!/^https?:/i.test(url)) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

/** Google's Search Suggestions, exactly as Google sent them (its terms forbid
 * editing, framing around, or interspersing them). They sit in a frame that
 * can run nothing and read nothing of this page: `allow-popups` lets a chip
 * open Google in a new tab, and no `allow-scripts` or `allow-same-origin`
 * leaves the HTML inert. The frame's own page only says where links open, to
 * send no referrer, and that it follows light or dark as Google's CSS does. */
export function searchSuggestionsDocument(html: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<meta name="color-scheme" content="light dark">' +
    '<meta name="referrer" content="no-referrer">' +
    '<base target="_blank"></head><body style="margin:0">' +
    html +
    "</body></html>"
  );
}

function SearchSuggestions({ html }: { html: string }) {
  return (
    <iframe
      title="Google Search Suggestions"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      srcDoc={searchSuggestionsDocument(html)}
      referrerPolicy="no-referrer"
      data-testid="search-suggestions"
      className="mt-2 block h-14 w-full border-0"
    />
  );
}

function safe(url: string): string | undefined {
  return /^https?:/i.test(url) ? url : undefined;
}

/** A document a message carried: its name, what Workbench found in it, and a way
 * to see the text it read, which is the place to look when an answer misses
 * something that is in the file. */
function DocumentFile({ file }: { file: AttachedFile }) {
  const uid = useId();
  const text = useDocumentText({ id: file.id, name: file.name, describedBy: `${uid}-name` });
  const meta = readingMeta(file.reading);
  const ready = file.reading?.state === "ready";
  return (
    <div data-testid="document-file" className="flex w-full max-w-xl flex-col items-end gap-0.5">
      <span
        id={`${uid}-name`}
        title={file.name}
        className="flex max-w-xs items-center gap-1 rounded-plexus border border-line bg-soft px-2 py-1 text-sm"
      >
        <FileText size={14} aria-hidden className="shrink-0" />
        <span className="truncate">{file.name}</span>
      </span>
      {(meta || ready) && (
        <span className="flex flex-wrap items-center justify-end gap-x-1 text-xs text-muted">
          {meta && <span data-testid="document-file-meta">{meta}</span>}
          {ready && text.action}
        </span>
      )}
      {text.panel && <div className="w-full">{text.panel}</div>}
    </div>
  );
}

function Attachment({ file }: { file: AttachedFile }) {
  const [url, setUrl] = useState<string | null>(null);
  const image = file.mediaType.startsWith("image/");
  useEffect(() => {
    if (!image) return;
    let made: string | null = null;
    let cancelled = false;
    void fileUrl(file.id)
      .then((u) => {
        made = u;
        if (!cancelled) setUrl(u);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [file.id, image]);
  if (image && url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ${file.name} full size`}
        data-testid="attachment-image"
      >
        <img
          src={url}
          alt={file.name}
          className="h-24 rounded-plexus border border-line object-cover"
        />
      </a>
    );
  }
  const Icon = file.mediaType.startsWith("audio/") ? Music : FileText;
  return (
    <span
      title={file.name}
      className="flex max-w-xs items-center gap-1 rounded-plexus border border-line bg-soft px-2 py-1 text-sm"
    >
      <Icon size={14} aria-hidden className="shrink-0" />
      <span className="truncate">{file.name}</span>
    </span>
  );
}
