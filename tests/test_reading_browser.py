"""Reading Workbench in Chrome: pictures and numbers of every kind of text
(workbench-reading.md). Opt-in, like the other browser checks; set
WORKBENCH_SHOTS to keep the pictures."""

# The answers below write the multiplication sign as models do.
# ruff: noqa: RUF001

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from . import documents as docs
from .conftest import World

# What a local model writes when it is asked something technical: headings,
# bold lead-ins, a formula, a table, steps with nested bullets, a quote, a
# rule, a task list and a command.
RICH = r"""Short answer: **it fits, but only with a quantized cache.** At 32k context a 27B model's KV cache is about *15.5 GiB* in `f16`, and the weights at Q4_K_M are another 16 GiB, so the two together fill a 5090's 32 GiB before llama.cpp has anywhere to work.

## How the cache is sized

Every token the model has read keeps a key and a value vector in every layer, so the cache grows with the context, not with the question:

```text
kv_bytes = 2 × layers × kv_heads × head_dim × context × bytes_per_value
```

For a typical 27B model with grouped-query attention:

| Setting | Value |
| --- | ---: |
| Layers | 62 |
| KV heads | 16 |
| Head dimension | 128 |
| Context | 32,768 tokens |
| Cache type | `f16` (2 bytes) |

That works out to **15.5 GiB**. Quantizing the cache to `q8_0` halves it, and the quality loss is hard to measure in practice.

### What to set in Eugene

1. Open the model's **Library profile**.
2. Set *Context* to `32768`.
3. Set *KV cache type* to `q8_0` for keys and values.
   - Keys are more sensitive than values; if answers get worse, keep keys at `f16`.
   - `q4_0` halves it again, but long-context recall suffers.
4. Press **Check fit** before you start it.

> **Note:** llama.cpp needs flash attention for a quantized V cache. Eugene turns it on when the profile asks for one.

The same thing from a terminal:

```bash
llama-server -m qwen3-27b-q4_k_m.gguf -c 32768 \
  --cache-type-k q8_0 --cache-type-v q8_0 -fa on -ngl 99
```

---

### If it still doesn't fit

- Drop to **24k context**: the cache shrinks in proportion.
- Offload a few layers to the processor with `--n-gpu-layers` (slower, but it runs).
- ~~Use a smaller batch size~~: the batch barely changes the cache.

Where you are now:

- [x] Weights at Q4_K_M
- [ ] Try Q5_K_M once the cache is quantized

See the [llama.cpp server options](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) for every flag."""

REASONING = [
    "The person asks whether a 27B model at 32k context fits on a 5090, which has 32 GB. ",
    "The cache is 2 × layers × KV heads × head dim × context × bytes per value.\n\n",
    "For a 27B model with GQA: 62 layers, 16 KV heads, head dim 128. ",
    "2 × 62 × 16 × 128 = 253,952 values per token, times 2 bytes, about 0.5 MB a token. ",
    "At 32,768 tokens that is about 15.5 GiB.\n\n",
    "Weights at Q4_K_M are about 16 GiB, so f16 does not fit with compute buffers. ",
    "q8_0 halves the cache to 7.75 GiB, which fits. Give the formula, a table, the settings.",
]

FOLLOW_UP = """With `q8_0` for keys and values the cache drops to **7.75 GiB**, so the whole thing comes to about 25 GiB:

- Weights: 16.2 GiB
- KV cache: 7.75 GiB
- Compute buffers: about 1 GiB

That leaves roughly 7 GiB free on the card, enough for a longer context or a small second model. Most people never notice the difference a `q8_0` cache makes; if you want to go further, `q4_0` on the values alone is a reasonable middle ground."""

STORY = """The radio arrived in a crate of salvage, wedged between a coil of rope and a box of brass fittings gone green with age. Mara almost threw it back into the sea.

She turned it on that night out of boredom more than hope. Static, then a voice, calm and unhurried and faintly amused, reading the weather for the coast. *Winds from the northeast at fifteen knots. Fog by morning, lifting before noon.*

It was a pleasant forecast. It was also, she realized as she copied it into the log, wrong: the sky outside was clear and the barometer steady. She went to bed thinking nothing of it.

The fog came at dawn.

By the third week she had stopped calling it a coincidence. The voice was never early and never late; it simply spoke about tomorrow as though tomorrow had already happened and been written down somewhere, in a book she was not allowed to read. "Storm warning," it said one evening, and for the first time there was something like regret in it. "All vessels should seek harbour."

Mara looked out at the water, where the lights of the *Saint Brendan* were just rounding the point."""

CUT = """## The transistor, from 1947 to today

The story usually starts at **Bell Labs** in December 1947, when John Bardeen and Walter Brattain pressed two gold contacts onto a slab of germanium and saw a signal come out stronger than it went in. William Shockley, who led their group, was"""

LIVE = (
    "Tides are the ocean answering the moon. The moon pulls hardest on the water nearest "
    "to it, and least on the water on the far side of the Earth, so the sea bulges toward "
    "the moon and away from it at once. As the Earth turns through both bulges, most "
    "coasts see two high tides a day."
)


# A few notes, so the Memory page and a chat's Memory drawer have something to read.
NOTES = [
    {"text": "I use metric units", "kind": "fact", "pinned": True},
    {"text": "Keep answers short, and give the number first", "kind": "instruction"},
    {"text": "I am building a birdhouse out of cedar", "kind": "project"},
    {"text": "I prefer Python to JavaScript", "kind": "preference"},
    {"text": "My flight to Denver leaves on March 3", "kind": "event"},
]


def pieces(text: str, size: int = 24) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


@pytest.mark.skipif(not os.getenv("WORKBENCH_PLAYWRIGHT"), reason="opt-in system Chrome acceptance")
def test_reading_in_chrome(world: World, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    assert world.settings.static_dir is not None
    shutil.copytree(
        root / "src/eugene_plexus_workbench/static", world.settings.static_dir, dirs_exist_ok=True
    )
    person = world.browser()
    person.sign_in("p-ada")

    def ask(
        chat: str,
        content: str,
        words: str,
        *,
        search: bool = False,
        attachments: list[str] | None = None,
    ) -> None:
        world.gateway.words = pieces(words)
        sent = person.post(
            f"/api/chats/{chat}/messages",
            json={"content": content, "search": search, "attachments": attachments or []},
        )
        assert sent.status_code == 201, sent.text
        person.wait_answer(chat)

    story = person.new_chat()
    ask(
        story,
        "Write the opening of a short story about a lighthouse keeper who finds a radio "
        "that only plays tomorrow's weather.",
        STORY,
    )
    cut = person.new_chat()
    world.gateway.finish = "length"
    world.gateway.output_cap = {"tokens": 4096, "source": "install"}
    ask(cut, "Tell me the full history of the transistor.", CUT)
    world.gateway.finish = "stop"
    world.gateway.output_cap = None
    rich = person.new_chat()
    world.gateway.reasoning = REASONING
    ask(
        rich,
        "How much VRAM does the KV cache need for a 27B model at 32k context, and will it "
        "fit on a 5090?",
        RICH,
    )
    world.gateway.reasoning = []
    ask(rich, "What if I quantize the cache to q8_0?", FOLLOW_UP, search=True)
    for note in NOTES:
        kept = person.post("/api/memory/notes", json=note)
        assert kept.status_code == 201, kept.text

    # A chat whose message carried a PDF, so its file, its answer and the files named
    # under the answer are among the things read; and files to attach in its composer.
    documents = person.new_chat()
    report = person.post(
        f"/api/chats/{documents}/files",
        files={"file": ("annual-report.pdf", docs.three_pages(), "application/pdf")},
    )
    assert report.status_code == 201, report.text
    person.wait_read(documents, report.json()["id"])
    ask(
        documents,
        "What is the code word in the annual report?",
        f"The code word is **{docs.CODE_WORD}**, on page 3 of the report.",
        attachments=[report.json()["id"]],
    )
    files = tmp_path / "files"
    files.mkdir()
    (files / "long-12-pages.pdf").write_bytes(docs.long_pdf(12))
    (files / "scan.pdf").write_bytes(docs.pdf_pictures(2))

    # What the page asks while the pictures are taken: slow enough to see.
    world.gateway.reasoning = pieces(REASONING[0] + REASONING[1], 12)
    world.gateway.words = [w + " " for w in LIVE.split()]
    world.gateway.delay = 0.12
    shots = Path(os.getenv("WORKBENCH_SHOTS") or tmp_path / "shots")
    cfg = tmp_path / "browser.json"
    cfg.write_text(
        json.dumps(
            {
                "url": world.workbench,
                "secret": person.secret,
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
                "chats": {"rich": rich, "story": story, "cut": cut, "documents": documents},
                "documents": {
                    "long": str(files / "long-12-pages.pdf"),
                    "scan": str(files / "scan.pdf"),
                },
                "live": "Why are there two tides a day?",
                "shots": str(shots),
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(root / "web/scripts/reading-browser.mjs"), str(cfg)],
        capture_output=True,
        text=True,
        timeout=240,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout
