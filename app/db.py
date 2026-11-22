"""SQLite unit of work with append-only originals, source snapshots and audit."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 jurisdiction TEXT NOT NULL, as_of TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
 review_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL, counterparty TEXT NOT NULL DEFAULT '',
 current_version_id TEXT REFERENCES versions(id), archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 contract_id TEXT NOT NULL REFERENCES contracts(id), number INTEGER NOT NULL,
 filename TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('initial','counterparty','revision','final','signed')),
 sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, text TEXT NOT NULL,
 parse_status TEXT NOT NULL, warnings TEXT NOT NULL, safety TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(contract_id,number)
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL, category TEXT NOT NULL, jurisdiction TEXT NOT NULL, as_of TEXT NOT NULL,
 url TEXT NOT NULL, citation TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT '',
 effective_at TEXT NOT NULL DEFAULT '', effect_status TEXT NOT NULL,
 verified_at TEXT NOT NULL DEFAULT '', verification_note TEXT NOT NULL DEFAULT '',
 is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 contract_id TEXT NOT NULL REFERENCES contracts(id), version_id TEXT NOT NULL REFERENCES versions(id),
 root_id TEXT NOT NULL, carried_from TEXT REFERENCES issues(id),
 origin_version_id TEXT NOT NULL REFERENCES versions(id), clause TEXT NOT NULL,
 quote TEXT NOT NULL, current_quote TEXT NOT NULL, anchor_status TEXT NOT NULL,
 risk TEXT NOT NULL, suggestion TEXT NOT NULL, pending TEXT NOT NULL,
 basis_source_id TEXT REFERENCES sources(id), basis_quote TEXT NOT NULL DEFAULT '',
 basis_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('open','in_review','resolved')),
 origin TEXT NOT NULL, human_confirmed INTEGER NOT NULL DEFAULT 0,
 resolution_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(version_id,root_id)
);
CREATE TABLE IF NOT EXISTS deliverables (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 contract_id TEXT NOT NULL REFERENCES contracts(id), version_id TEXT NOT NULL REFERENCES versions(id),
 kind TEXT NOT NULL CHECK(kind IN ('internal','external')), format TEXT NOT NULL,
 filename TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
 inspection TEXT NOT NULL, source_safety TEXT NOT NULL, created_at TEXT NOT NULL,
 approved_at TEXT, approved_by TEXT, review_note TEXT, approved_revision INTEGER
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT REFERENCES projects(id),
 actor TEXT NOT NULL, action TEXT NOT NULL, entity_id TEXT NOT NULL,
 detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
 project_id TEXT NOT NULL, contract_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES versions(id),
 PRIMARY KEY(project_id,contract_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS rewrite_tasks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 contract_id TEXT NOT NULL REFERENCES contracts(id), issue_id TEXT NOT NULL REFERENCES issues(id),
 root_id TEXT NOT NULL, target_version_id TEXT NOT NULL REFERENCES versions(id),
 audience TEXT NOT NULL CHECK(audience IN ('internal','business','counterparty')),
 tone TEXT NOT NULL CHECK(tone IN ('concise','gentle','firm')),
 mode TEXT NOT NULL CHECK(mode IN ('prepare','demo')),
 context_snapshot TEXT NOT NULL, context_sha256 TEXT NOT NULL,
 prompt_version TEXT NOT NULL, prompt_text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rewrite_drafts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES rewrite_tasks(id),
 project_id TEXT NOT NULL REFERENCES projects(id), number INTEGER NOT NULL,
 text TEXT NOT NULL, meaning_notes TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('demo_template','manual_input')),
 sha256 TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(task_id,number)
);
CREATE TABLE IF NOT EXISTS issue_expressions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 contract_id TEXT NOT NULL REFERENCES contracts(id), issue_id TEXT NOT NULL REFERENCES issues(id),
 root_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE REFERENCES rewrite_tasks(id),
 draft_id TEXT NOT NULL UNIQUE REFERENCES rewrite_drafts(id),
 version_id TEXT NOT NULL REFERENCES versions(id),
 audience TEXT NOT NULL CHECK(audience IN ('internal','business','counterparty')),
 tone TEXT NOT NULL CHECK(tone IN ('concise','gentle','firm')),
 text TEXT NOT NULL, meaning_notes TEXT NOT NULL, note TEXT NOT NULL,
 human_confirmed INTEGER NOT NULL CHECK(human_confirmed=1),
 meaning_confirmed INTEGER NOT NULL CHECK(meaning_confirmed=1),
 external_safe_confirmed INTEGER NOT NULL,
 adopted_review_revision INTEGER NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 scope TEXT NOT NULL CHECK(scope IN ('project','public')), query TEXT NOT NULL,
 parameters TEXT NOT NULL, started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_outcomes (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES research_runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id), provider TEXT NOT NULL,
 status TEXT NOT NULL, sent_externally INTEGER NOT NULL,
 payload TEXT NOT NULL, completed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_adoptions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES research_runs(id), candidate_id TEXT NOT NULL,
 source_id TEXT NOT NULL UNIQUE REFERENCES sources(id), request_sha256 TEXT NOT NULL,
 note TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,candidate_id)
);
CREATE INDEX IF NOT EXISTS research_project ON research_runs(project_id,started_at);
CREATE TRIGGER IF NOT EXISTS research_runs_no_update BEFORE UPDATE ON research_runs BEGIN
 SELECT RAISE(ABORT,'Research requests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_runs_no_delete BEFORE DELETE ON research_runs BEGIN
 SELECT RAISE(ABORT,'Research history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS research_outcomes_no_update BEFORE UPDATE ON research_outcomes BEGIN
 SELECT RAISE(ABORT,'Research outcomes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_outcomes_no_delete BEFORE DELETE ON research_outcomes BEGIN
 SELECT RAISE(ABORT,'Research outcomes cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS research_adoptions_no_update BEFORE UPDATE ON research_adoptions BEGIN
 SELECT RAISE(ABORT,'Research source links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_adoptions_no_delete BEFORE DELETE ON research_adoptions BEGIN
 SELECT RAISE(ABORT,'Research source links cannot be deleted'); END;
CREATE INDEX IF NOT EXISTS versions_contract ON versions(contract_id);
CREATE INDEX IF NOT EXISTS issues_version ON issues(version_id);
CREATE INDEX IF NOT EXISTS audit_project ON audit(project_id);
CREATE INDEX IF NOT EXISTS rewrite_tasks_issue ON rewrite_tasks(project_id,issue_id);
CREATE INDEX IF NOT EXISTS rewrite_drafts_task ON rewrite_drafts(task_id,number);
CREATE INDEX IF NOT EXISTS expressions_project ON issue_expressions(project_id,issue_id);
CREATE TRIGGER IF NOT EXISTS versions_no_update BEFORE UPDATE ON versions BEGIN
 SELECT RAISE(ABORT,'Versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS versions_no_delete BEFORE DELETE ON versions BEGIN
 SELECT RAISE(ABORT,'Versions cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS sources_no_update BEFORE UPDATE ON sources BEGIN
 SELECT RAISE(ABORT,'Sources are immutable snapshots'); END;
CREATE TRIGGER IF NOT EXISTS sources_no_delete BEFORE DELETE ON sources BEGIN
 SELECT RAISE(ABORT,'Sources cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS issues_no_delete BEFORE DELETE ON issues BEGIN
 SELECT RAISE(ABORT,'Review history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN
 SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN
 SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS rewrite_tasks_no_update BEFORE UPDATE ON rewrite_tasks BEGIN
 SELECT RAISE(ABORT,'Rewrite task evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS rewrite_tasks_no_delete BEFORE DELETE ON rewrite_tasks BEGIN
 SELECT RAISE(ABORT,'Rewrite task evidence cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS rewrite_drafts_no_update BEFORE UPDATE ON rewrite_drafts BEGIN
 SELECT RAISE(ABORT,'Rewrite drafts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS rewrite_drafts_no_delete BEFORE DELETE ON rewrite_drafts BEGIN
 SELECT RAISE(ABORT,'Rewrite drafts cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS issue_expressions_no_update BEFORE UPDATE ON issue_expressions BEGIN
 SELECT RAISE(ABORT,'Adopted expressions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS issue_expressions_no_delete BEFORE DELETE ON issue_expressions BEGIN
 SELECT RAISE(ABORT,'Adopted expressions cannot be deleted'); END;
"""


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    path.chmod(0o600)


@contextmanager
def transaction(path: Path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
