import { useDocumentFolders } from "../lib/useDocumentFolders";
import { DocumentFolders } from "./DocumentFolders";

export function ProjectFolders({ projectId }: { projectId: string }) {
  const folders = useDocumentFolders(projectId);
  return (
    <DocumentFolders
      folders={folders}
      projectId={projectId}
      off={folders.data?.enabled === false}
      busy={false}
      askHere={false}
      onAsk={() => undefined}
    />
  );
}
