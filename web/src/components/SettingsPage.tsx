/**
 * The Settings page (MV4: app before config): every setting a person has in Workbench, apart from
 * the pages that hold their data. Memory's, then Documents', then the connections other apps have,
 * then downloading all of one's data, and for the owner, the people removed from Eugene (row 7).
 * A person in a permission group sees what their groups set; the owner sets each group's memory
 * server (row 70c).
 * Each section links to its page, and each page links here (to its section, by the address's
 * `#memory` or `#documents`).
 */

import { ArrowLeft, Settings as SettingsIcon } from "lucide-react";
import { useEffect } from "react";

import { Connections } from "./Connections";
import { DocumentsSettings } from "./DocumentsSettings";
import { GroupServers, YourGroups } from "./GroupsSettings";
import { MemorySettings } from "./MemorySettings";
import { RemovedPeople } from "./RemovedPeople";
import { YourData } from "./YourData";

export function SettingsPage({
  onClose,
  onOpenMemory,
  onOpenDocuments,
  owner = false,
}: {
  onClose: () => void;
  onOpenMemory: () => void;
  onOpenDocuments: () => void;
  /** The install's owner also manages people removed from Eugene (row 7). */
  owner?: boolean;
}) {
  // A page's own link names its section: it is brought into view once the page is drawn.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView?.({ block: "start" });
  }, []);

  return (
    <section aria-label="Settings" className="page" data-testid="settings-page">
      <div className="page-col">
        <header className="page-head">
          <h1 id="view-heading" tabIndex={-1} className="title-page">
            <SettingsIcon size={20} aria-hidden />
            Settings
          </h1>
          <button type="button" data-testid="settings-back" className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <p className="help">
          Your own settings in this Workbench. Each says what is in effect and whose choice it is.
        </p>
        <MemorySettings onOpenMemory={onOpenMemory} />
        <DocumentsSettings onOpenDocuments={onOpenDocuments} />
        <YourGroups />
        {owner && <GroupServers />}
        <div id="connections" className="scroll-mt-4">
          <Connections />
        </div>
        <YourData />
        {owner && <RemovedPeople />}
      </div>
    </section>
  );
}
