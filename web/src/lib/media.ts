/**
 * The media area (workbench-media-screens.md): what a screen offers from a
 * model's listing, and how a result is said. Pure, so it is tested alone.
 */

export type Door = "images" | "speech" | "transcription";
export const DOORS: Door[] = ["images", "speech", "transcription"];
/** Each screen's tab, and what a model on it does, for the empty state. */
export const DOOR_WORDS: Record<Door, { tab: string; does: string }> = {
  images: { tab: "Images", does: "makes images" },
  speech: { tab: "Speech", does: "speaks" },
  transcription: { tab: "Transcription", does: "turns speech into text" },
};

export type Locality = "local" | "external" | "unknown";

/** What every screen's picker says of a model: who serves it, and where. */
export interface ServedModel {
  id: string;
  account: string | null;
  provider: string | null;
  locality: Locality;
  ready: boolean;
  onDemand: boolean;
}

/** An image model as `/api/media/doors` gives it (M5). A null setting is the
 * backend's to check: the form offers a free-text box, never an invented list. */
export interface ImageModel extends ServedModel {
  maxImages: number | null;
  qualities: string[] | null;
  backgrounds: string[] | null;
  outputFormats: string[] | null;
  minReferences: number;
  maxReferences: number | null;
  edits: boolean;
}

/** A speech model (slice 2). Voices null are the provider's to check. */
export interface SpeechModel extends ServedModel {
  voices: string[] | null;
  formats: string[];
}

/** A model the Transcription screen sends audio to. */
export interface TranscriptionModel extends ServedModel {
  translates: boolean;
}

export interface Doors {
  doors: {
    images: { models: ImageModel[] };
    speech: { models: SpeechModel[] };
    transcription: { models: TranscriptionModel[] };
  };
}

export interface MediaFile {
  id: string;
  name: string;
  mediaType: string;
  size: number;
  width: number | null;
  height: number | null;
}

export type MediaStatus = "running" | "done" | "failed" | "stopped" | "interrupted";

export interface ImageRequest {
  model?: string;
  prompt?: string;
  size?: string;
  n?: number;
  quality?: string;
  background?: string;
  outputFormat?: string;
  references?: string[];
  /** A file brought in, rather than made; or the recording transcribed. */
  name?: string;
  /** Speech: what to say, in which voice and format. */
  input?: string;
  voice?: string;
  format?: string;
  /** Transcription: the language spoken, whether to translate, the clip's length. */
  language?: string;
  translate?: boolean;
  clipSeconds?: number;
}

export interface MediaItem {
  id: string;
  door: Door;
  kind: "made" | "upload";
  model: string | null;
  request: ImageRequest;
  status: MediaStatus;
  createdAt: number;
  finishedAt: number | null;
  served: {
    driver?: string;
    backend?: string;
    latency_ms?: number;
    attempts?: number;
    requestId?: string | null;
  } | null;
  units: {
    images?: number;
    sizes?: (string | null)[];
    characters?: number;
    heardSeconds?: number | null;
    tokens?: number | null;
    clipSeconds?: number | null;
  } | null;
  text: string | null;
  error: { message: string; param: string | null; status: number | null } | null;
  files: MediaFile[];
  readOnly?: boolean;
}

/** A result's request, back in its screen's form: "Edit and send", with the
 * field a refusal named, so it can be marked (§2.5). */
export interface MediaDraft {
  request: ImageRequest;
  field: string | null;
  message: string | null;
}

export type MediaEvent =
  | { type: "media"; item: MediaItem }
  | { type: "reload" }
  | { type: "signed-out"; reason: string; message: string };

/** Where the address points: `/media/<door>` or `/media/<door>/<id>`. */
export function mediaFromPath(path: string): { door: Door; id: string | null } | null {
  const match = /^\/media\/([a-z]+)(?:\/([A-Za-z0-9_-]+))?\/?$/.exec(path);
  if (!match || !DOORS.includes(match[1] as Door)) return null;
  return { door: match[1] as Door, id: match[2] ?? null };
}

export function mediaPath(door: Door, id?: string | null): string {
  return id ? `/media/${door}/${id}` : `/media/${door}`;
}

/** The shapes offered, as OpenAI's gpt-image sizes; OpenRouter maps them to its
 * aspect ratios. `null` asks for no size: the model's own choice. */
export const SHAPES: { label: string; size: string | null }[] = [
  { label: "Square", size: "1024x1024" },
  { label: "Wide", size: "1536x1024" },
  { label: "Tall", size: "1024x1536" },
  { label: "Model's choice", size: null },
];

export const SIZE_PATTERN = /^\d{1,5}x\d{1,5}$/;

/** How a setting is offered: a list of the listed values, a free box when the
 * backend checks it, or not at all when the model takes none. */
export type Offer = { kind: "list"; values: string[] } | { kind: "free" } | { kind: "none" };

export function offer(values: string[] | null): Offer {
  if (values === null) return { kind: "free" };
  const listed = values.filter((v) => v !== "auto");
  return listed.length ? { kind: "list", values: listed } : { kind: "none" };
}

/** The most images one request may ask for: the listing's, or ten (the
 * OpenAI API's own limit) when the backend checks it. */
export function maxImages(model: ImageModel): number {
  return Math.max(1, Math.min(10, model.maxImages ?? 10));
}

/** How many reference images a model takes: [fewest, most]. */
export function referenceRange(model: ImageModel): [number, number] {
  const most = model.maxReferences ?? (model.edits ? 16 : 0);
  return [model.minReferences, most];
}

/** Where a request runs, said before sending (M6). External names the account
 * rather than claiming a charge: not every external account bills. */
export function whereItRuns(model: ServedModel): string {
  if (model.locality === "local") return "Runs on your own machines.";
  if (model.locality === "external") {
    const who = model.provider ?? model.account ?? "another service";
    const account = model.account && model.provider ? ` (${model.account})` : "";
    return `Runs on ${who}${account}, outside your machines. Any charge goes to that account.`;
  }
  return "Eugene cannot tell whether this model runs on your machines or on a paid account.";
}

/** Models grouped by the account that serves them, for the picker. */
export function groupModels<M extends ServedModel>(
  models: M[],
  query = "",
): { label: string; models: M[] }[] {
  const wanted = query.trim().toLowerCase();
  const groups = new Map<string, M[]>();
  for (const model of models) {
    if (wanted && !model.id.toLowerCase().includes(wanted)) continue;
    const label =
      model.locality === "local"
        ? "On your machines"
        : (model.provider ?? model.account ?? "Other");
    groups.set(label, [...(groups.get(label) ?? []), model]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) =>
      a === "On your machines" ? -1 : b === "On your machines" ? 1 : a.localeCompare(b),
    )
    .map(([label, list]) => ({ label, models: list.sort((x, y) => x.id.localeCompare(y.id)) }));
}

const PICKED = "workbench-media-model";

export function rememberedModel(person: string, door: Door): string | null {
  try {
    return window.localStorage.getItem(`${PICKED}:${door}:${person}`);
  } catch {
    return null;
  }
}

export function rememberModel(person: string, door: Door, model: string): void {
  try {
    window.localStorage.setItem(`${PICKED}:${door}:${person}`, model);
  } catch {
    // Remembering the last model is a convenience.
  }
}

const times = (size: string) => size.replace("x", " × ");

/** What was asked and what came back, side by side when they differ (§2.4). */
export function sizeWords(asked: string | undefined, file: MediaFile): string | null {
  const got = file.width && file.height ? `${file.width}x${file.height}` : null;
  if (asked && got && asked !== got) return `Asked ${times(asked)}, got ${times(got)}`;
  if (got) return times(got);
  return asked ? `Asked ${times(asked)}` : null;
}

/** A result's state in words, or null when it is simply done. */
export function statusWords(item: MediaItem): string | null {
  switch (item.status) {
    case "running":
      return item.door === "transcription"
        ? "Listening to it. You can close this tab; it keeps going."
        : "Making it. You can close this tab; it keeps going.";
    case "stopped":
      return "Stopped. The provider may still bill it.";
    case "interrupted":
      return "Workbench restarted while this was being made. The provider may have billed it.";
    case "failed":
      return item.error?.message ?? "It failed, and the gateway did not say why.";
    default:
      return null;
  }
}

/** What served it: the account, the latency, the attempts. */
export function servedWords(item: MediaItem): string | null {
  const s = item.served;
  if (!s) return null;
  const parts = [s.driver, s.latency_ms != null ? `${(s.latency_ms / 1000).toFixed(1)} s` : null];
  if (s.attempts && s.attempts > 1) parts.push(`${s.attempts} attempts`);
  const said = parts.filter(Boolean).join(" · ");
  return said ? `Served by ${said}` : null;
}

export function bytesWords(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** The request body for `POST /api/media/images`, from the form. */
export function imageBody(form: {
  model: string;
  prompt: string;
  size: string | null;
  n: number;
  quality: string;
  background: string;
  outputFormat: string;
  references: string[];
}): ImageRequest {
  const body: ImageRequest = { model: form.model, prompt: form.prompt.trim() };
  if (form.size) body.size = form.size;
  if (form.n > 1) body.n = form.n;
  if (form.quality.trim()) body.quality = form.quality.trim();
  if (form.background.trim()) body.background = form.background.trim();
  if (form.outputFormat.trim()) body.outputFormat = form.outputFormat.trim();
  if (form.references.length) body.references = form.references;
  return body;
}

/** Which form field a gateway refusal names, so it can be marked (§2.5). */
export function fieldOf(param: string | null | undefined): string | null {
  if (!param) return null;
  if (param === "output_format") return "outputFormat";
  if (param.startsWith("image")) return "references";
  return param;
}

/** What the model heard against the clip's own length (§4, M10). Measured
 * 2026-10-08: OpenRouter's whisper heard 1.5 s of a 3.1 s MP3, every time,
 * so a shortfall is said plainly. OpenAI rounds the seconds it counts up,
 * so only a shortfall is a claim; otherwise the clip's length is all. */
export function heardWords(item: MediaItem): { text: string; short: boolean } | null {
  const clip = item.units?.clipSeconds ?? item.request.clipSeconds ?? null;
  const heard = item.units?.heardSeconds ?? null;
  if (clip == null) return null;
  if (heard != null && heard < clip - 0.25) {
    return {
      text: `The model heard ${heard.toFixed(1)} s of this ${clip.toFixed(1)} s clip. Words after that may be missing.`,
      short: true,
    };
  }
  return { text: `Clip ${clip.toFixed(1)} s.`, short: false };
}

/** Whether this page may record: a microphone needs HTTPS or this computer
 * (localhost), and a browser that records. */
export function canRecord(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof MediaRecorder !== "undefined"
  );
}

export const RECORD_NEEDS_HTTPS =
  "Recording needs this page opened over HTTPS, or on the computer Workbench runs on. You can still choose a recording to upload.";

/** A clip's own length, decoded by the browser; null when it cannot say. */
export async function clipSeconds(file: Blob): Promise<number | null> {
  const Context =
    typeof window === "undefined"
      ? undefined
      : (window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
  if (!Context) return null;
  const context = new Context();
  try {
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    return Number.isFinite(decoded.duration) ? decoded.duration : null;
  } catch {
    return null;
  } finally {
    void context.close();
  }
}

/** The formats offered for speech, mp3 first: what the browser plays. */
export function speechFormats(model: SpeechModel): string[] {
  const listed = model.formats.length ? model.formats : ["mp3"];
  return listed.includes("mp3") ? ["mp3", ...listed.filter((f) => f !== "mp3")] : listed;
}

/** What a result says in the bin: the words asked for, spoken or heard. */
export function captionOf(item: MediaItem): string {
  if (item.kind === "upload") return `Brought in: ${item.request.name ?? "an image"}`;
  if (item.door === "speech") return item.request.input ?? "";
  if (item.door === "transcription") {
    const how = item.request.translate ? "Translated into English" : "Transcribed";
    return `${how}: ${item.request.name ?? "a recording"}`;
  }
  return item.request.prompt ?? "";
}
