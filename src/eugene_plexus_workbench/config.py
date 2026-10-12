"""Workbench's settings: the config trio the registry expects of apps we ship.

`GET /v1/config`, `GET /v1/config/schema` and `PATCH /v1/config`, in
`common.yaml`'s shapes, which the console renders on Workbench's page and
the agent checks every answer against. The bearer is the admin token the
agent hands Workbench at each start, and nothing else: no person signed
in to Workbench can change these, the owner included -- they are changed
from Eugene's console, by its operator.

Sixteen settings. `ownerReadsChats` (W13): whether the owner may read the chats
of the people they give Workbench to (Troy, C3 call 3: each business
decides). Three are memory's (`memory.py`, MR1): whether people
may keep notes at all, how many each may keep, and whether notes may be sent
to a model that runs outside this install's machines. Memory is not covered
by `ownerReadsChats`: no setting here lets anyone read another person's
notes. Five are documents' (`context.py`, MR2 and MR4): whether Workbench reads
documents at all (the files people attach, and the ones they keep), the most it
takes of one file, how long a file may be and still be read whole by default,
how many passages one person may keep, and whether passages of their saved
documents may be sent to a model that runs outside this install's machines. Three are
search by meaning's (MR5, `knowledge/meaning.py`): which search model finds passages by
what they say (`auto`, or one named), whether the text of documents may go to one that
runs outside the machines, and how fast Workbench asks it to add meaning to documents.
Four are the folders people point their documents at (MR7, `knowledge/folder_sync.py`): how
often Workbench looks at them, and how many files, how many megabytes and how many folders deep
it takes of one. Saved documents are not covered by `ownerReadsChats` either. Every one takes
effect at once, and `null` puts one back to its default.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ._generated.models import (
    ConfigDocument,
    ConfigField,
    ConfigFieldError,
    ConfigSchema,
    ConfigUpdateRequest,
    ConfigUpdateResult,
    ConfigValueType,
)
from .store import Store

OWNER_READS_CHATS = "ownerReadsChats"
MEMORY_ENABLED = "memoryEnabled"
MEMORY_MAX_NOTES = "memoryMaxNotes"
MEMORY_EXTERNAL_MODELS = "memoryExternalModels"
DOCUMENTS_ENABLED = "documentsEnabled"
DOCUMENTS_MAX_FILE_MB = "documentsMaxFileMB"
DOCUMENTS_WHOLE_UP_TO_TOKENS = "documentsWholeUpToTokens"
DOCUMENTS_MAX_PASSAGES = "documentsMaxPassagesPerPerson"
DOCUMENTS_EXTERNAL_MODELS = "documentsExternalModels"
DOCUMENTS_EMBEDDING_MODEL = "documentsEmbeddingModel"
DOCUMENTS_EXTERNAL_EMBEDDING = "documentsExternalEmbedding"
DOCUMENTS_EMBED_PER_MINUTE = "documentsEmbedRequestsPerMinute"
DOCUMENTS_FOLDER_SYNC_MINUTES = "documentsFolderSyncMinutes"
DOCUMENTS_FOLDER_MAX_FILES = "documentsFolderMaxFiles"
DOCUMENTS_FOLDER_MAX_MB = "documentsFolderMaxMB"
DOCUMENTS_FOLDER_MAX_DEPTH = "documentsFolderMaxDepth"

#: How far a note, or a passage of a saved document, may travel to a model outside
#: the machines (`memory_api`'s `resolve_external` says how the owner's and a person's
#: choices meet).
EXTERNAL_CHOICES = ("never", "ask", "always")
MAX_NOTES_LIMIT = 2000
MAX_FILE_MB_LIMIT = 500
MIN_WHOLE_TOKENS, MAX_WHOLE_TOKENS = 500, 200_000
MIN_PASSAGES_LIMIT, MAX_PASSAGES_LIMIT = 100, 1_000_000
MIN_EMBED_PER_MINUTE, MAX_EMBED_PER_MINUTE = 1, 6_000
MIN_SYNC_MINUTES, MAX_SYNC_MINUTES = 1, 1_440
MIN_FOLDER_FILES, MAX_FOLDER_FILES = 10, 100_000
MIN_FOLDER_MB, MAX_FOLDER_MB = 1, 102_400
MIN_FOLDER_DEPTH, MAX_FOLDER_DEPTH = 1, 31
MAX_MODEL_ID = 256
#: The search model setting's own word for "the first one that runs on this install's machines".
AUTO = "auto"

FIELDS = [
    ConfigField(
        key=OWNER_READS_CHATS,
        label="The owner may read people's chats and media",
        description=(
            "When on, the owner of this install can read, but not change, the chats and media "
            "(images and other results in their bins) of every person given Workbench, "
            "including those made before it was turned on, and each person sees a line above "
            "their chats saying so. When off, Workbench refuses reads of anyone else's chats "
            "and media. Local servers run with Workbench's file access, so use only programs "
            "trusted with everyone's data. This never covers a person's memory notes: nobody "
            "can read another person's notes, whatever this is set to."
        ),
        category="people",
        valueType=ConfigValueType.boolean,
        default=False,
    ),
    ConfigField(
        key=MEMORY_ENABLED,
        label="People can keep notes for Workbench to remember",
        description=(
            "When on (the default), each person can keep short notes about themselves on the "
            'Memory page or by writing "remember that ..." in a chat, and a chat sends them '
            "to its model so it can use them. When off, Workbench keeps no new notes and "
            "sends none to any model; people can still read, export and delete the notes they "
            "already have. Each person can also turn memory off for themselves, and for one "
            "chat at a time."
        ),
        category="memory",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=MEMORY_MAX_NOTES,
        label="Most notes one person can keep",
        description=(
            "How many notes each person can keep (default 300). At the limit a person is told "
            "so and keeps no new note until they delete one. Lowering it deletes nothing. "
            "Each note is at most 240 characters, and a chat sends only as many as fit its "
            "model's window (8% of it, at most 1,000 tokens), so a long list costs a chat "
            "nothing it cannot afford."
        ),
        category="memory",
        valueType=ConfigValueType.integer,
        default=300,
        minimum=1,
        maximum=MAX_NOTES_LIMIT,
    ),
    ConfigField(
        key=MEMORY_EXTERNAL_MODELS,
        label="Send notes to models that run outside your machines",
        description=(
            "What a chat does with a person's notes when its model runs on a cloud account or "
            "anywhere Eugene cannot confirm is this install's own machines. Ask (the default): "
            "the notes are held back until the person chooses to send them for that chat. "
            "Always: they are sent without asking. Each person can choose Never, Ask or Always "
            "for themselves, and this is what a person who has not chosen gets. Never is "
            "different: it is this install's rule, notes are never sent to such a model, and "
            "no person can change that."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(EXTERNAL_CHOICES),
        enumLabels=["Never", "Ask for each chat", "Always"],
        default="ask",
    ),
    ConfigField(
        key=DOCUMENTS_ENABLED,
        label="Workbench reads documents",
        description=(
            "When on (the default), a PDF, Word, Markdown, text, CSV, HTML or code file "
            "attached to a chat is read by Workbench, in a process of its own, and its text "
            "goes to the chat's model, so any model can answer from it; and each person can "
            "keep documents in My documents, which Workbench reads and searches for the "
            "chats that have Documents turned on. When off, Workbench reads no file: only "
            "images and audio, and PDFs to models that take them as files, are attached, as "
            "before, and no saved document is added or searched (people can still read and "
            "remove the ones they have). Turning it off does not stop a file already read "
            "from being replayed in the chat it was sent in."
        ),
        category="documents",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=DOCUMENTS_MAX_FILE_MB,
        label="Largest file Workbench reads (MB)",
        description=(
            "The biggest file, in mebibytes, Workbench takes to read (default 50). A bigger "
            "one is refused when it is attached, naming this limit. A file is read in a child "
            "process that is ended after two minutes and held to two gibibytes of memory, so "
            "a large or broken file fails by itself and never stops Workbench."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=50,
        minimum=1,
        maximum=MAX_FILE_MB_LIMIT,
    ),
    ConfigField(
        key=DOCUMENTS_WHOLE_UP_TO_TOKENS,
        label="Longest file read whole without asking (tokens)",
        description=(
            "A file is read whole, with no question, up to this many tokens (default 6,000) "
            "or a quarter of the model's window, whichever is less. A longer one is read only "
            "when the person chooses to read it all, which Workbench allows up to 70% of what "
            "remains of the window, or the first part of it. Tokens are counted as characters "
            "divided by three, deliberately high."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=6000,
        minimum=MIN_WHOLE_TOKENS,
        maximum=MAX_WHOLE_TOKENS,
    ),
    ConfigField(
        key=DOCUMENTS_MAX_PASSAGES,
        label="Most passages one person can keep",
        description=(
            "How many passages (about 300 tokens each; a page of text is two or three) the "
            "documents one person keeps may come to, in My documents and in the files they "
            "search in chats together (default 100,000). A file that would take a person past "
            "it is skipped, and says so; a person who is at the limit is told so and removes "
            "a document to add another. Lowering it deletes nothing."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=100_000,
        minimum=MIN_PASSAGES_LIMIT,
        maximum=MAX_PASSAGES_LIMIT,
    ),
    ConfigField(
        key=DOCUMENTS_EXTERNAL_MODELS,
        label="Send passages of saved documents to models that run outside your machines",
        description=(
            "What a chat does with the passages found in a person's saved documents when its "
            "model runs on a cloud account or anywhere Eugene cannot confirm is this install's "
            "own machines. Ask (the default): the passages are held back until the person "
            "chooses to send them for that chat. Always: they are sent without asking. Each "
            "person can choose Never, Ask or Always for themselves, and this is what a person "
            "who has not chosen gets. Never is different: it is this install's rule, passages "
            "are never sent to such a model, and no person can change that. A file a person "
            "attaches to a chat is their own act and is not held back by this."
        ),
        category="documents",
        valueType=ConfigValueType.enum,
        enumValues=list(EXTERNAL_CHOICES),
        enumLabels=["Never", "Ask for each chat", "Always"],
        default="ask",
    ),
    ConfigField(
        key=DOCUMENTS_EMBEDDING_MODEL,
        label="Search model for My documents",
        description=(
            "The model that finds a passage by what it says, and not only by its words. "
            "Automatic (the default) uses the first search model that runs on this install's "
            "own machines and is running now, and keeps the one a person's search was built "
            "with while it is running. Name one to choose it. A model that runs outside your "
            "machines is used only if you name it here and also allow it below: automatic "
            "never picks one. Changing the model changes nobody's search by itself: a person "
            "whose search was built with another model sees search by meaning paused, and "
            "chooses Rebuild, which adds meaning again with the new one. While no search model "
            "is running, documents are searched by words only."
        ),
        category="documents",
        valueType=ConfigValueType.string,
        default=AUTO,
    ),
    ConfigField(
        key=DOCUMENTS_EXTERNAL_EMBEDDING,
        label="Send the text of documents to a search model outside your machines",
        description=(
            "Off (the default): the text of the passages in people's documents, and the "
            "questions they ask, go only to a search model that runs on this install's own "
            "machines. On: if the model named above runs outside them (a cloud account), "
            "Workbench sends that account the text of every passage of everyone's documents "
            "and every question they ask, and it leaves your machines. Turn it on only where "
            "that is acceptable for everything people keep here."
        ),
        category="documents",
        valueType=ConfigValueType.boolean,
        default=False,
    ),
    ConfigField(
        key=DOCUMENTS_EMBED_PER_MINUTE,
        label="Most requests a minute for adding meaning to documents",
        description=(
            "While Workbench adds meaning to documents it sends the search model one request "
            "at a time, 16 passages each, and at most this many a minute (default 30). That "
            "is half of what a new key allows (60 a minute and 2 at once), so a chat never "
            "waits behind an import. Raise it only after raising the key's own limits in "
            "Eugene's console, or adding meaning will be refused as over the limit."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=30,
        minimum=MIN_EMBED_PER_MINUTE,
        maximum=MAX_EMBED_PER_MINUTE,
    ),
    ConfigField(
        key=DOCUMENTS_FOLDER_SYNC_MINUTES,
        label="How often Workbench looks at people's folders (minutes)",
        description=(
            "A person can point their documents at a folder the owner has granted them. "
            "Workbench looks at each such folder this often (default 10 minutes): it adds new "
            "files, reads again a file that was edited (and keeps what it had if the words are "
            "the same), and takes out a file that was deleted, so an answer is never more than "
            "this many minutes behind the folder. Each person can also choose Sync now. A "
            "shorter time finds edits sooner and reads the disk more often. Workbench only "
            "reads a folder: it never changes anything in it."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=10,
        minimum=MIN_SYNC_MINUTES,
        maximum=MAX_SYNC_MINUTES,
    ),
    ConfigField(
        key=DOCUMENTS_FOLDER_MAX_FILES,
        label="Most files Workbench keeps from one folder",
        description=(
            "The most files one folder in a person's documents may give (default 10,000), "
            "counting the files that are not ignored. Files are taken in name order, the ones "
            "nearest the top first; the rest are left out, and the person is told. Lowering it "
            "removes the extra files at the next look. A small file is read in a fraction of a "
            "second and a long PDF takes longer, so a big folder is searchable by degrees, and "
            "the Documents screen says how far it has got."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=10_000,
        minimum=MIN_FOLDER_FILES,
        maximum=MAX_FOLDER_FILES,
    ),
    ConfigField(
        key=DOCUMENTS_FOLDER_MAX_MB,
        label="Most megabytes Workbench copies from one folder",
        description=(
            "Workbench keeps its own copy of each file it reads from a folder, so the most it "
            "copies from one folder is limited (default 2,048 MB). A file that would take the "
            "folder past it is skipped, and says so. This counts only the files Workbench reads, "
            "not pictures or other files it skips. The most it reads of one file is the setting "
            "for the largest file above."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=2_048,
        minimum=MIN_FOLDER_MB,
        maximum=MAX_FOLDER_MB,
    ),
    ConfigField(
        key=DOCUMENTS_FOLDER_MAX_DEPTH,
        label="How many folders deep Workbench looks",
        description=(
            "How many folders below the one a person added Workbench goes into (default 12). "
            "A folder deeper than this is not looked at, and the person is told which. Links "
            "are never followed, whatever this is."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=12,
        minimum=MIN_FOLDER_DEPTH,
        maximum=MAX_FOLDER_DEPTH,
    ),
]

DEFAULTS: dict[str, Any] = {
    OWNER_READS_CHATS: False,
    MEMORY_ENABLED: True,
    MEMORY_MAX_NOTES: 300,
    MEMORY_EXTERNAL_MODELS: "ask",
    DOCUMENTS_ENABLED: True,
    DOCUMENTS_MAX_FILE_MB: 50,
    DOCUMENTS_WHOLE_UP_TO_TOKENS: 6000,
    DOCUMENTS_MAX_PASSAGES: 100_000,
    DOCUMENTS_EXTERNAL_MODELS: "ask",
    DOCUMENTS_EMBEDDING_MODEL: AUTO,
    DOCUMENTS_EXTERNAL_EMBEDDING: False,
    DOCUMENTS_EMBED_PER_MINUTE: 30,
    DOCUMENTS_FOLDER_SYNC_MINUTES: 10,
    DOCUMENTS_FOLDER_MAX_FILES: 10_000,
    DOCUMENTS_FOLDER_MAX_MB: 2_048,
    DOCUMENTS_FOLDER_MAX_DEPTH: 12,
}


async def owner_reads_chats(store: Store) -> bool:
    value = await store.setting(OWNER_READS_CHATS)
    return bool(value) if value is not None else bool(DEFAULTS[OWNER_READS_CHATS])


@dataclass(frozen=True)
class MemorySettings:
    """The install's memory settings, as they are in effect now."""

    enabled: bool
    max_notes: int
    external: str


async def memory_settings(store: Store) -> MemorySettings:
    enabled = await store.setting(MEMORY_ENABLED)
    limit = await store.setting(MEMORY_MAX_NOTES)
    external = await store.setting(MEMORY_EXTERNAL_MODELS)
    return MemorySettings(
        enabled=bool(enabled) if enabled is not None else bool(DEFAULTS[MEMORY_ENABLED]),
        max_notes=int(limit) if limit is not None else int(DEFAULTS[MEMORY_MAX_NOTES]),
        external=(
            str(external) if external in EXTERNAL_CHOICES else str(DEFAULTS[MEMORY_EXTERNAL_MODELS])
        ),
    )


@dataclass(frozen=True)
class DocumentSettings:
    """The install's document settings, as they are in effect now."""

    enabled: bool
    max_file_mb: int
    whole_up_to_tokens: int
    max_passages: int = 100_000
    #: What the install lets passages of saved documents do, for a model that runs
    #: outside its machines: `never`, `ask` or `always`.
    external: str = "ask"
    #: The search model: `auto`, or the id the owner named; whether the text of documents
    #: may go to one that runs outside the machines; and the requests a minute adding
    #: meaning may make.
    embedding_model: str = AUTO
    external_embedding: bool = False
    embed_per_minute: int = 30

    @property
    def max_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


async def document_settings(store: Store) -> DocumentSettings:
    enabled = await store.setting(DOCUMENTS_ENABLED)
    largest = await store.setting(DOCUMENTS_MAX_FILE_MB)
    whole = await store.setting(DOCUMENTS_WHOLE_UP_TO_TOKENS)
    passages = await store.setting(DOCUMENTS_MAX_PASSAGES)
    external = await store.setting(DOCUMENTS_EXTERNAL_MODELS)
    model = await store.setting(DOCUMENTS_EMBEDDING_MODEL)
    outside = await store.setting(DOCUMENTS_EXTERNAL_EMBEDDING)
    rate = await store.setting(DOCUMENTS_EMBED_PER_MINUTE)
    return DocumentSettings(
        enabled=bool(enabled) if enabled is not None else bool(DEFAULTS[DOCUMENTS_ENABLED]),
        max_file_mb=int(largest) if largest is not None else int(DEFAULTS[DOCUMENTS_MAX_FILE_MB]),
        whole_up_to_tokens=(
            int(whole) if whole is not None else int(DEFAULTS[DOCUMENTS_WHOLE_UP_TO_TOKENS])
        ),
        max_passages=(
            int(passages) if passages is not None else int(DEFAULTS[DOCUMENTS_MAX_PASSAGES])
        ),
        external=(
            str(external)
            if external in EXTERNAL_CHOICES
            else str(DEFAULTS[DOCUMENTS_EXTERNAL_MODELS])
        ),
        embedding_model=(model.strip() if isinstance(model, str) and model.strip() else AUTO),
        external_embedding=(
            bool(outside) if outside is not None else bool(DEFAULTS[DOCUMENTS_EXTERNAL_EMBEDDING])
        ),
        embed_per_minute=(
            int(rate) if rate is not None else int(DEFAULTS[DOCUMENTS_EMBED_PER_MINUTE])
        ),
    )


@dataclass(frozen=True)
class FolderSettings:
    """What the install lets a folder in a person's documents be, as it is in effect now."""

    sync_minutes: int = 10
    max_files: int = 10_000
    max_mb: int = 2_048
    max_depth: int = 12

    @property
    def max_bytes(self) -> int:
        return self.max_mb * 1024 * 1024


async def folder_settings(store: Store) -> FolderSettings:
    async def number(key: str) -> int:
        value = await store.setting(key)
        return int(value) if value is not None else int(DEFAULTS[key])

    return FolderSettings(
        sync_minutes=await number(DOCUMENTS_FOLDER_SYNC_MINUTES),
        max_files=await number(DOCUMENTS_FOLDER_MAX_FILES),
        max_mb=await number(DOCUMENTS_FOLDER_MAX_MB),
        max_depth=await number(DOCUMENTS_FOLDER_MAX_DEPTH),
    )


def _admin(request: Request) -> None:
    expected = request.app.state.settings.admin_token
    given = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(given, f"Bearer {expected}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Workbench's settings are changed from Eugene's console.",
        )


router = APIRouter(dependencies=[Depends(_admin)])


@router.get("/v1/config/schema", response_model=ConfigSchema, response_model_exclude_none=True)
async def get_schema(request: Request) -> ConfigSchema:
    return ConfigSchema(
        component="workbench",
        fields=await _fields(request),
        categories={
            "people": "People and their chats",
            "memory": "Memory",
            "documents": "Documents",
        },
    )


async def _fields(request: Request) -> list[ConfigField]:
    """The fields, with the search model setting showing what it comes to now (settings
    never lie): the models Eugene lists as suggestions, and a sentence saying which one is
    in use, or why none is. A gateway that cannot be asked leaves it as it is."""
    saved = getattr(request.app.state, "saved", None)
    meaning = getattr(saved, "meaning", None)
    if meaning is None:
        return FIELDS
    try:
        suggestions, status_ = await meaning.setting_view()
    except Exception:  # a settings page never fails because a gateway did
        return FIELDS
    shown = [
        f.model_copy(update={"suggestions": [AUTO, *suggestions], "status": status_})
        if f.key == DOCUMENTS_EMBEDDING_MODEL
        else f
        for f in FIELDS
    ]
    return shown


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: Store = request.app.state.store
    memory = await memory_settings(store)
    documents = await document_settings(store)
    folders = await folder_settings(store)
    return ConfigDocument.model_validate(
        {
            OWNER_READS_CHATS: await owner_reads_chats(store),
            MEMORY_ENABLED: memory.enabled,
            MEMORY_MAX_NOTES: memory.max_notes,
            MEMORY_EXTERNAL_MODELS: memory.external,
            DOCUMENTS_ENABLED: documents.enabled,
            DOCUMENTS_MAX_FILE_MB: documents.max_file_mb,
            DOCUMENTS_WHOLE_UP_TO_TOKENS: documents.whole_up_to_tokens,
            DOCUMENTS_MAX_PASSAGES: documents.max_passages,
            DOCUMENTS_EXTERNAL_MODELS: documents.external,
            DOCUMENTS_EMBEDDING_MODEL: documents.embedding_model,
            DOCUMENTS_EXTERNAL_EMBEDDING: documents.external_embedding,
            DOCUMENTS_EMBED_PER_MINUTE: documents.embed_per_minute,
            DOCUMENTS_FOLDER_SYNC_MINUTES: folders.sync_minutes,
            DOCUMENTS_FOLDER_MAX_FILES: folders.max_files,
            DOCUMENTS_FOLDER_MAX_MB: folders.max_mb,
            DOCUMENTS_FOLDER_MAX_DEPTH: folders.max_depth,
        }
    )


def _problem_with(key: str, value: Any) -> str | None:
    """Why `value` is not one this setting takes, or None when it is. A
    `bool` is not an integer here, whatever Python says."""
    if key in (OWNER_READS_CHATS, MEMORY_ENABLED, DOCUMENTS_ENABLED, DOCUMENTS_EXTERNAL_EMBEDDING):
        return None if isinstance(value, bool) else "Must be true or false."
    if key == DOCUMENTS_EMBEDDING_MODEL:
        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_MODEL_ID:
            return None
        return f"Must be auto, or the name of a search model (at most {MAX_MODEL_ID} characters)."
    if key == DOCUMENTS_EMBED_PER_MINUTE:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_EMBED_PER_MINUTE <= value <= MAX_EMBED_PER_MINUTE
        ):
            return None
        return f"Must be a whole number from {MIN_EMBED_PER_MINUTE} to {MAX_EMBED_PER_MINUTE:,}."
    ranges = {
        DOCUMENTS_FOLDER_SYNC_MINUTES: (MIN_SYNC_MINUTES, MAX_SYNC_MINUTES),
        DOCUMENTS_FOLDER_MAX_FILES: (MIN_FOLDER_FILES, MAX_FOLDER_FILES),
        DOCUMENTS_FOLDER_MAX_MB: (MIN_FOLDER_MB, MAX_FOLDER_MB),
        DOCUMENTS_FOLDER_MAX_DEPTH: (MIN_FOLDER_DEPTH, MAX_FOLDER_DEPTH),
    }
    if key in ranges:
        low, high = ranges[key]
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            return None
        return f"Must be a whole number from {low:,} to {high:,}."
    if key == DOCUMENTS_MAX_FILE_MB:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 1 <= value <= MAX_FILE_MB_LIMIT
        ):
            return None
        return f"Must be a whole number from 1 to {MAX_FILE_MB_LIMIT}."
    if key == DOCUMENTS_WHOLE_UP_TO_TOKENS:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_WHOLE_TOKENS <= value <= MAX_WHOLE_TOKENS
        ):
            return None
        return f"Must be a whole number from {MIN_WHOLE_TOKENS:,} to {MAX_WHOLE_TOKENS:,}."
    if key == DOCUMENTS_MAX_PASSAGES:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_PASSAGES_LIMIT <= value <= MAX_PASSAGES_LIMIT
        ):
            return None
        return f"Must be a whole number from {MIN_PASSAGES_LIMIT:,} to {MAX_PASSAGES_LIMIT:,}."
    if key == MEMORY_MAX_NOTES:
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_NOTES_LIMIT:
            return None
        return f"Must be a whole number from 1 to {MAX_NOTES_LIMIT:,}."
    if key in (MEMORY_EXTERNAL_MODELS, DOCUMENTS_EXTERNAL_MODELS):
        return None if value in EXTERNAL_CHOICES else "Must be never, ask or always."
    return "Workbench has no such setting."


@router.patch("/v1/config", response_model=ConfigUpdateResult)
async def patch_config(request: Request, body: ConfigUpdateRequest) -> ConfigUpdateResult:
    store: Store = request.app.state.store
    applied: list[str] = []
    rejected: list[ConfigFieldError] = []
    for key, value in (body.model_extra or {}).items():
        if key not in DEFAULTS:
            rejected.append(ConfigFieldError(key=key, message="Workbench has no such setting."))
            continue
        # `null` is the trio's way of saying "back to the default".
        problem = _problem_with(key, value) if value is not None else None
        if problem is not None:
            rejected.append(ConfigFieldError(key=key, message=problem))
            continue
        await store.put_setting(key, value)
        applied.append(key)
    return ConfigUpdateResult(applied=applied, rejected=rejected, requiresRestart=False)
