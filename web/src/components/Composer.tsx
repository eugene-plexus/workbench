import { Paperclip, Send, Square, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { api, patch, post } from "../lib/api";
import type { Chat, Me, Message, Model, Models } from "../lib/types";
import { modelLabel, SEARCH_HINT, SEARCH_LABEL, takes } from "../lib/words";

const MODEL_KEY = "workbench-model";
const ACCEPT =
  "image/png,image/jpeg,image/webp,image/gif,application/pdf,audio/wav,audio/x-wav,audio/mpeg";

interface Pending {
  id: string;
  name: string;
  mediaType: string;
}

function remembered(): string | null {
  try {
    return window.localStorage.getItem(MODEL_KEY);
  } catch {
    return null;
  }
}

function remember(model: string): void {
  try {
    window.localStorage.setItem(MODEL_KEY, model);
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
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error(`${file.name} could not be turned into a PNG.`);
  return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".png", { type: "image/png" });
}

export function Composer({
  chat,
  models,
  modelsError,
  running,
  onSent,
  onChat,
}: {
  chat: Chat;
  me: Me;
  models: Models | null;
  modelsError: string | null;
  running: boolean;
  onSent: (user: Message, answer: Message) => void;
  onChat: (chat: Chat) => void;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // The switch and the picker change the moment they are used; the chat on
  // the server follows, and a refusal puts them back and says why.
  const [searchWanted, setSearchWanted] = useState(chat.search);
  const [picked, setPicked] = useState<string | null>(chat.model);
  const files = useRef<HTMLInputElement>(null);

  const available = useMemo(() => models?.models ?? [], [models]);
  const chosenId =
    picked ??
    available.find((m) => m.id === remembered())?.id ??
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
    remember(id);
    try {
      onChat(await patch<Chat>(`/api/chats/${chat.id}`, { model: id }));
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

  async function attach(list: FileList | null) {
    setProblem(null);
    for (const original of Array.from(list ?? [])) {
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
        setProblem(error instanceof Error ? error.message : String(error));
      }
    }
    if (files.current) files.current.value = "";
  }

  async function send() {
    if (!chosenId || sending || running) return;
    if (!text.trim() && pending.length === 0) return;
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
      remember(chosenId);
      onSent({ ...sent.user, files: pending }, sent.answer);
      setText("");
      setPending([]);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setSending(false);
    }
  }

  async function stop() {
    try {
      await post(`/api/chats/${chat.id}/stop`);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  const blocked = !chosenId ? "There is no model to send to." : cannotTake;

  return (
    <div className="border-t border-line bg-panel px-4 py-3">
      <div className="mx-auto flex max-w-3xl flex-col gap-2">
        {pending.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Attached">
            {pending.map((file) => (
              <li
                key={file.id}
                className="flex items-center gap-1 rounded-plexus border border-line bg-soft px-2 py-0.5 text-sm"
              >
                {file.name}
                <button
                  type="button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => setPending((current) => current.filter((p) => p.id !== file.id))}
                >
                  <X size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          aria-label="Your message"
          data-testid="composer"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
          rows={3}
          placeholder="Ask anything. Enter sends; Shift+Enter starts a new line."
          className="w-full resize-y rounded-plexus border border-line bg-soft p-2"
        />
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
            className="flex items-center gap-1 rounded-plexus border border-line px-2 py-1 hover:bg-hover"
          >
            <Paperclip size={14} aria-hidden /> Attach
          </button>
          <label className="flex items-center gap-1">
            <span className="text-muted">Model</span>
            <select
              data-testid="model-picker"
              value={chosenId ?? ""}
              onChange={(e) => void setModel(e.target.value)}
              className="max-w-72 rounded-plexus border border-line bg-soft px-1 py-1"
            >
              {available.length === 0 && <option value="">No models</option>}
              {available.map((model) => (
                <option key={model.id} value={model.id}>
                  {modelLabel(model)}
                  {takes(model).length ? ` · takes ${takes(model).join(", ")}` : ""}
                </option>
              ))}
            </select>
          </label>
          <span className="text-muted">Tools:</span>
          <label className="flex items-center gap-1" title={searchOff ?? SEARCH_HINT}>
            <input
              type="checkbox"
              role="switch"
              data-testid="search-switch"
              checked={searchOn}
              disabled={searchOff !== null}
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
              disabled={blocked !== null || sending || (!text.trim() && pending.length === 0)}
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
