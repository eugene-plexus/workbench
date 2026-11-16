/**
 * A project someone shared with the person (row 47): who shared it and what the person may do,
 * their own chats in it (nobody else's), its instructions and pinned notes, read only, and its
 * documents, which someone who may add to it adds to.
 */

import { Pin, Users } from "lucide-react";
import type { ReactNode } from "react";

import { plural } from "../lib/memory";
import type { ProjectDetail } from "../lib/types";
import { CollectionPanel } from "./TeamCollections";

export function SharedProject({
  project,
  header,
  onOpenChat,
  onChanged,
}: {
  project: ProjectDetail;
  header: ReactNode;
  onOpenChat: (id: string) => void;
  onChanged: () => void;
}) {
  const shared = project.shared;
  if (!shared) return null;
  return (
    <section aria-label="Project" className="page" data-testid="project-page">
      <div className="page-col">
        {header}
        <p className="notice" data-testid="project-shared-by">
          <Users size={16} aria-hidden />
          Shared by {shared.owner}.{" "}
          {shared.role === "contribute" ? "You can read it and add documents." : "You can read it."}{" "}
          Your chats in it are yours alone.
        </p>
        <section className="section" data-testid="project-chats">
          <h2 className="title-section">Your chats</h2>
          {project.chatList.length === 0 ? (
            <p className="empty">No chats of yours in this project yet.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {project.chatList.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={() => onOpenChat(chat.id)}
                  >
                    {chat.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="section" data-testid="project-instructions">
          <h2 className="title-section">Instructions and pinned notes</h2>
          {(project.instructions ?? []).length === 0 ? (
            <p className="empty">None.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {(project.instructions ?? []).map((note) => (
                <li key={note.id} className="flex items-start gap-2">
                  <Pin size={14} aria-hidden className="mt-1 shrink-0 text-accent" />
                  {note.text}
                </li>
              ))}
            </ul>
          )}
          <p className="help">
            Every chat in this project is given these. Only {shared.owner} changes them.
          </p>
        </section>
        <section className="section" data-testid="project-documents">
          <h2 className="title-section">Documents</h2>
          <p className="help">
            {plural(project.documents, "document")}. Chats in this project search them when
            Documents is on in the chat.
          </p>
          {project.collectionId && (
            <CollectionPanel id={project.collectionId} documentsOnly onChanged={onChanged} />
          )}
        </section>
      </div>
    </section>
  );
}
