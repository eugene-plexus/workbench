import { Paperclip, Send, Square, X } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, del, patch, post } from "../lib/api";
import { readDraft, saveDraft } from "../lib/conveniences";
import type { Chat, Me, Message, Model, Models } from "../lib/types";
import { modelLabel, SEARCH_HINT, SEARCH_LABEL, takes } from "../lib/words";

const MODEL_KEY = "workbench-model";
const ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,application/pdf,audio/wav,audio/x-wav,audio/mpeg";

export interface Pending {
  id: string;
  name: string;
  mediaType: string;
}

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

/** The kind of attachment a model must take for this file, or null. */
export function needs(mediaType: string): "image" | "file" | "audio" | null {
  if (mediaType === "image/png" || mediaType === "image/jpeg") return "image";
  if (mediaType === "application/pdf") return "file";
  if (mediaType === "audio/wav" || mediaType === "audio/mpeg") return "audio";
  return null;
}

/** Why `model` cannot take `pending`, or null when it can. */
export function attachmentProblem(model: Model | undefined, pending: Pending[]): string | null {
  if (!model || pending.length === 0) return null;
  const missing = new Set<string>();
  for (const file of pending) {
    const kind = needs(file.mediaType);
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
  onSent: (user: Message, answer: Message) => void;
  onChat: (chat: Chat) => void;
}) {
  const [text, setText] = useState(() => readDraft(me.sub, chat.id));
  const [draftSaved, setDraftSaved] = useState(true);
  const [pending, setPending] = useState<Pending[]>(() => initialAttachments ?? []);
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

  function changeText(value: string) {
    setText(value);
    setDraftSaved(saveDraft(me.sub, chat.id, value));
  }

  useLayoutEffect(() => {
    const field = textarea.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(240, Math.max(80, field.scrollHeight))}px`;
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
  const cannotTake = attachmentProblem(chosen, pending);

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
        const stored = await api<Pending>(`/api/chats/${chat.id}/files`, {
          method: "POST",
          body: form,
        });
        setPending((current) => [...current, stored]);
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
    setRemoving(file.id);
    setProblem(null);
    try {
      await del(`/api/chats/${chat.id}/files/${encodeURIComponent(file.id)}`);
      setPending((current) => current.filter((p) => p.id !== file.id));
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
    if (removing !== null) return;
    if (!text.trim() && pending.length === 0) return;
    sendPending.current = true;
    setSending(true);
    setProblem(null);
    try {
      const sent = await post<{ user: Message; answer: Message }>(
        `/api/chats/${chat.id}/messages`,
        {
          content: text,
          attachments: pending.map((p) => p.id),
          model: chosenId,
          search: searchOn,
        },
      );
      remember(me.sub, chosen.id);
      onSent({ ...sent.user, files: pending }, sent.answer);
      changeText("");
      setPending([]);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
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
      className={`border-t bg-panel px-4 py-3 ${dragging ? "border-accent ring-2 ring-inset ring-accent" : "border-line"}`}
      data-testid="composer-area"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {dragging && (
          <p role="status" className="text-sm text-accent">
            {uploading || sending
              ? "Wait for the current upload or send to finish."
              : "Drop files to attach them."}
          </p>
        )}
        {uploading && (
          <p role="status" className="break-words text-sm text-muted">
            {uploading}
          </p>
        )}
        {pending.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Attached">
            {pending.map((file) => (
              <li
                key={file.id}
                className="flex max-w-full items-center gap-1 break-all rounded-plexus border border-line bg-soft px-2 py-0.5 text-sm"
              >
                {file.name}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  disabled={sending || removing !== null}
                  onClick={() => void remove(file)}
                >
                  <X size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          id="message-composer"
          ref={textarea}
          autoFocus
          disabled={sending}
          aria-keyshortcuts="Control+Alt+M"
          aria-describedby="composer-hint"
          aria-label="Your message"
          data-testid="composer"
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
          rows={3}
          placeholder="Ask anything"
          className="w-full resize-none overflow-y-auto rounded-plexus border border-line bg-soft p-2"
        />
        <p id="composer-hint" className="text-xs text-muted">
          Enter sends; Shift+Enter adds a line. Drop files here or paste an image.{" "}
          {text
            ? draftSaved
              ? "Text draft kept in this tab until you send or sign out."
              : "This browser cannot save your draft after you leave this chat."
            : ""}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input
            ref={files}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            data-testid="attach-input"
            onChange={(e) => void attach(e.target.files)}
          />
          <button
            type="button"
            onClick={() => files.current?.click()}
            disabled={uploading !== null || sending}
            className="flex items-center gap-1 rounded-plexus border border-line px-2 py-1 hover:bg-hover"
          >
            <Paperclip size={14} aria-hidden /> Attach
          </button>
          <label className="flex items-center gap-1">
            <span className="text-muted">Model</span>
            <select
              data-testid="model-picker"
              value={chosenId ?? ""}
              disabled={sending}
              onChange={(e) => void setModel(e.target.value)}
              className="min-w-0 max-w-52 rounded-plexus border border-line bg-soft px-1 py-1 sm:max-w-72"
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
          <span className="text-muted" title="Tools the model can use">
            Toolbox · Tools:
          </span>
          <label className="flex items-center gap-1" title={searchOff ?? SEARCH_HINT}>
            <input
              type="checkbox"
              role="switch"
              data-testid="search-switch"
              checked={searchOn}
              disabled={searchOff !== null || sending}
              onChange={(e) => void setSearch(e.target.checked)}
            />
            {SEARCH_LABEL}
          </label>
          <span className="flex-1" />
          {running ? (
            <button
              type="button"
              data-testid="stop"
              onClick={() => void stop()}
              className="flex items-center gap-1 rounded-plexus border border-line px-3 py-1.5 hover:bg-hover"
            >
              <Square size={14} aria-hidden /> Stop
            </button>
          ) : (
            <button
              type="button"
              data-testid="send"
              disabled={
                blocked !== null ||
                sending ||
                uploading !== null ||
                removing !== null ||
                (!text.trim() && pending.length === 0)
              }
              onClick={() => void send()}
              className="flex items-center gap-1 rounded-plexus bg-accent px-3 py-1.5 font-medium text-on-accent disabled:opacity-50"
            >
              <Send size={14} aria-hidden /> Send
            </button>
          )}
        </div>
        {searchOff && (
          <p className="text-xs text-muted" data-testid="search-off">
            Search is off: {searchOff}
          </p>
        )}
        {(blocked || problem || modelsError) && (
          <p role="alert" className="text-sm text-error">
            {problem ?? blocked ?? modelsError}
          </p>
        )}
      </div>
    </div>
  );
}
