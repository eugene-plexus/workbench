BEGIN TRANSACTION;
CREATE TABLE chats (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    model TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    search INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
INSERT INTO "chats" VALUES('chat-1','owner-1','Quarterly numbers','qwen3-8b','{"temperature": 0.7}',1,1789000100.0,1789000400.0);
INSERT INTO "chats" VALUES('chat-2','user-2','Recipes','gemma','{}',0,1789000200.0,1789000300.0);
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL
);
INSERT INTO "files" VALUES('file-1','owner-1','chat-1','q4-plan.pdf','application/pdf',48213,1789000199.0);
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    error TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    searches INTEGER NOT NULL DEFAULT 0,
    search INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    finish TEXT,
    created_at REAL NOT NULL,
    finished_at REAL,
    answer_from INTEGER,
    reasoning_from INTEGER
);
INSERT INTO "messages" VALUES('m1','chat-1',1,'user','What changed in Q3?','','[]','done',NULL,'[]',0,0,NULL,NULL,1789000101.0,NULL,NULL,NULL);
INSERT INTO "messages" VALUES('m2','chat-1',2,'assistant','Let me look that up.

Revenue rose 8 percent.','Searching first.','[]','done',NULL,'[{"url": "https://a.example/q3", "title": "Q3 report"}, {"url": "https://b.example/news", "title": "Newswire"}]',1,1,'qwen3-8b','stop',1789000102.0,1789000110.0,24,0);
INSERT INTO "messages" VALUES('m3','chat-1',3,'user','And Q4?','','["file-1"]','done',NULL,'[]',0,0,NULL,NULL,1789000200.0,NULL,NULL,NULL);
INSERT INTO "messages" VALUES('m4','chat-1',4,'assistant','Working on it','','[]','streaming',NULL,'[]',0,0,'qwen3-8b',NULL,1789000201.0,NULL,NULL,NULL);
INSERT INTO "messages" VALUES('m5','chat-2',1,'user','A dinner for six?','','[]','done',NULL,'[]',0,0,NULL,NULL,1789000201.0,NULL,NULL,NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('schema','2');
CREATE TABLE people (
    sub TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    role TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
INSERT INTO "people" VALUES('owner-1','Troy','troy','operator',1791899884.8375411,1791899884.8375411);
INSERT INTO "people" VALUES('user-2','Jessie','jessie','member',1791899884.8405409,1791899884.8405409);
CREATE TABLE sessions (
    id_hash TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    sub TEXT NOT NULL,
    refresh_token TEXT,
    access_expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
INSERT INTO "sessions" VALUES('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','owner-1',NULL,1790000000.0,1789000000.0,1792000000.0);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "settings" VALUES('systemPrompt','"Be brief."');
CREATE INDEX chats_by_owner ON chats (owner, updated_at);
CREATE INDEX messages_by_chat ON messages (chat_id, seq);
CREATE INDEX files_by_chat ON files (chat_id);
COMMIT;
