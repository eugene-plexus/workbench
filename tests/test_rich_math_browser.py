"""Formulas and footnotes in Chrome, under the page's own headers
(workbench-rich-content-roadmap.md RC1). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

# The answers below write Greek letters as models do.
# ruff: noqa: RUF001

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench.web import CSP

from . import conftest
from .conftest import World

# A technical answer: inline and display formulas in each of the four
# delimiters, a matrix, Unicode, formulas in a list and a table, a ```math
# fence, money, an escaped dollar, code with dollars, two formulas that cannot
# be shown, and two footnotes (one with a formula in it).
MATH = r"""## Heat through a wall

The flux is $q = -k \nabla T$, and through a slab \(q = k \frac{\Delta T}{L}\).[^1]

$$
\begin{pmatrix} a & b \\ c & d \end{pmatrix}
\begin{pmatrix} x \\ y \end{pmatrix}
=
\begin{pmatrix} ax + by \\ cx + dy \end{pmatrix}
$$

Over a surface and a time, \[ Q = \int_0^t \oint_S q \cdot dA \, dt \] and in Greek letters $α + β = γ$.

1. First, $E = mc^2$.
2. Then the kinetic energy $$E_k = \tfrac{1}{2} m v^2$$ follows.

| Quantity | Formula |
| --- | --- |
| Area | $\pi r^2$ |
| Volume | $\frac{4}{3}\pi r^3$ |

It costs $5 and $10, or \$20 with tax.[^2]

In code, `price = $5` and:

```python
total = "$5" + "$10"
```

A slip, $\frac{1}{$, stays readable, and so does $\nope{x}$.

```math
\sum_{n=1}^{\infty} \frac{1}{n^2} = \frac{\pi^2}{6}
```

A polynomial wider than a phone:

$$
f(x) = a_0 + a_1 x + a_2 x^2 + a_3 x^3 + a_4 x^4 + a_5 x^5 + a_6 x^6 + a_7 x^7 + a_8 x^8 + a_9 x^9 + a_{10} x^{10}
$$

[^1]: Fourier's law, for a slab of thickness $L$.
[^2]: Prices before tax."""

#: The formulas MATH draws, and the two it keeps as their source.
MATH_DRAWN = 12
MATH_FAILED = [r"\frac{1}{", r"\nope{x}"]

# The second answer's first version, with a footnote numbered as the first
# answer's.
FIRST_TRY = r"""The first try at it, with $\sqrt{2} \approx 1.414$.[^1]

[^1]: The first try's own note."""

# Its second version (Try again), the one shown: a formula that takes KaTeX
# a minute (36 s for this one, measured), one that would pull its line across
# the page, two that are fine, and a footnote numbered as the others.
HEAVY = (
    r"\def\a{" + "x" * 400 + r"}"
    r"\def\b{\a\a\a\a\a\a\a\a\a}\def\c{\b\b\b\b\b\b\b\b\b}\def\d{\c\c\c\c\c\c\c\c\c}\d"
)
SECOND_TRY = rf"""Two more, and two that cannot be shown.[^1]

This one would take a minute to lay out: ${HEAVY}$.

This one would pull the line across the page: $a\kern{{-500em}}b$.

And these are fine: $\sqrt{{2}} \approx 1.414$ and $e^{{i\pi}} + 1 = 0$.

[^1]: The second try's own note."""

# What a model writes before a search, and its answer after: each with a [^1].
DRAFT = "A first take on it.[^1]\n\n[^1]: Written before the search."
AFTER_SEARCH = "After the search, $x = 2$.[^1]\n\n[^1]: Found by the search."

PLAIN = "Tides are the ocean answering the moon, twice a day on most coasts."

# An answer being written: a formula in a paragraph, then a block, each
# arriving a piece at a time.
LIVE = [
    "Start with ",
    "$x ",
    "+ ",
    "y$",
    " and then",
    "\n\n",
    "$$\n",
    "\\frac{1}",
    "{2}\n",
    "$$",
    "\n\nDone.",
]


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_formulas_and_footnotes_in_chrome(
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_loop: None,
) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    person = world.browser()
    person.sign_in("p-ada")

    def ask(chat: str, content: str, words: list[str] | str, *, search: bool = False) -> None:
        world.gateway.words = [words] if isinstance(words, str) else words
        sent = person.post(
            f"/api/chats/{chat}/messages",
            json={"content": content, "search": search, "attachments": []},
        )
        assert sent.status_code == 201, sent.text
        person.wait_answer(chat)

    plain = person.new_chat()
    ask(plain, "Why are there two tides a day?", PLAIN)

    math = person.new_chat()
    ask(math, "How does heat move through a wall?", MATH)
    ask(math, "Show me a few more.", FIRST_TRY)
    world.gateway.words = [SECOND_TRY]
    again = person.post(f"/api/chats/{math}/retry")
    assert again.status_code == 201, again.text
    person.wait_answer(math)

    monkeypatch.setattr(conftest, "DRAFT", DRAFT)
    world.gateway.mode = "draft"
    draft = person.new_chat()
    ask(draft, "What is x?", AFTER_SEARCH, search=True)
    world.gateway.mode = "answer"
    drafted = person.get(f"/api/chats/{draft}").json()["messages"][-1]
    assert drafted.get("answerFrom"), drafted

    world.gateway.words = LIVE
    world.gateway.delay = 0.25
    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "csp": CSP,
                "playwright": os.environ["WORKBENCH_PLAYWRIGHT"],
                "chrome": os.getenv(
                    "WORKBENCH_CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe"
                ),
                "cookies": [
                    {
                        "name": c.name,
                        "value": c.value,
                        "url": world.workbench,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for c in person.http.cookies.jar
                ],
                "secret": person.secret,
                "chats": {"plain": plain, "math": math, "draft": draft},
                "math": {"drawn": MATH_DRAWN, "failed": MATH_FAILED},
                "live": "Write me a formula, a piece at a time.",
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/math-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
