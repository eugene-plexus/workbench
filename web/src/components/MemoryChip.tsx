/**
 * Memory in a chat: a chip in its header that says what the next message
 * carries, and the drawer it opens. The drawer says exactly what the model
 * was given, what it was not and why, and holds the chat's own switch,
 * Refresh memory, and the choice to send notes to a model that runs outside
 * the person's machines. The words about a chat that sends nothing are the
 * server's own (`reason`, `hint`): the drawer says what the request does,
 * and never a stand-in.
 */

import { Brain, ChevronRight, CircleAlert, Info, TriangleAlert } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { patch, post } from "../lib/api";
import {
  chipWords,
  type ChipTone,
  failureWords,
  kindLabel,
  noteTime,
  plural,
  readOnlyWords,
  tokenWords,
} from "../lib/memory";
import type {
  Chat,
  ChatMemory,
  ChatMemoryView,
  ChatSettings as Settings,
  NotSentNote,
  SentNote,
} from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { FOCUSABLE, useOverlay } from "./ChatSettings";
import { ReadOnlyTurn, ThisTurn } from "./MemoryTurn";
import { ScrollText } from "./ScrollText";

const TONE: Record<ChipTone, string> = {
  quiet: "",
  on: "text-fg",
  warn: "text-warn",
};

/** The chip in a chat's header. Its name is its words, so a screen reader and
 * a phone that shows only the glyph and a count say the same thing. */
export function MemoryChip({
  view,
  failed,
  open,
  onToggle,
}: {
  view: ChatMemoryView | null;
  failed: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const words = chipWords(view, failed);
  const Icon = words.tone === "warn" ? TriangleAlert : Brain;
  return (
    <button
      type="button"
      data-testid="memory-chip"
      aria-label={words.label}
      aria-expanded={open}
      aria-controls="memory-drawer"
      onClick={onToggle}
      className={`icon-btn gap-1.5 px-2 text-sm ${TONE[words.tone]}`}
    >
      <Icon
        size={16}
        aria-hidden
        className={`shrink-0 ${words.tone === "on" ? "text-accent" : ""}`}
      />
      <span aria-hidden className="max-sm:hidden">
        {words.label}
      </span>
      {words.short && (
        <span aria-hidden className="tabular-nums sm:hidden">
          {words.short}
        </span>
      )}
    </button>
  );
}

function NoteRow({ note, children }: { note: SentNote | NotSentNote; children?: ReactNode }) {
  return (
    <li className="flex flex-col gap-1 py-2">
      <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <span dir="auto" className="min-w-0 flex-1 basis-56 wrap-anywhere">
          {note.text}
        </span>
        <span className="flex flex-wrap gap-1.5">
          <span className="badge">{kindLabel(note.kind)}</span>
          {note.always && <span className="badge">Always used</span>}
        </span>
      </div>
      {"body" in note && note.body ? (
        <span data-testid="memory-note-body-sent" className="text-xs text-muted">
          Its details went whole: {plural(note.body.length, "character")}.
        </span>
      ) : null}
      {children}
    </li>
  );
}

/** Where an outside model's name stands in a sentence. */
function whoRuns(view: ChatMemory): string {
  return view.model?.runsOn ?? "another service";
}

/** The drawer's one status: what the next message does with the notes. */
function Status({
  view,
  busy,
  onSendHere,
  onStopSending,
  onManage,
}: {
  view: ChatMemory;
  busy: boolean;
  onSendHere: () => void;
  onStopSending: () => void;
  onManage: () => void;
}) {
  const code = view.reasonCode;
  if (view.sending) {
    const outside = view.outside;
    if (outside?.choice === "always") {
      const by =
        outside.setBy === "owner"
          ? "the owner of this Workbench has set"
          : outside.setBy === "you"
            ? "you have set"
            : "this Workbench is set";
      return (
        <p data-testid="memory-status" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          <span>
            Your notes go to {whoRuns(view)}, which runs outside your machines, because {by} notes
            to always go to such models.
          </span>
        </p>
      );
    }
    if (outside) {
      return (
        <div data-testid="memory-status" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          <div>
            Your notes go to {whoRuns(view)}, which runs outside your machines, because you chose
            Send for this chat.
            <div className="mt-2">
              <button
                type="button"
                data-testid="memory-stop-sending"
                disabled={busy}
                onClick={onStopSending}
                className="btn"
              >
                Stop sending
              </button>
            </div>
          </div>
        </div>
      );
    }
    const model = view.model?.id ?? "the model";
    return (
      <p data-testid="memory-status" className="text-sm">
        {view.frozen ? (
          <>
            Sent to {model} with every message in this chat. This chat took these notes
            {view.frozenAt ? ` ${noteTime(view.frozenAt)}` : ""}, so they stay the same until you
            choose Refresh memory.
          </>
        ) : (
          <>These notes will go to {model} with your next message.</>
        )}
      </p>
    );
  }
  const warn = code === "outside_model" || code === "model_unknown" || code === "window_too_small";
  const Glyph = warn ? TriangleAlert : Info;
  return (
    <div data-testid="memory-status" className={`notice ${warn ? "notice--warn" : ""}`}>
      <Glyph size={16} aria-hidden />
      <div>
        {view.reason}
        {view.hint && <span className="mt-1 block text-muted">{view.hint}</span>}
        {code === "outside_model" && view.outside?.canSendForThisChat && (
          <div className="mt-2">
            <button
              type="button"
              data-testid="memory-send-here"
              disabled={busy}
              onClick={onSendHere}
              className="btn"
            >
              Send for this chat
            </button>
          </div>
        )}
        {(code === "person_off" || code === "no_notes") && (
          <div className="mt-2">
            <button type="button" data-testid="memory-open-page" className="btn" onClick={onManage}>
              {code === "person_off" ? "Open Memory to turn it on" : "Open Memory to add notes"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function MemoryDrawer({
  chatId,
  view,
  error,
  onChat,
  onReload,
  onAdopt,
  onManage,
  onClose,
}: {
  chatId: string;
  view: ChatMemoryView | null;
  /** Why the last read failed, when it did. */
  error: string | null;
  /** The chat as the server now has it, after a setting here changed it. */
  onChat: (chat: Chat) => void;
  onReload: () => void | Promise<void>;
  /** Take what the server answered to a refresh as the view now. */
  onAdopt: (view: ChatMemoryView) => void;
  onManage: () => void;
  onClose: () => void;
}) {
  const uid = useId();
  const overlay = useOverlay();
  const sheet = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const openedAsOverlay = useRef(overlay);
  const [busy, setBusy] = useState(false);
  // The switch and the buttons are held while a setting is made, which takes the
  // keyboard's place from the one that was pressed: it is given back afterwards.
  const keepFocus = useKeepFocus(busy);
  // The switch shows what was just chosen while the server catches up; a refusal
  // puts it back (it is only read while busy).
  const [wanted, setWanted] = useState<boolean | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  // Over the whole window the drawer is a dialog: focus goes into it, and back
  // to the chip that opened it when it closes (also when it is a panel).
  useEffect(() => {
    const opener = document.activeElement;
    if (openedAsOverlay.current) heading.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);

  // Opening the drawer reads the notes again: it never shows an older copy.
  const reloaded = useRef(false);
  useEffect(() => {
    if (reloaded.current) return;
    reloaded.current = true;
    void onReload();
  }, [onReload]);

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !overlay || !sheet.current) return;
    const items = [...sheet.current.querySelectorAll<HTMLElement>(FOCUSABLE)].sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /** A setting of this chat changed: the chat comes back, and so does the view. */
  async function change(settings: Settings) {
    keepFocus();
    setBusy(true);
    setProblem(null);
    setSaid(null);
    try {
      onChat(await patch<Chat>(`/api/chats/${chatId}`, { settings }));
      await onReload();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    keepFocus();
    setBusy(true);
    setProblem(null);
    setSaid(null);
    try {
      onAdopt(await post<ChatMemoryView>(`/api/chats/${chatId}/memory/refresh`));
      setSaid("Refreshed. Your next message sends your notes as they are now.");
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const own = view && !view.readOnly ? view : null;
  const canSwitch =
    own !== null && own.reasonCode !== "install_off" && own.reasonCode !== "person_off";

  return (
    <section
      ref={sheet}
      id="memory-drawer"
      aria-label="Memory in this chat"
      role={overlay ? "dialog" : undefined}
      aria-modal={overlay ? true : undefined}
      className="sheet"
      data-testid="memory-drawer"
      onKeyDown={onKeyDown}
    >
      <div className="sheet-body">
        <div className="mx-auto flex max-w-3xl flex-col gap-5">
          <h2 ref={heading} tabIndex={-1} className="title-card">
            Memory in this chat
          </h2>
          {error && (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read this chat&apos;s notes</span>
                {error}
                <div className="mt-2">
                  <button
                    type="button"
                    data-testid="memory-drawer-retry"
                    className="btn"
                    onClick={() => void onReload()}
                  >
                    Try again
                  </button>
                </div>
              </div>
            </div>
          )}
          {!view && !error && (
            <p role="status" className="text-sm text-muted">
              Reading this chat&apos;s notes…
            </p>
          )}
          {view?.readOnly && (
            <div data-testid="memory-status" className="flex flex-col gap-2 text-sm">
              <p>{readOnlyWords(view)}</p>
              <ReadOnlyTurn view={view} />
              <p className="help">
                This is someone else&apos;s chat. You can see how many notes it used, not what they
                said: notes are private.
              </p>
            </div>
          )}
          {own && (
            <>
              <Status
                view={own}
                busy={busy}
                onSendHere={() => void change({ memoryExternal: true })}
                onStopSending={() => void change({ memoryExternal: null })}
                onManage={onManage}
              />
              {own.sending && own.frozen && own.changed && (
                <p data-testid="memory-changed" className="notice notice--warn">
                  <TriangleAlert size={16} aria-hidden />
                  <span>
                    <span className="notice-title">Notes changed since this chat started</span>
                    This chat still uses the notes it started with. Choose Refresh memory to use the
                    new ones.
                  </span>
                </p>
              )}
              {canSwitch && (
                <div className="field">
                  <label className="check items-center" htmlFor={`${uid}-switch`}>
                    <input
                      id={`${uid}-switch`}
                      type="checkbox"
                      role="switch"
                      data-testid="memory-switch"
                      aria-describedby={`${uid}-switch-help`}
                      checked={busy && wanted !== null ? wanted : own.chatSwitch === "on"}
                      disabled={busy}
                      onChange={(event) => {
                        setWanted(event.target.checked);
                        void change({ memory: event.target.checked ? null : "off" });
                      }}
                    />
                    Memory in this chat
                  </label>
                  <p id={`${uid}-switch-help`} className="help">
                    Turn it off to send no notes in this chat. It takes effect with your next
                    message, which may start a little slower.
                  </p>
                </div>
              )}
              {own.sent.length > 0 && (
                <section aria-labelledby={`${uid}-sent`} className="flex flex-col gap-1">
                  <h3 id={`${uid}-sent`} className="eyebrow text-muted">
                    Sent to the model in every turn
                  </h3>
                  {own.budget ? (
                    <p className="help">
                      {plural(own.sent.length, "note")}. {tokenWords(own.tokens, own.budget)} of
                      this model&apos;s memory room.
                    </p>
                  ) : null}
                  {own.relevance && (
                    <p data-testid="memory-relevance" className="help">
                      {own.relevance.how === "meaning and words"
                        ? "Chosen for what this chat started with, by meaning and words."
                        : (own.relevance.why ?? "Every note that fits, as you chose.")}
                    </p>
                  )}
                  <ul data-testid="memory-sent" className="divide-y divide-line text-sm">
                    {own.sent.map((note) => (
                      <NoteRow key={note.id} note={note} />
                    ))}
                  </ul>
                </section>
              )}
              {own.sending && <ThisTurn chatId={chatId} view={own} />}
              {own.notSent.length > 0 && (
                <section aria-labelledby={`${uid}-not-sent`} className="flex flex-col gap-1">
                  <h3 id={`${uid}-not-sent`} className="eyebrow text-muted">
                    Not sent
                  </h3>
                  <ul data-testid="memory-not-sent" className="divide-y divide-line text-sm">
                    {own.notSent.map((note) => (
                      <NoteRow key={note.id} note={note}>
                        <span className="text-xs text-muted">{note.reasonText}</span>
                      </NoteRow>
                    ))}
                  </ul>
                </section>
              )}
              {own.text && (
                <details
                  data-testid="memory-exact-details"
                  className="group/e text-sm"
                  onToggle={(event) => {
                    // Opened low in a panel that scrolls: bring the text into view.
                    const box = event.currentTarget;
                    if (box.open) box.scrollIntoView?.({ block: "nearest" });
                  }}
                >
                  <summary
                    data-testid="memory-exact-summary"
                    className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
                  >
                    <ChevronRight
                      size={14}
                      aria-hidden
                      className="transition-transform group-open/e:rotate-90"
                    />
                    The exact text the model was given
                  </summary>
                  <p className="help mt-1">
                    This is added to this chat&apos;s instructions, as the model reads it.
                  </p>
                  <ScrollText
                    label="Text sent to the model"
                    testId="memory-exact"
                    text={own.text}
                  />
                </details>
              )}
            </>
          )}
        </div>
      </div>
      <div className="sheet-foot">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-end gap-3">
          {problem && (
            <p role="alert" className="notice notice--error mr-auto">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
          {said && !problem && (
            <p role="status" className="mr-auto text-sm text-ok">
              {said}
            </p>
          )}
          <button
            type="button"
            data-testid="memory-close"
            onClick={onClose}
            className="btn btn-quiet"
          >
            Close memory
          </button>
          {own && (
            <button
              type="button"
              data-testid="memory-manage"
              onClick={onManage}
              className="btn btn-quiet"
            >
              Manage notes
            </button>
          )}
          {own && (
            <button
              type="button"
              data-testid="memory-refresh"
              disabled={busy || !(own.sending || own.frozen)}
              onClick={() => void refresh()}
              className={`btn ${own.changed ? "btn-primary" : ""}`}
            >
              Refresh memory
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
