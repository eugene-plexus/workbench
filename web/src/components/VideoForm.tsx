import { ImagePlus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, post } from "../lib/api";
import {
  type MediaDraft,
  type MediaFile,
  type MediaItem,
  type VideoModel,
  quoteWords,
  rememberModel,
  rememberedModel,
} from "../lib/media";
import type { Me } from "../lib/types";
import { ModelPicker } from "./AudioForms";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

const sendButton =
  "rounded-plexus bg-accent px-4 py-2 font-medium text-on-accent hover:opacity-90 disabled:opacity-50";

/** A size's width, so of two the same area the wider (landscape) comes first. */
const wide = (size: string) => Number(size.split("x")[0]) || 0;

const area = (size: string) => {
  const [w, h] = size.split("x").map(Number);
  return (w ?? 0) * (h ?? 0);
};

/** What a model is first offered at: its shortest length and its smallest
 * size, so the price a person first sees is the least the model asks. */
function cheapest(model: VideoModel | undefined): { seconds: number | null; size: string | null } {
  const seconds = model?.durations?.length ? Math.min(...model.durations) : null;
  const size = model?.sizes?.length
    ? [...model.sizes].sort((a, b) => area(a) - area(b) || wide(b) - wide(a))[0]!
    : null;
  return { seconds, size };
}

interface Form {
  model: string;
  prompt: string;
  seconds: number | null;
  size: string | null;
  frame: MediaFile | null;
}

/** The Video screen's form (workbench-media-screens.md §5): a prompt, a
 * length and a size from the model's listing, and a first frame where the
 * model takes one. **Sending asks first**, with the price from the listing
 * (M6, §6.4), because a video is the most a person can spend in one go. */
export function VideoForm({
  me,
  models,
  draft,
  onMade,
}: {
  me: Me;
  models: VideoModel[];
  draft: MediaDraft | null;
  onMade: (item: MediaItem) => void;
}) {
  const [form, setForm] = useState<Form>(() => {
    // No model is picked on a first visit, so a paid one is never chosen for anyone (M5).
    const model = models.find((m) => m.id === rememberedModel(me.sub, "video"));
    return { model: model?.id ?? "", prompt: "", ...cheapest(model), frame: null };
  });
  const [images, setImages] = useState<MediaFile[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<{ message: string; field: string | null } | null>(null);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const notYet = useRef<HTMLButtonElement>(null);
  const makeButton = useRef<HTMLButtonElement>(null);
  const cancelled = useRef(false);
  const model = models.find((m) => m.id === form.model);

  // Any change asks again: the price asked about is always what is sent.
  const set = (values: Partial<Form>) => {
    setConfirming(false);
    setForm((current) => ({ ...current, ...values }));
  };

  const loadImages = useCallback(async () => {
    try {
      const found = await api<{ items: MediaItem[] }>("/api/media?door=images");
      setImages(
        found.items.flatMap((i) => i.files).filter((f) => f.mediaType.startsWith("image/")),
      );
    } catch {
      // The bin's images are a convenience here; bringing one in still works.
    }
  }, []);

  useEffect(() => {
    if (model?.firstFrame) void loadImages();
  }, [model?.firstFrame, loadImages]);

  // The price asks a question: focus moves to the safe answer, and returns to
  // "Make the video" if it is declined.
  useEffect(() => {
    if (confirming) notYet.current?.focus();
    else if (cancelled.current) {
      cancelled.current = false;
      makeButton.current?.focus();
    }
  }, [confirming]);

  // "Edit and send" fills the form with what a result asked for.
  useEffect(() => {
    if (!draft) return;
    const asked = draft.request;
    const known = models.find((m) => m.id === asked.model);
    setConfirming(false);
    setForm((current) => ({
      model: known?.id ?? current.model,
      prompt: asked.prompt ?? "",
      seconds: asked.seconds ?? null,
      size: asked.size ?? null,
      frame: asked.firstFrame
        ? (images.find((f) => f.id === asked.firstFrame) ?? {
            id: asked.firstFrame,
            name: "the first frame you chose before",
            mediaType: "image/png",
            size: 0,
            width: null,
            height: null,
          })
        : null,
    }));
    setProblem(draft.message ? { message: draft.message, field: draft.field } : null);
    // Only a new draft fills the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const why = !model
    ? "Choose a model first."
    : !form.prompt.trim()
      ? "Describe the video first."
      : form.frame && !model.firstFrame
        ? `${model.id} does not start from an image. Remove the first frame.`
        : null;
  const ask = { seconds: form.seconds, size: form.size, firstFrame: form.frame !== null };

  async function send() {
    if (!model || why || sending) return;
    setSending(true);
    setProblem(null);
    try {
      const body: Record<string, unknown> = { model: model.id, prompt: form.prompt.trim() };
      if (form.seconds != null) body.seconds = form.seconds;
      if (form.size) body.size = form.size;
      if (form.frame) body.firstFrame = form.frame.id;
      onMade(await post<MediaItem>("/api/media/video", body));
      setConfirming(false);
    } catch (error) {
      setProblem({ message: problemOf(error), field: null });
      setConfirming(false);
    } finally {
      setSending(false);
    }
  }

  async function bringIn(file: File) {
    setUploading(true);
    setProblem(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const item = await api<MediaItem>("/api/media/images/upload", { method: "POST", body });
      const brought = item.files[0];
      if (brought) {
        setImages((shown) => [brought, ...shown.filter((f) => f.id !== brought.id)]);
        set({ frame: brought });
      }
    } catch (error) {
      setProblem({ message: problemOf(error), field: "firstFrame" });
    } finally {
      setUploading(false);
      if (upload.current) upload.current.value = "";
    }
  }

  const marked = (field: string) =>
    problem?.field === field ? "border-error-line" : "border-line";

  return (
    <form
      aria-label="Make a video"
      className="flex flex-col gap-3 rounded-plexus border border-line bg-panel p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!why) setConfirming(true);
      }}
    >
      <ModelPicker
        id="video-model"
        models={models}
        value={form.model}
        onChange={(id) => {
          const chosen = models.find((m) => m.id === id);
          set({ model: id, ...cheapest(chosen), frame: chosen?.firstFrame ? form.frame : null });
          if (id) rememberModel(me.sub, "video", id);
        }}
      />
      <div className="flex flex-col gap-1">
        <label htmlFor="video-prompt" className="text-sm font-medium">
          Describe the video
        </label>
        <textarea
          id="video-prompt"
          data-testid="video-prompt"
          rows={3}
          value={form.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          className={`rounded-plexus border ${marked("prompt")} bg-surface px-2 py-1`}
        />
      </div>
      {model && (
        <div className="flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-sm">
            Length
            {model.durations?.length ? (
              <select
                data-testid="video-seconds"
                value={form.seconds ?? ""}
                onChange={(e) => set({ seconds: e.target.value ? Number(e.target.value) : null })}
                className={`rounded-plexus border ${marked("seconds")} bg-surface px-2 py-1`}
              >
                {model.durations.map((d) => (
                  <option key={d} value={d}>
                    {d} s
                  </option>
                ))}
              </select>
            ) : (
              <>
                <input
                  type="number"
                  min={1}
                  max={120}
                  placeholder="Model's choice"
                  value={form.seconds ?? ""}
                  onChange={(e) => set({ seconds: e.target.value ? Number(e.target.value) : null })}
                  className={`w-32 rounded-plexus border ${marked("seconds")} bg-surface px-2 py-1`}
                />
                <span className="text-xs text-muted">
                  Checked by {model.provider ?? "the backend"} when sent
                </span>
              </>
            )}
          </label>
          {model.sizes?.length ? (
            <label className="flex flex-col gap-1 text-sm">
              Size
              <select
                data-testid="video-size"
                value={form.size ?? ""}
                onChange={(e) => set({ size: e.target.value || null })}
                className={`rounded-plexus border ${marked("size")} bg-surface px-2 py-1`}
              >
                <option value="">Model&apos;s choice</option>
                {model.sizes.map((s) => (
                  <option key={s} value={s}>
                    {s.replace("x", " × ")}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="self-end text-sm text-muted">This model chooses its own size.</p>
          )}
        </div>
      )}
      {model?.firstFrame && (
        <div className={`flex flex-col gap-2 rounded-plexus border ${marked("firstFrame")} p-2`}>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">First frame (optional)</span>
            <select
              data-testid="video-frame"
              value={form.frame?.id ?? ""}
              onChange={(e) =>
                set({
                  frame:
                    images.find((f) => f.id === e.target.value) ??
                    (form.frame?.id === e.target.value ? form.frame : null),
                })
              }
              className="rounded-plexus border border-line bg-surface px-2 py-1"
            >
              <option value="">None: from the words alone</option>
              {form.frame && !images.some((f) => f.id === form.frame?.id) && (
                <option value={form.frame.id}>{form.frame.name}</option>
              )}
              {images.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
          <div>
            <input
              ref={upload}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              data-testid="video-bring-in"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void bringIn(file);
              }}
            />
            <button
              type="button"
              className="flex items-center gap-1 text-sm text-accent"
              disabled={uploading}
              onClick={() => upload.current?.click()}
            >
              <ImagePlus size={14} aria-hidden />{" "}
              {uploading ? "Bringing it in…" : "Bring in an image"}
            </button>
          </div>
        </div>
      )}
      {problem && (
        <p role="alert" className="text-error" data-testid="video-problem">
          {problem.message}
        </p>
      )}
      {confirming && model ? (
        <div
          role="group"
          aria-label="Before sending"
          className="flex flex-col gap-2 rounded-plexus border border-warn-line bg-warn-bg p-3"
        >
          <p className="text-sm text-warn" data-testid="video-quote">
            {quoteWords(model, ask)}
          </p>
          <div className="flex items-center gap-3">
            <button
              type="button"
              data-testid="confirm-video"
              className={sendButton}
              disabled={sending}
              onClick={() => void send()}
            >
              {sending ? "Sending…" : "Make it"}
            </button>
            <button
              ref={notYet}
              type="button"
              className="underline"
              onClick={() => {
                cancelled.current = true;
                setConfirming(false);
              }}
            >
              Not yet
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-3">
          <button
            ref={makeButton}
            type="submit"
            data-testid="make-video"
            disabled={Boolean(why)}
            className={sendButton}
          >
            Make the video
          </button>
          {why && <p className="text-sm text-muted">{why}</p>}
        </div>
      )}
    </form>
  );
}
