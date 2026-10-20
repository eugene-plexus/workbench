"""The memory write policy (MV3b, design section 32): what a model may write to a person's
memory, checked in code where the note is written, never as words to the model.

Pure, like `memory.py`. A policy is a value per row, each row's values ordered from strictest
to loosest; the presets are Strict, Standard (the default) and Open, and Custom sets each row.
The owner sets the install's policy and whether people may loosen it; a person may always
choose a stricter one, row by row (Troy, 2026-10-19: "someone who knows what they're doing must
have final control").
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

STRICT, STANDARD, OPEN, CUSTOM = "strict", "standard", "open", "custom"
PRESETS_NAMES = (STRICT, STANDARD, OPEN)
NAMES = (*PRESETS_NAMES, CUSTOM)
#: Whether people may choose a policy looser than the owner's.
STRICTER_ONLY, ANY = "stricter", "any"

#: Each row's values, strictest first.
ROWS: dict[str, tuple[str, ...]] = {
    # What a note may be drawn from: the person's own words, or anything in the chat.
    "source": ("own", "any"),
    # Text shaped like a secret.
    "secrets": ("refuse", "allow"),
    # MR8's sensitive categories.
    "sensitive": ("refuse", "inbox", "keep"),
    # Web pages, tool results, document passages, past chats, attachments or pasted material in
    # the turns read.
    "untrusted": ("drop", "inbox", "keep"),
    # Instruction and procedure kinds from a model.
    "instructions": ("never", "inbox", "keep"),
    # Keep automatically.
    "keepAuto": ("off", "on"),
    # Suggestions from connected apps (MCP).
    "connections": ("off", "inbox"),
    # Model-kept notes stay on the machines until the person has seen them (call 12).
    "unseenLocal": ("yes", "no"),
}

PRESETS: dict[str, dict[str, str]] = {
    STRICT: {
        "source": "own",
        "secrets": "refuse",
        "sensitive": "refuse",
        "untrusted": "drop",
        "instructions": "never",
        "keepAuto": "off",
        "connections": "off",
        "unseenLocal": "yes",
    },
    STANDARD: {
        "source": "own",
        "secrets": "refuse",
        "sensitive": "refuse",
        "untrusted": "inbox",
        "instructions": "inbox",
        "keepAuto": "on",
        "connections": "inbox",
        "unseenLocal": "yes",
    },
    OPEN: {
        "source": "any",
        "secrets": "refuse",
        "sensitive": "inbox",
        "untrusted": "keep",
        "instructions": "keep",
        "keepAuto": "on",
        "connections": "inbox",
        "unseenLocal": "no",
    },
}

#: What each row allows beyond Standard, as the Memory screen says it (settings never lie).
LOOSER_WORDS: dict[tuple[str, str], str] = {
    ("source", "any"): "Models may draw notes from anything in a chat, answers and tool results "
    "included, not only your own words.",
    ("secrets", "allow"): "Notes shaped like a password or key are not refused.",
    ("sensitive", "inbox"): "Notes about health, religion, politics, sexuality, money or contact "
    "details go to Suggested notes instead of being refused.",
    ("sensitive", "keep"): "Notes about health, religion, politics, sexuality, money or contact "
    "details may be kept.",
    ("untrusted", "keep"): "Models may keep notes drawn from web pages, tool results, documents "
    "and pasted text.",
    ("instructions", "keep"): "Models may keep instructions and procedures by themselves.",
    ("unseenLocal", "no"): "Notes a model kept may go to outside models and connected apps "
    "before you have seen them.",
}
#: And what each row holds back beyond Standard.
STRICTER_WORDS: dict[tuple[str, str], str] = {
    ("untrusted", "drop"): "A note drawn while web pages, tool results, documents or pasted "
    "text are in the chat is dropped, not suggested.",
    ("instructions", "never"): "Models never suggest instructions or procedures.",
    ("keepAuto", "off"): "No model keeps notes by itself.",
    ("connections", "off"): "Connected apps cannot suggest notes.",
}


@dataclass(frozen=True)
class Policy:
    """A policy in effect: its name (a preset's, or `custom`) and its rows."""

    name: str
    rows: dict[str, str]

    def get(self, row: str) -> str:
        return self.rows[row]

    def to_json(self) -> str:
        """What a note records of the policy it was written under."""
        return json.dumps({"name": self.name, **self.rows}, sort_keys=True)

    def view(self) -> dict[str, Any]:
        standard = PRESETS[STANDARD]
        return {
            "name": self.name,
            "rows": dict(self.rows),
            "allows": [
                LOOSER_WORDS[(k, v)]
                for k, v in self.rows.items()
                if v != standard[k] and (k, v) in LOOSER_WORDS
            ],
            "holdsBack": [
                STRICTER_WORDS[(k, v)]
                for k, v in self.rows.items()
                if v != standard[k] and (k, v) in STRICTER_WORDS
            ],
        }


def preset(name: str) -> Policy:
    return Policy(name, dict(PRESETS[name]))


def custom(rows: dict[str, Any]) -> Policy:
    """A custom policy: each row as given when it is one of the row's values, else Standard's
    (a damaged setting never loosens anything)."""
    standard = PRESETS[STANDARD]
    return Policy(
        CUSTOM,
        {
            k: (str(rows.get(k)) if rows.get(k) in values else standard[k])
            for k, values in ROWS.items()
        },
    )


def _rank(row: str, value: str) -> int:
    return ROWS[row].index(value)


def stricter(a: Policy, b: Policy) -> Policy:
    """Row by row, the stricter of two policies; named for `a` when it is as strict as `b`
    everywhere, for `b` when `b` is, else `custom`."""
    rows = {k: a.rows[k] if _rank(k, a.rows[k]) <= _rank(k, b.rows[k]) else b.rows[k] for k in ROWS}
    name = a.name if rows == a.rows else b.name if rows == b.rows else CUSTOM
    return Policy(name, rows)


def is_stricter_or_equal(a: Policy, b: Policy) -> bool:
    """Whether `a` is at least as strict as `b` on every row."""
    return all(_rank(k, a.rows[k]) <= _rank(k, b.rows[k]) for k in ROWS)


def effective(install: Policy, people: str, personal: str | None) -> tuple[Policy, str]:
    """The policy in effect for a person, and whose word it is (`owner`, `you`). With
    `stricter`, the person's preset counts only where it is stricter, row by row; with `any`,
    their preset is theirs."""
    if personal not in PRESETS:
        return install, "owner"
    mine = preset(personal)
    if people == ANY:
        return mine, "you"
    combined = stricter(mine, install)
    return combined, "you" if combined.rows == mine.rows else "owner"


# --------------------------------------------------------------------------- #
# routing one proposed note
# --------------------------------------------------------------------------- #

KEEP, INBOX, DROP = "keep", "inbox", "drop"
_ORDER = (DROP, INBOX, KEEP)


def _least(*actions: str) -> str:
    return min(actions, key=_ORDER.index)


def route(
    policy: Policy,
    *,
    kind: str,
    sensitive: str | None,
    untrusted: bool,
    auto: bool,
    via: str = "model",
) -> tuple[str, str | None]:
    """Where a proposal that passed the content checks goes: `keep`, `inbox` or `drop`, and the
    row that sent it there (None when kept). `auto`: the model is set to Keep automatically for
    this person; `via`: `model` (the pass or `remember`) or `connection`."""
    if via == "connection":
        return (DROP, "connections") if policy.get("connections") == "off" else (INBOX, None)
    action, why = KEEP, None
    if kind in ("instruction", "procedure"):
        rule = policy.get("instructions")
        step = {"never": DROP, "inbox": INBOX, "keep": KEEP}[rule]
        if _ORDER.index(step) < _ORDER.index(action):
            action, why = step, "instructions"
    if sensitive is not None:
        step = {"refuse": DROP, "inbox": INBOX, "keep": KEEP}[policy.get("sensitive")]
        if _ORDER.index(step) < _ORDER.index(action):
            action, why = step, "sensitive"
    if untrusted:
        step = {"drop": DROP, "inbox": INBOX, "keep": KEEP}[policy.get("untrusted")]
        if _ORDER.index(step) < _ORDER.index(action):
            action, why = step, "untrusted"
    if action == KEEP and not (auto and policy.get("keepAuto") == "on"):
        action, why = INBOX, ("keepAuto" if auto else "mode")
    return action, why
