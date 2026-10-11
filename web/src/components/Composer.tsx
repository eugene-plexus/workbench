import {
  CircleAlert,
  Globe,
  Info,
  Paperclip,
  Send,
  Square,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, del, patch, post } from "../lib/api";
import { readDraft, saveDraft } from "../lib/conveniences";
import { ACCEPT, asAttached, endSentence, holdWords, refusalFor } from "../lib/documents";
import type { Chat, ChatFile, Me, MemoryCommand, Message, Model, Models } from "../lib/types";
import { useChatFiles, useFileAnnouncements } from "../lib/useChatFiles";
import { modelLabel, SEARCH_HINT, SEARCH_LABEL, SEARCH_PRIVACY, takes } from "../lib/words";
import { Attachments } from "./Attachments";

const MODEL_KEY = "workbench-model";

/** A file attached to the message being written: at least its id, name and type;
 * a document also says how it was read and what the picked model would do with it. */
export type Pending = ChatFile;

function remembered(person: string): string | null {
  try {
    return window.localStorage.getItem(`${MODEL_KEY}:${person}`);
  } catch {
    return null;
  }
}

function remember(person: string, model: string): void {
  try {
    window.localStorage.setItem(`${MODEL_KEY}:${person}`, model);
  } catch {
    // Remembering the last model is a convenience.
  }
}

/** A phone or a tablet's touch screen: the first pointer is a finger. jsdom
 * and old browsers have no matchMedia, and then it is not. */
function touchFirst(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
}

/** The kind of attachment a model must take for this file, or null. */
export function needs(mediaType: string): "image" | "file" | "audio" | null {
  if (mediaType === "image/png" || mediaType === "image/jpeg") return "image";
  if (mediaType === "application/pdf") return "file";
  if (mediaType === "audio/wav" || mediaType === "audio/mpeg") return "audio";
  return null;
}

/** Why `model` cannot take `pending`, or null when it can. A PDF that Workbench
 * reads is sent as text, which any model takes; one it did not read (the owner
 * has turned reading off) goes as the file itself, which only some do. */
export function attachmentProblem(model: Model | undefined, pending: Pending[]): string | null {
  if (!model || pending.length === 0) return null;
  const missing = new Set<string>();
  for (const file of pending) {
    const kind = file.reading ? null : needs(file.mediaType);
    if (kind === "image" && !model.imageInput) missing.add("images");
    if (kind === "file" && !model.fileInput) missing.add("PDFs");
    if (kind === "audio" && !model.audioInput) missing.add("audio");
  }
  if (missing.size === 0) return null;
  return `${model.id} does not take ${[...missing].join(" or ")}. Pick a model that does, or remove the file.`;
}

/** Why the search switch is off, or null when it can be used. */
export function searchProblem(models: Models | null, model: Model | undefined): string | null {
  if (!models) return null;
  if (!models.webSearch.available) return models.webSearch.reason ?? "Search cannot run here.";
  if (model && !model.webSearch) {
    return `${model.id} can neither search nor use tools, so it cannot search the web. Pick another model.`;
  }
  return null;
}

/** PNG and JPEG are what the gateway carries; other images become PNG here. */
async function asSendable(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.type === "image/png" || file.type === "image/jpeg") {
    return file;
  }
  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error(`${file.name} could not be turned into a PNG.`);
  return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".png", { type: "image/png" });
}

export function Composer({
  chat,
  initialAttachments,
  me,
  models,
  modelsError,
  running,
  onSent,
  onChat,
}: {
  chat: Chat;
  initialAttachments?: Pending[];
  me: Me;
  models: Models | null;
  modelsError: string | null;
  running: boolean;
  /** `memory`: what the message did to the person's notes, when it began with
   * "remember" or "forget" (the message is sent either way). */
  onSent: (user: Message, answer: Message, memory?: MemoryCommand) => void;
  onChat: (chat: Chat) => void;
}) {
  const [text, setText] = useState(() => readDraft(me.sub, chat.id));
  const [draftSaved, setDraftSaved] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // The switch and the picker change the moment they are used; the chat on
  // the server follows, and a refusal puts them back and says why.
  const [searchWanted, setSearchWanted] = useState(chat.search);
  const [picked, setPicked] = useState<string | null>(chat.model);
  const files = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const sendPending = useRef(false);
  const uploadPending = useRef(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  // Asked once, when the chat opens: only a mouse takes the focus (below).
  const [takeFocus] = useState(() => !touchFirst());

  function changeText(value: string) {
    setText(value);
    setDraftSaved(saveDraft(me.sub, chat.id, value));
  }

  useLayoutEffect(() => {
    const field = textarea.current;
    if (!field) return;
    // From 4.5 to 15 rem, not 72 to 240 px: a larger text setting in the
    // person's browser makes the box larger with it. jsdom has no root size.
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    field.style.height = "auto";
    field.style.height = `${Math.min(15 * rem, Math.max(4.5 * rem, field.scrollHeight))}px`;
  }, [text]);

  const available = useMemo(() => models?.models ?? [], [models]);
  const chosenId =
    picked ??
    available.find((m) => m.id === remembered(me.sub))?.id ??
    available.find((m) => m.ready)?.id ??
    available[0]?.id ??
    null;
  const chosen = available.find((m) => m.id === chosenId);
  const searchOff = searchProblem(models, chosen);
  const searchOn = searchWanted && !searchOff;
  // The files the message carries, and what the picked model would do with each.
  const docs = useChatFiles(chat.id, chosenId, initialAttachments);
  const pending = docs.files;
  const cannotTake = attachmentProblem(chosen, pending);
  const hold = holdWords(pending, docs.facts, docs.checking, chosenId);
  const filesSaid = useFileAnnouncements(pending, docs.facts?.model ?? null);

  // A new chat has no model on the server until one is sent or picked, yet the
  // picker already shows one. Telling the chat now keeps whatever asks the
  // server which model this chat uses (Memory's chip, which says where the notes
  // would go) in step with what the person sees. The first send carries the
  // model anyway, so a refusal here is not worth a message.
  const told = useRef<string | null>(null);
  useEffect(() => {
    if (chat.model || !chosen || told.current === chosen.id) return;
    told.current = chosen.id;
    patch<Chat>(`/api/chats/${chat.id}`, { model: chosen.id }).then(onChat, () => undefined);
  }, [chat.id, chat.model, chosen, onChat]);

  async function setModel(id: string) {
    const before = picked;
    setPicked(id);
    try {
      onChat(await patch<Chat>(`/api/chats/${chat.id}`, { model: id }));
      remember(me.sub, id);
    } catch (error) {
      setPicked(before);
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  async function setSearch(on: boolean) {
    setSearchWanted(on);
    try {
      onChat(await patch<Chat>(`/api/chats/${chat.id}`, { search: on }));
    } catch (error) {
      setSearchWanted(!on);
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  async function attach(list: FileList | File[] | null) {
    if (sendPending.current || uploadPending.current || !list?.length) return;
    uploadPending.current = true;
    setProblem(null);
    const originals = Array.from(list);
    const failures: string[] = [];
    for (const [index, original] of originals.entries()) {
      setUploading(`Uploading ${index + 1} of ${originals.length}: ${original.name}`);
      try {
        const file = await asSendable(original);
        const form = new FormData();
        form.append("file", file);
        // Asked for the model that is picked, so a document's first decision is for it.
        const query = chosenId ? `?model=${encodeURIComponent(chosenId)}` : "";
        const stored = await api<Pending>(`/api/chats/${chat.id}/files${query}`, {
          method: "POST",
          body: form,
        });
        docs.add(stored);
      } catch (error) {
        failures.push(
          `${original.name}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }
    if (failures.length) setProblem(failures.join(" "));
    uploadPending.current = false;
    setUploading(null);
    if (files.current) files.current.value = "";
  }

  /** An upload taken back before it is sent is deleted, so it stops counting
   * against the chat's limit (workbench#3). A refusal keeps the chip. */
  async function remove(file: Pending) {
    // The button that was pressed goes with its file: if the keyboard was on a
    // chip, it goes back to the message box, where the person was writing.
    const fromKeyboard = document.activeElement?.closest('[data-testid="attached"]') != null;
    setRemoving(file.id);
    setProblem(null);
    try {
      await del(`/api/chats/${chat.id}/files/${encodeURIComponent(file.id)}`);
      docs.drop(file.id);
      if (fromKeyboard) textarea.current?.focus();
    } catch (error) {
      setProblem(
        `${file.name} was not removed: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setRemoving(null);
    }
  }

  async function send() {
    if (!chosen || sendPending.current || uploadPending.current || running || cannotTake) return;
    if (removing !== null || hold !== null) return;
    if (!text.trim() && pending.length === 0) return;
    sendPending.current = true;
    setSending(true);
    setProblem(null);
    try {
      const sent = await post<{ user: Message; answer: Message; memory?: MemoryCommand }>(
        `/api/chats/${chat.id}/messages`,
        {
          content: text,
          attachments: pending.map((p) => p.id),
          model: chosenId,
          search: searchOn,
        },
      );
      remember(me.sub, chosen.id);
      onSent({ ...sent.user, files: pending.map(asAttached) }, sent.answer, sent.memory);
      changeText("");
      docs.clear();
    } catch (error) {
      // A file that cannot go says so on its own chip; nothing was stored.
      const refused = refusalFor(error, "documents");
      if (refused && refused.files.length > 0) docs.refuse(refused.files);
      else setProblem(refused?.message ?? (error instanceof Error ? error.message : String(error)));
    } finally {
      setSending(false);
      sendPending.current = false;
      textarea.current?.focus();
    }
  }

  async function stop() {
    try {
      await post(`/api/chats/${chat.id}/stop`);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  const blocked = !chosenId
    ? "There is no model to send to."
    : !chosen
      ? "This model is no longer available. Pick another model."
      : cannotTake;

  // What the draft's state says, beside the keys: the keys are for a keyboard
  // and leave a phone, but a draft that cannot be kept is something the person
  // must read wherever they are, so it is its own line and never hides.
  const draftLost = Boolean(text) && !draftSaved;

  return (
    <div
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = uploading || sending ? "none" : "copy";
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        void attach(e.dataTransfer.files);
      }}
      className={`border-t bg-panel px-4 pb-4 pt-3 ${dragging ? "border-accent ring-2 ring-inset ring-accent" : "border-line"}`}
      data-testid="composer-area"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {dragging && (
          <p role="status" className="notice">
            <Paperclip size={16} aria-hidden />
            {uploading || sending
              ? "Wait for the current upload or send to finish."
              : "Drop files to attach them."}
          </p>
        )}
        {uploading && (
          <p role="status" className="text-sm text-muted wrap-anywhere">
            {uploading}
          </p>
        )}
        <Attachments
          files={pending}
          facts={docs.facts}
          problems={docs.problems}
          choosing={docs.choosing}
          locked={sending}
          removing={removing}
          onRemove={(file) => void remove(file)}
          onChoose={docs.choose}
        />
        {docs.error && pending.some((file) => file.kind === "document") && (
          <p
            data-testid="files-error"
            role="alert"
            className="flex items-start gap-1.5 text-sm text-warn"
          >
            <TriangleAlert size={14} aria-hidden className="mt-1 shrink-0" />
            Workbench could not check on your files: {endSentence(docs.error)} It keeps trying.
          </p>
        )}
        {/* What a screen reader is told as a file is read, or stops fitting. */}
        <div
          aria-live="polite"
          aria-atomic="true"
          className="sr-only"
          data-testid="files-announcer"
        >
          {filesSaid}
        </div>
        <div className="flex flex-col gap-1">
          <textarea
            id="message-composer"
            ref={textarea}
            // A finger opening a chat must not raise the keyboard over it.
            autoFocus={takeFocus}
            disabled={sending}
            aria-keyshortcuts="Control+Alt+M"
            aria-describedby={draftLost ? "composer-hint composer-draft-lost" : "composer-hint"}
            aria-label="Your message"
            data-testid="composer"
            dir="auto"
            value={text}
            onChange={(e) => changeText(e.target.value)}
            onPaste={(e) => {
              const images = Array.from(e.clipboardData.files).filter((file) =>
                file.type.startsWith("image/"),
              );
              if (images.length && !sending && !uploading) {
                e.preventDefault();
                void attach(images);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
            rows={2}
            placeholder="Ask anything"
            className="control block min-h-[4.5rem] w-full resize-none overflow-y-auto px-3 py-2.5 text-read leading-[1.55]"
          />
          <p id="composer-hint" className="text-xs text-muted max-sm:sr-only">
            Enter sends; Shift+Enter adds a line. Drop files here or paste an image.{" "}
            {text && draftSaved ? "Text draft kept in this tab until you send or sign out." : ""}
          </p>
          {draftLost && (
            <p
              id="composer-draft-lost"
              data-testid="draft-lost"
              className="flex items-start gap-1.5 text-xs text-warn"
            >
              <TriangleAlert size={14} aria-hidden className="mt-px shrink-0" />
              This browser cannot save your draft after you leave this chat.
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <input
            ref={files}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            aria-label="Choose files to attach"
            data-testid="attach-input"
            onChange={(e) => void attach(e.target.files)}
          />
          <div className="flex min-w-0 flex-[1_1_16rem] items-center gap-3">
            <button
              type="button"
              data-testid="attach"
              onClick={() => files.current?.click()}
              disabled={uploading !== null || sending}
              className="btn"
            >
              <Paperclip size={14} aria-hidden /> Attach
            </button>
            <label className="flex min-w-0 max-w-full flex-1 items-center gap-1">
              <span className="text-muted">Model</span>
              <select
                data-testid="model-picker"
                value={chosenId ?? ""}
                disabled={sending}
                onChange={(e) => void setModel(e.target.value)}
                className="control control--sm min-w-0 max-w-full sm:max-w-72"
              >
                {available.length === 0 && <option value="">No models</option>}
                {chosenId && !chosen && <option value={chosenId}>{chosenId} (unavailable)</option>}
                {available.map((model) => (
                  <option key={model.id} value={model.id}>
                    {modelLabel(model)}
                    {takes(model).length ? ` · takes ${takes(model).join(", ")}` : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <span
              className="inline-flex items-center gap-1.5 text-muted"
              title="Tools the model can use"
            >
              <Wrench size={14} aria-hidden />
              Toolbox · Tools:
            </span>
            <label className="check items-center" title={searchOff ?? SEARCH_HINT}>
              <input
                type="checkbox"
                role="switch"
                data-testid="search-switch"
                aria-describedby={searchOn ? "search-privacy" : undefined}
                checked={searchOn}
                disabled={searchOff !== null || sending}
                onChange={(e) => void setSearch(e.target.checked)}
              />
              {SEARCH_LABEL}
            </label>
            {running ? (
              <button type="button" data-testid="stop" onClick={() => void stop()} className="btn">
                <Square size={14} aria-hidden /> Stop
              </button>
            ) : (
              <button
                type="button"
                data-testid="send"
                aria-describedby={hold ? "send-held" : undefined}
                disabled={
                  blocked !== null ||
                  hold !== null ||
                  sending ||
                  uploading !== null ||
                  removing !== null ||
                  (!text.trim() && pending.length === 0)
                }
                onClick={() => void send()}
                className="btn btn-primary"
              >
                <Send size={14} aria-hidden /> Send
              </button>
            )}
          </div>
        </div>
        {hold && (
          // Why Send waits, beside it: a file still being read, or one that
          // needs a choice. Not an error, so not an alert.
          <p
            id="send-held"
            data-testid="send-held"
            className="flex items-start justify-end gap-1.5 text-right text-sm text-muted wrap-anywhere"
          >
            <Info size={14} aria-hidden className="mt-1 shrink-0" />
            {hold}
          </p>
        )}
        {searchOff && (
          <p
            className="flex items-start gap-1.5 text-sm text-muted wrap-anywhere"
            data-testid="search-off"
          >
            <Info size={14} aria-hidden className="mt-1 shrink-0" />
            Search is off: {searchOff}
          </p>
        )}
        {searchOn && (
          // Where the search words go, on the screen and not only in the
          // switch's tooltip, which a phone never shows.
          <p
            id="search-privacy"
            data-testid="search-privacy"
            className="flex items-start gap-1.5 text-xs text-muted"
          >
            <Globe size={14} aria-hidden className="mt-px shrink-0" />
            Search is on. {SEARCH_PRIVACY}
          </p>
        )}
        {(blocked || problem || modelsError) && (
          <p role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem ?? blocked ?? modelsError}
          </p>
        )}
      </div>
    </div>
  );
}
