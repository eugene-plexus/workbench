import { useState } from "react";
import { patch } from "../lib/api";
import { fetchProjects, problemWords } from "../lib/projects";
import type { DocumentFolder, Project } from "../lib/types";

export function FolderPlace({
  folder,
  onChanged,
}: {
  folder: DocumentFolder;
  onChanged: () => void;
}) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [choice, setChoice] = useState(folder.projectId ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function open() {
    try {
      setProjects(await fetchProjects());
    } catch (error) {
      setProblem(problemWords(error));
    }
  }
  async function move() {
    setBusy(true);
    setProblem(null);
    try {
      await patch(`/api/documents/folders/${encodeURIComponent(folder.id)}`, {
        projectId: choice || null,
      });
      setProjects(null);
      onChanged();
    } catch (error) {
      setProblem(problemWords(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-2">
      {!projects ? (
        <button
          type="button"
          data-testid="folder-move-open"
          className="btn self-start"
          onClick={() => void open()}
        >
          Move to project
        </button>
      ) : (
        <div className="flex flex-wrap gap-2">
          <label>
            Keep folder in{" "}
            <select
              data-testid="folder-move-place"
              className="control"
              value={choice}
              disabled={busy}
              onChange={(e) => setChoice(e.target.value)}
            >
              <option value="">My documents</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            data-testid="folder-move-save"
            className="btn"
            disabled={busy}
            onClick={() => void move()}
          >
            Move folder
          </button>
          <button
            type="button"
            data-testid="folder-move-cancel"
            className="btn"
            disabled={busy}
            onClick={() => setProjects(null)}
          >
            Cancel
          </button>
        </div>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
