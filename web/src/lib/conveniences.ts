import type { Chat, ChatDetail } from "./types";
import { answerParts, statusWords } from "./words";

const DRAFT_PREFIX = "workbench-draft:";
const draftKey = (person: string, chat: string) =>
  `${DRAFT_PREFIX}${JSON.stringify([person, chat])}`;

/** Draft text stays in this tab, scoped to the signed-in person and chat. */
export function readDraft(person: string, chat: string): string {
  try {
    return sessionStorage.getItem(draftKey(person, chat)) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(person: string, chat: string, text: string): boolean {
  try {
    if (text) sessionStorage.setItem(draftKey(person, chat), text);
    else sessionStorage.removeItem(draftKey(person, chat));
    return true;
  } catch {
    return false;
  }
}

export function clearDrafts(): void {
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(DRAFT_PREFIX)) sessionStorage.removeItem(key);
    }
  } catch {
    // Storage may be disabled by the browser.
  }
}

/** Calendar boundaries, including daylight-saving days, in the reader's timezone. */
export function chatGroups(chats: Chat[], query: string, now = new Date()) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const week = new Date(today);
  week.setDate(today.getDate() - 7);
  const groups = ["Today", "Yesterday", "Previous 7 days", "Older"].map((label) => ({
    label,
    chats: [] as Chat[],
  }));
  const needle = query.trim().toLocaleLowerCase();
  for (const chat of [...chats].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (!chat.title.toLocaleLowerCase().includes(needle)) continue;
    const time = chat.updatedAt * 1000;
    const index = time >= +today ? 0 : time >= +yesterday ? 1 : time >= +week ? 2 : 3;
    groups[index]!.chats.push(chat);
  }
  return groups.filter((group) => group.chats.length > 0);
}

function oneLine(text: string): string {
  return text.replace(/[\r\n]+/g, " ");
}

function literal(text: string): string {
  const fence = "`".repeat(Math.max(3, ...[...text.matchAll(/`+/g)].map((m) => m[0].length + 1)));
  return `${fence}\n${text}\n${fence}`;
}

/** A local text snapshot; file contents and app credentials are never fetched. */
export function chatMarkdown(detail: ChatDetail): string {
  const lines = [
    `# ${oneLine(detail.chat.title)}`,
    "Exported from Workbench. Attachment contents are not included.",
  ];
  for (const message of detail.messages) {
    lines.push(`## ${message.role === "user" ? "You" : "Assistant"}`);
    lines.push(new Date(message.createdAt * 1000).toISOString());
    if (message.model) lines.push(`Model: ${oneLine(message.model)}`);
    if (message.role === "assistant") {
      if (message.reasoning) lines.push("### Reasoning", message.reasoning);
      const { draft, answer } = answerParts(message);
      if (draft) lines.push("### Written before using tools", draft, "### Answer");
      if (answer) lines.push(answer);
      for (const round of message.toolRounds ?? []) {
        for (const call of round.calls) {
          lines.push(
            `### Tool: ${oneLine(call.serverName)} / ${oneLine(call.tool)} (${call.status})`,
          );
          lines.push(literal(JSON.stringify(call.arguments, null, 2)));
          if (call.result !== null) lines.push(literal(call.result));
        }
      }
      if (message.sources.length) {
        lines.push(
          "### Sources",
          ...message.sources.map((s) => `${oneLine(s.title)}: ${oneLine(s.url)}`),
        );
      }
      if (message.status === "running") lines.push("Answer still in progress when exported.");
      const status = statusWords(message);
      if (status) lines.push(status);
    } else {
      lines.push(message.content);
    }
    if (message.files?.length) {
      lines.push("Attachments: " + message.files.map((f) => oneLine(f.name)).join(", "));
    }
  }
  return lines.join("\n\n") + "\n";
}

export function exportChat(detail: ChatDetail): void {
  const blob = new Blob([chatMarkdown(detail)], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const name = detail.chat.title
    .replace(/[^\p{L}\p{N} _-]/gu, "")
    .trim()
    .slice(0, 80);
  link.href = url;
  link.download = `Workbench-${name || "chat"}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
