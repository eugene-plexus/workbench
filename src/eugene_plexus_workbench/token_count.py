"""How many tokens a text is, as far as Workbench can tell without a tokenizer.

A deliberate overestimate: `ceil(characters / 3.0)`, and one token for each
CJK character, which a Latin rule would badly undercount. Eugene has no
tokenizer by design, and an over-long prompt is the gateway's 400, which is
the failure this exists to avoid. It is the same rule `memory.py` spends its
budget with (a test holds the two together), written to run at C speed
because a document is millions of characters and a note is hundreds.
"""

from __future__ import annotations

import math
import re

CHARS_PER_TOKEN = 3.0

#: Hiragana, Katakana, CJK extension A and the unified block, Hangul syllables,
#: compatibility ideographs, full-width forms, and the extensions from B on.
_CJK = re.compile(
    "[\U00003040-\U000030ff\U00003400-\U00004dbf\U00004e00-\U00009fff\U0000ac00-\U0000d7af"
    "\U0000f900-\U0000faff\U0000ff00-\U0000ffef\U00020000-\U0002ffff]"
)


def counts(text: str) -> tuple[int, int]:
    """(characters that are not CJK, CJK characters)."""
    cjk = len(_CJK.findall(text))
    return len(text) - cjk, cjk


def of(other: int, cjk: int) -> int:
    """The tokens for these counts."""
    return math.ceil(other / CHARS_PER_TOKEN) + cjk


def estimate(text: str) -> int:
    return of(*counts(text))
