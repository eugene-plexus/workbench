"""Which models learn (MV3, design sections 28-29): Eugene's recommendation for a model, from a
measurement, and what a model does for a person once their choice, the owner's and that
recommendation meet.

Pure, like `memory.py`: the store reads choices and the install's measurements, the routes show
them. **The person decides** (Troy, 2026-10-19): a recommendation is advice with its reason,
never a lock; any model may be set to any mode, measured or not. Keep automatically is never a
default.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from . import config, memory_measure

OFF, SUGGEST, AUTO = "off", "suggest", "auto"
MODES = (OFF, SUGGEST, AUTO)
#: Whose word a mode is: the person's (for this model, or for models they have not set), the
#: owner's, Eugene's measurement, or nobody's.
YOU, OWNER, MEASURED, DEFAULT = "you", "owner", "measured", "default"
#: Recommendation levels.
KEEP, SUGGEST_ONLY, NOT_RECOMMENDED, UNMEASURED = "keep", "suggest", "no", "unmeasured"

SHIPPED_FILE = "memory_measured.json"


@dataclass(frozen=True)
class Measurement:
    """One measurement of a model: Eugene's (shipped, matched by pattern) or the install's own
    (Measure this model, by exact id)."""

    name: str
    by: str  # "eugene" or "install"
    measured_on: str  # YYYY-MM-DD
    result: dict[str, Any]

    @property
    def verdict(self) -> dict[str, Any]:
        return self.result.get("verdict") or {}


@functools.cache
def _shipped() -> tuple[tuple[re.Pattern[str], Measurement], ...]:
    """Eugene's measurements, written by `specs/scripts/m-memory-summarize.py --ship`. A
    fine-tune that removes refusals ("abliterated", "uncensored") is a different model and
    never matches."""
    try:
        raw = json.loads(resources.files(__package__).joinpath(SHIPPED_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return ()
    out = []
    for entry in raw.get("models") or []:
        try:
            pattern = re.compile(
                r"^(?!.*(?:abliterat|uncensor)).*" + entry["pattern"], re.IGNORECASE
            )
        except (KeyError, re.error):
            continue
        out.append(
            (
                pattern,
                Measurement(
                    name=str(entry.get("name") or entry["pattern"]),
                    by="eugene",
                    measured_on=str(entry.get("measuredOn") or ""),
                    result=entry,
                ),
            )
        )
    return tuple(out)


def shipped_names() -> list[str]:
    """The models Eugene measured, by name."""
    return [m.name for _, m in _shipped()]


def measurement_for(
    model: str | None,
    local: dict[str, dict[str, Any]] | None = None,
    *,
    for_default: bool = False,
) -> Measurement | None:
    """The newest measurement of `model`: the install's own run of this exact id when there is
    one (it measured this build, on these machines), else Eugene's shipped one. With
    `for_default` (what a model does for someone who has not chosen), only the owner's own run
    counts beside Eugene's: another person's is advice, not the install's default."""
    if not model:
        return None
    own = (local or {}).get(model)
    if (
        own is not None
        and isinstance(own.get("result"), dict)
        and (not for_default or own["result"].get("startedByOwner") is True)
    ):
        when = dt.datetime.fromtimestamp(float(own.get("measuredAt") or 0), dt.UTC)
        return Measurement(model, "install", when.date().isoformat(), own["result"])
    return next((m for pattern, m in _shipped() if pattern.search(model)), None)


def recommended_for_suggest(measured: Measurement | None) -> bool:
    return bool(measured and measured.verdict.get("suggest"))


def tags_in_request(measured: Measurement | None) -> bool:
    """MV1-C1: the person's tag list goes in a model's request only when its measurement with a
    list met the bar its measurement without one met."""
    return bool(measured and measured.verdict.get("tags"))


def _pct(share: float) -> str:
    return f"{100 * share:.1f}".removesuffix(".0") + "%"


def recommendation(measured: Measurement | None) -> dict[str, Any]:
    """Eugene's recommendation and its reason, in research 7.5's three wordings."""
    if measured is None:
        return {"level": UNMEASURED, "text": "Not measured yet.", "measuredOn": None, "by": None}
    verdict = measured.verdict
    result = measured.result
    # The numbers of the sets the verdict was judged on: every set for Keep automatically,
    # MR8's for Suggest (call MV3-C1).
    judged = (
        result.get("pass") if verdict.get("keep") else result.get("passMr8") or result.get("pass")
    )
    numbers = memory_measure.headline(judged) if judged else {"false": 0.0, "recall": 0.0}
    when = f"measured on {measured.measured_on}" if measured.measured_on else "measured"
    said = f"{when}, false memories {_pct(numbers['false'])}, recall {_pct(numbers['recall'])}"
    base = {
        "measuredOn": measured.measured_on or None,
        "by": measured.by,
        "numbers": numbers,
        # An install's run by someone other than the owner is advice: it sets no default.
        "advice": measured.by == "install" and measured.result.get("startedByOwner") is not True,
    }
    if verdict.get("keep"):
        return {**base, "level": KEEP, "text": f"Recommended for Keep automatically: {said}."}
    why = (verdict.get("why") or {}).get("keep")
    if verdict.get("suggest"):
        return {
            **base,
            "level": SUGGEST_ONLY,
            "text": f"Recommended for Suggest: {said}. Not for Keep automatically: {why}.",
        }
    miss = (verdict.get("why") or {}).get("suggest") or "it missed the bar"
    return {
        **base,
        "level": NOT_RECOMMENDED,
        "text": f"Not recommended: {when}, {miss}.",
    }


def effective(
    install: config.MemorySettings,
    prefs: dict[str, Any],
    model: str | None,
    *,
    choice: str | None = None,
    measured: Measurement | None = None,
    keep_allowed: bool = False,
) -> tuple[str, str]:
    """What `model` does for this person: `(off | suggest | auto, whose word)`, the first rule
    that applies (design section 29). Keep automatically comes to Suggest while the person's
    write policy does not allow it (`keep_allowed`, MV3b)."""
    if not install.enabled or install.suggestions == "off":
        return OFF, OWNER
    if not prefs["enabled"]:
        return OFF, YOU
    if choice in MODES:
        return (SUGGEST if choice == AUTO and not keep_allowed else choice), YOU
    if prefs.get("learn_set"):
        return (OFF if prefs["learn"] == OFF else SUGGEST), YOU
    if install.suggestions == "measured" and recommended_for_suggest(measured):
        return SUGGEST, MEASURED
    return OFF, DEFAULT


def proposes_merges(chosen: str | None, mode: str, override: bool | None) -> bool:
    """Troy's MEM-MERGE: on for Keep automatically, with an explicit override."""
    return mode != OFF and (override if override is not None else chosen == AUTO)
