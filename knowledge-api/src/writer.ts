import { Database } from "bun:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { basename, dirname, join } from "node:path";

// A lifetime OS-backed SQLite lock supplements the expiring worker lease. A
// stalled API must never lose write ownership to another process. Multiple Store
// handles within this process share ownership; process death releases the lock.
const owners = new Map<string, { lock: Database; references: number }>();

export function writerDatabase(path: string): Database {
  if (path === ":memory:") return new Database(path, { create: true, strict: true });
  const key = existsSync(path)
    ? realpathSync(path)
    : join(realpathSync(dirname(path)), basename(path));
  let owner = owners.get(key);
  if (!owner) {
    const lock = new Database(`${key}.writer-lock`, { create: true });
    try {
      lock.exec("PRAGMA busy_timeout=0; BEGIN IMMEDIATE");
    } catch {
      lock.close();
      throw new Error("Knowledge database already has a writer process");
    }
    owner = { lock, references: 0 };
    owners.set(key, owner);
  }
  const release = () => {
    if (owner && --owner.references === 0) {
      owner.lock.close();
      owners.delete(key);
    }
  };
  owner.references++;
  let db: Database;
  try {
    db = new Database(path, { create: true, strict: true });
  } catch (error) {
    release();
    throw error;
  }
  const close = db.close.bind(db);
  let closed = false;
  db.close = (throwOnError?: boolean) => {
    if (closed) return;
    close(throwOnError);
    closed = true;
    release();
  };
  return db;
}
