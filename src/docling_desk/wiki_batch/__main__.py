"""Explicit operator commands; no model calls during import, sync or enqueue."""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from docling_desk.config import DATA as DEFAULT_DATA

from .files import NeedsReview, worker_lock
from .instructions import load as load_instructions
from .provider import AzureClient, Runtime, config_from_env
from .repository import Repository
from .snapshot import snapshot_for
from .store import BatchStore
from .terminology import load_registry
from .worker import BatchWorker


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wikiの同期・調査・検証付き翻訳バッチ")
    parser.add_argument(
        "action",
        choices=[
            "sync",
            "enqueue",
            "run",
            "status",
            "pause",
            "resume",
            "retry",
            "terms-check",
            "prepare",
            "import-workspace",
            "instructions",
        ],
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="sources/wiki/manifests/dataを含むWikiワークスペース",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(os.environ.get("DOCLING_DATA_DIR", DEFAULT_DATA)),
        help="画面用の保存領域",
    )
    parser.add_argument(
        "--instructions", type=Path, help="翻訳指示のJSON。省略時はワークスペースの設定"
    )
    parser.add_argument("--key")
    parser.add_argument(
        "--from", dest="from_root", type=Path, help="停止済みWikiワークスペースのコピー元"
    )
    parser.add_argument("--collection")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--trial", action="store_true")
    parser.add_argument("--prepare-action", choices=["build", "check", "migrate", "plan"])
    args = parser.parse_args(argv)
    if (
        (args.key and args.action not in {"enqueue", "retry"})
        or (args.collection and args.action != "enqueue")
        or (args.limit is not None and args.action not in {"enqueue", "run"})
        or (args.prepare_action and args.action != "prepare")
        or (args.instructions and args.action not in {"instructions", "enqueue", "run"})
    ):
        parser.error("指定した操作ではこの絞り込みオプションを利用できません")
    args.prepare_action = args.prepare_action or "check"
    if args.limit is not None and args.limit < 1:
        parser.error("--limitは正の整数にしてください")
    if args.trial and (args.action != "run" or args.limit is not None):
        parser.error("--trialはrun専用で、--limitと併用できません")
    if (args.action == "import-workspace") != (args.from_root is not None):
        parser.error("import-workspaceには--fromを指定してください")
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("Wikiワークスペースがありません")
    load_dotenv(root / ".env", override=False)
    stopped = [False]
    runtime = Runtime(stopping=lambda: stopped[0])

    def stop(_signal, _frame):
        stopped[0] = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def execute():
        if args.action == "instructions":
            return {"schemaVersion": 1, "instructions": load_instructions(root, args.instructions)}
        if args.action == "import-workspace":
            from .migration import import_workspace

            return import_workspace(args.from_root, root, args.data)
        if args.action == "prepare":
            from . import preparation
            from .writer_lock import wiki_lock

            preparation.configure(root)
            if args.prepare_action == "plan":
                return {"directories": [p.name for p in preparation.export_directories(root)]}
            if (
                args.prepare_action in {"build", "migrate"}
                and (root / "data/translation/publication.json").exists()
            ):
                raise NeedsReview("公開途中の処理を先に復旧してください。")
            with wiki_lock(root):
                result = getattr(preparation, args.prepare_action)()
            return result or {"prepared": True}
        if args.action == "terms-check":
            registry = load_registry(root)
            return {"entries": len(registry["entries"]), "valid": True}
        if args.action == "sync":
            return Repository(root, args.data).sync()
        store = BatchStore(root)
        try:
            if args.action == "status":
                return store.status()
            if args.action in {"pause", "resume"}:
                store.set("paused", int(args.action == "pause"))
                return store.status()
            if args.action == "retry":
                return {"requeued": store.retry(args.key)}
            instructions = load_instructions(root, args.instructions)
            repository = Repository(root, args.data)
            repository.sync()

            def enqueue(limit, automatic=False):
                registry = load_registry(root)
                models = {
                    "draft": os.environ.get("AZURE_OPENAI_LUNA_DEPLOYMENT", ""),
                    "verify": os.environ.get("AZURE_OPENAI_SOL_DEPLOYMENT", ""),
                }
                if not all(name.strip() for name in models.values()):
                    raise ValueError("登録前に下訳・検証のデプロイ名を設定してください。")
                registered = (
                    {r[0] for r in store.db.execute("SELECT DISTINCT page_key FROM jobs")}
                    if automatic
                    else set()
                )
                pages = [
                    p
                    for p in repository.pages
                    if p["translation_status"] == "untranslated"
                    and p["key"] not in registered
                    and (args.key is None or p["key"] == args.key)
                    and (args.collection is None or p["collection"] == args.collection)
                ]
                if args.key and not any(p["key"] == args.key for p in repository.pages):
                    raise ValueError("指定記事がありません。")
                if args.collection and not any(
                    p["collection"] == args.collection for p in repository.pages
                ):
                    raise ValueError("指定資料群がありません。")
                pages.sort(key=lambda p: (p["collection"] != "glossary", p["key"]))
                added, errors = 0, []
                for page in pages[:limit] if limit is not None else pages:
                    if runtime.stopping():
                        break
                    try:
                        snapshot = snapshot_for(repository, page, models, registry, instructions)
                    except NeedsReview as exc:
                        errors.append({"key": page["key"], "error": str(exc)})
                        store.event(
                            "preparation_review", json.dumps(errors[-1], ensure_ascii=False)
                        )
                        continue
                    added += store.enqueue(snapshot, runtime.now())
                return {"added": added, "needs_review": errors}

            if args.action == "enqueue":
                if (root / "data/translation/publication.json").exists():
                    raise NeedsReview("公開途中の処理を先に復旧してください。")
                return enqueue(args.limit)
            config = config_from_env()
            store.set("paused", "0")
            count = store.db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running','retry_wait','publishing')"
            ).fetchone()[0]
            registration = enqueue(3 if args.trial else args.limit, True) if not count else None
            client = AzureClient(store, config, runtime)
            result = BatchWorker(store, client, repository, runtime, instructions=instructions).run(
                args.limit, args.trial
            )
            return {
                **result,
                **(
                    {"registration": registration, "needs_review": registration["needs_review"]}
                    if registration
                    else {}
                ),
            }
        finally:
            store.close()

    try:
        if args.action in {"run", "enqueue", "retry", "sync", "prepare", "import-workspace"}:
            with worker_lock(root):
                result = execute()
        else:
            result = execute()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        outcome = result.get("outcome", {})
        return int(
            bool(
                (args.action == "run" and result.get("paused"))
                or outcome.get("needsReview")
                or outcome.get("failed")
                or result.get("needs_review")
            )
        )
    except (ValueError, OSError) as exc:
        # Operator errors contain identifiers only; transport never exposes credentials.
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
