import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { SpeechModel, TranscriptionModel } from "../lib/media";
import type { Me } from "../lib/types";
import { SpeechForm, TranscriptionForm } from "./AudioForms";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn() }));

const me: Me = { sub: "p-ada", name: "Ada", username: "ada", owner: false, ownerReadsChats: false };
const served = {
  account: "openrouter",
  provider: "OpenRouter",
  locality: "external" as const,
  ready: true,
  onDemand: false,
};
const kokoro: SpeechModel = {
  ...served,
  id: "openrouter/kokoro",
  voices: ["af_heart", "af_bella"],
  formats: ["mp3", "wav"],
};
const local: SpeechModel = {
  ...served,
  id: "local-voice",
  provider: null,
  account: "voice",
  locality: "local",
  voices: null,
  formats: ["wav", "mp3", "opus"],
};
const turbo: TranscriptionModel = { ...served, id: "openrouter/whisper-turbo", translates: false };
const whisper: TranscriptionModel = {
  ...served,
  id: "oai/whisper-1",
  provider: "OpenAI",
  translates: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

it("offers the model's own voices, and a free box where it lists none", () => {
  render(<SpeechForm me={me} models={[kokoro, local]} draft={null} onMade={() => undefined} />);
  const picker = screen.getByTestId("speech-model");
  expect(picker).toHaveValue("");
  fireEvent.change(picker, { target: { value: "openrouter/kokoro" } });
  const voice = screen.getByTestId("speech-voice");
  expect(voice.tagName).toBe("SELECT");
  expect([...(voice as HTMLSelectElement).options].map((o) => o.value)).toEqual([
    "",
    "af_heart",
    "af_bella",
  ]);
  fireEvent.change(picker, { target: { value: "local-voice" } });
  expect(screen.getByTestId("speech-voice").tagName).toBe("INPUT");
  expect(screen.getByText(/Checked by the backend when sent/)).toBeInTheDocument();
  expect(screen.getByTestId("where-it-runs")).toHaveTextContent("Runs on your own machines.");
  // mp3 first: the format a browser plays everywhere.
  expect(
    [...(screen.getByTestId("speech-format") as HTMLSelectElement).options].map((o) => o.value),
  ).toEqual(["mp3", "wav", "opus"]);
});

it("sends text, voice and format, and waits for each first", async () => {
  vi.mocked(post).mockResolvedValue({ id: "m1" });
  const made = vi.fn();
  render(<SpeechForm me={me} models={[kokoro]} draft={null} onMade={made} />);
  fireEvent.change(screen.getByTestId("speech-model"), { target: { value: "openrouter/kokoro" } });
  expect(screen.getByText("Write what to say first.")).toBeInTheDocument();
  fireEvent.change(screen.getByTestId("speech-input"), {
    target: { value: "  The bench is ready. " },
  });
  expect(screen.getByText("Choose a voice first.")).toBeInTheDocument();
  expect(screen.getByTestId("make-speech")).toBeDisabled();
  fireEvent.change(screen.getByTestId("speech-voice"), { target: { value: "af_bella" } });
  fireEvent.click(screen.getByTestId("make-speech"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/media/speech", {
      model: "openrouter/kokoro",
      input: "The bench is ready.",
      voice: "af_bella",
      format: "mp3",
    }),
  );
  expect(made).toHaveBeenCalledWith({ id: "m1" });
});

it("marks the voice a refusal named when the request comes back to be edited", () => {
  const draft = {
    request: { model: "local-voice", input: "Hi", voice: "nope", format: "mp3" },
    field: "voice",
    message: "voice: 'local-voice' has no voice 'nope'.",
  };
  render(<SpeechForm me={me} models={[kokoro, local]} draft={draft} onMade={() => undefined} />);
  expect(screen.getByTestId("speech-problem")).toHaveTextContent("has no voice 'nope'");
  expect(screen.getByTestId("speech-voice")).toHaveValue("nope");
  expect(screen.getByTestId("speech-voice")).toHaveClass("border-error-line");
});

it("says recording needs HTTPS where the page cannot record, and still takes an upload", async () => {
  vi.mocked(api).mockResolvedValue({ id: "m2" });
  const made = vi.fn();
  render(<TranscriptionForm me={me} models={[turbo, whisper]} onMade={made} />);
  expect(screen.getByTestId("record-needs-https")).toHaveTextContent(/HTTPS/);
  expect(screen.queryByTestId("record")).toBeNull();
  fireEvent.change(screen.getByTestId("transcription-model"), {
    target: { value: "openrouter/whisper-turbo" },
  });
  expect(screen.queryByTestId("translate")).toBeNull();
  const file = new File([new Uint8Array([1, 2, 3])], "note.webm", { type: "audio/webm" });
  fireEvent.change(screen.getByTestId("choose-recording"), { target: { files: [file] } });
  expect(await screen.findByTestId("chosen-recording")).toHaveTextContent("note.webm");
  fireEvent.click(screen.getByTestId("make-transcript"));
  await waitFor(() => expect(api).toHaveBeenCalled());
  const [path, init] = vi.mocked(api).mock.calls[0]!;
  expect(path).toBe("/api/media/transcription");
  const body = (init as RequestInit).body as FormData;
  expect(body.get("model")).toBe("openrouter/whisper-turbo");
  expect(body.get("translate")).toBeNull();
  expect((body.get("file") as File).name).toBe("note.webm");
});

it("offers translation only on a model that translates, and sends no language then", async () => {
  vi.mocked(api).mockResolvedValue({ id: "m3" });
  render(<TranscriptionForm me={me} models={[turbo, whisper]} onMade={() => undefined} />);
  fireEvent.change(screen.getByTestId("transcription-model"), {
    target: { value: "oai/whisper-1" },
  });
  fireEvent.change(screen.getByPlaceholderText("for example en"), { target: { value: "fr" } });
  fireEvent.click(screen.getByTestId("translate"));
  const file = new File([new Uint8Array([1])], "note.wav", { type: "audio/wav" });
  fireEvent.change(screen.getByTestId("choose-recording"), { target: { files: [file] } });
  await screen.findByTestId("chosen-recording");
  expect(screen.getByTestId("make-transcript")).toHaveTextContent("Translate it");
  fireEvent.click(screen.getByTestId("make-transcript"));
  await waitFor(() => expect(api).toHaveBeenCalled());
  const body = (vi.mocked(api).mock.calls[0]![1] as RequestInit).body as FormData;
  expect(body.get("translate")).toBe("true");
  expect(body.get("language")).toBeNull();
});
