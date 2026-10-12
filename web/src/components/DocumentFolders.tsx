/**
 * The Folders section of the Documents screen (MR7): the folders the owner of this Workbench gave
 * the person, which they can point their Documents at. Workbench reads a folder's files, only
 * reading, into the person's own documents and keeps them in step: a file that is new is added,
 * one that was edited is read again (and its old words are gone at once), one that was deleted is
 * removed. A chat with Documents on finds them like any other document, and the Sources name the
 * file by its folder and path.
 *
 * Each folder is a card (FolderCard) with where it stands, what it holds, Sync now, its files,
 * what was left out and why, its rules and Remove folder. Below them, Add a folder (AddFolder),
 * and the limits stated plainly from the numbers in effect: no file watcher, a quick check that an
 * edit keeping a file's size and time slips past, folders on job sites that cannot be added, and a
 * first read of many files that takes a while. Uploads and folders are counted apart.
 *
 * What a person sees is the server's, as it came: folders are read again by themselves
 * (`useDocumentFolders`), each second while a check is under way or files are being read, so the
 * counts move.
 */

import { CircleAlert, Info, MessageSquarePlus } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { FOLDERS_INTRO, foldersTotals, limitsWords, READ_ONLY_WORDS } from "../lib/documentFolders";
import type { DocumentFolder, DocumentFolders as FoldersPage, Me } from "../lib/types";
import { useServerNow } from "../lib/useServerNow";
import { AddFolder } from "./AddFolder";
import { FolderCard } from "./FolderCard";

export function DocumentFolders({
  folders,
  me,
  off,
  busy,
  askHere,
  onAsk,
  onTools,
}: {
  /** What `useDocumentFolders` gives. */
  folders: {
    data: FoldersPage | null;
    problem: string | null;
    reload: () => Promise<void>;
  };
  me?: Pick<Me, "owner"> | null;
  /** The owner has turned documents off: folders can be looked at and removed, nothing else. */
  off: boolean;
  /** Something else on the screen is being changed. */
  busy: boolean;
  /** Whether Ask about your documents is here: the person has files in folders and none of their
   * own, so the button that sits above their own files is not on the screen. */
  askHere: boolean;
  onAsk: () => void;
  /** Opens Toolbox · Tools, where the owner gives folders to people. */
  onTools?: () => void;
}) {
  const uid = useId();
  const { data, problem, reload } = folders;
  const now = useServerNow(10_000, data);
  const [focus, setFocus] = useState<{ folder: string | null } | null>(null);

  // After a folder is added, the keyboard goes to its card; after one is removed, to the heading of
  // this section, since what had it is gone.
  useEffect(() => {
    if (!focus) return;
    const target = focus.folder
      ? [...document.querySelectorAll<HTMLElement>("[data-folder-id]")]
          .find((card) => card.dataset.folderId === focus.folder)
          ?.querySelector<HTMLElement>("h3")
      : document.getElementById(`${uid}-folders`);
    if (!target && focus.folder) return;
    target?.focus();
    setFocus(null);
  }, [focus, data, uid]);

  const heading = (
    <h2 id={`${uid}-folders`} tabIndex={-1} className="title-section">
      Folders
    </h2>
  );

  if (!data) {
    return (
      <section aria-labelledby={`${uid}-folders`} data-testid="folders-section" className="section">
        {heading}
        {problem ? (
          <div role="alert" data-testid="folders-problem" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <div>
              <span className="notice-title">Could not read your folders</span>
              {problem}
              <div className="mt-2">
                <button
                  type="button"
                  data-testid="folders-retry"
                  className="btn"
                  onClick={() => void reload()}
                >
                  Try again
                </button>
              </div>
            </div>
          </div>
        ) : (
          <p role="status" className="text-sm text-muted">
            Reading your folders…
          </p>
        )}
      </section>
    );
  }

  const locked = off || !data.enabled;
  const list = data.folders;
  const owner = me?.owner ?? false;

  return (
    <section aria-labelledby={`${uid}-folders`} data-testid="folders-section" className="section">
      {heading}
      <p data-testid="folders-intro" className="help">
        {FOLDERS_INTRO} {READ_ONLY_WORDS}
      </p>
      {problem && (
        <div role="alert" data-testid="folders-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read your folders again</span>
            {problem}
            <div className="mt-2">
              <button
                type="button"
                data-testid="folders-retry"
                className="btn"
                onClick={() => void reload()}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {!data.available && (
        <p data-testid="folders-unavailable" className="notice">
          <Info size={16} aria-hidden />
          <span>
            <span className="notice-title">Folders cannot be used on this Workbench</span>
            {data.reason ?? "Workbench cannot read folders here."}
          </span>
        </p>
      )}
      {list.length > 0 && (
        <div className="flex flex-col gap-1">
          <p data-testid="folders-totals" className="text-sm">
            {foldersTotals(list)}
          </p>
          <p className="text-xs text-muted">
            These are counted apart from the files you add yourself.
          </p>
        </div>
      )}
      {askHere && (
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
      {list.length > 0 && (
        <ul data-testid="folders-list" className="flex flex-col gap-3">
          {list.map((folder: DocumentFolder) => (
            <FolderCard
              key={folder.id}
              folder={folder}
              limits={data.limits}
              now={now}
              locked={locked}
              disabled={busy}
              onChanged={() => void reload()}
              onRemoved={() => setFocus({ folder: null })}
            />
          ))}
        </ul>
      )}
      {!locked && data.available && (
        <AddFolder
          choices={data.grants}
          limits={data.limits}
          owner={owner}
          disabled={busy}
          onTools={onTools}
          onAdded={(added) => {
            setFocus({ folder: added.id });
            void reload();
          }}
        />
      )}
      {!locked && (
        <ul data-testid="folders-limits" className="list-disc space-y-1 pl-5 text-xs text-muted">
          {limitsWords(data.limits).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
