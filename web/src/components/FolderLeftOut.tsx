/**
 * What the last check of a folder left out, and why: each thing with its path and the server's own
 * reason, grouped by the rule or limit that left it out (ignored by a rule or a .gitignore file,
 * hidden, temporary, a link that is never followed, a file kept online by a cloud-sync program,
 * too deep, over a limit), and the folders that could not be looked into with what was seen. The
 * server names the first 200 of all of them, and counts every one, so a longer list says how many
 * are not named.
 *
 * Read when it is opened and again after each check, so it never says what an older check found.
 */

import { CircleAlert } from "lucide-react";
import { useId } from "react";

import { count } from "../lib/documents";
import { leftGroups, leftTotal } from "../lib/documentFolders";
import type { DocumentFolder } from "../lib/types";
import { useFolderDetail } from "../lib/useFolderDetail";

export function FolderLeftOut({ folder, again }: { folder: DocumentFolder; again: string }) {
  const uid = useId();
  const { detail, problem, reload } = useFolderDetail(folder.id, again);
  const groups = detail ? leftGroups(detail) : [];
  const total = leftTotal(groups);
  const named = groups.reduce((sum, group) => sum + group.items.length, 0);
  const unlisted = detail?.unlisted ?? [];
  const nothing = detail !== null && groups.length === 0 && unlisted.length === 0;

  return (
    <div
      id={`${uid}-left`}
      role="group"
      aria-label={`What was left out of ${folder.name}`}
      data-testid="folder-left"
      className="flex flex-col gap-3 rounded-plexus border border-line p-3"
    >
      {problem && (
        <div role="alert" data-testid="folder-left-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read what was left out</span>
            {problem}
            <div className="mt-2">
              <button
                type="button"
                data-testid="folder-left-retry"
                aria-label={`Try again to read what was left out of ${folder.name}`}
                className="btn"
                onClick={reload}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {!detail && !problem && (
        <p role="status" className="text-sm text-muted">
          Reading what was left out…
        </p>
      )}
      {nothing && (
        <p data-testid="folder-left-nothing" className="text-sm">
          Nothing was left out. Every name Workbench found was read or tried.
        </p>
      )}
      {groups.map((group) => (
        <section
          key={group.code}
          aria-labelledby={`${uid}-${group.code}`}
          data-testid="folder-left-group"
          data-code={group.code}
          className="flex flex-col gap-1.5"
        >
          <h4 id={`${uid}-${group.code}`} className="title-sub">
            {group.heading}{" "}
            <span data-testid="folder-left-total" className="font-normal tabular-nums text-muted">
              ({group.total.toLocaleString()})
            </span>
          </h4>
          {group.items.length > 0 && (
            <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
              {group.items.map((item) => (
                <li
                  key={`${item.kind}:${item.path}`}
                  data-testid="folder-left-item"
                  data-kind={item.kind}
                  className="flex flex-col gap-0.5 px-3 py-2"
                >
                  <p
                    dir="auto"
                    data-testid="folder-left-path"
                    className="text-sm font-medium wrap-anywhere"
                  >
                    {item.path}
                    {item.kind === "folder" && "/"}
                  </p>
                  <p data-testid="folder-left-reason" className="text-xs text-muted wrap-anywhere">
                    {item.reason}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {group.total > group.items.length && (
            <p className="text-xs text-muted">
              {group.items.length === 0
                ? `${count(group.total, "name")} not named here.`
                : `${group.items.length.toLocaleString()} of ${group.total.toLocaleString()} are named here.`}
            </p>
          )}
        </section>
      ))}
      {unlisted.length > 0 && (
        <section
          aria-labelledby={`${uid}-unlisted`}
          data-testid="folder-left-unlisted"
          className="flex flex-col gap-1.5"
        >
          <h4 id={`${uid}-unlisted`} className="title-sub">
            Could not be looked into{" "}
            <span className="font-normal tabular-nums text-muted">
              ({(folder.last?.unlistedTotal ?? unlisted.length).toLocaleString()})
            </span>
          </h4>
          <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
            {unlisted.map((item) => (
              <li
                key={item.path}
                data-testid="folder-left-unlisted-item"
                className="flex flex-col gap-0.5 px-3 py-2"
              >
                <p dir="auto" className="text-sm font-medium wrap-anywhere">
                  {item.path || "The folder itself"}
                </p>
                <p className="text-xs text-muted wrap-anywhere">
                  Workbench could not look in it ({item.cause}). Its files are as at the last check;
                  it looks again at the next.
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
      {detail && total > named && (
        <p data-testid="folder-left-more" className="text-xs text-muted">
          Workbench names the first {named.toLocaleString()} of the {total.toLocaleString()} things
          it left out.
        </p>
      )}
    </div>
  );
}
