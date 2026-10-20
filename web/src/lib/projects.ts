/**
 * Projects (MV7): the person's named groups of chats and notes, and the words and
 * fetchers around them. A refusal is shown in the server's own words.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { Chat, Project } from "./types";

/** What went wrong, in the server's words when it gave some. */
export function problemWords(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The address of a project's page. */
export const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`;

/** The person's projects, by name. */
export async function fetchProjects(): Promise<Project[]> {
  const got = await api<{ projects?: Project[] }>("/api/projects");
  return Array.isArray(got?.projects) ? got.projects : [];
}

/** A project's chats, newest first. */
export function chatsIn(chats: Chat[], projectId: string): Chat[] {
  return chats
    .filter((chat) => chat.projectId === projectId)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** The words "In {name}" for a note or chat kept to a project, or null for none. */
export function placeName(projects: Project[], projectId: string | null | undefined) {
  if (!projectId) return null;
  return projects.find((p) => p.id === projectId)?.name ?? null;
}

/**
 * The person's projects, read when `enabled` and again on `reload`. Null until the first
 * answer; a failure is kept in `problem`, and the last list stays.
 */
export function useProjects(enabled = true) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const reload = useCallback(async () => {
    try {
      const next = await fetchProjects();
      if (!live.current) return;
      setProjects(next);
      setProblem(null);
    } catch (failure) {
      if (live.current) setProblem(problemWords(failure));
    }
  }, []);
  useEffect(() => {
    if (enabled) void reload();
  }, [enabled, reload]);
  return { projects, problem, reload };
}
