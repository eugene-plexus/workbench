import { useState } from "react";

import { post } from "../lib/api";
import type { SiteLinking } from "../lib/types";
import { CopyButton } from "./CopyButton";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Whose account a person's calls run as on one machine, and how to change
 * that: link their own account at the machine, or take the link away. Shown
 * only when Eugene says anything (it says nothing of Eugene's own owner, who
 * links no account). */
export function SiteLinkNote({
  site,
  label,
  linking,
  onChanged,
}: {
  site: string;
  label: string;
  linking: SiteLinking;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const { linked, account, linkPage } = linking;
  if (linked === undefined && !account && !linkPage) return null;
  return (
    <div className="flex flex-col gap-1 text-sm" data-testid={`site-link-${site}`}>
      {account && <p>Runs as {account}</p>}
      {linked === false && linkPage && (
        <>
          <p>
            To work as yourself there, link your own account: on {label}, open {linkPage} and sign
            in. Until then your calls run as the machine&apos;s owner, inside the folders shared
            with you.
          </p>
          <CopyButton text={linkPage} label={`the link page for ${label}`} />
        </>
      )}
      {linked === true && (
        <button
          type="button"
          disabled={busy}
          className="self-start text-error"
          onClick={async () => {
            setBusy(true);
            setProblem(null);
            try {
              await post(`/api/job-sites/${encodeURIComponent(site)}/links/remove`, {});
              onChanged();
            } catch (error) {
              setProblem(problemOf(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          Remove my link on {label}
        </button>
      )}
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
    </div>
  );
}
