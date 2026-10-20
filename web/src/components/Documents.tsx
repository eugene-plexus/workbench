/**
 * The Documents screen: the person's own files, which Workbench reads and searches
 * for the words in a question when a chat has Documents on. Files are added here (many
 * at once, by choosing or dropping them), read in the background with each one's state
 * in plain words, and always removable: a removed document is gone for real. A box
 * tries a question, and shows the passages that would be sent, without asking a model.
 *
 * How it is searched is said from a live look, in the server's own headline: by meaning
 * and words, or by words only and why, with what the person can do (Rebuild search, or get
 * a search model from Eugene's console), and each document says how much of its meaning is
 * added. Beside it, the result sorter that puts the best passages in order (MR6b) says which
 * one is in use, how fast it reads and the time a sort may add (Sort time), or why passages are
 * not sorted, with the numbers. The screen promises nothing more. Every setting shows the value
 * in effect and whose word it is. Where the owner of this Workbench has turned documents off, the
 * screen says so and offers nothing that would keep a document; what a person already
 * has stays theirs to read and remove. A search that is damaged says so in the
 * server's words and offers to make it again from the person's own files.
 *
 * Folders (MR7) are a section of their own (DocumentFolders): the folders the owner gave the
 * person, which Workbench reads and keeps in step. Their files are documents too, searched by every
 * chat with Documents on, and counted apart from the files the person added themselves.
 */

import {
  ArrowLeft,
  CircleAlert,
  CircleCheck,
  Eye,
  Files,
  Info,
  MessageSquarePlus,
  Settings as SettingsIcon,
} from "lucide-react";
import { useEffect, useId, useState } from "react";

import { del, fileOf, post } from "../lib/api";
import {
  emptyWords,
  introWords,
  meaningRebuiltWords,
  meaningTotalsWords,
} from "../lib/documentMeaning";
import { failureWords, saveFile } from "../lib/memory";
import {
  capsWords,
  OWNER_OFF,
  OWNER_OFF_EFFECT,
  rebuiltWords,
  totalsWords,
} from "../lib/savedDocuments";
import type { DocumentsPage, Me, MeaningRebuilt, Rebuilt } from "../lib/types";
import { anySearchable } from "../lib/documentFolders";
import { useDocumentFolders } from "../lib/useDocumentFolders";
import { useKeepFocus } from "../lib/useKeepFocus";
import { useSavedDocuments } from "../lib/useSavedDocuments";
import { AddDocuments } from "./AddDocuments";
import { DocumentFolders } from "./DocumentFolders";
import { DocumentRow } from "./DocumentRow";
import { DocumentsSearch } from "./DocumentsSearch";
import { DocumentsSort } from "./DocumentsSort";
import { TestQuestion } from "./TestQuestion";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

export function Documents({
  onClose,
  onAsk,
  onTools,
  onOpenSettings,
  me,
}: {
  onClose: () => void;
  onAsk: () => void;
  /** Opens the Settings page at Documents' section (MV4). */
  onOpenSettings?: () => void;
  /** Opens Toolbox · Tools, where the owner gives folders to people (the Folders section says so). */
  onTools?: () => void;
  /** Who is reading, and where Eugene's console is: a search model is the owner's to get. */
  me?: Pick<Me, "owner" | "consoleUrl"> | null;
}) {
  const uid = useId();
  const { data, blocked, problem: loadProblem, reload } = useSavedDocuments();
  // The folders the person pointed their Documents at (MR7): read apart from their own files, so a
  // folder of ten thousand files is not part of every look at the list.
  const folders = useDocumentFolders();
  const [problem, setProblem] = useState<string | null>(null);
  const [rebuilt, setRebuilt] = useState<Rebuilt | null>(null);
  const [meaningRebuilt, setMeaningRebuilt] = useState<MeaningRebuilt | null>(null);
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

  // What was said of adding meaning again is true while there is meaning to add: once none is
  // owed it goes, and does not come back for the next document that is added.
  useEffect(() => {
    if (data && data.totals.owed === 0) setMeaningRebuilt(null);
  }, [data]);

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
      {onOpenSettings && (
        <button
          type="button"
          data-testid="documents-open-settings"
          className="btn btn-quiet"
          onClick={onOpenSettings}
        >
          <SettingsIcon size={14} aria-hidden />
          Documents settings
        </button>
      )}
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

  /** Add meaning again with the search model that is running. Words are never touched. */
  async function rebuildMeaning() {
    setProblem(null);
    setMeaningRebuilt(null);
    try {
      setMeaningRebuilt(
        await working(() => post<MeaningRebuilt>("/api/documents/meaning/rebuild")),
      );
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
  const kept = folders.data?.folders ?? [];
  // Where the owner has turned documents off, a person's folders are shown only so that they can be
  // removed: with none, there is nothing to say about folders.
  const showFolders = !off || kept.length > 0;
  const fromFolders = kept.some((folder) => folder.counts.files > 0);

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
                {introWords(data.search)}
              </p>
              <DocumentsSearch
                facts={data.search}
                me={me}
                busy={busy}
                onRebuild={() => void rebuildMeaning()}
              />
              <DocumentsSort sort={data.search.sort} me={me} />
            </div>
            {data.ownerReadsChats && (
              <p data-testid="documents-owner-reads" className="notice notice--warn">
                <Eye size={16} aria-hidden />
                The owner of this Workbench can read your chats, including any passages an answer
                quotes. The owner cannot open this page or your files.
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
        {meaningRebuilt && (
          <p role="status" data-testid="documents-meaning-rebuilt" className="notice notice--ok">
            <CircleCheck size={16} aria-hidden />
            {meaningRebuiltWords({
              passages: meaningRebuilt.passages,
              model: meaningRebuilt.search.model?.resolved ?? null,
            })}
          </p>
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
                <p className="help">{emptyWords(data.search)}</p>
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
        {showFolders && (
          <DocumentFolders
            folders={folders}
            me={me}
            off={off}
            busy={busy}
            askHere={!off && ready === 0 && anySearchable(kept)}
            onAsk={onAsk}
            onTools={onTools}
          />
        )}
        {!off && (data.documents.length > 0 || fromFolders) && (
          <TestQuestion search={data.search} />
        )}
      </div>
    </section>
  );
}

/** The totals, and the caps, stated. */
function Foot({ data }: { data: DocumentsPage }) {
  const meaning = meaningTotalsWords(data);
  return (
    <div className="flex flex-col gap-1">
      <p data-testid="documents-totals" className="text-sm">
        {totalsWords(data.totals)}
      </p>
      {meaning && (
        <p data-testid="documents-meaning-totals" className="text-sm">
          {meaning}
        </p>
      )}
      <ul data-testid="documents-caps" className="flex flex-col gap-0.5 text-xs text-muted">
        {capsWords(data).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}
