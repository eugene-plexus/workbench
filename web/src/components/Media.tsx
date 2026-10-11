import {
  ArrowLeft,
  Boxes,
  CircleAlert,
  Download,
  Eye,
  ImagePlus,
  MessageSquarePlus,
  RotateCcw,
  Square,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { api, del, fileUrl, post } from "../lib/api";
import { watchStream } from "../lib/events";
import {
  DOORS,
  DOOR_WORDS,
  type Door,
  type Doors,
  type ImageModel,
  type MediaEvent,
  type MediaDraft,
  type MediaFile,
  type MediaItem,
  SHAPES,
  SIZE_PATTERN,
  billedWords,
  bytesWords,
  captionOf,
  fieldOf,
  heardWords,
  imageBody,
  maxImages,
  offer,
  referenceRange,
  rememberModel,
  rememberedModel,
  servedWords,
  sizeWords,
  statusWords,
  videoWords,
} from "../lib/media";
import type { Me } from "../lib/types";
import { ModelPicker, SpeechForm, TranscriptionForm } from "./AudioForms";
import { CopyButton } from "./CopyButton";
import { Mascot } from "./Mascot";
import { VideoForm } from "./VideoForm";
import { WorkingScene } from "./WorkingScene";
import { useClock } from "../lib/useClock";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** After a question is answered the button that asked it is back: keyboard
 * focus goes there instead of to the page. */
const refocus = (id: string) => queueMicrotask(() => document.getElementById(id)?.focus());

export interface Handoff {
  id: string;
  name: string;
  mediaType: string;
}

/** The media area: a tab per screen, its form above and its bin below
 * (workbench-media-screens.md §2): Images, Speech, Transcription and Video,
 * whose bin is the list of long jobs, running and finished (M11). */
export function Media({
  me,
  door,
  focus,
  person,
  onClose,
  onDoor,
  onToChat,
  onTextToChat,
}: {
  me: Me;
  door: Door;
  focus: string | null;
  /** The owner reading someone else's bins (M3), or null for one's own. */
  person: { sub: string; name: string } | null;
  onClose: () => void;
  onDoor: (door: Door) => void;
  onToChat: (chatId: string, attachment: Handoff) => void;
  /** A transcript sent to a new chat, as text waiting in its composer (M7). */
  onTextToChat: (text: string) => void;
}) {
  const uid = useId();
  const [doors, setDoors] = useState<Doors | null>(null);
  const [doorsError, setDoorsError] = useState<string | null>(null);
  const [items, setItems] = useState<MediaItem[]>([]);
  const [bytes, setBytes] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [addReference, setAddReference] = useState<MediaFile | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);
  // Which read a "Try again" is waiting on, so it is pressed once.
  const [retrying, setRetrying] = useState<"doors" | "bin" | null>(null);
  // Which bin the list on screen was last read for: "Nothing here yet" waits for it.
  const [listedFor, setListedFor] = useState<string | null>(null);
  const tabRefs = useRef(new Map<Door, HTMLButtonElement>());
  // The page is its own scroller (the window never scrolls), so "Edit and send"
  // brings the form into view by scrolling this, not the window.
  const page = useRef<HTMLElement>(null);
  const readOnly = person !== null;
  const binKey = `${person?.sub ?? ""}|${door}`;
  const emptyId = `${uid}-empty`;

  const loadDoors = useCallback(async () => {
    try {
      setDoors(await api<Doors>("/api/media/doors"));
      setDoorsError(null);
    } catch (error) {
      setDoorsError(problemOf(error));
    }
  }, []);

  const loadList = useCallback(async () => {
    try {
      if (person) {
        const found = await api<{ items: MediaItem[] }>(
          `/api/people/${encodeURIComponent(person.sub)}/media?door=${door}`,
        );
        setItems(found.items);
      } else {
        const found = await api<{ items: MediaItem[]; bytes: number }>(`/api/media?door=${door}`);
        setItems(found.items);
        setBytes(found.bytes);
      }
      setListError(null);
    } catch (error) {
      setListError(problemOf(error));
    }
    setListedFor(binKey);
  }, [door, person, binKey]);

  useEffect(() => {
    void loadList();
    if (readOnly) return;
    void loadDoors();
    const every = window.setInterval(() => void loadDoors(), 20000);
    const watching = watchStream<MediaEvent>(
      "/api/media/events",
      (event) => {
        if (event.type === "reload") void loadList();
        if (event.type !== "media" || event.item.door !== door) return;
        setItems((shown) => {
          const rest = shown.filter((i) => i.id !== event.item.id);
          return [event.item, ...rest].sort((a, b) => b.createdAt - a.createdAt);
        });
        if (event.item.status !== "running") void loadList();
      },
      () => void loadList(),
    );
    return () => {
      window.clearInterval(every);
      watching.close();
    };
  }, [door, readOnly, loadDoors, loadList]);

  useEffect(() => {
    if (focus) document.getElementById(`media-${focus}`)?.scrollIntoView({ block: "center" });
  }, [focus, items.length]);

  /** The same read again, for the person to ask for: both reads keep their own failure. */
  async function retry(what: "doors" | "bin") {
    setRetrying(what);
    try {
      await (what === "doors" ? loadDoors() : loadList());
    } finally {
      setRetrying(null);
    }
  }

  const served = DOORS.filter((d) => (doors?.doors[d]?.models.length ?? 0) > 0);
  // A tab shows when a model serves its screen (M1); the one asked for always does.
  const tabs =
    readOnly || doors === null ? [door] : DOORS.filter((d) => d === door || served.includes(d));
  const nothingServes = doors !== null && !served.includes(door);

  async function remove(item: MediaItem) {
    try {
      await del(`/api/media/${encodeURIComponent(item.id)}`);
      setItems((shown) => shown.filter((i) => i.id !== item.id));
      void loadList();
    } catch (error) {
      setActionError(problemOf(error));
    }
  }

  async function emptyBin() {
    try {
      await del(`/api/media?door=${door}`);
      setConfirmEmpty(false);
      await loadList();
    } catch (error) {
      setActionError(problemOf(error));
    }
  }

  async function toChat(item: MediaItem, file: MediaFile) {
    try {
      const sent = await post<{ chatId: string; attachment: Handoff }>(
        `/api/media/${encodeURIComponent(item.id)}/to-chat`,
        { fileId: file.id },
      );
      onToChat(sent.chatId, sent.attachment);
    } catch (error) {
      setActionError(problemOf(error));
    }
  }

  async function again(item: MediaItem) {
    const asked = { ...item.request };
    delete asked.name;
    try {
      const made = await post<MediaItem>(`/api/media/${item.door}`, asked);
      setItems((shown) => [made, ...shown.filter((i) => i.id !== made.id)]);
    } catch (error) {
      setActionError(problemOf(error));
    }
  }

  /** WAI-ARIA tabs: arrows, Home and End move to a tab and show it. */
  function moveTab(event: KeyboardEvent, from: Door) {
    const at = tabs.indexOf(from);
    const to =
      event.key === "ArrowRight"
        ? tabs[(at + 1) % tabs.length]
        : event.key === "ArrowLeft"
          ? tabs[(at - 1 + tabs.length) % tabs.length]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[tabs.length - 1]
              : undefined;
    if (!to) return;
    event.preventDefault();
    tabRefs.current.get(to)?.focus();
    if (to !== door) onDoor(to);
  }

  return (
    <section ref={page} aria-label="Bins · Media" className="page">
      <div className="page-col">
        <header className="page-head">
          <div className="min-w-0">
            <h1 id="view-heading" tabIndex={-1} className="title-page w-fit max-w-full">
              <Boxes size={20} aria-hidden />
              Bins · Media
            </h1>
            <p className="help mt-1">
              {readOnly
                ? `${person.name}'s media, read only.`
                : "What you make or bring in is kept here, even if you close this tab."}
            </p>
          </div>
          <button className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <div role="tablist" aria-label="Media screens" className="tabs flex-wrap">
          {tabs.map((d) => (
            <button
              key={d}
              ref={(el) => {
                if (el) tabRefs.current.set(d, el);
                else tabRefs.current.delete(d);
              }}
              type="button"
              role="tab"
              id={`media-tab-${d}`}
              aria-selected={d === door}
              aria-controls="media-panel"
              tabIndex={d === door ? 0 : -1}
              onClick={() => d !== door && onDoor(d)}
              onKeyDown={(event) => moveTab(event, d)}
              className="tab"
            >
              {DOOR_WORDS[d].tab}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          id="media-panel"
          aria-labelledby={`media-tab-${door}`}
          className="flex flex-col gap-6"
        >
          {readOnly && (
            <p role="status" className="notice notice--warn">
              <Eye size={16} aria-hidden />
              You are reading {person.name}&apos;s media. You cannot change, send or delete it.
            </p>
          )}
          {doorsError && (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read which models are served</span>
                {doorsError}
                <TryAgain
                  busy={retrying !== null}
                  waiting={retrying === "doors"}
                  onClick={() => void retry("doors")}
                />
              </div>
            </div>
          )}
          {!readOnly && nothingServes && <NothingServes me={me} door={door} />}
          {!readOnly && !nothingServes && doors && door === "speech" && (
            <SpeechForm
              me={me}
              models={doors.doors.speech.models}
              draft={draft}
              onMade={(made) =>
                setItems((shown) => [made, ...shown.filter((i) => i.id !== made.id)])
              }
            />
          )}
          {!readOnly && !nothingServes && doors && door === "transcription" && (
            <TranscriptionForm
              me={me}
              models={doors.doors.transcription.models}
              onMade={(made) =>
                setItems((shown) => [made, ...shown.filter((i) => i.id !== made.id)])
              }
            />
          )}
          {!readOnly && !nothingServes && doors && door === "video" && (
            <VideoForm
              me={me}
              models={doors.doors.video.models}
              draft={draft}
              onMade={(made) =>
                setItems((shown) => [made, ...shown.filter((i) => i.id !== made.id)])
              }
            />
          )}
          {!readOnly && !nothingServes && doors && door === "images" && (
            <ImageForm
              me={me}
              models={doors.doors.images.models}
              draft={draft}
              bin={items}
              addReference={addReference}
              onReferenceTaken={() => setAddReference(null)}
              onMade={(made) =>
                setItems((shown) => [made, ...shown.filter((i) => i.id !== made.id)])
              }
              onBroughtIn={(item) =>
                setItems((shown) => [item, ...shown.filter((i) => i.id !== item.id)])
              }
            />
          )}
          <section aria-labelledby={`${uid}-bin`} className="section">
            <h2 id={`${uid}-bin`} className="title-section">
              {door === "video" ? "Work orders · Long jobs" : readOnly ? "Their bin" : "Your bin"}
            </h2>
            {!readOnly && (
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                <p className="text-sm text-muted" data-testid="bin-total">
                  Your bins hold {bytesWords(bytes)}.
                </p>
                {items.length > 0 && !confirmEmpty && (
                  <button
                    id={emptyId}
                    type="button"
                    className="btn btn-quiet btn-danger -me-2"
                    onClick={() => setConfirmEmpty(true)}
                  >
                    Empty this bin
                  </button>
                )}
              </div>
            )}
            {!readOnly && confirmEmpty && (
              <div
                role="group"
                aria-label="Empty this bin?"
                className="notice notice--error flex-col"
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setConfirmEmpty(false);
                    refocus(emptyId);
                  }
                }}
              >
                <p>
                  Delete all {items.length} {items.length === 1 ? "result" : "results"} in this bin?
                  This cannot be undone.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    autoFocus
                    type="button"
                    className="btn"
                    onClick={() => {
                      setConfirmEmpty(false);
                      refocus(emptyId);
                    }}
                  >
                    Keep them
                  </button>
                  <button type="button" className="btn btn-danger" onClick={() => void emptyBin()}>
                    Delete them all
                  </button>
                </div>
              </div>
            )}
            {listError && (
              <div role="alert" className="notice notice--error">
                <CircleAlert size={16} aria-hidden />
                <div>
                  <span className="notice-title">Could not read this bin</span>
                  {listError}
                  <TryAgain
                    busy={retrying !== null}
                    waiting={retrying === "bin"}
                    onClick={() => void retry("bin")}
                  />
                </div>
              </div>
            )}
            {actionError && (
              <div role="alert" className="notice notice--error">
                <CircleAlert size={16} aria-hidden />
                <div>
                  {actionError}
                  <button
                    type="button"
                    className="btn btn-quiet ml-2"
                    onClick={() => setActionError(null)}
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}
            {items.length === 0 && !listError && listedFor !== binKey && (
              <p role="status" className="text-sm text-muted">
                Loading…
              </p>
            )}
            {items.length === 0 && !listError && listedFor === binKey && (
              <p className="empty">
                {readOnly ? "Nothing here yet." : "Nothing here yet. What you make appears here."}
              </p>
            )}
            <ul className="flex flex-col gap-3" data-testid="media-bin">
              {items.map((item) => (
                <MediaCard
                  key={item.id}
                  item={item}
                  focused={item.id === focus}
                  readOnly={readOnly}
                  onDelete={() => void remove(item)}
                  onStop={() =>
                    void post(`/api/media/${encodeURIComponent(item.id)}/stop`).catch((error) =>
                      setActionError(problemOf(error)),
                    )
                  }
                  onAgain={() => void again(item)}
                  onEdit={() => {
                    // A refused request comes back with the field the gateway named marked.
                    setDraft({
                      request: { ...item.request },
                      field: fieldOf(item.error?.param),
                      message: item.status === "failed" ? (item.error?.message ?? null) : null,
                    });
                    // jsdom has no Element.scrollTo: there is nothing to scroll.
                    page.current?.scrollTo?.({ top: 0 });
                  }}
                  onToChat={(file) => void toChat(item, file)}
                  onReference={(file) => setAddReference(file)}
                  onTextToChat={onTextToChat}
                />
              ))}
            </ul>
          </section>
        </div>
      </div>
    </section>
  );
}

/** A failed read's next step: ask again. One read at a time, and it says so while it waits. */
function TryAgain({
  busy,
  waiting,
  onClick,
}: {
  busy: boolean;
  waiting: boolean;
  onClick: () => void;
}) {
  return (
    <div className="mt-2">
      <button type="button" className="btn" disabled={busy} onClick={onClick}>
        {waiting ? "Trying again…" : "Try again"}
      </button>
    </div>
  );
}

function NothingServes({ me, door }: { me: Me; door: Door }) {
  return (
    <div className="card flex-row items-center gap-4">
      <Mascot pose="guide" size={96} />
      <div className="flex min-w-0 flex-col gap-1.5 text-sm" data-testid="no-models">
        <p className="title-card">No model here {DOOR_WORDS[door].does} yet.</p>
        {me.owner ? (
          <p>
            Add one in Eugene: <strong>Backends</strong>, then{" "}
            <strong>Add a provider account</strong>.{" "}
            {me.consoleUrl && (
              <a className="link" href={me.consoleUrl} target="_blank" rel="noopener noreferrer">
                Open Eugene&apos;s console
              </a>
            )}
          </p>
        ) : (
          <p>Ask the owner of this Workbench to add one.</p>
        )}
        {door !== "transcription" && (
          <p className="help">
            {LOCAL_LATER[door]} models that run on your own machines are coming later.
          </p>
        )}
      </div>
    </div>
  );
}

const LOCAL_LATER: Record<Door, string> = {
  images: "Image",
  speech: "Speech",
  transcription: "Transcription",
  video: "Video",
};

type Draft = MediaDraft;

interface FormState {
  model: string;
  prompt: string;
  shape: string | null | "custom";
  custom: string;
  n: number;
  quality: string;
  background: string;
  outputFormat: string;
  references: MediaFile[];
}

/** The words a refusal's field goes by on the Images screen, so the alert can begin with them. */
const IMAGE_FIELDS: Record<string, string> = {
  prompt: "Describe the image",
  size: "Shape",
  n: "How many",
  quality: "Quality",
  background: "Background",
  outputFormat: "Format",
  references: "Reference images",
};

function ImageForm({
  me,
  models,
  draft,
  bin,
  addReference,
  onReferenceTaken,
  onMade,
  onBroughtIn,
}: {
  me: Me;
  models: ImageModel[];
  draft: Draft | null;
  bin: MediaItem[];
  addReference: MediaFile | null;
  onReferenceTaken: () => void;
  onMade: (item: MediaItem) => void;
  onBroughtIn: (item: MediaItem) => void;
}) {
  const uid = useId();
  const [form, setForm] = useState<FormState>(() => ({
    // No model is picked on a first visit, so a paid one is never chosen for anyone (M5).
    model: models.find((m) => m.id === rememberedModel(me.sub, "images"))?.id ?? "",
    prompt: "",
    shape: SHAPES[0]!.size,
    custom: "",
    n: 1,
    quality: "",
    background: "",
    outputFormat: "",
    references: [],
  }));
  const [problem, setProblem] = useState<{ message: string; field: string | null } | null>(null);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  const model = models.find((m) => m.id === form.model);

  // "Edit and send" fills the form with what a result asked for.
  useEffect(() => {
    if (!draft) return;
    const { request: asked } = draft;
    const files = bin.flatMap((i) => i.files);
    const shaped = SHAPES.some((s) => s.size === (asked.size ?? null));
    setForm((current) => ({
      ...current,
      model: models.some((m) => m.id === asked.model) ? asked.model! : current.model,
      prompt: asked.prompt ?? "",
      shape: shaped ? (asked.size ?? null) : "custom",
      custom: shaped ? "" : (asked.size ?? ""),
      n: asked.n ?? 1,
      quality: asked.quality ?? "",
      background: asked.background ?? "",
      outputFormat: asked.outputFormat ?? "",
      references: (asked.references ?? [])
        .map((id) => files.find((f) => f.id === id))
        .filter((f): f is MediaFile => f !== undefined),
    }));
    setProblem(draft.message ? { message: draft.message, field: draft.field } : null);
    // Only a new draft fills the form; the bin changing does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  useEffect(() => {
    if (!addReference) return;
    setForm((current) =>
      current.references.some((r) => r.id === addReference.id)
        ? current
        : { ...current, references: [...current.references, addReference] },
    );
    onReferenceTaken();
  }, [addReference, onReferenceTaken]);

  const set = (values: Partial<FormState>) => setForm((current) => ({ ...current, ...values }));
  const size = form.shape === "custom" ? form.custom.trim() : form.shape;
  const [fewest, most] = model ? referenceRange(model) : [0, 0];
  const why = !model
    ? "Choose a model first."
    : !form.prompt.trim()
      ? "Describe the image first."
      : form.shape === "custom" && !SIZE_PATTERN.test(size ?? "")
        ? "Type a size as width x height, such as 800x600."
        : form.references.length < fewest
          ? `${model.id} only edits: add at least ${fewest} reference image${fewest === 1 ? "" : "s"}.`
          : form.references.length > most
            ? most === 0
              ? `${model.id} makes images from words only. Remove the reference images.`
              : `${model.id} takes at most ${most} reference images.`
            : null;

  async function send() {
    if (!model || why || sending) return;
    setSending(true);
    setProblem(null);
    try {
      const made = await post<MediaItem>(
        "/api/media/images",
        imageBody({
          model: model.id,
          prompt: form.prompt,
          size: size || null,
          n: form.n,
          quality: form.quality,
          background: form.background,
          outputFormat: form.outputFormat,
          references: form.references.map((r) => r.id),
        }),
      );
      onMade(made);
    } catch (error) {
      setProblem({ message: problemOf(error), field: null });
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
      onBroughtIn(item);
      if (item.files[0]) set({ references: [...form.references, item.files[0]] });
    } catch (error) {
      setProblem({ message: problemOf(error), field: "references" });
    } finally {
      setUploading(false);
      if (upload.current) upload.current.value = "";
    }
  }

  // What a refusal named is marked: the danger edge, aria-invalid, and the alert it is described by.
  const alertId = `${uid}-problem`;
  const named = (field: string) => problem?.field === field;
  const edge = (field: string) => (named(field) ? " border-error-line border-2" : "");
  /** A group has no aria-invalid: its edge and the alert it is described by say it. */
  const groupEdge = (field: string) =>
    named(field) ? "border-error-line border-2 group-invalid" : "border-line";
  const invalid = (field: string, ...also: string[]) => ({
    "aria-invalid": named(field) || undefined,
    "aria-describedby": [...also, ...(named(field) ? [alertId] : [])].join(" ") || undefined,
  });
  const reasonId = `${uid}-why`;
  const title = problem?.field ? IMAGE_FIELDS[problem.field] : undefined;

  return (
    <form
      aria-label="Make an image"
      className="card gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <ModelPicker
        id="image-model"
        models={models}
        value={form.model}
        onChange={(id) => {
          set({ model: id, n: 1 });
          if (id) rememberModel(me.sub, "images", id);
        }}
      />
      <div className="field">
        <label htmlFor="image-prompt" className="field-label">
          Describe the image
        </label>
        <textarea
          id="image-prompt"
          data-testid="image-prompt"
          rows={3}
          value={form.prompt}
          onChange={(e) => set({ prompt: e.target.value })}
          className={`control${edge("prompt")}`}
          {...invalid("prompt")}
        />
      </div>
      {model && (
        <fieldset
          aria-describedby={named("size") ? alertId : undefined}
          className={`flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 rounded-plexus border p-3 ${groupEdge("size")}`}
        >
          <legend className="field-label px-1">Shape</legend>
          {SHAPES.map((shape) => (
            <label key={shape.label} className="check">
              <input
                type="radio"
                name="shape"
                checked={form.shape === shape.size}
                onChange={() => set({ shape: shape.size })}
              />
              <span>{shape.label}</span>
            </label>
          ))}
          <label className="check">
            <input
              type="radio"
              name="shape"
              checked={form.shape === "custom"}
              onChange={() => set({ shape: "custom" })}
            />
            <span>Custom</span>
          </label>
          {form.shape === "custom" && (
            <input
              aria-label="Custom size, width x height"
              placeholder="800x600"
              value={form.custom}
              onChange={(e) => set({ custom: e.target.value })}
              className="control control--sm w-28"
            />
          )}
        </fieldset>
      )}
      {model && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Count
            model={model}
            value={form.n}
            onChange={(n) => set({ n })}
            edge={edge("n")}
            mark={invalid("n")}
          />
          <Choice
            label="Quality"
            values={model.qualities}
            value={form.quality}
            provider={model.provider}
            onChange={(quality) => set({ quality })}
            edge={edge("quality")}
            mark={invalid("quality")}
          />
          <Choice
            label="Background"
            values={model.backgrounds}
            value={form.background}
            provider={model.provider}
            onChange={(background) => set({ background })}
            edge={edge("background")}
            mark={invalid("background")}
          />
          <Choice
            label="Format"
            values={model.outputFormats}
            value={form.outputFormat}
            provider={model.provider}
            onChange={(outputFormat) => set({ outputFormat })}
            edge={edge("outputFormat")}
            mark={invalid("outputFormat")}
          />
        </div>
      )}
      {model && most > 0 && (
        <div
          role="group"
          aria-labelledby={`${uid}-references`}
          aria-describedby={named("references") ? alertId : undefined}
          className={`flex flex-col gap-2 rounded-plexus border p-3 ${groupEdge("references")}`}
        >
          <p id={`${uid}-references`} className="field-label">
            Reference images
          </p>
          <p className="help">
            {fewest > 0
              ? `${model.id} edits images: add ${fewest} to ${most}.`
              : `Optional: up to ${most}, from your bin or brought in. The model edits from them.`}
          </p>
          {form.references.length > 0 && (
            <ul className="flex flex-wrap gap-2" data-testid="references">
              {form.references.map((ref) => (
                <li
                  key={ref.id}
                  className="inline-flex max-w-full items-center rounded-plexus border border-line bg-soft pl-2 text-sm"
                >
                  <span className="min-w-0 wrap-anywhere">{ref.name}</span>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Stop using ${ref.name}`}
                    onClick={() =>
                      set({ references: form.references.filter((r) => r.id !== ref.id) })
                    }
                  >
                    <X size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div>
            <input
              ref={upload}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              hidden
              data-testid="bring-in"
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
        <div id={alertId} role="alert" className="notice notice--error" data-testid="image-problem">
          <CircleAlert size={16} aria-hidden />
          <div>
            {title && <span className="notice-title">{title}</span>}
            {problem.message}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="submit"
          data-testid="make-image"
          disabled={Boolean(why) || sending}
          aria-describedby={why ? reasonId : undefined}
          className="btn btn-primary"
        >
          {sending ? "Sending…" : form.n > 1 ? `Make ${form.n} images` : "Make the image"}
        </button>
        {why && (
          <p id={reasonId} className="help">
            {why}
          </p>
        )}
      </div>
    </form>
  );
}

type Mark = { "aria-invalid": true | undefined; "aria-describedby": string | undefined };

function Count({
  model,
  value,
  onChange,
  edge,
  mark,
}: {
  model: ImageModel;
  value: number;
  onChange: (n: number) => void;
  edge: string;
  mark: Mark;
}) {
  const id = useId();
  const most = maxImages(model);
  if (most === 1) {
    return <p className="help sm:col-span-2">This model makes one image at a time.</p>;
  }
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        How many
      </label>
      <input
        id={id}
        type="number"
        min={1}
        max={most}
        value={value}
        onChange={(e) => onChange(Math.max(1, Math.min(most, Number(e.target.value) || 1)))}
        className={`control w-24 ${edge}`}
        {...mark}
      />
    </div>
  );
}

function Choice({
  label,
  values,
  value,
  provider,
  onChange,
  edge,
  mark,
}: {
  label: string;
  values: string[] | null;
  value: string;
  provider: string | null;
  onChange: (value: string) => void;
  edge: string;
  mark: Mark;
}) {
  const id = useId();
  const offered = offer(values);
  if (offered.kind === "none") return null;
  const help = `${id}-help`;
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {offered.kind === "list" ? (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`control${edge}`}
          {...mark}
        >
          <option value="">Model&apos;s choice</option>
          {offered.values.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      ) : (
        <>
          <input
            id={id}
            value={value}
            placeholder="Model's choice"
            onChange={(e) => onChange(e.target.value)}
            className={`control${edge}`}
            {...mark}
            aria-describedby={[help, mark["aria-describedby"]].filter(Boolean).join(" ")}
          />
          <p id={help} className="help">
            Checked by {provider ?? "the backend"} when sent
          </p>
        </>
      )}
    </div>
  );
}

function MediaCard({
  item,
  focused,
  readOnly,
  onDelete,
  onStop,
  onAgain,
  onEdit,
  onToChat,
  onReference,
  onTextToChat,
}: {
  item: MediaItem;
  focused: boolean;
  readOnly: boolean;
  onDelete: () => void;
  onStop: () => void;
  onAgain: () => void;
  onEdit: () => void;
  onToChat: (file: MediaFile) => void;
  onReference: (file: MediaFile) => void;
  onTextToChat: (text: string) => void;
}) {
  const uid = useId();
  const [confirm, setConfirm] = useState(false);
  const video = item.door === "video";
  const working = video && item.status === "running";
  const now = useClock(working);
  const status = statusWords(item, now);
  const served = servedWords(item);
  const billed = billedWords(item);
  const upload = item.kind === "upload";
  const caption = captionOf(item);
  // Each button says which card it acts on, by the start of the caption.
  const start = caption.trim().length > 40 ? `${caption.trim().slice(0, 40)}…` : caption.trim();
  const about = start || "this result";
  const heard = heardWords(item);
  const remakes = item.door !== "transcription";
  // A video costs money: it is sent again only through the form, which asks first.
  const again = remakes && !video;
  const deleteId = `${uid}-delete`;
  const failed = item.status === "failed";
  return (
    <li
      id={`media-${item.id}`}
      data-testid="media-item"
      data-status={item.status}
      className={`card${focused ? " border-accent" : ""}`}
    >
      <p className="whitespace-pre-wrap break-words text-sm font-medium text-strong">{caption}</p>
      {item.files.length > 0 && (
        <ul className="flex flex-wrap gap-4">
          {item.files.map((file) => {
            const image = file.mediaType.startsWith("image/");
            return (
              <li key={file.id} className="flex min-w-0 max-w-full flex-col gap-1.5">
                {image ? (
                  <BinImage
                    file={file}
                    alt={upload ? "" : `Made from: ${item.request.prompt ?? ""}`}
                  />
                ) : file.mediaType.startsWith("video/") ? (
                  <BinVideo file={file} asked={item.request} />
                ) : (
                  <BinAudio file={file} />
                )}
                {image && (
                  <span className="text-xs text-muted" data-testid="size-words">
                    {sizeWords(item.request.size, file)}
                  </span>
                )}
                <span className="-ms-2 flex flex-wrap gap-1">
                  <DownloadButton file={file} about={about} />
                  {!readOnly && (image || item.door === "speech") && (
                    <button
                      type="button"
                      aria-label={`Send to a chat: ${file.name}, ${about}`}
                      className="btn btn-quiet btn-wrap"
                      onClick={() => onToChat(file)}
                    >
                      <MessageSquarePlus size={14} aria-hidden />
                      Send to a chat
                    </button>
                  )}
                  {!readOnly && image && (
                    <button
                      type="button"
                      aria-label={`Use as reference: ${file.name}, ${about}`}
                      className="btn btn-quiet btn-wrap"
                      onClick={() => onReference(file)}
                    >
                      Use as reference
                    </button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {item.text != null && item.text !== "" && (
        <div className="flex flex-col gap-2 rounded-plexus border border-line bg-soft p-3">
          <p className="whitespace-pre-wrap break-words" data-testid="transcript">
            {item.text}
          </p>
          <span className="flex flex-wrap items-center gap-2 text-sm">
            <CopyButton text={item.text} label={`transcript: ${about}`} />
            {!readOnly && (
              <button
                type="button"
                aria-label={`Send to a chat: transcript, ${about}`}
                className="btn btn-quiet btn-wrap"
                onClick={() => onTextToChat(item.text ?? "")}
              >
                <MessageSquarePlus size={14} aria-hidden />
                Send to a chat
              </button>
            )}
          </span>
        </div>
      )}
      {heard && item.status === "done" && (
        <p
          className={
            heard.short ? "flex items-start gap-1.5 text-sm text-warn" : "text-xs text-muted"
          }
          data-testid="heard-words"
        >
          {heard.short && <TriangleAlert size={14} aria-hidden className="mt-1 shrink-0" />}
          {heard.text}
        </p>
      )}
      {status && working && (
        <WorkingScene active>
          <p className="text-sm text-muted" data-testid="media-status">
            {status}
          </p>
        </WorkingScene>
      )}
      {status && !working && (
        <p
          role={failed ? "alert" : undefined}
          className={failed ? "notice notice--error" : "text-sm text-muted"}
          data-testid="media-status"
        >
          {failed && <CircleAlert size={16} aria-hidden />}
          {failed ? (
            <span>
              {status}
              {item.error?.param && (
                <span className="text-muted"> (the gateway named: {item.error.param})</span>
              )}
            </span>
          ) : (
            status
          )}
        </p>
      )}
      {billed && (
        <p className="text-sm" data-testid="billed-words">
          {billed}
        </p>
      )}
      <p className="text-xs text-muted">
        {[item.model, served, new Date(item.createdAt * 1000).toLocaleString()]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {!readOnly && (
        <div className="-mx-2 flex flex-wrap items-center gap-1">
          {item.status === "running" && (
            <button
              type="button"
              aria-label={`Stop: ${about}`}
              className="btn btn-quiet btn-wrap"
              onClick={onStop}
            >
              <Square size={14} aria-hidden />
              Stop
            </button>
          )}
          {!upload && remakes && item.status !== "running" && (
            <>
              {item.status === "done" && again && (
                <button
                  type="button"
                  aria-label={`Again: ${about}`}
                  className="btn btn-quiet btn-wrap"
                  onClick={onAgain}
                >
                  <RotateCcw size={14} aria-hidden />
                  Again
                </button>
              )}
              <button
                type="button"
                aria-label={`Edit and send: ${about}`}
                className="btn btn-quiet btn-wrap"
                onClick={onEdit}
              >
                Edit and send
              </button>
            </>
          )}
          {!confirm && (
            <button
              id={deleteId}
              type="button"
              aria-label={`Delete: ${about}`}
              className="btn btn-quiet btn-danger btn-wrap ml-auto"
              onClick={() => setConfirm(true)}
            >
              <Trash2 size={14} aria-hidden />
              Delete
            </button>
          )}
        </div>
      )}
      {!readOnly && confirm && (
        <div
          role="group"
          aria-label={`Delete this ${upload ? "image" : "result"}?`}
          className="notice notice--error flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setConfirm(false);
              refocus(deleteId);
            }
          }}
        >
          <p>Delete this {upload ? "image" : "result"}? This cannot be undone.</p>
          <div className="flex flex-wrap gap-2">
            <button
              autoFocus
              type="button"
              aria-label={`Keep: ${about}`}
              className="btn btn-wrap"
              onClick={() => {
                setConfirm(false);
                refocus(deleteId);
              }}
            >
              Keep
            </button>
            <button
              type="button"
              aria-label={`Delete for good: ${about}`}
              className="btn btn-danger btn-wrap"
              onClick={onDelete}
            >
              Delete
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

/** A bin file shown from a blob: address, since an `<img src>` cannot carry
 * the request secret (W3). */
function BinImage({ file, alt }: { file: MediaFile; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    fileUrl(file.id)
      .then((u) => {
        made = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setUrl(u);
      })
      .catch((error) => !cancelled && setFailed(problemOf(error)));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [file.id]);
  if (failed) {
    return (
      <p role="alert" className="notice notice--error">
        <CircleAlert size={16} aria-hidden />
        Could not load {file.name}: {failed}
      </p>
    );
  }
  if (!url) return <div className="h-40 w-40 rounded-plexus bg-hover" aria-hidden />;
  return (
    <img
      src={url}
      alt={alt}
      data-testid="bin-image"
      className="max-h-64 max-w-xs rounded-plexus border border-line object-contain"
    />
  );
}

/** A bin video, played from a blob: address as images are, with its own
 * length and size beside what was asked (§2.4), read once it loads. */
function BinVideo({
  file,
  asked,
}: {
  file: MediaFile;
  asked: { seconds?: number; size?: string };
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [got, setGot] = useState<{ seconds: number; width: number; height: number } | null>(null);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    fileUrl(file.id)
      .then((u) => {
        made = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setUrl(u);
      })
      .catch((error) => !cancelled && setFailed(problemOf(error)));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [file.id]);
  if (failed) {
    return (
      <p role="alert" className="notice notice--error">
        <CircleAlert size={16} aria-hidden />
        Could not load {file.name}: {failed}
      </p>
    );
  }
  if (!url) return <div className="h-40 w-72 max-w-full rounded-plexus bg-hover" aria-hidden />;
  const said = videoWords(asked, got);
  return (
    <>
      <video
        controls
        src={url}
        data-testid="bin-video"
        className="max-h-80 w-full max-w-md rounded-plexus border border-line"
        onLoadedMetadata={(e) => {
          const v = e.currentTarget;
          if (Number.isFinite(v.duration) && v.videoWidth) {
            setGot({ seconds: v.duration, width: v.videoWidth, height: v.videoHeight });
          }
        }}
      />
      {said && (
        <span className="text-xs text-muted" data-testid="video-words">
          {said}
        </span>
      )}
    </>
  );
}

/** A bin clip, played from a blob: address for the same reason. */
function BinAudio({ file }: { file: MediaFile }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null;
    let cancelled = false;
    fileUrl(file.id)
      .then((u) => {
        made = u;
        if (cancelled) URL.revokeObjectURL(u);
        else setUrl(u);
      })
      .catch((error) => !cancelled && setFailed(problemOf(error)));
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [file.id]);
  if (failed) {
    return (
      <p role="alert" className="notice notice--error">
        <CircleAlert size={16} aria-hidden />
        Could not load {file.name}: {failed}
      </p>
    );
  }
  if (!url) return <div className="h-10 w-72 max-w-full rounded-plexus bg-hover" aria-hidden />;
  return <audio controls src={url} data-testid="bin-audio" className="w-72 max-w-full" />;
}

function DownloadButton({ file, about }: { file: MediaFile; about: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        aria-label={`Download: ${file.name}, ${about}`}
        className="btn btn-quiet btn-wrap"
        onClick={async () => {
          setFailed(null);
          try {
            const url = await fileUrl(file.id);
            const link = document.createElement("a");
            link.href = url;
            link.download = file.name;
            link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
          } catch (error) {
            setFailed(problemOf(error));
          }
        }}
      >
        <Download size={14} aria-hidden />
        Download
      </button>
      {failed && (
        <p role="alert" className="notice notice--error ms-2 w-[calc(100%-0.5rem)]">
          <CircleAlert size={16} aria-hidden />
          Could not download {file.name}: {failed}
        </p>
      )}
    </>
  );
}
