import { useEffect, useState } from "react";
import { api, post } from "../lib/api";
import { failureWords } from "../lib/memory";

interface Progress {
  state: "running" | "stopped" | "done";
  since?: number | null;
  sent: number;
  total: number;
  skipped: number;
  msPerExchange?: number | null;
  samples?: number[];
  error?: string | null;
  errorAt?: number;
  pauseReason?: string | null;
}
interface Preview {
  exchanges: number;
  chats: number;
  tokens: number;
  estimateSeconds: number | null;
  backfill: Progress | null;
}
const duration = (seconds: number) =>
  `${Math.max(1, Math.ceil(seconds / 60)).toLocaleString()} min`;

export function MemoryBackfill({ allowed = true }: { allowed?: boolean }) {
  const [data, setData] = useState<Preview | null>(null);
  const [asking, setAsking] = useState(false);
  const [since, setSince] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const progress = data?.backfill;

  useEffect(() => {
    let live = true;
    api<Preview>("/api/memory/server/backfill").then(
      (value) => {
        if (live && typeof value?.exchanges === "number") setData(value);
      },
      (error: unknown) => {
        if (live) setProblem(failureWords(error));
      },
    );
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (progress?.state !== "running") return;
    const timer = window.setTimeout(() => {
      api<Preview>("/api/memory/server/backfill").then(setData, (error: unknown) =>
        setProblem(failureWords(error)),
      );
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [progress]);

  async function preview(day = since) {
    setBusy(true);
    setAsking(false);
    setProblem(null);
    try {
      setData(
        await api<Preview>(
          `/api/memory/server/backfill${day ? `?since=${encodeURIComponent(day)}` : ""}`,
        ),
      );
      setAsking(true);
    } catch (error) {
      setProblem(failureWords(error));
    } finally {
      setBusy(false);
    }
  }
  async function send() {
    setBusy(true);
    setProblem(null);
    try {
      setData(await post<Preview>("/api/memory/server/backfill", since ? { since } : {}));
      setAsking(false);
    } catch (error) {
      setProblem(failureWords(error));
    } finally {
      setBusy(false);
    }
  }
  async function stop() {
    setBusy(true);
    try {
      setData(await api<Preview>("/api/memory/server/backfill", { method: "DELETE" }));
    } catch (error) {
      setProblem(failureWords(error));
    } finally {
      setBusy(false);
    }
  }
  const left =
    progress?.msPerExchange && (progress.samples?.length ?? 0) >= 10
      ? duration(
          (Math.max(0, progress.total - progress.sent - progress.skipped) *
            progress.msPerExchange) /
            1000,
        )
      : null;
  return (
    <div className="flex flex-col gap-2 text-sm" data-testid="memory-backfill">
      {progress && (
        <p role="status">
          {progress.state === "running"
            ? progress.pauseReason ||
              `Sending past chats: ${progress.sent.toLocaleString()} of ${progress.total.toLocaleString()}${left ? ` · about ${left} left` : " · time shown after the first 10 are sent"}`
            : progress.state === "stopped"
              ? `Stopped: ${progress.sent.toLocaleString()} of ${progress.total.toLocaleString()} sent. What was sent stays on your server.`
              : `Past chats sent: ${progress.sent.toLocaleString()} of ${progress.total.toLocaleString()}. ${progress.skipped.toLocaleString()} were not sent.`}
          {progress.error &&
            ` Not reached${progress.errorAt ? ` since ${new Date(progress.errorAt * 1000).toLocaleTimeString()}` : ""}: ${progress.error}`}
        </p>
      )}
      {progress?.state === "running" ? (
        <button
          type="button"
          data-testid="backfill-stop"
          className="btn self-start"
          disabled={busy}
          onClick={() => void stop()}
        >
          Stop
        </button>
      ) : (
        allowed &&
        !asking && (
          <button
            type="button"
            data-testid="backfill-open"
            className="btn self-start"
            disabled={busy}
            onClick={() => void preview()}
          >
            {progress?.state === "stopped" ? "Resume" : "Send my past chats"}
          </button>
        )
      )}
      {asking && data && (
        <div className="flex flex-col gap-2" role="group" aria-label="Send past chats">
          {progress?.state !== "stopped" && (
            <label>
              All, or since a date{" "}
              <input
                type="date"
                data-testid="backfill-since"
                aria-label="Since"
                className="control"
                value={since}
                disabled={busy}
                onChange={(event) => {
                  setSince(event.target.value);
                  void preview(event.target.value);
                }}
              />
            </label>
          )}
          <p>
            {data.exchanges.toLocaleString()} exchanges from {data.chats.toLocaleString()} chats,
            about {data.tokens.toLocaleString()} tokens.
          </p>
          <p>
            Chats in projects, chats with memory off and turns that look like a password are not
            sent.
          </p>
          <p>
            {data.estimateSeconds === null
              ? "Time: shown after the first 10 are sent."
              : `Time: about ${duration(data.estimateSeconds)} at your server's measured speed.`}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="backfill-send"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void send()}
            >
              Send
            </button>
            <button
              type="button"
              data-testid="backfill-cancel"
              className="btn"
              onClick={() => setAsking(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
    </div>
  );
}
