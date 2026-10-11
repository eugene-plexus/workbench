"""Which search model Workbench uses, and what it needs of the text it is given
(MR5, design 4.2 and 4.9). Nothing here talks to the gateway or the disk: every rule is
a function of its arguments, so each is a unit test.

**A search model** is a model the gateway lists with the `embeddings` surface. Workbench
reaches it only through the gateway, with its own key, and only for the passages of a
person's documents and the question they ask.

**Which one** (`choose`): the install's `documentsEmbeddingModel` is `auto`, or the id of
one model. `auto` is the first model that runs **on the install's own machines** and is
ready, and it prefers the one this person's search was built with while that is running, so
starting a second search model does not pause anybody's search. **A model that runs
outside the machines is used only when the owner has allowed it and named it**
(`documentsExternalEmbedding`): there is no cloud default, and `auto` never picks one,
because the text of a person's documents would leave the machines.

**What a model needs of its text** (`KNOWN`, MRC20): some models are trained to be given a
prefix, and are quietly worse without it (Qwen3-Embedding without its instruction loses
12.8 points of recall on real text). The table below says what each family wants for a
question and for a passage. A model that is not in it is used with no prefixes at all, and
that is said (`Identity.known`), never assumed to be fine. A passage is embedded with the
headings above it (`Notes > Budget > GPUs`, a new line, the text): the stored passage stays
its plain words.

**What a search is built with** (`Identity`) is kept in the index's `meta`: the model the
gateway says answered (never the name that was asked for: an alias answers with its first
target's), how many numbers each vector has, and the prefixes that were used. It is compared
with every answer, so a search is never given a vector from another model's space.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

#: The surface a search model has in the gateway's list of models.
SURFACE = "embeddings"
AUTO = "auto"

#: What a question is asked with, for the Qwen3-Embedding family (its model card: the
#: instruction is the task, worth +12.8 points of recall@10 on real text, MR5's research).
QWEN3_TASK = "Given a question, retrieve passages from the person's documents that answer it"
#: Below this the closest passage is not evidence by itself (design 4.4, item 7). It counts only
#: when the words of the question found nothing at all. Measured in MR5's live run (Eugene's 76
#: design documents, 2,810 passages, Qwen3-Embedding-0.6B Q8_0 with the instruction): the closest
#: passage to a question the documents answer scored 0.565 to 0.817 (60 of 60), and to a
#: question they do not 0.267 to 0.435 (12 of 12; twelve more, written after, 0.309 to 0.442).
#: The floor is the middle of that gap. A question near the documents' subject that they do not
#: answer can land in between (0.378 to 0.550 for eight), which is why this only ever stops a
#: question the words found nothing for. Only the 0.6B model was measured; the family's larger
#: models are given the same number until someone measures them.
QWEN3_FLOOR = 0.5


@dataclass(frozen=True)
class Known:
    """What one family of search models wants of its text."""

    family: str
    pattern: re.Pattern[str]
    query_prefix: str
    #: May hold `{title}`, which is filled with the passage's headings (or `none`); otherwise
    #: the headings go on a line of their own before the text.
    document_prefix: str
    max_input_tokens: int | None
    #: The best similarity that is not, by itself, an answer; `None` where it is not measured.
    floor: float | None = None


KNOWN: tuple[Known, ...] = (
    Known(
        "qwen3-embedding",
        re.compile(r"qwen3[-_ .]?embedding", re.IGNORECASE),
        f"Instruct: {QWEN3_TASK}\nQuery:",
        "",
        32_768,
        QWEN3_FLOOR,
    ),
    # Version 2 first: its pattern is the narrower of the two, and its window is 512 tokens.
    Known(
        "nomic-embed-text-v2",
        re.compile(r"nomic[-_ .]?embed[-_ .]?text[-_ .]?v2", re.IGNORECASE),
        "search_query: ",
        "search_document: ",
        512,
    ),
    Known(
        "nomic-embed-text",
        re.compile(r"nomic[-_ .]?embed", re.IGNORECASE),
        "search_query: ",
        "search_document: ",
        2_048,
    ),
    Known(
        "embeddinggemma",
        re.compile(r"embedding[-_ .]?gemma", re.IGNORECASE),
        "task: search result | query: ",
        "title: {title} | text: ",
        8_192,
    ),
)


def lookup(model_id: str) -> Known | None:
    """The rules for this model, by its id, or None when Workbench does not know it."""
    return next((k for k in KNOWN if k.pattern.search(model_id)), None)


def floor_for(model_id: str) -> float | None:
    known = lookup(model_id)
    return known.floor if known is not None else None


# --------------------------------------------------------------------------- #
# what a search is built with
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Identity:
    """The search model an index was built with, as its `meta` records it."""

    #: The model the gateway said answered the first batch.
    model: str
    #: How many numbers each of its vectors has, as stored.
    dim: int
    query_prefix: str = ""
    document_prefix: str = ""
    max_input_tokens: int | None = None
    #: Whether the prefixes are the ones the table knows for this model. When not, none
    #: were used, and the Documents screen says so.
    known: bool = False
    family: str | None = None
    built_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> Identity | None:
        """None when nothing is recorded; `ValueError` when what is recorded is not an
        identity (the caller says the index is damaged, and offers Rebuild)."""
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("model"), str)
                or not data["model"]
                or not isinstance(data.get("dim"), int)
                or isinstance(data["dim"], bool)
                or data["dim"] < 1
            ):
                raise ValueError("not a search model's identity")
            limit = data.get("max_input_tokens")
            return cls(
                model=data["model"],
                dim=data["dim"],
                query_prefix=str(data.get("query_prefix") or ""),
                document_prefix=str(data.get("document_prefix") or ""),
                max_input_tokens=limit if isinstance(limit, int) and limit > 0 else None,
                known=bool(data.get("known")),
                family=data.get("family") if isinstance(data.get("family"), str) else None,
                built_at=float(data.get("built_at") or 0.0),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"the search model this index was built with is unreadable: {exc}"
            ) from exc


def identity_for(model: str, dim: int, now: float = 0.0) -> Identity:
    """What an index is built with when its first passages are embedded by `model`."""
    known = lookup(model)
    return Identity(
        model=model,
        dim=dim,
        query_prefix=known.query_prefix if known else "",
        document_prefix=known.document_prefix if known else "",
        max_input_tokens=known.max_input_tokens if known else None,
        known=known is not None,
        family=known.family if known else None,
        built_at=now,
    )


#: Longest text of a question that is embedded: the latest message and a little of the one
#: before it, as the words search is given (`search.plan_query`), and no more.
MAX_QUESTION_CHARS = 1_400


def passage_input(identity: Identity, heading: str | None, text: str) -> str:
    """What a passage is embedded as: the model's document prefix, the headings above the
    passage (free context, design 4.3), then its words. The stored passage is its words."""
    if "{title}" in identity.document_prefix:
        return identity.document_prefix.replace("{title}", heading or "none") + text
    head = f"{heading}\n" if heading else ""
    return f"{identity.document_prefix}{head}{text}"


def question_input(identity: Identity, question: str) -> str:
    """What a question is embedded as: the model's query prefix, then the question."""
    return f"{identity.query_prefix}{question[:MAX_QUESTION_CHARS]}"


def unknown_prefixes(identity: Identity) -> str | None:
    """The sentence that says a model's prefixes are not known, or None when they are."""
    if identity.known:
        return None
    return (
        f"Workbench does not know which prefixes {identity.model} wants on a question and on a "
        "passage, so it uses none. Search by meaning may be a little less exact."
    )


# --------------------------------------------------------------------------- #
# which search model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SearchModel:
    """A model the gateway lists with the embeddings surface."""

    id: str
    #: `local`, `external` or `unknown`: whether it runs on this install's own machines.
    locality: str
    #: Whether a backend is running it now.
    ready: bool
    #: Who runs it, for the words that say it is outside the machines.
    who: str | None = None

    @property
    def local(self) -> bool:
        return self.locality == "local"


def search_models(listing: Mapping[str, Any]) -> tuple[list[SearchModel], set[str]]:
    """The search models in the gateway's list of models, in its order, and every id the
    list holds (a model that is not a search model is told from one that is not there)."""
    found: list[SearchModel] = []
    every: set[str] = set()
    for model in listing.get("data") or []:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str):
            continue
        every.add(model["id"])
        info = model.get("x_eugene_plexus") or {}
        if SURFACE not in (info.get("surfaces") or []):
            continue
        drivers = info.get("drivers") or []
        owner = str(model.get("owned_by") or "")
        found.append(
            SearchModel(
                id=model["id"],
                locality=str(info.get("locality") or "unknown"),
                ready=(info.get("ready_backends") or 0) > 0,
                who=owner or (str(drivers[0]) if len(drivers) == 1 else None),
            )
        )
    return found, every


@dataclass(frozen=True)
class Choice:
    """Which search model a search uses now, or why none."""

    #: The model to use, or None when none can be used now.
    model: SearchModel | None
    #: `ok`, or why not: `none_set_up`, `not_running`, `not_listed`, `not_search_model`,
    #: `outside` (a model outside the machines, not allowed), `outside_only`.
    code: str
    #: The setting as it is: `auto`, or the id the owner named.
    setting: str
    #: The model the words are about, when there is one even though it cannot be used.
    named: str | None = None
    #: Models that run outside the machines and were left out, by id.
    outside: tuple[str, ...] = ()
    #: In words a person reads, when `model` is None.
    message: str | None = None

    @property
    def automatic(self) -> bool:
        return self.setting == AUTO


def where_words(model: SearchModel) -> str:
    if model.locality == "external":
        return f"on {model.who}" if model.who else "outside your machines"
    return "somewhere Eugene cannot place"


def choose(
    models: Sequence[SearchModel],
    listed: Collection[str],
    setting: str,
    allow_external: bool,
    built_with: str | None = None,
) -> Choice:
    """The search model to use. `setting` is `auto` or an id; `built_with` is the model this
    person's search was built with, which `auto` keeps while it is running."""
    setting = (setting or AUTO).strip() or AUTO
    outside = tuple(m.id for m in models if not m.local)
    if setting == AUTO:
        local = [m for m in models if m.local]
        ready = [m for m in local if m.ready]
        if ready:
            kept = next((m for m in ready if built_with and m.id == built_with), None)
            return Choice(kept or ready[0], "ok", AUTO, outside=outside)
        if local or built_with:
            # The one this search was built with, if it is one Eugene still lists; else the
            # first listed; else, when it is gone from the list altogether, it by name: the
            # search is waiting for that model, and saying "none is set up" would be untrue.
            listed_here = {m.id for m in local}
            name = (
                built_with if built_with in listed_here else (local[0].id if local else built_with)
            )
            assert name is not None
            return Choice(
                None,
                "not_running",
                AUTO,
                name,
                outside,
                f"Searching by words only: the search model {name} is not running. "
                "Start it in Eugene's console under Backends.",
            )
        if outside:
            return Choice(
                None,
                "outside_only",
                AUTO,
                None,
                outside,
                "Searching by words only: the only search models Eugene lists run outside your "
                f"machines ({', '.join(outside[:3])}{', ...' if len(outside) > 3 else ''}), and "
                "the owner has not allowed sending your documents' text there.",
            )
        return Choice(
            None,
            "none_set_up",
            AUTO,
            None,
            outside,
            "Searching by words only: no search model is set up.",
        )
    found = next((m for m in models if m.id == setting), None)
    if found is None:
        if setting in listed:
            return Choice(
                None,
                "not_search_model",
                setting,
                setting,
                outside,
                f"Searching by words only: {setting} is not a search model. Choose one in the "
                "Search model setting in Eugene's console.",
            )
        return Choice(
            None,
            "not_listed",
            setting,
            setting,
            outside,
            f"Searching by words only: Eugene lists no search model named {setting}. Check the "
            "Search model setting in Eugene's console.",
        )
    if not found.local and not allow_external:
        return Choice(
            None,
            "outside",
            setting,
            setting,
            outside,
            f"Searching by words only: {setting} runs {where_words(found)}, outside your "
            "machines, and the owner has not allowed sending your documents' text there.",
        )
    if not found.ready:
        return Choice(
            None,
            "not_running",
            setting,
            setting,
            outside,
            f"Searching by words only: the search model {setting} is not running. "
            "Start it in Eugene's console under Backends.",
        )
    return Choice(found, "ok", setting, outside=outside)


# --------------------------------------------------------------------------- #
# how a time is said
# --------------------------------------------------------------------------- #


def duration_words(seconds: float) -> str:
    """A time a person reads, rounded the way a person says it: `less than a minute`,
    `about 14 minutes`, `about 2 hours`."""
    if seconds < 60:
        return "less than a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = seconds / 3600
    if hours < 48:
        whole = round(hours * 2) / 2
        text = f"{whole:g}"
        return f"about {text} hour{'s' if whole != 1 else ''}"
    days = round(hours / 24)
    return f"about {days} days"
