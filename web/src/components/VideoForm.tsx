import { CircleAlert, CircleDollarSign, ImagePlus } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

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

/** The words a refusal's field goes by on the Video screen, so the alert can begin with them. */
const VIDEO_FIELDS: Record<string, string> = {
  prompt: "Describe the video",
  seconds: "Length",
  size: "Size",
  firstFrame: "First frame",
};

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
  const uid = useId();
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

  // What a refusal named is marked: the danger edge, aria-invalid, and the alert it is described by.
  const alertId = `${uid}-problem`;
  const named = (field: string) => problem?.field === field;
  const edge = (field: string) => (named(field) ? " border-error-line border-2" : "");
  const invalid = (field: string, ...also: string[]) => ({
    "aria-invalid": named(field) || undefined,
    "aria-describedby": [...also, ...(named(field) ? [alertId] : [])].join(" ") || undefined,
  });
  const reasonId = `${uid}-why`;

  return (
    <form
      aria-label="Make a video"
      className="card gap-4"
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
      <div className="field">
        <label htmlFor="video-prompt" className="field-label">
          Describe the video
        </label>
        <textarea
          id="video-prompt"
          data-testid="video-prompt"
          rows={3}
          value={form.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          className={`control${edge("prompt")}`}
          {...invalid("prompt")}
        />
      </div>
      {model && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="field">
            <label htmlFor={`${uid}-seconds`} className="field-label">
              Length
            </label>
            {model.durations?.length ? (
              <select
                id={`${uid}-seconds`}
                data-testid="video-seconds"
                value={form.seconds ?? ""}
                onChange={(e) => set({ seconds: e.target.value ? Number(e.target.value) : null })}
                className={`control${edge("seconds")}`}
                {...invalid("seconds")}
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
                  id={`${uid}-seconds`}
                  type="number"
                  min={1}
                  max={120}
                  placeholder="Model's choice"
                  value={form.seconds ?? ""}
                  onChange={(e) => set({ seconds: e.target.value ? Number(e.target.value) : null })}
                  className={`control w-32 ${edge("seconds")}`}
                  {...invalid("seconds", `${uid}-seconds-help`)}
                />
                <p id={`${uid}-seconds-help`} className="help">
                  Checked by {model.provider ?? "the backend"} when sent
                </p>
              </>
            )}
          </div>
          {model.sizes?.length ? (
            <div className="field">
              <label htmlFor={`${uid}-size`} className="field-label">
                Size
              </label>
              <select
                id={`${uid}-size`}
                data-testid="video-size"
                value={form.size ?? ""}
                onChange={(e) => set({ size: e.target.value || null })}
                className={`control${edge("size")}`}
                {...invalid("size")}
              >
                <option value="">Model&apos;s choice</option>
                {model.sizes.map((s) => (
                  <option key={s} value={s}>
                    {s.replace("x", " × ")}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <p className="help self-end">This model chooses its own size.</p>
          )}
        </div>
      )}
      {model?.firstFrame && (
        <div className="flex flex-col gap-2">
          <div className="field">
            <label htmlFor={`${uid}-frame`} className="field-label">
              First frame <small>(optional)</small>
            </label>
            <select
              id={`${uid}-frame`}
              data-testid="video-frame"
              value={form.frame?.id ?? ""}
              onChange={(e) =>
                set({
                  frame:
                    images.find((f) => f.id === e.target.value) ??
                    (form.frame?.id === e.target.value ? form.frame : null),
                })
              }
              className={`control${edge("firstFrame")}`}
              {...invalid("firstFrame")}
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
          </div>
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
              className="btn"
              disabled={uploading}
              onClick={() => upload.current?.click()}
            >
              <ImagePlus size={14} aria-hidden />
              {uploading ? "Bringing it in…" : "Bring in an image"}
            </button>
          </div>
        </div>
      )}
      {problem && (
        <div id={alertId} role="alert" className="notice notice--error" data-testid="video-problem">
          <CircleAlert size={16} aria-hidden />
          <div>
            {problem.field && VIDEO_FIELDS[problem.field] && (
              <span className="notice-title">{VIDEO_FIELDS[problem.field]}</span>
            )}
            {problem.message}
          </div>
        </div>
      )}
      {confirming && model ? (
        <div role="group" aria-label="Before sending" className="notice notice--warn">
          <CircleDollarSign size={16} aria-hidden />
          <div className="flex flex-col gap-3">
            <p data-testid="video-quote">{quoteWords(model, ask)}</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="confirm-video"
                className="btn btn-primary"
                disabled={sending}
                onClick={() => void send()}
              >
                {sending ? "Sending…" : "Make it"}
              </button>
              <button
                ref={notYet}
                type="button"
                className="btn"
                onClick={() => {
                  cancelled.current = true;
                  setConfirming(false);
                }}
              >
                Not yet
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            ref={makeButton}
            type="submit"
            data-testid="make-video"
            disabled={Boolean(why)}
            aria-describedby={why ? reasonId : undefined}
            className="btn btn-primary"
          >
            Make the video
          </button>
          {why && (
            <p id={reasonId} className="help">
              {why}
            </p>
          )}
        </div>
      )}
    </form>
  );
}
