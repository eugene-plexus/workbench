import { CircleAlert, Eye, LogOut, Plus, Search, X } from "lucide-react";
import { useState } from "react";

import { consolePage } from "../lib/documentMeaning";

import { api } from "../lib/api";
import { chatGroups } from "../lib/conveniences";
import type { Chat, Me, Person } from "../lib/types";
import {
  DEV_MODE,
  DEV_MODE_EXPLAINED,
  OWNER_READS,
  PRODUCTION_MODE,
  PRODUCTION_MODE_EXPLAINED,
} from "../lib/words";
import { Logo } from "./Mascot";

export function Sidebar({
  me,
  chats,
  current,
  onOpen,
  onOpenMedia,
  onClose,
  onNew,
  onSignOut,
  creating,
  error,
  onRetry,
  chatsLoaded = true,
  onRunPreviews,
}: {
  me: Me;
  chats: Chat[];
  current: string | null;
  onOpen: (id: string) => void;
  /** The owner opens a person's bins, read only (M3). */
  onOpenMedia: (person: { sub: string; name: string }) => void;
  onClose: () => void;
  onNew: () => void;
  onSignOut: () => void;
  creating: boolean;
  error: string | null;
  onRetry: () => void;
  /** False until the first list of chats has come back (or failed). */
  chatsLoaded?: boolean;
  /** Keep whether the person's previews run when shown (rich content RC6). */
  onRunPreviews?: (on: boolean) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const groups = chatGroups(chats, query);
  const dev = me.installMode === "dev";
  return (
    <nav
      id="chat-navigation"
      aria-label="Chats"
      className="flex w-full shrink-0 flex-col border-r border-line bg-panel md:w-72"
    >
      <div className="flex items-center gap-2 px-4 py-3">
        <Logo />
        <span className="text-xl font-semibold tracking-[-0.01em]">Workbench</span>
        <button onClick={onClose} aria-label="Back to chat" className="icon-btn ml-auto md:hidden">
          <X size={18} />
        </button>
      </div>
      {/* The button, the mode chip, the search box and the chats below all span
          the same width, and their text starts at the same x. */}
      <div className="px-2">
        <button
          type="button"
          data-testid="new-chat"
          onClick={onNew}
          disabled={creating}
          aria-keyshortcuts="Control+Alt+N"
          className="btn w-full"
        >
          <Plus size={16} aria-hidden className="text-accent" />{" "}
          {creating ? "Creating…" : "New chat"}
        </button>
      </div>
      {me.installMode && (
        <p className="mx-2 mt-3">
          <span
            data-testid="install-mode"
            tabIndex={0}
            title={dev ? DEV_MODE_EXPLAINED : PRODUCTION_MODE_EXPLAINED}
            aria-describedby="install-mode-explained"
            className={`badge ${dev ? "border-error-line bg-error-bg text-error" : ""}`}
          >
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
            {dev ? DEV_MODE : PRODUCTION_MODE}
          </span>
          {/* Who can see what is said on the screen in both modes, for the
              keyboard and the touch screen that have no tooltip. */}
          <span id="install-mode-explained" className="help mt-1 block">
            {dev ? DEV_MODE_EXPLAINED : PRODUCTION_MODE_EXPLAINED}
          </span>
        </p>
      )}
      {!me.owner && me.ownerReadsChats && (
        <div data-testid="owner-reads" className="notice notice--warn mx-2 mt-3">
          <Eye size={16} aria-hidden />
          {OWNER_READS}
        </div>
      )}
      <h2 className="eyebrow px-4 pb-1 pt-4 text-muted">Chats</h2>
      <div className="mx-2 mb-2 flex items-center gap-1 rounded-plexus border border-line-strong bg-soft px-2 focus-within:border-accent focus-within:ring-1 focus-within:ring-accent">
        <Search size={14} aria-hidden className="shrink-0 text-muted" />
        <input
          id="chat-search"
          type="search"
          aria-label="Search chat names"
          aria-keyshortcuts="Control+Alt+F"
          placeholder="Search chat names"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setQuery("");
            // Enter opens the first chat that matches, so search-and-open
            // needs no mouse.
            const first = groups[0]?.chats[0];
            if (e.key === "Enter" && query.trim() && first) {
              e.preventDefault();
              onOpen(first.id);
            }
          }}
          // The box draws its own focus (the wrapper's blue edge and ring); a
          // hidden outline still shows in Windows high contrast.
          className="min-w-0 flex-1 bg-transparent py-2 text-sm focus:outline-hidden [&::-webkit-search-cancel-button]:hidden"
        />
        {query && (
          <button
            aria-label="Clear chat search"
            onClick={() => setQuery("")}
            className="icon-btn -mr-1 shrink-0"
          >
            <X size={14} />
          </button>
        )}
      </div>
      {error && (
        <div role="alert" className="notice notice--error mx-2 mb-2">
          <CircleAlert size={16} aria-hidden />
          {error}{" "}
          <button onClick={onRetry} className="btn btn-quiet -my-1 ml-auto shrink-0">
            Try again
          </button>
        </div>
      )}
      <ul className="flex-1 overflow-y-auto px-2" data-testid="chat-list">
        {!chatsLoaded && !error && (
          <li role="status" className="px-2 py-1 text-sm text-muted">
            Loading chats…
          </li>
        )}
        {chatsLoaded && chats.length === 0 && (
          <li className="px-2 py-1 text-sm text-muted">
            No chats yet. Choose New chat to start one.
          </li>
        )}
        {chats.length > 0 && groups.length === 0 && (
          <li role="status" className="px-2 py-2 text-sm text-muted wrap-anywhere">
            No chats match “{query}”.
          </li>
        )}
        {groups.map((group) => (
          <li key={group.label}>
            <h3 className="px-2 pb-1 pt-3 text-xs font-medium text-muted">{group.label}</h3>
            <ul className="flex flex-col gap-0.5">
              {group.chats.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(chat.id)}
                    aria-current={chat.id === current ? "page" : undefined}
                    title={chat.title}
                    // The open chat has a 3 px blue rail, a tint and a heavier
                    // weight: three cues, none of them a fill alone, and the
                    // rail is a border, which Windows high contrast keeps. The
                    // text does not move: the rail's width comes out of the
                    // padding.
                    className={`flex w-full items-center gap-2 rounded-plexus py-1.5 pr-2 text-left text-sm pointer-coarse:py-2.5 ${
                      chat.id === current
                        ? "border-l-[3px] border-accent bg-tint pl-[5px] font-medium"
                        : "pl-2 hover:bg-hover"
                    }`}
                  >
                    {chat.running && (
                      <span
                        role="img"
                        aria-label="An answer is being written"
                        data-testid="chat-running"
                        className="run-dot h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent"
                        title="An answer is being written"
                      />
                    )}
                    <span className="truncate">{chat.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {me.owner && me.ownerReadsChats && <PeoplesChats onOpen={onOpen} onOpenMedia={onOpenMedia} />}
      {me.owner && me.consoleUrl && (
        <div className="flex flex-col gap-2 border-t border-line px-4 py-3 text-sm">
          <a
            href={consolePage(me.consoleUrl, "people")}
            target="_blank"
            rel="noopener noreferrer"
            className="link inline-flex min-h-7 items-center"
          >
            Manage accounts and groups ↗
          </a>
          <a
            href={me.consoleUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="link inline-flex min-h-7 items-center"
          >
            Open Eugene console ↗
          </a>
        </div>
      )}
      {me.accountUrl && (
        <a
          href={me.accountUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="link border-t border-line px-4 py-3 text-sm"
        >
          My sign-in and sessions ↗
        </a>
      )}
      {onRunPreviews && (
        <RunPreviewsSwitch on={me.runPreviews !== false} onChange={onRunPreviews} />
      )}
      <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-2 text-sm">
        <span className="truncate" data-testid="me">
          {me.name}
          {me.owner && <span className="text-muted"> (owner)</span>}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          className="btn btn-quiet -mr-2 shrink-0 text-muted hover:text-fg"
        >
          <LogOut size={14} aria-hidden /> Sign out
        </button>
      </div>
    </nav>
  );
}

/**
 * Whether the person's previews run by themselves when shown (rich content RC6),
 * as Workbench keeps it: the box shows what was kept, and says so when keeping
 * it failed.
 */
function RunPreviewsSwitch({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (on: boolean) => Promise<void>;
}) {
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  return (
    <div className="border-t border-line px-4 py-2 text-sm">
      <label className="flex min-h-7 items-center gap-2">
        <input
          type="checkbox"
          checked={on}
          disabled={saving}
          onChange={async (event) => {
            const next = event.target.checked;
            setSaving(true);
            setProblem("");
            try {
              await onChange(next);
            } catch (error) {
              setProblem(`Not changed: ${error instanceof Error ? error.message : String(error)}`);
            } finally {
              setSaving(false);
            }
          }}
        />
        Run previews when shown
      </label>
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
    </div>
  );
}

/** The owner's read-only view, when the business allows it (W4). */
function PeoplesChats({
  onOpen,
  onOpenMedia,
}: {
  onOpen: (id: string) => void;
  onOpenMedia: (person: { sub: string; name: string }) => void;
}) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [chats, setChats] = useState<Record<string, Chat[]>>({});
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setPeople((await api<{ people: Person[] }>("/api/people")).people);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <details
      className="border-t border-line px-2 py-2 text-sm"
      onToggle={(e) => {
        if ((e.target as HTMLDetailsElement).open && people === null) void load();
      }}
    >
      <summary className="flex items-center gap-2 rounded-plexus px-2 py-1 text-muted hover:bg-hover hover:text-fg">
        <Eye size={14} aria-hidden /> People&apos;s chats and media (read only)
      </summary>
      {error && (
        <p role="alert" className="notice notice--error mt-2">
          <CircleAlert size={16} aria-hidden />
          {error}
        </p>
      )}
      {people === null && !error && (
        <p role="status" className="px-2 py-1 text-muted">
          Loading people…
        </p>
      )}
      {people?.length === 0 && (
        <p className="px-2 py-1 text-muted">Nobody else has used Workbench yet.</p>
      )}
      <ul>
        {people?.map((person) => (
          <li key={person.sub}>
            <button
              type="button"
              aria-expanded={chats[person.sub] !== undefined}
              className="w-full truncate rounded-plexus px-2 py-1 text-left hover:bg-hover"
              onClick={async () => {
                setError(null);
                try {
                  const found = await api<{ chats: Chat[] }>(
                    `/api/people/${encodeURIComponent(person.sub)}/chats`,
                  );
                  setChats((current) => ({ ...current, [person.sub]: found.chats }));
                } catch (e) {
                  setError(
                    `${person.name}'s chats did not load: ${e instanceof Error ? e.message : String(e)}`,
                  );
                }
              }}
            >
              {person.name} ({person.chats})
            </button>
            {person.media > 0 && (
              <button
                type="button"
                className="block w-full truncate rounded-plexus py-0.5 pl-5 pr-2 text-left text-muted hover:bg-hover hover:text-fg"
                onClick={() => onOpenMedia({ sub: person.sub, name: person.name })}
              >
                Their images ({person.media})
              </button>
            )}
            {chats[person.sub]?.length === 0 && <p className="py-0.5 pl-5 text-muted">No chats.</p>}
            <ul>
              {chats[person.sub]?.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    className="w-full truncate rounded-plexus py-0.5 pl-5 pr-2 text-left text-muted hover:bg-hover hover:text-fg"
                    onClick={() => onOpen(chat.id)}
                  >
                    {chat.title}
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </details>
  );
}
