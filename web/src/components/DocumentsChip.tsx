/**
 * Documents in a chat: a chip in its header that says what the next message does
 * with the person's documents, and the drawer it opens. The drawer holds the chat's
 * own switch, "Documents only", and the choice to send passages to a model that
 * runs outside the person's machines, and says how much there is to search and,
 * when nothing would be, why. The words about a chat that sends nothing are the
 * server's own (`reason`, `hint`): the drawer says what the request does, and never
 * a stand-in. What was found for an answer is under that answer, not here.
 *
 * Also the hint in the composer: a person who has documents to ask about and has
 * Documents off in this chat is told, once, quietly, and can turn it on there.
 */

import { CircleAlert, Files, Info, TriangleAlert } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { patch } from "../lib/api";
import { failureWords } from "../lib/memory";
import {
  budgetWords,
  chatChipWords,
  type ChipTone,
  countsWords,
  hintWords,
  readOnlyWords,
  stateWords,
} from "../lib/savedDocuments";
import type {
  Chat,
  ChatDocuments,
  ChatDocumentsView,
  ChatSettings as Settings,
} from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { FOCUSABLE, useOverlay } from "./ChatSettings";

const TONE: Record<ChipTone, string> = {
  quiet: "",
  on: "text-fg",
  warn: "text-warn",
};

/** The chip in a chat's header. Its name is its words, so a screen reader and a
 * narrow window that shows only the glyph and a count say the same thing. */
export function DocumentsChip({
  view,
  failed,
  open,
  onToggle,
}: {
  view: ChatDocumentsView | null;
  failed: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const words = chatChipWords(view, failed);
  const Icon = words.tone === "warn" ? TriangleAlert : Files;
  return (
    <button
      type="button"
      data-testid="documents-chip"
      aria-label={words.label}
      aria-expanded={open}
      aria-controls="documents-drawer"
      onClick={onToggle}
      className={`icon-btn gap-1.5 px-2 text-sm ${TONE[words.tone]}`}
    >
      <Icon
        size={16}
        aria-hidden
        className={`shrink-0 ${words.tone === "on" ? "text-accent" : ""}`}
      />
      <span aria-hidden className="max-xl:hidden">
        {words.label}
      </span>
      {words.short && (
        <span aria-hidden className="tabular-nums xl:hidden">
          {words.short}
        </span>
      )}
    </button>
  );
}

/** Where an outside model's name stands in a sentence. */
function whoRuns(view: ChatDocuments): string {
  return view.model?.runsOn ?? "another service";
}

/** The drawer's one status: what the next message does with the documents. */
function Status({
  view,
  busy,
  onSendHere,
  onStopSending,
  onManage,
}: {
  view: ChatDocuments;
  busy: boolean;
  onSendHere: () => void;
  onStopSending: () => void;
  onManage: () => void;
}) {
  const code = view.reasonCode;
  const model = view.model?.id ?? "the model";
  const { library, chat } = view.counts;
  if (view.sending) {
    const outside = view.outside;
    // Only the files of this chat that the person chose to search: their own act,
    // searched whatever the switch says.
    if (!view.searching.library) {
      return (
        <div data-testid="documents-status" className="notice">
          <Info size={16} aria-hidden />
          <div>
            The files of this chat that you chose to search are searched for each question, and the
            passages that match go to {model}.
            {view.reason && <span className="mt-1 block text-muted">{view.reason}</span>}
            {view.hint && <span className="mt-1 block text-muted">{view.hint}</span>}
            {code === "outside_model" && outside?.canSendForThisChat && (
              <div className="mt-2">
                <button
                  type="button"
                  data-testid="documents-send-here"
                  disabled={busy}
                  onClick={onSendHere}
                  className="btn"
                >
                  Send for this chat
                </button>
              </div>
            )}
          </div>
        </div>
      );
    }
    if (outside?.choice === "always") {
      const by =
        outside.setBy === "owner"
          ? "the owner of this Workbench has set"
          : outside.setBy === "you"
            ? "you have set"
            : "this Workbench is set";
      return (
        <p data-testid="documents-status" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          <span>
            The passages of your documents that match a question go to {whoRuns(view)}, which runs
            outside your machines, because {by} documents to always go to such models.
          </span>
        </p>
      );
    }
    if (outside) {
      return (
        <div data-testid="documents-status" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          <div>
            The passages of your documents that match a question go to {whoRuns(view)}, which runs
            outside your machines, because you chose Send for this chat.
            <div className="mt-2">
              <button
                type="button"
                data-testid="documents-stop-sending"
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
    if (library.documents === 0) {
      return (
        <div data-testid="documents-status" className="notice">
          <Info size={16} aria-hidden />
          <div>
            {library.waiting > 0
              ? "Your documents are still being read, so none can be searched yet. They are searched as soon as they are ready."
              : "You have no documents to search yet."}
            {library.waiting === 0 && (
              <div className="mt-2">
                <button
                  type="button"
                  data-testid="documents-open-page"
                  className="btn"
                  onClick={onManage}
                >
                  Open Documents to add some
                </button>
              </div>
            )}
          </div>
        </div>
      );
    }
    return (
      <p data-testid="documents-status" className="text-sm">
        Each question you send is searched for in your documents, and the passages that match go to{" "}
        {model}. Sources under each answer says which.
        {chat.documents > 0 && " The files of this chat that you chose to search are searched too."}
      </p>
    );
  }
  const warn = code === "outside_model" || code === "model_unknown" || code === "window_too_small";
  const Glyph = warn ? TriangleAlert : Info;
  return (
    <div data-testid="documents-status" className={`notice ${warn ? "notice--warn" : ""}`}>
      <Glyph size={16} aria-hidden />
      <div>
        {view.reason}
        {view.hint && <span className="mt-1 block text-muted">{view.hint}</span>}
        {code === "outside_model" && view.outside?.canSendForThisChat && (
          <div className="mt-2">
            <button
              type="button"
              data-testid="documents-send-here"
              disabled={busy}
              onClick={onSendHere}
              className="btn"
            >
              Send for this chat
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function DocumentsDrawer({
  chatId,
  view,
  error,
  onChat,
  onReload,
  onManage,
  onClose,
}: {
  chatId: string;
  view: ChatDocumentsView | null;
  /** Why the last read failed, when it did. */
  error: string | null;
  /** The chat as the server now has it, after a setting here changed it. */
  onChat: (chat: Chat) => void;
  onReload: () => void | Promise<void>;
  onManage: () => void;
  onClose: () => void;
}) {
  const uid = useId();
  const overlay = useOverlay();
  const sheet = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const openedAsOverlay = useRef(overlay);
  const [busy, setBusy] = useState(false);
  // The switches and the buttons are held while a setting is made, which takes the
  // keyboard's place from the one that was pressed: it is given back afterwards.
  const keepFocus = useKeepFocus(busy);
  // A switch shows what was just chosen while the server catches up; a refusal puts
  // it back (these are only read while busy).
  const [wanted, setWanted] = useState<{ on?: boolean; only?: boolean }>({});
  const [problem, setProblem] = useState<string | null>(null);

  // Over the whole window the drawer is a dialog: focus goes into it, and back to the
  // chip that opened it when it closes (also when it is a panel).
  useEffect(() => {
    const opener = document.activeElement;
    if (openedAsOverlay.current) heading.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);

  // Opening the drawer reads the chat's documents again: it never shows an older copy.
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
    try {
      onChat(await patch<Chat>(`/api/chats/${chatId}`, { settings }));
      await onReload();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const own = view && !view.readOnly ? view : null;
  const canSwitch = own !== null && own.reasonCode !== "install_off";

  return (
    <section
      ref={sheet}
      id="documents-drawer"
      aria-label="Documents in this chat"
      role={overlay ? "dialog" : undefined}
      aria-modal={overlay ? true : undefined}
      className="sheet"
      data-testid="documents-drawer"
      onKeyDown={onKeyDown}
    >
      <div className="sheet-body">
        <div className="mx-auto flex max-w-3xl flex-col gap-5">
          <h2 ref={heading} tabIndex={-1} className="title-card">
            Documents in this chat
          </h2>
          {error && (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read this chat&apos;s documents</span>
                {error}
                <div className="mt-2">
                  <button
                    type="button"
                    data-testid="documents-drawer-retry"
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
              Reading this chat&apos;s documents…
            </p>
          )}
          {view?.readOnly && (
            <div data-testid="documents-status" className="flex flex-col gap-2 text-sm">
              <p>{readOnlyWords(view)}</p>
              <p className="help">
                This is someone else&apos;s chat. You can see whether Documents was on, not what is
                in them: what a person keeps in their documents is private to them.
              </p>
            </div>
          )}
          {own && (
            <>
              <Status
                view={own}
                busy={busy}
                onSendHere={() => void change({ documentsExternal: true })}
                onStopSending={() => void change({ documentsExternal: null })}
                onManage={onManage}
              />
              {canSwitch && (
                <>
                  <div className="field">
                    <label className="check items-center" htmlFor={`${uid}-switch`}>
                      <input
                        id={`${uid}-switch`}
                        type="checkbox"
                        role="switch"
                        data-testid="documents-switch"
                        aria-describedby={`${uid}-switch-help`}
                        checked={busy && wanted.on !== undefined ? wanted.on : own.switch === "on"}
                        disabled={busy}
                        onChange={(event) => {
                          setWanted({ on: event.target.checked });
                          void change({ documents: event.target.checked ? "on" : "off" });
                        }}
                      />
                      Documents in this chat
                    </label>
                    <p id={`${uid}-switch-help`} className="help">
                      Search your documents for each question you send here, and send the passages
                      that match to the model. It takes effect with your next message.
                    </p>
                  </div>
                  <div className="field">
                    <label className="check items-center" htmlFor={`${uid}-only`}>
                      <input
                        id={`${uid}-only`}
                        type="checkbox"
                        role="switch"
                        data-testid="documents-only-switch"
                        aria-describedby={`${uid}-only-help`}
                        checked={
                          busy && wanted.only !== undefined ? wanted.only : own.documentsOnly
                        }
                        disabled={busy}
                        onChange={(event) => {
                          setWanted({ only: event.target.checked });
                          void change({ documentsOnly: event.target.checked ? true : null });
                        }}
                      />
                      Documents only
                    </label>
                    <p id={`${uid}-only-help`} className="help">
                      The model is told to answer only from the passages sent. If nothing matches,
                      Workbench says so itself and does not ask the model.
                    </p>
                  </div>
                </>
              )}
              {own.reasonCode !== "install_off" && (
                <section aria-labelledby={`${uid}-counts`} className="flex flex-col gap-1">
                  <h3 id={`${uid}-counts`} className="eyebrow text-muted">
                    What there is to search
                  </h3>
                  <ul data-testid="documents-counts" className="flex flex-col gap-0.5 text-sm">
                    {countsWords(own).map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  {own.sending && <p className="help">{budgetWords(own.budget)}</p>}
                </section>
              )}
              {own.chatDocuments.length > 0 && (
                <section aria-labelledby={`${uid}-files`} className="flex flex-col gap-1">
                  <h3 id={`${uid}-files`} className="eyebrow text-muted">
                    Files of this chat you chose to search
                  </h3>
                  <ul data-testid="documents-chat-files" className="divide-y divide-line text-sm">
                    {own.chatDocuments.map((doc) => (
                      <li key={doc.id} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
                        <span className="min-w-0 flex-1 basis-48 wrap-anywhere">{doc.name}</span>
                        <span className="text-xs text-muted wrap-anywhere">
                          {stateWords(doc).label}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
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
          <button
            type="button"
            data-testid="documents-close"
            onClick={onClose}
            className="btn btn-quiet"
          >
            Close documents
          </button>
          {own && (
            <button
              type="button"
              data-testid="documents-manage"
              onClick={onManage}
              className="btn btn-quiet"
            >
              Manage documents
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

/** The hint in the composer: the person has documents to ask about, and this chat
 * has Documents off. Quiet: one line, a button that turns it on, and one that hides it. */
export function DocumentsHint({
  documents,
  busy,
  problem,
  onTurnOn,
  onHide,
}: {
  /** How many of the person's documents can be searched now. */
  documents: number;
  busy: boolean;
  problem: string | null;
  onTurnOn: () => void;
  onHide: () => void;
}) {
  return (
    <div data-testid="documents-hint" className="flex flex-col gap-1">
      <p className="flex items-center gap-x-2 text-xs text-muted">
        <Files size={14} aria-hidden className="shrink-0" />
        <span className="min-w-0 flex-1">{hintWords(documents)}</span>
        <span className="flex shrink-0 items-center">
          <button
            type="button"
            data-testid="documents-hint-on"
            aria-label="Turn on Documents"
            disabled={busy}
            onClick={onTurnOn}
            className="act link"
          >
            Turn on
          </button>
          <button
            type="button"
            data-testid="documents-hint-hide"
            aria-label="Hide this hint"
            disabled={busy}
            onClick={onHide}
            className="act"
          >
            Hide
          </button>
        </span>
      </p>
      {problem && (
        <p role="alert" data-testid="documents-hint-problem" className="text-xs text-warn">
          {problem}
        </p>
      )}
    </div>
  );
}
