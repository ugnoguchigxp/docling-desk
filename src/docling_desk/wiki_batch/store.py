"""SQLite ledger compatible with the legacy system's saved jobs and checkpoints."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from docling_desk.sqlite_writer import connect


class BatchStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        file = self.root / "data/translation.sqlite"
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.is_symlink():
            raise ValueError("翻訳DBの保存先が不正です。")
        self.db = connect(file, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,page_key TEXT NOT NULL,input_hash TEXT NOT NULL,recipe_hash TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,retry_at INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,error TEXT,priority INTEGER NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(page_key,input_hash,recipe_hash));
        CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,retry_at,priority,created_at);
        CREATE TABLE IF NOT EXISTS packets(job_id TEXT NOT NULL REFERENCES jobs(id),position INTEGER NOT NULL,draft TEXT,verification TEXT,corrected INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(job_id,position));
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),call_key TEXT NOT NULL,role TEXT NOT NULL,started_at INTEGER NOT NULL,finished_at INTEGER,status TEXT NOT NULL,reserved INTEGER NOT NULL,response TEXT,error TEXT,usage TEXT);
        CREATE INDEX IF NOT EXISTS attempts_call ON attempts(call_key,status);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,job_id TEXT,time INTEGER NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research(job_id TEXT NOT NULL REFERENCES jobs(id),step TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(job_id,step));
        PRAGMA user_version=2;
        """)

    def close(self):
        self.db.close()

    def setting(self, key, fallback=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else fallback

    def number(self, key):
        return int(float(self.setting(key, "0")))

    def set(self, key, value):
        self.db.execute(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def event(self, kind, detail="", job_id=None, now=None):
        self.db.execute(
            "INSERT INTO events(job_id,time,kind,detail) VALUES(?,?,?,?)",
            (job_id, now if now is not None else int(time.time() * 1000), kind, detail),
        )

    def enqueue(self, snapshot, now=None):
        now = int(time.time() * 1000) if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            job_id = str(uuid4())
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,page_key,input_hash,recipe_hash,status,payload,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    snapshot["page"]["key"],
                    snapshot["inputHash"],
                    snapshot["recipeHash"],
                    "queued",
                    json.dumps(snapshot, ensure_ascii=False),
                    0 if snapshot["page"]["collection"] == "glossary" else 10,
                    now,
                    now,
                ),
            ).rowcount
            if inserted:
                self.db.executemany(
                    "INSERT INTO packets(job_id,position) VALUES(?,?)",
                    [(job_id, packet["position"]) for packet in snapshot["packets"]],
                )
                self.event("queued", snapshot["page"]["key"], job_id, now)
            self.db.execute("COMMIT")
            return bool(inserted)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("翻訳ジョブが見つかりません。")
        return dict(row)

    def update(self, job_id, status, error=None, retry_at=0, now=None):
        now = int(time.time() * 1000) if now is None else now
        changed = self.db.execute(
            "UPDATE jobs SET status=?,error=?,retry_at=?,updated_at=? WHERE id=?",
            (status, error, retry_at, now, job_id),
        ).rowcount
        if changed != 1:
            raise ValueError("翻訳ジョブの状態を保存できません。")
        self.event(status, error or "", job_id, now)

    def checkpoint(self, job_id, step):
        row = self.db.execute(
            "SELECT value FROM research WHERE job_id=? AND step=?", (job_id, step)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, job_id, step, value):
        self.db.execute(
            "INSERT INTO research VALUES(?,?,?) ON CONFLICT(job_id,step) DO UPDATE SET value=excluded.value",
            (job_id, step, json.dumps(value, ensure_ascii=False)),
        )
        self.event("research_checkpoint", step, job_id)

    def packet(self, job_id, position):
        row = self.db.execute(
            "SELECT draft,verification,corrected FROM packets WHERE job_id=? AND position=?",
            (job_id, position),
        ).fetchone()
        if row is None:
            raise ValueError("翻訳単位の保存記録がありません。")
        return dict(row)

    def save_packet(self, job_id, position, draft, verification=None, corrected=False):
        changed = self.db.execute(
            "UPDATE packets SET draft=?,verification=?,corrected=? WHERE job_id=? AND position=?",
            (
                json.dumps(draft, ensure_ascii=False),
                json.dumps(verification, ensure_ascii=False) if verification is not None else None,
                int(corrected),
                job_id,
                position,
            ),
        ).rowcount
        if changed != 1:
            raise ValueError("翻訳単位を保存できません。")

    def recover(self, now):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for row in self.db.execute(
                "SELECT id,started_at FROM attempts WHERE status='pending'"
            ).fetchall():
                self.set(
                    "blocked_until", max(self.number("blocked_until"), row["started_at"] + 210000)
                )
                self.db.execute(
                    "UPDATE attempts SET status='unknown',error=? WHERE id=?",
                    ("送信後の応答を確認できませんでした", row["id"]),
                )
            self.db.execute(
                "UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (now,)
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def trial(self, now):
        saved = json.loads(self.setting("trial_run", "null"))
        if saved and any(
            self.job(i)["status"] in {"queued", "running", "retry_wait", "publishing"}
            for i in saved["jobIds"]
        ):
            return saved
        rows = self.db.execute(
            """WITH candidates AS (SELECT id,created_at,rowid ordinal,row_number() OVER(PARTITION BY page_key ORDER BY created_at DESC,rowid DESC) position FROM jobs WHERE status IN ('queued','retry_wait')) SELECT id FROM candidates WHERE position=1 ORDER BY created_at DESC,ordinal DESC LIMIT 3"""
        ).fetchall()
        saved = {"id": str(uuid4()), "createdAt": now, "jobIds": [r[0] for r in rows]}
        self.set("trial_run", json.dumps(saved))
        return saved

    def next(self, now, job_ids=None):
        if job_ids == []:
            return None
        extra, args = "", [now]
        if job_ids is not None:
            extra = " AND id IN (" + ",".join("?" for _ in job_ids) + ")"
            args += job_ids
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','retry_wait','publishing') AND (status='publishing' OR NOT EXISTS(SELECT 1 FROM jobs WHERE status='publishing')) AND retry_at<=?"
                + extra
                + " ORDER BY status='publishing' DESC,priority,created_at,page_key LIMIT 1",
                args,
            ).fetchone()
            if row and row["status"] != "publishing":
                self.update(row["id"], "running", now=now)
            self.db.execute("COMMIT")
            return self.job(row["id"]) if row else None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def retry(self, key=None):
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE status IN ('failed','needs_review')"
        ).fetchall()
        selected = [r for r in rows if key is None or key in {r["id"], r["page_key"]}]
        for row in selected:
            job_id = row["id"]
            consistency = self.checkpoint(job_id, "term-consistency") or {}
            failed_ids = {issue["id"] for issue in consistency.get("issues", [])}
            if failed_ids:
                snapshot = json.loads(row["payload"])
                for packet in snapshot["packets"]:
                    if failed_ids & {u["id"] for u in packet["units"]}:
                        self.db.execute(
                            "UPDATE packets SET draft=NULL,verification=NULL,corrected=0 WHERE job_id=? AND position=?",
                            (job_id, packet["position"]),
                        )
            if self.checkpoint(job_id, "term-resolution-error"):
                self.db.execute(
                    "DELETE FROM research WHERE job_id=? AND (step LIKE 'resolve/%' OR step LIKE 'input/resolve/%' OR step IN ('complete','registered-terms'))",
                    (job_id,),
                )
            # Explicit retries reopen bounded research; automatic retries never do.
            result = self.checkpoint(job_id, "complete")
            if result and result.get("stopReason") != "sufficient":
                self.save(job_id, "before-manual-retry/" + uuid4().hex, result)
                self.db.execute(
                    "DELETE FROM research WHERE job_id=? AND step NOT LIKE 'analyze/%' AND step NOT LIKE 'plan/%' AND step NOT LIKE 'before-manual-retry/%'",
                    (job_id,),
                )
            self.db.execute(
                "DELETE FROM research WHERE job_id=? AND (step='term-consistency' OR step LIKE 'term-consistency/%' OR step='term-resolution-error')",
                (job_id,),
            )
            self.db.execute(
                "UPDATE packets SET draft=NULL,verification=NULL,corrected=0 WHERE job_id=? AND (verification IS NULL OR json_extract(verification,'$.approved')<>1)",
                (job_id,),
            )
            self.db.execute(
                "UPDATE attempts SET status='discarded' WHERE job_id=? AND status<>'pending' AND call_key NOT IN (SELECT ?||'/'||position||'/draft' FROM packets WHERE job_id=? AND verification IS NOT NULL)",
                (job_id, job_id, job_id),
            )
            self.update(job_id, "queued")
        return len(selected)

    def status(self):
        pid = self.number("worker_pid")
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                pass
        return {
            "counts": dict(
                self.db.execute("SELECT status,count(*) FROM jobs GROUP BY status").fetchall()
            ),
            "paused": self.setting("paused") == "1",
            "workerAlive": alive,
            "workerStage": self.setting("worker_stage") or None,
            "blockedUntil": self.number("blocked_until"),
            "nextPageAt": self.number("next_page_at"),
        }
