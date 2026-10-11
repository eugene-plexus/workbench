import { CircleAlert, Mic, Square } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { api, post } from "../lib/api";
import {
  type MediaDraft,
  type MediaItem,
  RECORD_NEEDS_HTTPS,
  type ServedModel,
  type SpeechModel,
  type TranscriptionModel,
  canRecord,
  clipSeconds,
  bytesWords,
  groupModels,
  rememberModel,
  rememberedModel,
  speechFormats,
  voiceLabel,
  whereItRuns,
} from "../lib/media";
import type { Me } from "../lib/types";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** The gateway's own limit on audio to transcribe. */
const TRANSCRIBE_LIMIT = 25 * 1024 * 1024;

/** The picker every screen shares: models grouped by where they run, a search
 * box when there are many, and no model chosen on a first visit (M5). */
export function ModelPicker<M extends ServedModel>({
  id,
  models,
  value,
  onChange,
}: {
  id: string;
  models: M[];
  value: string;
  onChange: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const groups = useMemo(() => groupModels(models, query), [models, query]);
  const chosen = models.find((m) => m.id === value);
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        Model
      </label>
      {models.length > 8 && (
        <input
          type="search"
          aria-label="Find a model"
          placeholder="Find a model"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="control control--sm"
        />
      )}
      <select
        id={id}
        data-testid={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby={chosen ? `${id}-where` : undefined}
        className="control"
      >
        <option value="">Choose a model</option>
        {groups.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.ready ? "" : m.onDemand ? " (starts when asked)" : " (not ready)"}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {chosen && (
        <p id={`${id}-where`} className="help" data-testid="where-it-runs">
          {whereItRuns(chosen)}
        </p>
      )}
    </div>
  );
}

/** The words a refusal's field goes by on the Speech screen, so the alert can begin with them. */
const SPEECH_FIELDS: Record<string, string> = {
  input: "What to say",
  voice: "Voice",
  response_format: "Format",
};

/** The Speech screen's form (workbench-media-screens.md §4): text, a voice
 * from the model's own list (free text where it lists none), a format. */
export function SpeechForm({
  me,
  models,
  draft,
  onMade,
}: {
  me: Me;
  models: SpeechModel[];
  draft: MediaDraft | null;
  onMade: (item: MediaItem) => void;
}) {
  const uid = useId();
  const [model, setModel] = useState(
    () => models.find((m) => m.id === rememberedModel(me.sub, "speech"))?.id ?? "",
  );
  const [input, setInput] = useState("");
  const [voice, setVoice] = useState("");
  const [format, setFormat] = useState("mp3");
  const [voiceQuery, setVoiceQuery] = useState("");
  const [problem, setProblem] = useState<{ message: string; field: string | null } | null>(null);
  const [sending, setSending] = useState(false);
  const chosen = models.find((m) => m.id === model);
  const formats = chosen ? speechFormats(chosen) : ["mp3"];
  const voices = chosen?.voices ?? null;
  const wanted = voiceQuery.trim().toLowerCase();
  // A voice is found by its name or its id.
  const shownVoices = (voices ?? []).filter(
    (v) =>
      !wanted ||
      v.toLowerCase().includes(wanted) ||
      (chosen?.voiceNames?.[v] ?? "").toLowerCase().includes(wanted),
  );

  // "Edit and send" fills the form with what a result asked for.
  useEffect(() => {
    if (!draft) return;
    const asked = draft.request;
    if (asked.model && models.some((m) => m.id === asked.model)) setModel(asked.model);
    setInput(asked.input ?? "");
    setVoice(asked.voice ?? "");
    setFormat(asked.format ?? "mp3");
    setProblem(draft.message ? { message: draft.message, field: draft.field } : null);
    // Only a new draft fills the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const why = !chosen
    ? "Choose a model first."
    : !input.trim()
      ? "Write what to say first."
      : !voice.trim()
        ? "Choose a voice first."
        : voices && !voices.includes(voice)
          ? `${chosen.id} has no voice ${voice}. Choose one from the list.`
          : null;

  async function send() {
    if (!chosen || why || sending) return;
    setSending(true);
    setProblem(null);
    try {
      onMade(
        await post<MediaItem>("/api/media/speech", {
          model: chosen.id,
          input: input.trim(),
          voice: voice.trim(),
          format: formats.includes(format) ? format : formats[0],
        }),
      );
    } catch (error) {
      setProblem({ message: problemOf(error), field: null });
    } finally {
      setSending(false);
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
      aria-label="Speak some text"
      className="card gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <ModelPicker
        id="speech-model"
        models={models}
        value={model}
        onChange={(id) => {
          setModel(id);
          setVoice("");
          if (id) rememberModel(me.sub, "speech", id);
        }}
      />
      <div className="field">
        <label htmlFor="speech-input" className="field-label">
          What to say
        </label>
        <textarea
          id="speech-input"
          data-testid="speech-input"
          rows={4}
          maxLength={4096}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className={`control${edge("input")}`}
          {...invalid("input", `${uid}-count`)}
        />
        <p id={`${uid}-count`} className="help tabular-nums">
          {input.length} of 4,096 characters
        </p>
      </div>
      {chosen && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="field">
            <label htmlFor="speech-voice" className="field-label">
              Voice
            </label>
            {voices ? (
              <>
                {voices.length > 12 && (
                  <input
                    type="search"
                    aria-label="Find a voice"
                    placeholder="Find a voice"
                    value={voiceQuery}
                    onChange={(e) => setVoiceQuery(e.target.value)}
                    className="control control--sm"
                  />
                )}
                <select
                  id="speech-voice"
                  data-testid="speech-voice"
                  value={voice}
                  onChange={(e) => setVoice(e.target.value)}
                  className={`control${edge("voice")}`}
                  {...invalid("voice")}
                >
                  <option value="">Choose a voice</option>
                  {shownVoices.map((v) => (
                    <option key={v} value={v}>
                      {voiceLabel(chosen, v)}
                    </option>
                  ))}
                </select>
              </>
            ) : (
              <>
                <input
                  id="speech-voice"
                  data-testid="speech-voice"
                  value={voice}
                  placeholder="A voice name"
                  onChange={(e) => setVoice(e.target.value)}
                  className={`control${edge("voice")}`}
                  {...invalid("voice", `${uid}-voice-help`)}
                />
                <p id={`${uid}-voice-help`} className="help">
                  Checked by {chosen.provider ?? "the backend"} when sent
                </p>
              </>
            )}
          </div>
          <div className="field">
            <label htmlFor="speech-format" className="field-label">
              Format
            </label>
            <select
              id="speech-format"
              data-testid="speech-format"
              value={formats.includes(format) ? format : formats[0]}
              onChange={(e) => setFormat(e.target.value)}
              className={`control${edge("response_format")}`}
              {...invalid("response_format")}
            >
              {formats.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      {problem && (
        <div
          id={alertId}
          role="alert"
          className="notice notice--error"
          data-testid="speech-problem"
        >
          <CircleAlert size={16} aria-hidden />
          <div>
            {problem.field && SPEECH_FIELDS[problem.field] && (
              <span className="notice-title">{SPEECH_FIELDS[problem.field]}</span>
            )}
            {problem.message}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="submit"
          data-testid="make-speech"
          disabled={Boolean(why) || sending}
          aria-describedby={why ? reasonId : undefined}
          className="btn btn-primary"
        >
          {sending ? "Sending…" : "Speak it"}
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

/** The Transcription screen's form (§4): a recording chosen or made here,
 * its length measured by the browser, a language, and translation where the
 * model translates. */
export function TranscriptionForm({
  me,
  models,
  onMade,
}: {
  me: Me;
  models: TranscriptionModel[];
  onMade: (item: MediaItem) => void;
}) {
  const uid = useId();
  const [model, setModel] = useState(
    () => models.find((m) => m.id === rememberedModel(me.sub, "transcription"))?.id ?? "",
  );
  const [clip, setClip] = useState<{ file: File; seconds: number | null } | null>(null);
  const [language, setLanguage] = useState("");
  const [translate, setTranslate] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState<{ stop: () => void; started: number } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const upload = useRef<HTMLInputElement>(null);
  const chooseButton = useRef<HTMLButtonElement>(null);
  const chosen = models.find((m) => m.id === model);
  const recordable = canRecord();
  const translating = translate && Boolean(chosen?.translates);

  useEffect(() => {
    if (!recording) return;
    const tick = window.setInterval(
      () => setElapsed((performance.now() - recording.started) / 1000),
      200,
    );
    return () => window.clearInterval(tick);
  }, [recording]);

  async function choose(file: File, seconds?: number) {
    setProblem(null);
    setClip({ file, seconds: seconds ?? (await clipSeconds(file)) });
  }

  async function record() {
    setProblem(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      const started = performance.now();
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = recorder.mimeType || "audio/webm";
        const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        setRecording(null);
        void choose(
          new File([new Blob(chunks, { type })], `recording.${extension}`, { type }),
          (performance.now() - started) / 1000,
        );
      };
      recorder.start();
      setElapsed(0);
      setRecording({ stop: () => recorder.stop(), started });
    } catch (error) {
      setProblem(
        `The browser did not let Workbench use a microphone (${problemOf(error)}). Check the page's microphone permission, or choose a recording to upload.`,
      );
    }
  }

  const why = !chosen
    ? "Choose a model first."
    : recording
      ? "Stop the recording first."
      : !clip
        ? "Record or choose a recording first."
        : clip.file.size > TRANSCRIBE_LIMIT
          ? `That recording is ${bytesWords(clip.file.size)}, and Eugene carries at most ${bytesWords(TRANSCRIBE_LIMIT)} to be transcribed.`
          : null;
  const reasonId = `${uid}-why`;

  async function send() {
    if (!chosen || !clip || why || sending) return;
    setSending(true);
    setProblem(null);
    try {
      const body = new FormData();
      body.append("model", chosen.id);
      if (language.trim() && !translating) body.append("language", language.trim());
      if (translating) body.append("translate", "true");
      if (clip.seconds != null) body.append("clipSeconds", String(clip.seconds));
      body.append("file", clip.file);
      onMade(await api<MediaItem>("/api/media/transcription", { method: "POST", body }));
      setClip(null);
      if (upload.current) upload.current.value = "";
    } catch (error) {
      setProblem(problemOf(error));
    } finally {
      setSending(false);
    }
  }

  return (
    <form
      aria-label="Turn a recording into text"
      className="card gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <ModelPicker
        id="transcription-model"
        models={models}
        value={model}
        onChange={(id) => {
          setModel(id);
          if (id) rememberModel(me.sub, "transcription", id);
        }}
      />
      <div role="group" aria-labelledby={`${uid}-recording`} className="flex flex-col gap-2">
        <p id={`${uid}-recording`} className="field-label">
          Recording
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={upload}
            type="file"
            accept="audio/*,video/webm,video/mp4"
            hidden
            data-testid="choose-recording"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void choose(file);
            }}
          />
          <button
            ref={chooseButton}
            type="button"
            className="btn"
            disabled={Boolean(recording)}
            onClick={() => upload.current?.click()}
          >
            Choose a recording
          </button>
          {recordable ? (
            recording ? (
              <button
                type="button"
                data-testid="stop-recording"
                className="btn btn-danger"
                onClick={() => recording.stop()}
              >
                <Square size={14} aria-hidden />
                Stop recording ({elapsed.toFixed(0)} s)
              </button>
            ) : (
              <button
                type="button"
                data-testid="record"
                className="btn"
                onClick={() => void record()}
              >
                <Mic size={14} aria-hidden />
                Record
              </button>
            )
          ) : (
            <p className="help" data-testid="record-needs-https">
              {RECORD_NEEDS_HTTPS}
            </p>
          )}
        </div>
        {clip && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <p
              className={`min-w-0 wrap-anywhere ${clip.file.size > TRANSCRIBE_LIMIT ? "text-error" : ""}`}
              data-testid="chosen-recording"
            >
              {clip.file.name}
              {clip.seconds != null ? `, ${clip.seconds.toFixed(1)} s` : ""}
              {`, ${bytesWords(clip.file.size)} of ${bytesWords(TRANSCRIBE_LIMIT)} allowed`}
            </p>
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                setClip(null);
                if (upload.current) upload.current.value = "";
                chooseButton.current?.focus();
              }}
            >
              Remove recording
            </button>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <div className="field min-w-48 flex-1 sm:max-w-xs">
          <label htmlFor={`${uid}-language`} className="field-label">
            Language spoken <small>(optional)</small>
          </label>
          <input
            id={`${uid}-language`}
            value={language}
            placeholder="for example en"
            maxLength={16}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={translating}
            className="control"
          />
        </div>
        {chosen?.translates && (
          <label className="check">
            <input
              type="checkbox"
              data-testid="translate"
              checked={translate}
              onChange={(e) => setTranslate(e.target.checked)}
            />
            <span>Translate into English</span>
          </label>
        )}
      </div>
      {problem && (
        <p role="alert" className="notice notice--error" data-testid="transcription-problem">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="submit"
          data-testid="make-transcript"
          disabled={Boolean(why) || sending}
          aria-describedby={why ? reasonId : undefined}
          className="btn btn-primary"
        >
          {sending ? "Sending…" : translating ? "Translate it" : "Turn it into text"}
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
