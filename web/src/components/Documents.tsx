/**
 * The Documents screen: the person's own files, which Workbench reads and searches
 * for the words in a question when a chat has Documents on. Files are added here (many
 * at once, by choosing or dropping them), read in the background with each one's state
 * in plain words, and always removable: a removed document is gone for real. A box
 * tries a question, and shows the passages that would be sent, without asking a model.
 *
 * Search here is by words only, and the screen says so truthfully, in the server's own
 * headline, and promises nothing more. Every setting shows the value in effect and
 * whose word it is. Where the owner of this Workbench has turned documents off, the
 * screen says so and offers nothing that would keep a document; what a person already
 * has stays theirs to read and remove. A search that is damaged says so in the
 * server's words and offers to make it again from the person's own files.
 */

import {
  ArrowLeft,
  CircleAlert,
  CircleCheck,
  Eye,
  Files,
  Info,
  MessageSquarePlus,
  Search,
} from "lucide-react";
import { useEffect, useId, useState } from "react";

import { del, fileOf, post, put } from "../lib/api";
import { failureWords, saveFile } from "../lib/memory";
import {
  capsWords,
  EXTERNAL_CHOICES,
  externalLabel,
  INTRO,
  newChatsWords,
  OWNER_OFF,
  OWNER_OFF_EFFECT,
  rebuiltWords,
  setByWords,
  totalsWords,
  WORDS_ONLY_HELP,
} from "../lib/savedDocuments";
import type { DocumentsPage, ExternalChoice, Rebuilt } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { useSavedDocuments } from "../lib/useSavedDocuments";
import { AddDocuments } from "./AddDocuments";
import { DocumentRow } from "./DocumentRow";
import { TestQuestion } from "./TestQuestion";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

export function Documents({ onClose, onAsk }: { onClose: () => void; onAsk: () => void }) {
  const uid = useId();
  const { data, blocked, problem: loadProblem, reload } = useSavedDocuments();
  const [problem, setProblem] = useState<string | null>(null);
  const [rebuilt, setRebuilt] = useState<Rebuilt | null>(null);
  // The words about what happened to one document, by its id.
  const [rowProblems, setRowProblems] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  // Buttons are held while a change is made, which takes the keyboard's place from the
  // one that was pressed: it is given back when the change is done.
  const keepFocus = useKeepFocus(busy);
  const [focusId, setFocusId] = useState<string | null>(null);

  // After a change the thing a person was on may be gone (a removed document): focus
  // goes to the list's heading, once the page has redrawn.
  useEffect(() => {
    if (!focusId) return;
    document.getElementById(focusId)?.focus();
    setFocusId(null);
  }, [focusId, data]);

  /** Work that changes the documents or the settings: the screen is busy while it
   * runs, and reads again after it, whether it worked or not. */
  const working: Working = async (work) => {
    keepFocus();
    setBusy(true);
    try {
      return await work();
    } finally {
      await reload();
      setBusy(false);
    }
  };

  async function act(work: () => Promise<void>, focus?: string) {
    setProblem(null);
    try {
      await working(work);
      if (focus) setFocusId(focus);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  /** Work on one document: a refusal is said on its own row. */
  async function actOn(id: string, work: () => Promise<void>, focus?: string) {
    setProblem(null);
    setRowProblems((now) => {
      const rest = { ...now };
      delete rest[id];
      return rest;
    });
    try {
      await working(work);
      if (focus) setFocusId(focus);
    } catch (failure) {
      setRowProblems((now) => ({ ...now, [id]: failureWords(failure) }));
    }
  }

  const header = (
    <header className="page-head">
      <h1 id="view-heading" tabIndex={-1} className="title-page">
        <Files size={20} aria-hidden />
        Documents
      </h1>
      <button type="button" data-testid="documents-back" className="btn" onClick={onClose}>
        <ArrowLeft size={14} aria-hidden />
        Back to chat
      </button>
    </header>
  );

  const problems = (
    <>
      {loadProblem && (
        <div role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">
              {data ? "Could not read your documents again" : "Could not read your documents"}
            </span>
            {loadProblem}
            <div className="mt-2">
              <button
                type="button"
                data-testid="documents-retry"
                className="btn"
                onClick={() => void reload()}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {problem && (
        <p role="alert" data-testid="documents-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
    </>
  );

  async function rebuild() {
    setProblem(null);
    setRebuilt(null);
    try {
      setRebuilt(await working(() => post<Rebuilt>("/api/documents/rebuild")));
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  if (blocked) {
    return (
      <section aria-label="Documents" className="page" data-testid="documents-page">
        <div className="page-col">
          {header}
          <div role="alert" data-testid="documents-damaged" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <div>
              <span className="notice-title">
                {blocked.canRebuild
                  ? "Your documents' search is damaged"
                  : "Your documents cannot be searched right now"}
              </span>
              <span className="wrap-anywhere">{blocked.message}</span>
              {blocked.canRebuild && (
                <span className="mt-1 block text-muted">
                  Rebuild search makes it again from the files you added, and reads them again one
                  at a time. Nothing you added is lost.
                </span>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                {blocked.canRebuild && (
                  <button
                    type="button"
                    data-testid="documents-rebuild"
                    disabled={busy}
                    className="btn btn-primary"
                    onClick={() => void rebuild()}
                  >
                    Rebuild search
                  </button>
                )}
                <button
                  type="button"
                  data-testid="documents-retry"
                  className="btn"
                  onClick={() => void reload()}
                >
                  Try again
                </button>
              </div>
            </div>
          </div>
          {problem && (
            <p role="alert" data-testid="documents-problem" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
        </div>
      </section>
    );
  }

  if (!data) {
    return (
      <section aria-label="Documents" className="page" data-testid="documents-page">
        <div className="page-col">
          {header}
          {loadProblem ? (
            problems
          ) : (
            <p role="status" className="text-sm text-muted">
              Loading your documents…
            </p>
          )}
        </div>
      </section>
    );
  }

  const off = !data.installEnabled;
  const bare = off && data.documents.length === 0;
  const ready = data.documents.filter((doc) => doc.searchable).length;

  const list = (
    <ul
      data-testid="documents-list"
      className="divide-y divide-line rounded-plexus border border-line bg-panel"
    >
      {data.documents.map((doc) => (
        <DocumentRow
          key={doc.id}
          doc={doc}
          locked={off}
          busy={busy}
          problem={rowProblems[doc.id] ?? null}
          onOpen={() =>
            void actOn(doc.id, async () => {
              const file = await fileOf(`/api/documents/${encodeURIComponent(doc.id)}/original`);
              saveFile(file.blob, file.name ?? doc.name);
            })
          }
          onReread={() =>
            void actOn(doc.id, () =>
              post(`/api/documents/${encodeURIComponent(doc.id)}/reread`).then(() => undefined),
            )
          }
          onRemove={() =>
            void actOn(
              doc.id,
              () => del(`/api/documents/${encodeURIComponent(doc.id)}`),
              `${uid}-list`,
            )
          }
        />
      ))}
    </ul>
  );

  return (
    <section aria-label="Documents" className="page" data-testid="documents-page">
      <div className="page-col">
        {header}
        {off ? (
          <p data-testid="documents-owner-off" className="notice">
            <Info size={16} aria-hidden />
            <span>
              <span className="notice-title">{OWNER_OFF}</span>
              {OWNER_OFF_EFFECT}
              {data.documents.length > 0 &&
                " The documents you already have stay yours: you can still open and remove them."}
            </span>
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-3">
              <p data-testid="documents-intro" className="help">
                {INTRO}
              </p>
              <p data-testid="documents-search" className="notice">
                <Search size={16} aria-hidden />
                <span>
                  <span className="notice-title">{data.search.headline}</span>
                  {!data.search.meaning && WORDS_ONLY_HELP}
                </span>
              </p>
            </div>
            {data.ownerReadsChats && (
              <p data-testid="documents-owner-reads" className="notice notice--warn">
                <Eye size={16} aria-hidden />
                The owner of this Workbench can read your chats, and an answer in a chat can quote
                your documents. The owner cannot open this page or your files.
              </p>
            )}
          </>
        )}
        {problems}
        {rebuilt && (
          <p role="status" data-testid="documents-rebuilt" className="notice notice--ok">
            <CircleCheck size={16} aria-hidden />
            {rebuiltWords(rebuilt)}
          </p>
        )}
        {!off && (
          <Settings
            data={data}
            busy={busy}
            onPrefs={(body) =>
              void act(() => put("/api/documents/prefs", body).then(() => undefined))
            }
          />
        )}
        {!bare && (
          <section aria-labelledby={`${uid}-list`} className="section">
            <h2 id={`${uid}-list`} tabIndex={-1} className="title-section">
              Your documents
            </h2>
            {!off && <AddDocuments onChanged={() => void reload()} />}
            {data.documents.length === 0 ? (
              <div data-testid="documents-empty" className="card">
                <h3 className="title-card">No documents yet</h3>
                <p className="help">
                  Add files above. Workbench reads each one, so a chat with Documents on can find
                  the passages that have the words of your question.
                </p>
              </div>
            ) : (
              <>
                {!off && ready > 0 && (
                  <div>
                    <button
                      type="button"
                      data-testid="documents-ask"
                      className="btn btn-primary"
                      onClick={onAsk}
                    >
                      <MessageSquarePlus size={14} aria-hidden />
                      Ask about your documents
                    </button>
                  </div>
                )}
                {list}
                <Foot data={data} />
              </>
            )}
          </section>
        )}
        {!off && data.documents.length > 0 && <TestQuestion />}
      </div>
    </section>
  );
}

/** The totals, and the caps, stated. */
function Foot({ data }: { data: DocumentsPage }) {
  return (
    <div className="flex flex-col gap-1">
      <p data-testid="documents-totals" className="text-sm">
        {totalsWords(data.totals)}
      </p>
      <ul data-testid="documents-caps" className="flex flex-col gap-0.5 text-xs text-muted">
        {capsWords(data).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

// --- settings ---

function Settings({
  data,
  busy,
  onPrefs,
}: {
  data: DocumentsPage;
  busy: boolean;
  onPrefs: (body: { newChats?: boolean; external?: ExternalChoice | null }) => void;
}) {
  const uid = useId();
  const { prefs } = data;
  const { effective } = prefs;
  const ownerSet = effective.externalSetBy === "owner";
  // What was just chosen shows while the server catches up, and a refusal puts the old
  // value back: these are only read while the page is busy.
  const [wanted, setWanted] = useState<{ newChats?: boolean; external?: ExternalChoice }>({});
  const newChats = busy && wanted.newChats !== undefined ? wanted.newChats : effective.newChats;
  const external = busy && wanted.external !== undefined ? wanted.external : effective.external;
  return (
    <section aria-labelledby={`${uid}-settings`} className="section">
      <h2 id={`${uid}-settings`} className="title-section">
        Settings
      </h2>
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-new`}>
          <input
            id={`${uid}-new`}
            type="checkbox"
            role="switch"
            data-testid="documents-new-chats-switch"
            aria-describedby={`${uid}-new-help`}
            checked={newChats}
            disabled={busy}
            onChange={(event) => {
              setWanted({ newChats: event.target.checked });
              onPrefs({ newChats: event.target.checked });
            }}
          />
          Documents in new chats
        </label>
        <p id={`${uid}-new-help`} data-testid="documents-new-chats-effective" className="help">
          {newChatsWords(prefs)}
        </p>
      </div>
      <fieldset className="flex min-w-0 flex-col gap-2" aria-describedby={`${uid}-outside-help`}>
        <legend className="field-label mb-1">Models that run outside your machines</legend>
        <p id={`${uid}-outside-help`} className="help">
          Some models run on another company&apos;s computers, through a cloud account added to
          Eugene. The passages of your documents that match a question would leave your machines to
          reach them. Files you attach to one chat are your own choice, so this does not hold them
          back.
        </p>
        {EXTERNAL_CHOICES.map((choice) => (
          <div key={choice.value}>
            <label className="check">
              <input
                type="radio"
                name={`${uid}-external`}
                value={choice.value}
                data-testid={`documents-external-${choice.value}`}
                aria-describedby={`${uid}-external-${choice.value}`}
                checked={external === choice.value}
                disabled={busy || ownerSet}
                onChange={() => {
                  setWanted({ external: choice.value });
                  onPrefs({ external: choice.value });
                }}
              />
              <span>{choice.label}</span>
            </label>
            <p id={`${uid}-external-${choice.value}`} className="help ps-[1.625rem]">
              {choice.help}
            </p>
          </div>
        ))}
        <p data-testid="documents-external-effective" className="help">
          In effect: <strong>{externalLabel(effective.external)}</strong>.{" "}
          {setByWords(effective.externalSetBy)}
        </p>
        {prefs.external !== null && !ownerSet && (
          <button
            type="button"
            data-testid="documents-external-default"
            disabled={busy}
            onClick={() => onPrefs({ external: null })}
            className="btn btn-quiet -ms-2 self-start"
          >
            Use the default
          </button>
        )}
      </fieldset>
    </section>
  );
}
