import { useEffect, useState } from "react";
import { api, post } from "./api";

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type ObjectValue = { [key: string]: Json };
export interface Surface {
  id: string;
  messageId: string;
  protocol: string;
  sourceName?: string | null;
  version: string;
  revision: number;
  state: string;
  actionable: boolean;
  expiresAt: number;
  view: ObjectValue;
  fallback: string;
}
export interface Action {
  id: string;
  surfaceId: string;
  revision: number;
  name: string;
  arguments: ObjectValue;
  status: string;
  result: ObjectValue | null;
  error: string | null;
}
export interface Snapshot {
  cursor: number;
  surfaces: Surface[];
  actions: Action[];
  readOnly: boolean;
}
export const interactionsPath = (chat: string) =>
  `/api/v1/chats/${encodeURIComponent(chat)}/interactions`;
export const surfacePath = (chat: string, surface: Surface) =>
  `${interactionsPath(chat)}/surfaces/${encodeURIComponent(surface.id)}`;
export const words = (error: unknown) => (error instanceof Error ? error.message : String(error));
export const object = (value: Json | undefined): ObjectValue =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};

/** Serial polling reads whole committed projections; failure unmounts executable views. */
export function useInteractions(chat: string, via: string | undefined) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generation, refresh] = useState(0);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const next = await api<Snapshot>(
          `${interactionsPath(chat)}${via ? `?via=${encodeURIComponent(via)}` : ""}`,
        );
        if (!Array.isArray(next?.surfaces) || !Array.isArray(next?.actions)) {
          throw new Error("Workbench returned an invalid interaction snapshot.");
        }
        if (live) {
          setSnapshot(next);
          setError(null);
        }
      } catch (failure) {
        if (live) {
          setSnapshot(null);
          setError(words(failure));
        }
      }
      if (live) timer = setTimeout(() => void read(), 1000);
    };
    void read();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [chat, via, generation]);
  return { snapshot, error, refresh: () => refresh((n) => n + 1) };
}

export const propose = (
  chat: string,
  surface: Surface,
  name: string,
  args: ObjectValue,
  id: string = crypto.randomUUID(),
) =>
  post<Action>(`${surfacePath(chat, surface)}/actions`, {
    id,
    revision: surface.revision,
    name,
    arguments: args,
  });

/** Same four pure functions and JSON pointers as the validated forms-v1 backend. */
export function resolve(value: Json | undefined, data: Json, budget = { left: 1024 }): Json {
  if (--budget.left < 0) throw new Error("This form is too complex.");
  if (Array.isArray(value)) return value.map((v) => resolve(v, data, budget));
  if (value === undefined) return null;
  if (value === null || typeof value !== "object") return value;
  if (Object.keys(value).length === 1 && typeof value.path === "string") {
    let node: Json = data;
    for (const part of value.path
      .split("/")
      .slice(1)
      .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"))) {
      if (node === null || typeof node !== "object" || !Object.hasOwn(node, part)) return null;
      node = (node as ObjectValue)[part] ?? null;
    }
    return node;
  }
  if (typeof value.call !== "string") return value;
  const args = object(value.args);
  const input = resolve(args.value, data, budget);
  if (value.call === "required")
    return (
      input !== null &&
      input !== "" &&
      !(typeof input === "object" && Object.keys(input).length === 0)
    );
  if (value.call === "not") {
    if (typeof input !== "boolean") throw new Error("Expected a boolean form value.");
    return !input;
  }
  if (value.call === "and" || value.call === "or") {
    const values = resolve(args.values, data, budget);
    if (!Array.isArray(values) || values.some((v) => typeof v !== "boolean"))
      throw new Error("Expected boolean form values.");
    return value.call === "and" ? values.every((v) => v) : values.some((v) => v);
  }
  throw new Error("This form uses an unsupported function.");
}
