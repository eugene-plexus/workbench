"""Mermaid diagrams in Chrome, under the page's own headers
(workbench-rich-content-roadmap.md RC2). Opt-in, like the other browser
checks; set WORKBENCH_SHOTS to keep the pictures."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from eugene_plexus_workbench.web import CSP

from .conftest import World

FLOWCHART = """flowchart TD
  A[Ask for a model] --> B{Is it in the library?}
  B -- Yes --> C[Load it]
  B -- No --> D[Download it]
  D --> C"""

SEQUENCE = """sequenceDiagram
  participant W as Workbench
  participant G as Gateway
  W->>G: A chat completion
  G-->>W: Tokens, as they come
  Note over W,G: One stream per answer"""

CLASSES = """classDiagram
  class Engine {
    +String name
    +start() void
  }
  class LlamaCpp
  Engine <|-- LlamaCpp"""

STATES = """stateDiagram-v2
  [*] --> Idle
  Idle --> Loading : a request
  Loading --> Serving : ready
  Serving --> Idle : unloaded
  Serving --> [*]"""

# Thirty steps left to right: wider than a phone, and twice its size is wider
# than a PNG may be.
WIDE = "flowchart LR\n" + "\n".join(
    f"  s{i}[Step {i}] --> s{i + 1}[Step {i + 1}]" for i in range(30)
)

# Everything a diagram might try: its own configuration and a stylesheet
# that would fetch, HTML in a label, a shape that is a picture from elsewhere,
# a click callback and a script link.
HOSTILE = """%%{init: {"securityLevel": "loose", "htmlLabels": true, "flowchart": {"htmlLabels": true}, "themeCSS": ".node rect { fill: url(https://evil.example/theme.png) }"}}%%
flowchart TD
  A["<img src='https://evil.example/label.png'> <b onclick='alert(1)'>bold</b>"] --> B[Plain]
  C@{ img: "https://evil.example/shape.png", label: "A picture", pos: "t", w: 60, h: 60 } --> B
  click A call alert(1)
  click B href "javascript:alert(2)"
  style B fill:#7a3b3b
  classDef loud stroke-width:4px
  class A loud"""

BROKEN = "flowchart TD\n  A --> "
UNKNOWN = "hello world\n  a --> b"
# Past 20,000 characters.
LONG = "flowchart TD\n" + "\n".join(f"  n{i}[Node {i}] --> n{i + 1}" for i in range(900))


def fence(source: str) -> str:
    return f"```mermaid\n{source}\n```"


DIAGRAMS = f"""## How a model gets served

{fence(FLOWCHART)}

The calls:

{fence(SEQUENCE)}

The classes, and the states:

{fence(CLASSES)}

{fence(STATES)}

The first one again, as it was:

{fence(FLOWCHART)}

A long one:

{fence(WIDE)}

One that tries too much:

{fence(HOSTILE)}

Three that cannot be drawn:

{fence(BROKEN)}

{fence(UNKNOWN)}

{fence(LONG)}

And plain code stays code:

```python
print("not a diagram")
```"""

#: Drawn, in order: flowchart, sequence, class, state, the flowchart again, wide, hostile.
DRAWN_KINDS = [
    "Flowchart",
    "Sequence diagram",
    "Class diagram",
    "State diagram",
    "Flowchart",
    "Flowchart",
    "Flowchart",
]
#: The notes of the three that cannot be drawn, in order (a prefix of each).
FAILED_NOTES = [
    "Diagram not shown: Parse error on line ",
    "Diagram not shown: Mermaid has no kind of diagram called “hello”",
    "Diagram not shown: it is longer than 20,000 characters",
]

PIE = """pie title Where the time went
  "Coding" : 55
  "Reviews" : 30
  "Meetings" : 15"""

FIRST_TRY = f"""The first try, with a pie chart and the flowchart from before.

{fence(PIE)}

{fence(FLOWCHART)}"""

# A hundred boxes, one inside the next: 1,711 characters that took Mermaid
# 20 s to lay out (measured), then one that is fine.
NESTED = (
    "flowchart TD\n" + "".join(f"subgraph s{i}\n" for i in range(100)) + "x --> y\n" + "end\n" * 100
)
AFTER = """erDiagram
  CHAT ||--o{ MESSAGE : holds
  MESSAGE ||--o{ VERSION : has"""
SECOND_TRY = f"""The second try: one that takes too long, then one that is fine.

{fence(NESTED)}

{fence(AFTER)}"""

# Every other kind Workbench draws, one each.
GALLERY_SOURCES = [
    (
        "Gantt chart",
        """gantt
  title The release
  dateFormat YYYY-MM-DD
  section Build
  Design :a1, 2026-01-01, 7d
  Code :after a1, 10d
  section Test
  Acceptance :2026-01-15, 5d""",
    ),
    (
        "User journey",
        """journey
  title Setting up a model
  section Find it
    Search the hub: 4: Me
    Check it fits: 3: Me
  section Run it
    Load it: 5: Me, Workbench""",
    ),
    (
        "Timeline",
        """timeline
  title Releases
  2025 : v0.1
  2026 : v0.2 : v0.3""",
    ),
    (
        "Git graph",
        """gitGraph
  commit
  branch feature
  checkout feature
  commit
  commit
  checkout main
  merge feature
  commit""",
    ),
    (
        "Mind map",
        """mindmap
  root((Plexus))
    Engines
      llama.cpp
      vLLM
    Library
      Models
      Profiles""",
    ),
    (
        "Quadrant chart",
        """quadrantChart
  title Reach and effort
  x-axis Low effort --> High effort
  y-axis Low reach --> High reach
  A: [0.3, 0.6]
  B: [0.45, 0.23]
  C: [0.7, 0.8]""",
    ),
    (
        "XY chart",
        """xychart-beta
  title "Tokens a second"
  x-axis [a, b, c, d]
  y-axis "tok/s" 0 --> 120
  bar [40, 80, 100, 60]
  line [40, 80, 100, 60]""",
    ),
]
GALLERY = "Every other kind:\n\n" + "\n\n".join(fence(source) for _, source in GALLERY_SOURCES)

PLAIN = "Tides are the ocean answering the moon, twice a day on most coasts."

# An answer being written: a diagram arriving a piece at a time.
LIVE = [
    "Here it is:\n\n",
    "```mermaid\n",
    "flowchart LR\n",
    "  a[Write] --> b[Draw]\n",
    "  b --> c[Show]\n",
    "```",
    "\n\nDone.",
]
LIVE_SOURCE = "flowchart LR\n  a[Write] --> b[Draw]\n  b --> c[Show]"


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_diagrams_in_chrome(world: World, tmp_path: Path, quiet_loop: None) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    person = world.browser()
    person.sign_in("p-ada")

    def ask(chat: str, content: str, words: list[str] | str) -> None:
        world.gateway.words = [words] if isinstance(words, str) else words
        sent = person.post(
            f"/api/chats/{chat}/messages",
            json={"content": content, "search": False, "attachments": []},
        )
        assert sent.status_code == 201, sent.text
        person.wait_answer(chat)

    plain = person.new_chat()
    ask(plain, "Why are there two tides a day?", PLAIN)

    diagrams = person.new_chat()
    ask(diagrams, "How does a model get served?", DIAGRAMS)
    ask(diagrams, "Draw me a couple more.", FIRST_TRY)
    world.gateway.words = [SECOND_TRY]
    again = person.post(f"/api/chats/{diagrams}/retry")
    assert again.status_code == 201, again.text
    person.wait_answer(diagrams)

    gallery = person.new_chat()
    ask(gallery, "Show me every other kind.", GALLERY)

    world.gateway.words = LIVE
    world.gateway.delay = 0.3
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
                "chats": {"plain": plain, "diagrams": diagrams, "gallery": gallery},
                "diagrams": {
                    "kinds": DRAWN_KINDS,
                    "failed": FAILED_NOTES,
                    "flowchart": FLOWCHART,
                    "hostile": HOSTILE,
                    "gallery": [kind for kind, _ in GALLERY_SOURCES],
                },
                "live": {"ask": "Draw it as it comes.", "source": LIVE_SOURCE},
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/diagram-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        # The checks quote Mermaid's words, curly quotes and all.
        encoding="utf-8",
        timeout=600,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
