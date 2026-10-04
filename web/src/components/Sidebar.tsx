import { Eye, LogOut, Plus, Search, X } from "lucide-react";
import { useState } from "react";

import { api } from "../lib/api";
import { chatGroups } from "../lib/conveniences";
import type { Chat, Me, Person } from "../lib/types";
import { OWNER_READS } from "../lib/words";
import { Logo } from "./Mascot";

export function Sidebar({
  me,
  chats,
  current,
  onOpen,
  onClose,
  onNew,
  onSignOut,
  creating,
  error,
  onRetry,
}: {
  me: Me;
  chats: Chat[];
  current: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
  onNew: () => void;
  onSignOut: () => void;
  creating: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const [query, setQuery] = useState("");
  const groups = chatGroups(chats, query);
  return (
    <nav
      id="chat-navigation"
      aria-label="Chats"
      className="flex w-full shrink-0 flex-col border-r border-line bg-panel md:w-72"
    >
      <div className="flex items-center gap-2 px-4 py-3">
        <Logo />
        <span className="text-lg font-semibold">Workbench</span>
        <button
          onClick={onClose}
          aria-label="Back to chat"
          className="ml-auto rounded-plexus p-2 md:hidden"
        >
          <X size={18} />
        </button>
      </div>
      <div className="px-3">
        <button
          type="button"
          data-testid="new-chat"
          onClick={onNew}
          disabled={creating}
          aria-keyshortcuts="Control+Alt+N"
          className="flex w-full items-center justify-center gap-2 rounded-plexus border border-line bg-soft px-3 py-2 hover:bg-hover"
        >
          <Plus size={16} aria-hidden /> {creating ? "Creating…" : "New chat"}
        </button>
      </div>
      {!me.owner && me.ownerReadsChats && (
        <p
          data-testid="owner-reads"
          className="mx-3 mt-3 rounded-plexus border border-warn-line bg-warn-bg px-3 py-2 text-sm text-warn"
        >
          {OWNER_READS}
        </p>
      )}
      <h2 className="px-4 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-muted">
        Chats
      </h2>
      <div className="mx-3 mb-2 flex items-center gap-1 rounded-plexus border border-line bg-soft px-2">
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
          }}
          className="min-w-0 flex-1 bg-transparent py-2 text-sm"
        />
        {query && (
          <button aria-label="Clear chat search" onClick={() => setQuery("")} className="p-1">
            <X size={14} />
          </button>
        )}
      </div>
      {error && (
        <div role="alert" className="px-4 py-2 text-sm text-error">
          {error}{" "}
          <button onClick={onRetry} className="underline">
            Try again
          </button>
        </div>
      )}
      <ul className="flex-1 overflow-y-auto px-2" data-testid="chat-list">
        {chats.length === 0 && <li className="px-2 py-1 text-sm text-muted">No chats yet.</li>}
        {chats.length > 0 && groups.length === 0 && (
          <li role="status" className="px-2 py-2 text-sm text-muted">
            No chats match “{query}”.
          </li>
        )}
        {groups.map((group) => (
          <li key={group.label}>
            <h3 className="px-2 pb-1 pt-3 text-xs text-muted">{group.label}</h3>
            <ul>
              {group.chats.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(chat.id)}
                    aria-current={chat.id === current ? "page" : undefined}
                    title={chat.title}
                    className={`flex w-full items-center gap-2 truncate rounded-plexus px-2 py-1.5 text-left text-sm hover:bg-hover ${
                      chat.id === current ? "bg-soft font-medium" : ""
                    }`}
                  >
                    {chat.running && (
                      <span
                        className="h-2 w-2 shrink-0 rounded-full bg-accent"
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
      {me.owner && me.ownerReadsChats && <PeoplesChats onOpen={onOpen} />}
      {me.owner && me.consoleUrl && (
        <a
          href={me.consoleUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="border-t border-line px-4 py-3 text-sm text-muted hover:underline"
        >
          Open Eugene console ↗
        </a>
      )}
      <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-3 text-sm">
        <span className="truncate" data-testid="me">
          {me.name}
          {me.owner && <span className="text-muted"> (owner)</span>}
        </span>
        <button
          type="button"
          onClick={onSignOut}
          className="flex items-center gap-1 rounded-plexus px-2 py-1 text-muted hover:bg-hover"
        >
          <LogOut size={14} aria-hidden /> Sign out
        </button>
      </div>
    </nav>
  );
}

/** The owner's read-only view, when the business allows it (W4). */
function PeoplesChats({ onOpen }: { onOpen: (id: string) => void }) {
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
      className="border-t border-line px-3 py-2 text-sm"
      onToggle={(e) => {
        if ((e.target as HTMLDetailsElement).open && people === null) void load();
      }}
    >
      <summary className="flex cursor-pointer items-center gap-2 text-muted">
        <Eye size={14} aria-hidden /> People&apos;s chats (read only)
      </summary>
      {error && <p className="text-error">{error}</p>}
      {people?.length === 0 && (
        <p className="py-1 text-muted">Nobody else has used Workbench yet.</p>
      )}
      <ul>
        {people?.map((person) => (
          <li key={person.sub}>
            <button
              type="button"
              className="w-full truncate py-1 text-left hover:underline"
              onClick={async () => {
                const found = await api<{ chats: Chat[] }>(
                  `/api/people/${encodeURIComponent(person.sub)}/chats`,
                );
                setChats((current) => ({ ...current, [person.sub]: found.chats }));
              }}
            >
              {person.name} ({person.chats})
            </button>
            <ul className="pl-3">
              {chats[person.sub]?.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    className="w-full truncate py-0.5 text-left text-muted hover:underline"
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
