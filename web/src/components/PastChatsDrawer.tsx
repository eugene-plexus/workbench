/**
 * Past chats in a chat's Memory drawer (MV2): the person's switch, Use past chats, whether this
 * chat's model may receive them, and which exchanges the turn being read was sent.
 */

import { useId, useState } from "react";

import { plural, tokenWords } from "../lib/memory";
import { chatAt, openChatAt, type PastChatsState, type PastChatsTurn } from "../lib/pastChats";

export function PastChatsSection({
  state,
  turn,
  busy,
  onSwitch,
}: {
  state: PastChatsState;
  turn: PastChatsTurn | null | undefined;
  busy: boolean;
  onSwitch: (on: boolean) => void;
}) {
  const uid = useId();
  const [wanted, setWanted] = useState<boolean | null>(null);
  return (
    <section
      aria-labelledby={`${uid}-past`}
      data-testid="past-chats-drawer"
      className="flex flex-col gap-1"
    >
      <h3 id={`${uid}-past`} className="eyebrow text-muted">
        Past chats
      </h3>
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-switch`}>
          <input
            id={`${uid}-switch`}
            type="checkbox"
            role="switch"
            data-testid="past-chats-switch"
            aria-describedby={`${uid}-help`}
            checked={busy && wanted !== null ? wanted : state.on}
            disabled={busy}
            onChange={(event) => {
              setWanted(event.target.checked);
              onSwitch(event.target.checked);
            }}
          />
          Use past chats in this chat
        </label>
        <p id={`${uid}-help`} data-testid="past-chats-effective" className="help">
          {!state.allowed
            ? [state.reason, state.hint].filter(Boolean).join(" ")
            : state.on
              ? `Each message is sent what your other chats said that bears on it, up to ${(state.budget ?? 0).toLocaleString()} tokens.`
              : "Off: this chat is sent nothing from your other chats."}{" "}
          {state.allowed &&
            (state.searchTool
              ? "This model can search your past chats."
              : "This model cannot search your past chats.")}
        </p>
      </div>
      {turn && (
        <>
          <p data-testid="past-chats-turn" className="help">
            {turn.exchanges.length === 0
              ? "No past chat was sent with this turn."
              : `${plural(turn.exchanges.length, "past exchange")} sent. ${tokenWords(turn.tokens, turn.budget)}.`}
            {turn.forgotten ? ` ${plural(turn.forgotten, "chat")} deleted since.` : ""}
            {turn.why ? ` ${turn.why}` : ""}
          </p>
          {turn.exchanges.length > 0 && (
            <ul data-testid="past-chats-turn-list" className="divide-y divide-line text-sm">
              {turn.exchanges.map((one) => (
                <li key={one.messageId} className="flex flex-wrap items-baseline gap-x-2 py-2">
                  <a
                    href={chatAt(one.chatId, one.messageId)}
                    onClick={(event) => {
                      if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                      event.preventDefault();
                      openChatAt(one.chatId, one.messageId);
                    }}
                    className="text-accent underline-offset-2 hover:underline"
                  >
                    <span dir="auto">{one.title}</span>
                  </a>
                  <span className="text-xs text-muted">{one.date}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
