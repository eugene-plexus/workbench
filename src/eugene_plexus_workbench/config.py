"""Workbench's settings: the config trio the registry expects of apps we ship.

`GET /v1/config`, `GET /v1/config/schema` and `PATCH /v1/config`, in
`common.yaml`'s shapes, which the console renders on Workbench's page and
the agent checks every answer against. The bearer is the admin token the
agent hands Workbench at each start, and nothing else: no person signed
in to Workbench can change these, the owner included -- they are changed
from Eugene's console, by its operator.

Nineteen settings. `ownerReadsChats` (W13): whether the owner may read the chats
of the people they give Workbench to (Troy, C3 call 3: each business
decides). `removedPeopleDays` (row 7): how long the data of a person removed from Eugene is
hidden before it is deleted (`removal.py`). Three are memory's (`memory.py`, MR1): whether people
may keep notes at all, how many each may keep, and whether notes may be sent
to a model that runs outside this install's machines. Memory is not covered
by `ownerReadsChats`: no setting here lets anyone read another person's
notes. One is connections' (MR9b, `connections.py`): whether people may connect
other apps to their own notes and documents. Five are documents' (`context.py`,
MR2 and MR4): whether Workbench reads
documents at all (the files people attach, and the ones they keep), the most it
takes of one file, how long a file may be and still be read whole by default,
how many passages one person may keep, and whether passages of their saved
documents may be sent to a model that runs outside this install's machines. Three are
search by meaning's (MR5, `knowledge/meaning.py`): which search model finds passages by
what they say (`auto`, or one named), whether the text of documents may go to one that
runs outside the machines, and how fast Workbench asks it to add meaning to documents.
Two are sorting's (MR6b, `knowledge/sorting.py`): which result sorter puts the best of the
passages a search found in order (`auto`, `off`, or one named; a sorter outside the machines
is used only when it is named and the setting above allows the text to go there), and the
seconds a sort may add to an answer, over which passages are left in the order they were found.
Four are the folders people point their documents at (MR7, `knowledge/folder_sync.py`): how
often Workbench looks at them, and how many files, how many megabytes and how many folders deep
it takes of one. Saved documents are not covered by `ownerReadsChats` either. Four are long
chats' (row 26, `long_chats.py`): whether a chat that outgrows its model's window is summarised,
at what share of the window (a person's permission group may set its own), the ceiling for a model
outside the machines, and which model writes the summaries. Every one takes effect at once, and
`null` puts one back to its default.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from . import long_chats, memory_policy
from ._generated.models import (
    ConfigDocument,
    ConfigField,
    ConfigFieldError,
    ConfigFieldShowWhen,
    ConfigFieldStatus,
    ConfigFieldStatusLevel,
    ConfigSchema,
    ConfigUpdateRequest,
    ConfigUpdateResult,
    ConfigValueType,
    ModelRole,
)
from .store import Store

log = logging.getLogger(__name__)

OWNER_READS_CHATS = "ownerReadsChats"
#: Row 7: whether a removed person's data is kept (hidden) for a number of days, or not at all,
#: and how many days.
REMOVED_PEOPLE_RETENTION = "removedPeopleRetention"
RETENTION_DAYS, RETENTION_NONE = "days", "none"
RETENTION_CHOICES = (RETENTION_DAYS, RETENTION_NONE)
REMOVED_PEOPLE_DAYS = "removedPeopleDays"
MIN_REMOVED_DAYS, MAX_REMOVED_DAYS = 1, 365
MEMORY_ENABLED = "memoryEnabled"
MEMORY_MAX_NOTES = "memoryMaxNotes"
MEMORY_MAX_BODY_CHARS = "memoryMaxBodyChars"
MEMORY_EXTERNAL_MODELS = "memoryExternalModels"
MEMORY_SUGGESTIONS = "memorySuggestions"
SUGGESTION_CHOICES = ("off", "choose", "measured")
#: MV3b: the memory write policy, whether people may loosen it, and Custom's rows.
MEMORY_WRITE_POLICY = "memoryWritePolicy"
MEMORY_POLICY_PEOPLE = "memoryPolicyPeople"
#: MV5: which memory servers people may connect: none, those off this host, or any address.
MEMORY_SERVERS = "memoryServers"
SERVER_CHOICES = ("off", "outside", "any")
POLICY_ROW_KEYS = {
    "source": "memoryPolicySource",
    "secrets": "memoryPolicySecrets",
    "sensitive": "memoryPolicySensitive",
    "untrusted": "memoryPolicyUntrusted",
    "instructions": "memoryPolicyInstructions",
    "keepAuto": "memoryPolicyKeepAuto",
    "connections": "memoryPolicyConnections",
    "unseenLocal": "memoryPolicyUnseenLocal",
}
CONNECTIONS = "connections"
DOCUMENTS_ENABLED = "documentsEnabled"
DOCUMENTS_MAX_FILE_MB = "documentsMaxFileMB"
DOCUMENTS_WHOLE_UP_TO_TOKENS = "documentsWholeUpToTokens"
DOCUMENTS_MAX_PASSAGES = "documentsMaxPassagesPerPerson"
DOCUMENTS_EXTERNAL_MODELS = "documentsExternalModels"
DOCUMENTS_EMBEDDING_MODEL = "documentsEmbeddingModel"
DOCUMENTS_EXTERNAL_EMBEDDING = "documentsExternalEmbedding"
DOCUMENTS_EMBED_PER_MINUTE = "documentsEmbedRequestsPerMinute"
DOCUMENTS_SORT_MODEL = "documentsSortModel"
DOCUMENTS_SORT_SECONDS = "documentsSortSeconds"
DOCUMENTS_WHOLE_BY_PARTS = "documentsWholeByParts"
DOCUMENTS_PAGE_READER = "documentsPageReader"
DOCUMENTS_PAGE_READER_MAX_PAGES = "documentsPageReaderMaxPages"
DOCUMENTS_FOLDER_SYNC_MINUTES = "documentsFolderSyncMinutes"
DOCUMENTS_FOLDER_MAX_FILES = "documentsFolderMaxFiles"
DOCUMENTS_FOLDER_MAX_MB = "documentsFolderMaxMB"
DOCUMENTS_FOLDER_MAX_DEPTH = "documentsFolderMaxDepth"
#: Row 26: long chats.
LONG_CHATS_SUMMARISE = "longChatsSummarise"
LONG_CHATS_PERCENT = "longChatsSummariseAtPercent"
LONG_CHATS_CEILING = "longChatsOutsideCeilingTokens"
LONG_CHATS_MODEL = "longChatsSummaryModel"

#: How far a note, or a passage of a saved document, may travel to a model outside
#: the machines (`memory_api`'s `resolve_external` says how the owner's and a person's
#: choices meet).
EXTERNAL_CHOICES = ("never", "ask", "always")
MAX_NOTES_LIMIT = 200_000
#: A note's body (MV1): the store's own ceiling, which the setting cannot pass.
MAX_BODY_CHARS_LIMIT = 20_000
MAX_FILE_MB_LIMIT = 500
MIN_WHOLE_TOKENS, MAX_WHOLE_TOKENS = 500, 200_000
MIN_PASSAGES_LIMIT, MAX_PASSAGES_LIMIT = 100, 1_000_000
MIN_EMBED_PER_MINUTE, MAX_EMBED_PER_MINUTE = 1, 6_000
MIN_SORT_SECONDS, MAX_SORT_SECONDS = 0.5, 120.0
MIN_SYNC_MINUTES, MAX_SYNC_MINUTES = 1, 1_440
MIN_FOLDER_FILES, MAX_FOLDER_FILES = 10, 100_000
MIN_FOLDER_MB, MAX_FOLDER_MB = 1, 102_400
MIN_FOLDER_DEPTH, MAX_FOLDER_DEPTH = 1, 31
MAX_MODEL_ID = 256
#: The search model setting's own word for "the first one that runs on this install's machines".
AUTO = "auto"
#: The result sorter setting's other word: passages are left in the order the search found them.
OFF = "off"

FIELDS = [
    ConfigField(
        key=OWNER_READS_CHATS,
        label="The owner may read people's chats and media",
        description=(
            "Lets the owner of this install read, but not change, the chats and "
            "media of everyone given Workbench.\n\n"
            "That includes chats and media made before it was turned on, and each "
            "person sees a line above their chats saying so. Media means images and "
            "other results in their bins. Off, Workbench refuses reads of anyone "
            "else's chats and media. Local servers run with Workbench's file access, "
            "so use only programs trusted with everyone's data. This never covers a "
            "person's memory notes: nobody can read another person's notes."
        ),
        category="people",
        valueType=ConfigValueType.boolean,
        default=False,
    ),
    ConfigField(
        key=REMOVED_PEOPLE_RETENTION,
        label="Keep a removed person's data",
        description=(
            "Whether a person removed from Eugene has their data kept, hidden, for some days, "
            "or deleted at once.\n\n"
            "Kept, their chats, files, notes and documents are hidden from everyone, the owner "
            "included; the owner can export them, keep them longer, or delete them now from "
            "Workbench's Settings. **Not at all** deletes them the moment Workbench finds out, "
            "with every copy Workbench keeps. A change applies to people removed after it: "
            "people already removed keep their dates."
        ),
        category="people",
        valueType=ConfigValueType.enum,
        enumValues=list(RETENTION_CHOICES),
        enumLabels=["For a number of days", "Not at all"],
        default=RETENTION_DAYS,
    ),
    ConfigField(
        key=REMOVED_PEOPLE_DAYS,
        label="Days before a removed person's data is deleted",
        description=(
            "How long Workbench hides the data of a person removed from Eugene before it "
            "deletes it.\n\n"
            "Deleting removes every copy Workbench keeps. A change applies to people removed "
            "after it: people already removed keep their dates."
        ),
        category="people",
        valueType=ConfigValueType.integer,
        default=30,
        minimum=MIN_REMOVED_DAYS,
        maximum=MAX_REMOVED_DAYS,
        showWhen=ConfigFieldShowWhen(key=REMOVED_PEOPLE_RETENTION, equals=RETENTION_DAYS),
    ),
    ConfigField(
        key=MEMORY_ENABLED,
        label="People can keep notes for Workbench to remember",
        description=(
            "Lets each person keep short notes about themselves that a chat sends "
            "to its model.\n\n"
            'People add notes on the Memory page or by writing "remember that ..." in '
            "a chat. Off, Workbench keeps no new notes and sends none to any model; "
            "people can still read, export and delete the notes they have. Each person "
            "can also turn memory off for themselves, and for one chat at a time."
        ),
        category="memory",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=MEMORY_MAX_NOTES,
        label="Most notes one person can keep",
        description=(
            "How many notes each person can keep.\n\n"
            "At the limit a person is told so and keeps no new note until they delete "
            "one; lowering it deletes nothing. Earlier versions of a note count. A chat "
            "sends pinned notes in every turn, at most 2,000 tokens or 4% of its model's "
            "window, and in each turn only the other notes that bear on what was asked, "
            "at most 6,000 tokens or 10% of the window."
        ),
        category="memory",
        valueType=ConfigValueType.integer,
        default=20_000,
        minimum=1,
        maximum=MAX_NOTES_LIMIT,
    ),
    ConfigField(
        key=MEMORY_MAX_BODY_CHARS,
        label="Longest note body",
        description=(
            "How many characters a note's body may hold, below its one-line title.\n\n"
            "A body holds a procedure, a brief or a checklist, in Markdown. A chat sends "
            "only the parts of a body that bear on what was asked. 0 means notes have no "
            "body. Lowering it shortens nothing already kept."
        ),
        category="memory",
        valueType=ConfigValueType.integer,
        default=MAX_BODY_CHARS_LIMIT,
        minimum=0,
        maximum=MAX_BODY_CHARS_LIMIT,
    ),
    ConfigField(
        key=MEMORY_EXTERNAL_MODELS,
        label="Send notes to models that run outside your machines",
        description=(
            "What a chat does with a person's notes when its model runs outside "
            "this install's own machines.\n\n"
            "Outside means a cloud account, or anywhere Eugene cannot confirm is "
            "one of them.\n\n"
            "- **Ask**: notes are held back until the person chooses to send them "
            "for that chat.\n"
            "- **Always**: notes are sent without asking.\n"
            "- **Never**: notes are never sent, and no person can change that.\n\n"
            "With Ask or Always, each person can choose for themselves; this is what a "
            "person who has not chosen gets."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(EXTERNAL_CHOICES),
        enumLabels=["Never", "Ask for each chat", "Always"],
        default="ask",
    ),
    ConfigField(
        key=MEMORY_SUGGESTIONS,
        label="Suggest notes from people's chats",
        description=(
            "Whether a chat's model may suggest notes from what a person wrote, for them to "
            "keep or turn away.\n\n"
            "A suggestion quotes the person's own words, never answers, files or pasted text, "
            "and Workbench refuses any that looks like a secret or is about health, religion, "
            "politics, sexuality, money or contact details. Nothing is kept until the person "
            "keeps it.\n\n"
            "- **Off**: no chat suggests notes.\n"
            "- **Each person chooses**: off until a person turns on Learn from my chats.\n"
            "- **On for measured models**: also on, for a person who has not chosen, when the "
            "chat's model passed Eugene's measurement of suggestions. A person can still turn "
            "it off."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(SUGGESTION_CHOICES),
        enumLabels=["Off", "Each person chooses", "On for measured models"],
        default="measured",
    ),
    ConfigField(
        key=CONNECTIONS,
        label="People may connect other apps to their notes and documents",
        description=(
            "Lets people connect apps such as Claude Code to their own notes and documents.\n\n"
            "A connection lets an app such as Claude Code, Codex or Open WebUI read the "
            "person's own notes and search their own documents through Workbench.\n\n"
            "Nothing is reachable until a person makes a connection and chooses what it may "
            "do. A connection reads only its own person's notes and documents, works only "
            "while they are signed in to Workbench, and can suggest notes only to their "
            "Suggested notes on their Memory page. The app may send what it reads to a model "
            "outside your machines.\n\n"
            "- **On**: people may make connections.\n"
            "- **Off**: every connection stops at once, and no one can make one."
        ),
        category="memory",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=DOCUMENTS_ENABLED,
        label="Workbench reads documents",
        description=(
            "Lets Workbench read files that people attach to chats or keep in My "
            "documents.\n\n"
            "- **On**: a PDF, Word, Markdown, text, CSV, HTML or code file attached "
            "to a chat is read and its text goes to the chat's model. People can "
            "also keep documents in My documents, which chats with Documents turned "
            "on search.\n"
            "- **Off**: no file is read. Only images and audio, and PDFs for models "
            "that take them as files, are attached, and no saved document is added or "
            "searched; people can still read and remove theirs. Files already read "
            "stay in the chat they were sent in."
        ),
        category="documents",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=DOCUMENTS_MAX_FILE_MB,
        label="Largest file Workbench reads (MiB)",
        description=(
            "The largest file Workbench will read (default: 50 MiB). Larger files are "
            "refused when attached, with this limit shown. A file is read in a child "
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
            "Files up to this many tokens are read whole, without asking.\n\n"
            "The limit is also capped at a quarter of the model's window. A longer "
            "file is read only when the person chooses to read it all, up to 70% of "
            "what remains of the window, or just its first part. Tokens are counted as "
            "characters divided by three, deliberately high."
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
            "The most passages one person's documents may come to, in My documents "
            "and in chats together.\n\n"
            "A passage is about 300 tokens; a page of text is two or three. A file "
            "that would take a person past the limit is skipped, and says so; a person "
            "at the limit is told and removes a document to add another. Lowering it "
            "deletes nothing."
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
            "What a chat does with passages from a person's saved documents when "
            "its model runs outside this install's own machines.\n\n"
            "Outside means a cloud account, or anywhere Eugene cannot confirm is "
            "one of them.\n\n"
            "- **Ask**: passages are held back until the person chooses to send them "
            "for that chat.\n"
            "- **Always**: passages are sent without asking.\n"
            "- **Never**: passages are never sent, and no person can change that.\n\n"
            "With Ask or Always, each person can choose for themselves; this is what a "
            "person who has not chosen gets. A file a person attaches to a chat is "
            "their own act and is not held back."
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
            "The model that finds a passage by what it says, not only by its "
            "words.\n\n"
            "**Automatic** uses the first search model running on this install's "
            "own machines, and keeps the one a person's search was built with "
            "while it is running. A model outside your machines is used only if "
            "you name it here and also allow it below.\n\n"
            "A person whose search was built with another model sees search by meaning "
            "paused until they choose Rebuild. With no search model running, documents "
            "are searched by words only."
        ),
        category="documents",
        valueType=ConfigValueType.string,
        default=AUTO,
    ),
    ConfigField(
        key=DOCUMENTS_EXTERNAL_EMBEDDING,
        label="Send the text of documents to a search model or result sorter outside your machines",
        description=(
            "Lets Workbench send people's document text and questions to a search "
            "model or result sorter outside your machines.\n\n"
            "Off, they go only to ones that run on this install's own machines. On, a "
            "search model outside them (a cloud account) receives the text of every "
            "passage of everyone's documents and every question, and a result sorter "
            "outside them receives the best passages of every question and the "
            "question itself. Turn it on only where that is acceptable for everything "
            "people keep here."
        ),
        category="documents",
        valueType=ConfigValueType.boolean,
        default=False,
    ),
    ConfigField(
        key=DOCUMENTS_EMBED_PER_MINUTE,
        label="Most requests a minute for adding meaning to documents",
        description=(
            "The most requests a minute Workbench sends the search model while it "
            "adds meaning to documents.\n\n"
            "Requests go one at a time, 16 passages each. The default is half of what "
            "a new key allows (60 a minute and 2 at once), so a chat never waits "
            "behind an import. Raise it only after raising the key's own limits in "
            "Eugene's console, or adding meaning will be refused as over the limit."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=30,
        minimum=MIN_EMBED_PER_MINUTE,
        maximum=MAX_EMBED_PER_MINUTE,
    ),
    ConfigField(
        key=DOCUMENTS_SORT_MODEL,
        label="Result sorter for My documents",
        description=(
            "The model that puts a search's best passages in the order that "
            "answers the question best.\n\n"
            "**Automatic** uses the first result sorter running on this install's own "
            "machines. **Off** keeps the order the search found. A sorter outside your "
            "machines is used only if you name it here and also allow sending document "
            "text outside them. If the sorter is stopped, too slow or fails, the "
            "passages keep the order they were found and Test a question says why."
        ),
        category="documents",
        valueType=ConfigValueType.string,
        default=AUTO,
    ),
    ConfigField(
        key=DOCUMENTS_SORT_SECONDS,
        label="Sort time (seconds; a sort is given up on after twice this)",
        description=(
            "How long a sort may be expected to take before Workbench leaves it "
            "out.\n\n"
            "Workbench estimates it from the passages' tokens and the sorter's recent "
            "speed. Over this, passages stay in the order the search found them, and "
            "the Documents screen says why. A sort that takes more than twice this is "
            "given up on, and the sorter is retried by a small test, never by a "
            "question. Raise it for a sorter on a processor: 18 passages took about 7 "
            "seconds on a 12-thread one, 0.6 on an RTX 5090."
        ),
        category="documents",
        valueType=ConfigValueType.number,
        default=3.0,
        minimum=MIN_SORT_SECONDS,
        maximum=MAX_SORT_SECONDS,
    ),
    ConfigField(
        key=DOCUMENTS_FOLDER_SYNC_MINUTES,
        label="How often Workbench looks at people's folders (minutes)",
        description=(
            "How often Workbench looks at the folders people have pointed their "
            "documents at.\n\n"
            "Each look adds new files, reads an edited file again (keeping what it had "
            "if the words are the same) and takes out a deleted one, so an answer is "
            "never more than this many minutes behind the folder. Each person can also "
            "choose Sync now. A shorter time finds edits sooner and reads the disk "
            "more often. Workbench only reads a folder; it never changes anything in "
            "it."
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
            "The most files Workbench takes from one folder in a person's "
            "documents, not counting ignored files.\n\n"
            "Files are taken in name order, nearest the top first; the rest are left "
            "out, and the person is told. Lowering it removes the extra files at the "
            "next look. A big folder becomes searchable by degrees, and the Documents "
            "screen says how far it has got."
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
            "The most megabytes Workbench copies from one folder, because it keeps "
            "its own copy of each file it reads.\n\n"
            "A file that would take the folder past the limit is skipped, and says so. "
            "This counts only the files Workbench reads, not pictures or other files "
            "it skips. The most it reads of one file is the largest-file setting "
            "above."
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
    ConfigField(
        key=LONG_CHATS_SUMMARISE,
        label="Summarise chats that outgrow their model's window",
        description=(
            "Lets a long chat go on by summarising its earlier messages for the model.\n\n"
            "The chat's own model writes the summary, and every message stays in the chat. Off, "
            "a chat too long for its model is refused before it is sent. Each person can turn it "
            "off for one chat."
        ),
        category="chats",
        valueType=ConfigValueType.boolean,
        default=True,
    ),
    ConfigField(
        key=LONG_CHATS_PERCENT,
        label="Summarise at this share of the window (percent)",
        description=(
            "How full a chat's model window may get before earlier messages are summarised.\n\n"
            "A person's permission group may set its own share, and then that applies to them. "
            "Summarising leaves the chat at about half the window, so several turns go before "
            "the next summary."
        ),
        category="chats",
        valueType=ConfigValueType.integer,
        default=long_chats.SUMMARISE_AT_PERCENT,
        minimum=long_chats.MIN_PERCENT,
        maximum=long_chats.MAX_PERCENT,
    ),
    ConfigField(
        key=LONG_CHATS_CEILING,
        label="Summarise at this many tokens for models outside your machines",
        description=(
            "A chat with a model outside this install's machines is also summarised at this "
            "many tokens.\n\n"
            "Every turn sends the whole chat, and an outside model bills for it. 0 means no "
            "ceiling: only the share of the window applies."
        ),
        category="chats",
        valueType=ConfigValueType.integer,
        default=long_chats.OUTSIDE_CEILING_TOKENS,
        minimum=0,
        maximum=long_chats.MAX_CEILING,
    ),
    ConfigField(
        key=LONG_CHATS_MODEL,
        label="Model that writes summaries",
        description=(
            "Which model summarises a long chat's earlier messages.\n\n"
            "Empty is the chat's own model, which has the messages in its cache already. "
            "Otherwise name a model on this install's machines: a chat's words never go to "
            "a model outside them for a summary. If it does not answer, the chat's own model "
            "writes the summary."
        ),
        category="chats",
        valueType=ConfigValueType.string,
        default="",
    ),
]

_ROW_LABELS: dict[str, tuple[str, str, list[str]]] = {
    "source": (
        "Custom policy: what a note may be drawn from",
        "**Own words**: the person's own words, pasted material left out. **Anything**: any "
        "text in the chat, answers and tool results included.",
        ["Own words", "Anything in the chat"],
    ),
    "secrets": (
        "Custom policy: text shaped like a secret",
        "Whether a model's note that looks like a password, key, card or account number is "
        "refused. The check catches the shape of a secret, not its meaning.",
        ["Refused", "Allowed"],
    ),
    "sensitive": (
        "Custom policy: notes about sensitive subjects",
        "Health, religion, politics, sexuality, money and contact details.",
        ["Refused", "To Suggested notes", "Kept"],
    ),
    "untrusted": (
        "Custom policy: web pages, tool results, documents or pasted text in the chat",
        "What happens to a note a model writes while such text is in the turns it read.",
        ["Dropped", "To Suggested notes", "Kept like any other"],
    ),
    "instructions": (
        "Custom policy: instructions and procedures from a model",
        "Instructions and procedures shape every later answer.",
        ["Never", "To Suggested notes", "May be kept automatically"],
    ),
    "keepAuto": (
        "Custom policy: Keep automatically",
        "Whether people may let a model keep notes without asking.",
        ["Not available", "Available"],
    ),
    "connections": (
        "Custom policy: suggestions from connected apps",
        "Whether apps connected to a person's notes may suggest notes.",
        ["Off", "To Suggested notes"],
    ),
    "unseenLocal": (
        "Custom policy: notes a model kept, before the person has seen them",
        "**Stay on the machines**: not sent to outside models or connected apps until seen.",
        ["Stay on the machines", "Go where notes go"],
    ),
}

POLICY_FIELDS = [
    ConfigField(
        key=MEMORY_WRITE_POLICY,
        label="What models may write to people's memory",
        description=(
            "What models may write to people's memory, checked by Workbench before a note is "
            "stored.\n\n"
            "- **Strict**: no model keeps notes by itself, and a note drawn beside web pages, "
            "tool results, documents or pasted text is dropped.\n"
            "- **Standard**: people may let a model keep notes; with such text in the chat a "
            "note waits in Suggested notes, and a kept note stays on your machines until its "
            "person has seen it.\n"
            "- **Open**: models may keep notes drawn from anything in a chat; for testing.\n"
            "- **Custom**: set each rule.\n\n"
            "Notes shaped like a secret are refused under every preset."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(memory_policy.NAMES),
        enumLabels=["Strict", "Standard", "Open", "Custom"],
        default=memory_policy.STANDARD,
    ),
    ConfigField(
        key=MEMORY_POLICY_PEOPLE,
        label="People may choose a looser write policy",
        description=(
            "A person may always choose a stricter policy for their own memory.\n\n"
            "- **Stricter only**: never looser than this install's.\n"
            "- **Any**: any preset, looser too."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=[memory_policy.STRICTER_ONLY, memory_policy.ANY],
        enumLabels=["Stricter only", "Any"],
        default=memory_policy.STRICTER_ONLY,
    ),
    ConfigField(
        key=MEMORY_SERVERS,
        label="Memory servers people may connect",
        description=(
            "Whether people may connect their own memory server, which receives their chats "
            "and is asked for memory each turn. Eugene cannot check what it keeps.\n\n"
            "- **Off**: none.\n"
            "- **Off this host**: any https address, or http on a private network; never this "
            "host or loopback.\n"
            "- **Any address**: loopback too; for testing."
        ),
        category="memory",
        valueType=ConfigValueType.enum,
        enumValues=list(SERVER_CHOICES),
        enumLabels=["Off", "Off this host", "Any address"],
        default="outside",
    ),
    *[
        ConfigField(
            key=POLICY_ROW_KEYS[row],
            label=label,
            description=description,
            category="memory",
            valueType=ConfigValueType.enum,
            enumValues=list(memory_policy.ROWS[row]),
            enumLabels=labels,
            default=memory_policy.PRESETS[memory_policy.STANDARD][row],
            showWhen=ConfigFieldShowWhen(key=MEMORY_WRITE_POLICY, equals=memory_policy.CUSTOM),
        )
        for row, (label, description, labels) in _ROW_LABELS.items()
    ],
]
FIELDS += POLICY_FIELDS
FIELDS += [
    ConfigField(
        key=DOCUMENTS_WHOLE_BY_PARTS,
        label="Read whole documents in parts",
        description=(
            "Reads all parts for a whole-document question.\n\n"
            "Always starts automatically. Ask offers Read all of it first. "
            "Never uses passages only. At most 3 documents and 40 parts; Stop ends the reading."
        ),
        category="documents",
        valueType=ConfigValueType.enum,
        default="always",
        enumValues=["ask", "always", "never"],
    ),
    ConfigField(
        key=DOCUMENTS_PAGE_READER,
        label="Model that reads scanned pages",
        description=(
            "Off: scanned pages are skipped.\n\n"
            "Choose a gateway vision model, or role:reads-pages. Each person chooses "
            "Read scanned pages before images are sent. The passages' outside-model rule applies."
        ),
        category="documents",
        valueType=ConfigValueType.string,
        default="",
        suggestions=["", "role:reads-pages"],
    ),
    ConfigField(
        key=DOCUMENTS_PAGE_READER_MAX_PAGES,
        label="Most scanned pages read per document",
        description=(
            "Limits the scanned pages read from one document.\n\n"
            "The page count and model are shown before a person starts reading. "
            "Documents over this limit are left unread with their count."
        ),
        category="documents",
        valueType=ConfigValueType.integer,
        default=200,
        minimum=1,
        maximum=5000,
    ),
]

DEFAULTS: dict[str, Any] = {
    OWNER_READS_CHATS: False,
    REMOVED_PEOPLE_RETENTION: RETENTION_DAYS,
    REMOVED_PEOPLE_DAYS: 30,
    MEMORY_ENABLED: True,
    MEMORY_MAX_NOTES: 20_000,
    MEMORY_MAX_BODY_CHARS: MAX_BODY_CHARS_LIMIT,
    MEMORY_EXTERNAL_MODELS: "ask",
    MEMORY_SUGGESTIONS: "measured",
    MEMORY_WRITE_POLICY: memory_policy.STANDARD,
    MEMORY_POLICY_PEOPLE: memory_policy.STRICTER_ONLY,
    MEMORY_SERVERS: "outside",
    **{
        key: memory_policy.PRESETS[memory_policy.STANDARD][row]
        for row, key in POLICY_ROW_KEYS.items()
    },
    CONNECTIONS: True,
    DOCUMENTS_ENABLED: True,
    DOCUMENTS_MAX_FILE_MB: 50,
    DOCUMENTS_WHOLE_UP_TO_TOKENS: 6000,
    DOCUMENTS_MAX_PASSAGES: 100_000,
    DOCUMENTS_EXTERNAL_MODELS: "ask",
    DOCUMENTS_EMBEDDING_MODEL: AUTO,
    DOCUMENTS_EXTERNAL_EMBEDDING: False,
    DOCUMENTS_EMBED_PER_MINUTE: 30,
    DOCUMENTS_SORT_MODEL: AUTO,
    DOCUMENTS_SORT_SECONDS: 3.0,
    DOCUMENTS_WHOLE_BY_PARTS: "always",
    DOCUMENTS_PAGE_READER: "",
    DOCUMENTS_PAGE_READER_MAX_PAGES: 200,
    DOCUMENTS_FOLDER_SYNC_MINUTES: 10,
    DOCUMENTS_FOLDER_MAX_FILES: 10_000,
    DOCUMENTS_FOLDER_MAX_MB: 2_048,
    DOCUMENTS_FOLDER_MAX_DEPTH: 12,
    LONG_CHATS_SUMMARISE: True,
    LONG_CHATS_PERCENT: long_chats.SUMMARISE_AT_PERCENT,
    LONG_CHATS_CEILING: long_chats.OUTSIDE_CEILING_TOKENS,
    LONG_CHATS_MODEL: "",
}


async def connections_enabled(store: Store) -> bool:
    value = await store.setting(CONNECTIONS)
    return bool(value) if value is not None else bool(DEFAULTS[CONNECTIONS])


async def owner_reads_chats(store: Store) -> bool:
    value = await store.setting(OWNER_READS_CHATS)
    return bool(value) if value is not None else bool(DEFAULTS[OWNER_READS_CHATS])


async def removed_people_days(store: Store) -> int:
    value = await store.setting(REMOVED_PEOPLE_DAYS)
    return int(value) if value is not None else int(DEFAULTS[REMOVED_PEOPLE_DAYS])


async def removed_people_retention(store: Store) -> str:
    """`days` (kept hidden for `removedPeopleDays`) or `none` (deleted at once)."""
    value = await store.setting(REMOVED_PEOPLE_RETENTION)
    return str(value) if value in RETENTION_CHOICES else str(DEFAULTS[REMOVED_PEOPLE_RETENTION])


@dataclass(frozen=True)
class MemorySettings:
    """The install's memory settings, as they are in effect now."""

    enabled: bool
    max_notes: int
    external: str
    #: Memory suggestions (MR8): `off`, `choose` (each person turns them on) or `measured`
    #: (also on, for a person who has not chosen, with a model that passed the measurement).
    suggestions: str = "measured"
    #: MV1: the most characters a new or edited note's body may hold.
    max_body_chars: int = MAX_BODY_CHARS_LIMIT
    #: MV3b: the write policy's name, Custom's rows as set (Standard's where unset), and
    #: whether people may loosen it.
    policy_name: str = memory_policy.STANDARD
    policy_rows: dict[str, str] = field(
        default_factory=lambda: dict(memory_policy.PRESETS[memory_policy.STANDARD])
    )
    policy_people: str = memory_policy.STRICTER_ONLY
    #: MV5: which memory servers people may connect (`SERVER_CHOICES`).
    servers: str = "outside"

    @property
    def policy(self) -> memory_policy.Policy:
        """The install's write policy in effect: a preset, or Custom's rows."""
        if self.policy_name in memory_policy.PRESETS:
            return memory_policy.preset(self.policy_name)
        return memory_policy.custom(self.policy_rows)


async def memory_settings(store: Store) -> MemorySettings:
    enabled = await store.setting(MEMORY_ENABLED)
    limit = await store.setting(MEMORY_MAX_NOTES)
    body = await store.setting(MEMORY_MAX_BODY_CHARS)
    external = await store.setting(MEMORY_EXTERNAL_MODELS)
    suggestions = await store.setting(MEMORY_SUGGESTIONS)
    name = await store.setting(MEMORY_WRITE_POLICY)
    people = await store.setting(MEMORY_POLICY_PEOPLE)
    servers = await store.setting(MEMORY_SERVERS)
    rows = {row: await store.setting(key) for row, key in POLICY_ROW_KEYS.items()}
    return MemorySettings(
        policy_name=str(name) if name in memory_policy.NAMES else memory_policy.STANDARD,
        policy_rows=memory_policy.custom(rows).rows,
        policy_people=(
            str(people)
            if people in (memory_policy.STRICTER_ONLY, memory_policy.ANY)
            else memory_policy.STRICTER_ONLY
        ),
        servers=str(servers) if servers in SERVER_CHOICES else str(DEFAULTS[MEMORY_SERVERS]),
        suggestions=(
            str(suggestions)
            if suggestions in SUGGESTION_CHOICES
            else str(DEFAULTS[MEMORY_SUGGESTIONS])
        ),
        enabled=bool(enabled) if enabled is not None else bool(DEFAULTS[MEMORY_ENABLED]),
        max_notes=int(limit) if limit is not None else int(DEFAULTS[MEMORY_MAX_NOTES]),
        max_body_chars=int(body) if body is not None else MAX_BODY_CHARS_LIMIT,
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
    #: The result sorter (MR6b): `auto`, `off`, or the id the owner named; and the seconds a sort
    #: may add to an answer. A sorter outside the machines needs `external_embedding` too.
    sort_model: str = AUTO
    sort_seconds: float = 3.0
    whole_by_parts: str = "always"
    page_reader: str = ""
    page_reader_max_pages: int = 200

    @property
    def max_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


def _sort_seconds(value: Any) -> float:
    """The Sort time in effect: what was saved when it is a number the setting takes (a database
    that was edited by hand never stops a search), otherwise the default."""
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and MIN_SORT_SECONDS <= value <= MAX_SORT_SECONDS
    ):
        return float(value)
    return float(DEFAULTS[DOCUMENTS_SORT_SECONDS])


async def document_settings(store: Store) -> DocumentSettings:
    enabled = await store.setting(DOCUMENTS_ENABLED)
    largest = await store.setting(DOCUMENTS_MAX_FILE_MB)
    whole = await store.setting(DOCUMENTS_WHOLE_UP_TO_TOKENS)
    passages = await store.setting(DOCUMENTS_MAX_PASSAGES)
    external = await store.setting(DOCUMENTS_EXTERNAL_MODELS)
    model = await store.setting(DOCUMENTS_EMBEDDING_MODEL)
    outside = await store.setting(DOCUMENTS_EXTERNAL_EMBEDDING)
    rate = await store.setting(DOCUMENTS_EMBED_PER_MINUTE)
    sorter = await store.setting(DOCUMENTS_SORT_MODEL)
    sort_time = await store.setting(DOCUMENTS_SORT_SECONDS)
    whole_parts = await store.setting(DOCUMENTS_WHOLE_BY_PARTS)
    page_model = await store.setting(DOCUMENTS_PAGE_READER)
    page_max = await store.setting(DOCUMENTS_PAGE_READER_MAX_PAGES)
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
        sort_model=(sorter.strip() if isinstance(sorter, str) and sorter.strip() else AUTO),
        sort_seconds=_sort_seconds(sort_time),
        whole_by_parts=whole_parts if whole_parts in EXTERNAL_CHOICES else "always",
        page_reader=page_model.strip() if isinstance(page_model, str) else "",
        page_reader_max_pages=page_max
        if isinstance(page_max, int) and not isinstance(page_max, bool) and 1 <= page_max <= 5000
        else 200,
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


@dataclass(frozen=True)
class LongChatSettings:
    """The install's long-chat settings, as they are in effect now (row 26)."""

    summarise: bool = True
    percent: int = long_chats.SUMMARISE_AT_PERCENT
    ceiling: int = long_chats.OUTSIDE_CEILING_TOKENS
    #: The model that writes summaries; empty is the chat's own.
    model: str = ""


def _whole_in(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


async def long_chat_settings(store: Store) -> LongChatSettings:
    """What is saved, where it is a value the setting takes; the default otherwise, so a store
    edited by hand never stops a chat."""
    summarise = await store.setting(LONG_CHATS_SUMMARISE)
    percent = await store.setting(LONG_CHATS_PERCENT)
    ceiling = await store.setting(LONG_CHATS_CEILING)
    model = await store.setting(LONG_CHATS_MODEL)
    return LongChatSettings(
        summarise=summarise if isinstance(summarise, bool) else True,
        percent=(
            percent
            if _whole_in(percent, long_chats.MIN_PERCENT, long_chats.MAX_PERCENT)
            else long_chats.SUMMARISE_AT_PERCENT
        ),
        ceiling=(
            ceiling
            if ceiling == 0 or _whole_in(ceiling, long_chats.MIN_CEILING, long_chats.MAX_CEILING)
            else long_chats.OUTSIDE_CEILING_TOKENS
        ),
        model=model.strip() if isinstance(model, str) else "",
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
    from .person_context import catalog_only

    context = catalog_only.set(True)
    try:
        fields = await _fields(request)
    finally:
        catalog_only.reset(context)
    return ConfigSchema(
        component="workbench",
        fields=fields,
        categories={
            "people": "People and their chats",
            "memory": "Memory",
            "documents": "Documents",
            "chats": "Long chats",
        },
    )


def _suggestions_status() -> ConfigFieldStatus:
    """What "On for measured models" comes to now: which models Eugene's measurement recommends
    for Suggest (an install's own measurements add to these on the Memory screen)."""
    from .memory_models import measurement_for, recommended_for_suggest, shipped_names

    passed = [n for n in shipped_names() if recommended_for_suggest(measurement_for(n))]
    if not passed:
        return ConfigFieldStatus(
            level=ConfigFieldStatusLevel.info,
            text=(
                "No model has passed the measurement yet, so suggestions are on only for "
                "people who turn them on."
            ),
        )
    return ConfigFieldStatus(
        level=ConfigFieldStatusLevel.info,
        text=f"Models that passed the measurement: {', '.join(passed)}.",
    )


async def _fields(request: Request) -> list[ConfigField]:
    """The fields, with the search model and the result sorter settings showing what they come
    to now (settings never lie): the models Eugene lists as suggestions, and a sentence saying
    which one is in use, or why none is; and for Sort time, what it does with the sorter's
    measured speed. A gateway that cannot be asked leaves a field as it is."""
    saved = getattr(request.app.state, "saved", None)
    meaning = getattr(saved, "meaning", None)
    sorting = getattr(saved, "sorting", None)
    shown = [
        f.model_copy(update={"status": _suggestions_status()}) if f.key == MEMORY_SUGGESTIONS else f
        for f in FIELDS
    ]
    if meaning is not None:
        try:
            suggestions, status_ = await meaning.setting_view()
        except Exception:  # a settings page never fails because a gateway did
            log.warning(
                "the search model setting could not say what it comes to; it is left as it is",
                exc_info=True,
            )
        else:
            shown = [
                f.model_copy(update={"suggestions": [AUTO, *suggestions], "status": status_})
                if f.key == DOCUMENTS_EMBEDDING_MODEL
                else f
                for f in shown
            ]
    if sorting is not None:
        try:
            view = await sorting.fields()
        except Exception:  # nor because of a sorter
            log.warning(
                "the result sorter settings could not say what they come to; they are left as "
                "they are",
                exc_info=True,
            )
        else:
            shown = [
                f.model_copy(update={"suggestions": [AUTO, OFF, *view.ids], "status": view.model})
                if f.key == DOCUMENTS_SORT_MODEL
                else f.model_copy(update={"status": view.seconds})
                if f.key == DOCUMENTS_SORT_SECONDS
                else f
                for f in shown
            ]
    # Suggestions are choices, never a change to the saved value or its default.
    # Keep roles available even before their models are running or assigned.
    role_fields = {
        DOCUMENTS_EMBEDDING_MODEL: (ModelRole.embeddings, [AUTO]),
        DOCUMENTS_SORT_MODEL: (ModelRole.rerank, [AUTO, OFF]),
    }
    shown = [
        f.model_copy(
            update={
                "suggestions": list(
                    dict.fromkeys(
                        [
                            f"role:{role_fields[f.key][0].value}",
                            *(f.suggestions or role_fields[f.key][1]),
                        ]
                    )
                )
            }
        )
        if f.key in role_fields
        else f
        for f in shown
    ]
    return shown


@router.get("/v1/config", response_model=ConfigDocument)
async def get_config(request: Request) -> ConfigDocument:
    store: Store = request.app.state.store
    memory = await memory_settings(store)
    documents = await document_settings(store)
    folders = await folder_settings(store)
    chats = await long_chat_settings(store)
    return ConfigDocument.model_validate(
        {
            OWNER_READS_CHATS: await owner_reads_chats(store),
            REMOVED_PEOPLE_RETENTION: await removed_people_retention(store),
            REMOVED_PEOPLE_DAYS: await removed_people_days(store),
            MEMORY_ENABLED: memory.enabled,
            MEMORY_MAX_NOTES: memory.max_notes,
            MEMORY_MAX_BODY_CHARS: memory.max_body_chars,
            MEMORY_EXTERNAL_MODELS: memory.external,
            MEMORY_SUGGESTIONS: memory.suggestions,
            MEMORY_WRITE_POLICY: memory.policy_name,
            MEMORY_POLICY_PEOPLE: memory.policy_people,
            MEMORY_SERVERS: memory.servers,
            **{key: memory.policy_rows[row] for row, key in POLICY_ROW_KEYS.items()},
            CONNECTIONS: await connections_enabled(store),
            DOCUMENTS_ENABLED: documents.enabled,
            DOCUMENTS_MAX_FILE_MB: documents.max_file_mb,
            DOCUMENTS_WHOLE_UP_TO_TOKENS: documents.whole_up_to_tokens,
            DOCUMENTS_MAX_PASSAGES: documents.max_passages,
            DOCUMENTS_EXTERNAL_MODELS: documents.external,
            DOCUMENTS_EMBEDDING_MODEL: documents.embedding_model,
            DOCUMENTS_EXTERNAL_EMBEDDING: documents.external_embedding,
            DOCUMENTS_EMBED_PER_MINUTE: documents.embed_per_minute,
            DOCUMENTS_SORT_MODEL: documents.sort_model,
            DOCUMENTS_SORT_SECONDS: documents.sort_seconds,
            DOCUMENTS_WHOLE_BY_PARTS: documents.whole_by_parts,
            DOCUMENTS_PAGE_READER: documents.page_reader,
            DOCUMENTS_PAGE_READER_MAX_PAGES: documents.page_reader_max_pages,
            DOCUMENTS_FOLDER_SYNC_MINUTES: folders.sync_minutes,
            DOCUMENTS_FOLDER_MAX_FILES: folders.max_files,
            DOCUMENTS_FOLDER_MAX_MB: folders.max_mb,
            DOCUMENTS_FOLDER_MAX_DEPTH: folders.max_depth,
            LONG_CHATS_SUMMARISE: chats.summarise,
            LONG_CHATS_PERCENT: chats.percent,
            LONG_CHATS_CEILING: chats.ceiling,
            LONG_CHATS_MODEL: chats.model,
        }
    )


def _problem_with(key: str, value: Any) -> str | None:
    """Why `value` is not one this setting takes, or None when it is. A
    `bool` is not an integer here, whatever Python says."""
    if key == DOCUMENTS_WHOLE_BY_PARTS:
        return None if value in EXTERNAL_CHOICES else "Must be ask, always or never."
    if key == DOCUMENTS_PAGE_READER:
        return (
            None
            if isinstance(value, str) and len(value.strip()) <= MAX_MODEL_ID
            else "Use a model name, role:reads-pages, or leave it empty for Off."
        )
    if key == DOCUMENTS_PAGE_READER_MAX_PAGES:
        return (
            None
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5000
            else "Must be a whole number from 1 to 5,000."
        )
    if key in (
        OWNER_READS_CHATS,
        MEMORY_ENABLED,
        CONNECTIONS,
        DOCUMENTS_ENABLED,
        DOCUMENTS_EXTERNAL_EMBEDDING,
        LONG_CHATS_SUMMARISE,
    ):
        return None if isinstance(value, bool) else "Must be true or false."
    if key == LONG_CHATS_PERCENT:
        if _whole_in(value, long_chats.MIN_PERCENT, long_chats.MAX_PERCENT):
            return None
        return f"Must be a whole number from {long_chats.MIN_PERCENT} to {long_chats.MAX_PERCENT}."
    if key == LONG_CHATS_CEILING:
        if value == 0 or _whole_in(value, long_chats.MIN_CEILING, long_chats.MAX_CEILING):
            return None
        return (
            f"Must be 0 (no ceiling), or a whole number from {long_chats.MIN_CEILING:,} to "
            f"{long_chats.MAX_CEILING:,}."
        )
    if key == LONG_CHATS_MODEL:
        if isinstance(value, str) and len(value.strip()) <= MAX_MODEL_ID:
            return None
        return f"Must be empty, or the name of a model (at most {MAX_MODEL_ID} characters)."
    if key == DOCUMENTS_EMBEDDING_MODEL:
        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_MODEL_ID:
            return None
        return f"Must be auto, or the name of a search model (at most {MAX_MODEL_ID} characters)."
    if key == DOCUMENTS_SORT_MODEL:
        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_MODEL_ID:
            return None
        return (
            f"Must be auto, off, or the name of a result sorter (at most {MAX_MODEL_ID} "
            "characters)."
        )
    if key == DOCUMENTS_SORT_SECONDS:
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and MIN_SORT_SECONDS <= value <= MAX_SORT_SECONDS
        ):
            return None
        return f"Must be a number of seconds from {MIN_SORT_SECONDS:g} to {MAX_SORT_SECONDS:g}."
    if key == DOCUMENTS_EMBED_PER_MINUTE:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_EMBED_PER_MINUTE <= value <= MAX_EMBED_PER_MINUTE
        ):
            return None
        return f"Must be a whole number from {MIN_EMBED_PER_MINUTE} to {MAX_EMBED_PER_MINUTE:,}."
    ranges = {
        REMOVED_PEOPLE_DAYS: (MIN_REMOVED_DAYS, MAX_REMOVED_DAYS),
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
    if key == MEMORY_MAX_BODY_CHARS:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= MAX_BODY_CHARS_LIMIT
        ):
            return None
        return f"Must be a whole number from 0 to {MAX_BODY_CHARS_LIMIT:,}."
    if key in (MEMORY_EXTERNAL_MODELS, DOCUMENTS_EXTERNAL_MODELS):
        return None if value in EXTERNAL_CHOICES else "Must be never, ask or always."
    if key == MEMORY_SUGGESTIONS:
        return None if value in SUGGESTION_CHOICES else "Must be off, choose or measured."
    if key == MEMORY_WRITE_POLICY:
        return None if value in memory_policy.NAMES else "Must be strict, standard, open or custom."
    if key == MEMORY_POLICY_PEOPLE:
        return (
            None
            if value in (memory_policy.STRICTER_ONLY, memory_policy.ANY)
            else ("Must be stricter or any.")
        )
    if key == MEMORY_SERVERS:
        return None if value in SERVER_CHOICES else "Must be off, outside or any."
    if key == REMOVED_PEOPLE_RETENTION:
        return None if value in RETENTION_CHOICES else "Must be days or none."
    for row, row_key in POLICY_ROW_KEYS.items():
        if key == row_key:
            values = memory_policy.ROWS[row]
            return None if value in values else f"Must be {', '.join(values)}."
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
