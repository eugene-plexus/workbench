/**
 * Search chats (MV2): a screen for finding what was said in the person's own past chats, and
 * opening a chat at the exchange that matched. Only their own chats; nothing here is sent to a
 * model.
 */

import { CircleAlert, Search, X } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";

import { api } from "../lib/api";
import { failureWords } from "../lib/memory";
import {
  chatAt,
  type Order,
  type PastChatResult,
  type PastChatsSearch,
  type PastChatsStatus,
  statusWords,
  VIA_WORDS,
  type When,
} from "../lib/pastChats";

const WHEN: [When, string][] = [
  ["any", "Any time"],
  ["week", "Past week"],
  ["month", "Past month"],
  ["year", "Past year"],
];

export function PastChats({
  onOpen,
  onClose,
}: {
  onOpen: (chatId: string, messageId: string) => void;
  onClose: () => void;
}) {
  const uid = useId();
  const [query, setQuery] = useState("");
  const [when, setWhen] = useState<When>("any");
  const [order, setOrder] = useState<Order>("best");
  const [found, setFound] = useState<PastChatsSearch | null>(null);
  const [status, setStatus] = useState<PastChatsStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [asked, setAsked] = useState<string | null>(null);

  // How far the person's chats are in search, until they all are.
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    async function look() {
      try {
        const got = await api<PastChatsStatus>("/api/past-chats/status");
        if (stop) return;
        setStatus(got);
        if (got.backfill.running || got.owed) timer = window.setTimeout(() => void look(), 3000);
      } catch {
        // The line is a courtesy; searching still says what goes wrong.
      }
    }
    void look();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, []);

  async function search(text: string, nextWhen: When, nextOrder: Order, offset = 0) {
    if (!text.trim()) {
      setFound(null);
      setAsked(null);
      return;
    }
    setBusy(true);
    setProblem(null);
    try {
      const params = new URLSearchParams({
        q: text.trim(),
        when: nextWhen,
        order: nextOrder,
        offset: String(offset),
      });
      const got = await api<PastChatsSearch>(`/api/past-chats?${params}`);
      setFound((was) =>
        offset > 0 && was ? { ...got, results: [...was.results, ...got.results] } : got,
      );
      setStatus(got.status);
      setAsked(text.trim());
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void search(query, when, order);
  }

  const line = status ? statusWords(status) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto" data-testid="past-chats">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 pb-10 pt-6">
        <div className="flex items-start gap-2">
          <h1 id="view-heading" className="title-card flex-1">
            Search chats
          </h1>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <p className="help">
          Find what you asked and what the answers said, in all your chats. Only you can search
          them.
        </p>
        <form role="search" onSubmit={submit} className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-1 rounded-plexus border border-line-strong bg-soft px-2 focus-within:border-accent focus-within:ring-1 focus-within:ring-accent">
              <Search size={14} aria-hidden className="shrink-0 text-muted" />
              <input
                id={`${uid}-q`}
                type="search"
                data-testid="past-chats-query"
                aria-label="Search your chats"
                placeholder="Search your chats"
                value={query}
                maxLength={500}
                onChange={(event) => setQuery(event.target.value)}
                className="min-w-0 flex-1 bg-transparent py-1.5 outline-none"
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Searching…" : "Search"}
            </button>
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="field">
              <label htmlFor={`${uid}-when`} className="field-label">
                When
              </label>
              <select
                id={`${uid}-when`}
                data-testid="past-chats-when"
                value={when}
                onChange={(event) => {
                  const next = event.target.value as When;
                  setWhen(next);
                  void search(query, next, order);
                }}
                className="control w-40"
              >
                {WHEN.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-order`} className="field-label">
                Order
              </label>
              <select
                id={`${uid}-order`}
                data-testid="past-chats-order"
                value={order}
                onChange={(event) => {
                  const next = event.target.value as Order;
                  setOrder(next);
                  void search(query, when, next);
                }}
                className="control w-40"
              >
                <option value="best">Best match</option>
                <option value="newest">Newest</option>
              </select>
            </div>
          </div>
        </form>
        {line && (
          <p role="status" data-testid="past-chats-status" className="help">
            {line}
          </p>
        )}
        {problem && (
          <p role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem}
          </p>
        )}
        {found && asked && (
          <section aria-label="Results" className="flex flex-col gap-2">
            <p role="status" data-testid="past-chats-summary" className="help">
              {found.total === 0
                ? `Nothing in your chats matched “${asked}”.`
                : `${found.total.toLocaleString()} ${found.total === 1 ? "exchange" : "exchanges"} matched.`}{" "}
              {found.why ?? ""}
            </p>
            <ul data-testid="past-chats-results" className="divide-y divide-line">
              {found.results.map((result) => (
                <ResultRow key={result.messageId} result={result} onOpen={onOpen} />
              ))}
            </ul>
            {found.results.length < found.total && (
              <button
                type="button"
                className="btn self-start"
                disabled={busy}
                onClick={() => void search(asked, when, order, found.results.length)}
              >
                Show more
              </button>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function ResultRow({
  result,
  onOpen,
}: {
  result: PastChatResult;
  onOpen: (chatId: string, messageId: string) => void;
}) {
  return (
    <li className="flex flex-col gap-1 py-3" data-testid="past-chats-result">
      <a
        href={chatAt(result.chatId, result.messageId)}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
          event.preventDefault();
          onOpen(result.chatId, result.messageId);
        }}
        className="font-medium text-accent underline-offset-2 hover:underline"
      >
        <span dir="auto">{result.title}</span>
      </a>
      <span className="text-xs text-muted">
        {result.date} · {VIA_WORDS[result.via] ?? ""}
      </span>
      <p dir="auto" className="text-sm wrap-anywhere">
        <span className="text-muted">You: </span>
        {result.question}
      </p>
      {result.answer && (
        <p dir="auto" className="text-sm text-muted wrap-anywhere">
          {result.answer}
        </p>
      )}
    </li>
  );
}
