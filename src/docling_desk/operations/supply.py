"""List locked dependencies and pinned models.

Declared metadata is not clearance to use a package. Unknown stays unknown.
"""

from __future__ import annotations

import json
import re
from importlib import metadata
from pathlib import Path

import yaml


def _declared_license(name: str) -> str | None:
    try:
        dist = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return None
    return dist.get("License-Expression") or dist.get("License") or None


def _python_packages(path: Path) -> list[dict]:
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        packages.append(
            {
                "name": name,
                "version": version,
                "ecosystem": "pypi",
                "source": "requirements-lock.txt",
                "scope": "runtime",
                "direct": None,
                "depends_on": [],
                "distribution": ["python", "docker"],
                "declared_license": _declared_license(name),
                "cleared": False,
            }
        )
    return packages


def _snapshot_key(name: str, version: str, snapshots: dict) -> str | None:
    prefix = f"{name}@{version}"
    if prefix in snapshots:
        return prefix
    matches = [key for key in snapshots if key.startswith(prefix + "(") or key == prefix]
    return matches[0] if matches else None


def _closure(roots: list[tuple[str, str]], snapshots: dict) -> set[str]:
    seen = set()
    pending = list(roots)
    while pending:
        name, version = pending.pop()
        key = _snapshot_key(name, version.split("(", 1)[0], snapshots)
        if key is None or key in seen:
            continue
        seen.add(key)
        for dep, dep_version in (snapshots[key].get("dependencies") or {}).items():
            if isinstance(dep_version, str):
                pending.append((dep, dep_version))
    return seen


def _lock_packages(lock_text: str, package_json: dict, ecosystem: str, source: str) -> list[dict]:
    if ecosystem == "bun":
        return _bun_packages(lock_text, package_json, source)
    document = yaml.safe_load(lock_text)
    snapshots = document.get("snapshots") or {}
    importer = (document.get("importers") or {}).get(".") or {}
    prod_roots = [
        (name, str(spec.get("version", "")))
        for name, spec in (importer.get("dependencies") or {}).items()
    ]
    dev_roots = [
        (name, str(spec.get("version", "")))
        for name, spec in (importer.get("devDependencies") or {}).items()
    ]
    prod_keys = _closure(prod_roots, snapshots)
    dev_keys = _closure(dev_roots, snapshots)
    direct_prod = set(package_json.get("dependencies") or {})
    direct_dev = set(package_json.get("devDependencies") or {})
    packages = []
    seen = set()
    for key in sorted(set(prod_keys) | set(dev_keys)):
        name, version = key.split("(", 1)[0].rsplit("@", 1)
        identity = (name, version)
        if identity in seen:
            continue
        seen.add(identity)
        runtime = key in prod_keys
        depends = sorted((snapshots[key].get("dependencies") or {}).keys())
        packages.append(
            {
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "source": source,
                "scope": "runtime" if runtime else "development",
                "direct": name in direct_prod or name in direct_dev,
                "depends_on": depends,
                "distribution": ["frontend", "docker"] if runtime else ["development"],
                "declared_license": None,
                "cleared": False,
            }
        )
    return packages


def _jsonc(text: str) -> dict:
    """Accept trailing commas. Commas inside strings stay in place."""
    cleaned: list[str] = []
    in_string = False
    escape = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            cleaned.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            cleaned.append(char)
            index += 1
            continue
        if char == ",":
            nxt = index + 1
            while nxt < len(text) and text[nxt] in " \t\r\n":
                nxt += 1
            if nxt < len(text) and text[nxt] in "}]":
                index += 1
                continue
        cleaned.append(char)
        index += 1
    return json.loads("".join(cleaned))


def _bun_packages(lock_text: str, package_json: dict, source: str) -> list[dict]:
    document = _jsonc(lock_text)
    workspace = (document.get("workspaces") or {}).get("") or {}
    prod = set((workspace.get("dependencies") or {}) | (package_json.get("dependencies") or {}))
    dev = set(
        (workspace.get("devDependencies") or {}) | (package_json.get("devDependencies") or {})
    )
    packages = []
    for name, record in (document.get("packages") or {}).items():
        if not isinstance(record, list) or not record:
            continue
        resolved = record[0]
        if not isinstance(resolved, str) or "@" not in resolved:
            continue
        resolved_name, version = resolved.rsplit("@", 1)
        if resolved_name != name:
            continue
        meta = record[2] if len(record) > 2 and isinstance(record[2], dict) else {}
        dependencies = meta.get("dependencies", {})
        optional = meta.get("optionalDependencies", {})
        runtime = name in prod
        packages.append(
            {
                "name": name,
                "version": version,
                "ecosystem": "bun",
                "source": source,
                "scope": "runtime" if runtime else "development",
                "direct": name in prod or name in dev,
                "depends_on": sorted(set(dependencies) | set(optional)),
                "distribution": ["knowledge-api"] if runtime else ["development"],
                "declared_license": None,
                "cleared": False,
            }
        )
    return packages


def _docker_packages(dockerfile: str) -> list[str]:
    match = re.search(r"apt-get install -y --no-install-recommends\s+(.*?)&&", dockerfile, re.S)
    if not match:
        return []
    return [part for part in match.group(1).split() if not part.startswith("\\") and part != "\\"]


def _clearance(root: Path) -> list[dict]:
    path = root / "supply/clearance.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = value.get("records") if isinstance(value, dict) else None
    return records if isinstance(records, list) else []


def _confirmed(records: list[dict], name: str, version: str | None) -> dict | None:
    for record in records:
        if not isinstance(record, dict) or record.get("name") != name:
            continue
        if version is not None and record.get("version") not in {None, version}:
            continue
        if (
            record.get("status") == "confirmed"
            and isinstance(record.get("reviewer"), str)
            and record.get("reviewer")
            and isinstance(record.get("reviewed_on"), str)
            and record.get("reviewed_on")
        ):
            return record
    return None


def _assets(root: Path, records: list[dict]) -> list[dict]:
    assets = []
    for model in json.loads(
        (root / "src/docling_desk/resources/models/manifest.json").read_text(encoding="utf-8")
    ):
        confirmed = _confirmed(records, model["repo"], model["revision"])
        assets.append(
            {
                "name": model["repo"],
                "kind": "model",
                "version": model["revision"],
                "source": "src/docling_desk/resources/models/manifest.json",
                "declared_license": model.get("declared_license"),
                "license_source": model.get("license_source"),
                "license": "unknown" if confirmed is None else confirmed.get("license", "unknown"),
                "distribution": ["docker"],
                "cleared": confirmed is not None,
                "reviewer": None if confirmed is None else confirmed.get("reviewer"),
                "reviewed_on": None if confirmed is None else confirmed.get("reviewed_on"),
                "notice": "static/frontend/licenses.txt は画面のpackage表示です。モデルの利用条件は別です。",
            }
        )
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    for name in _docker_packages(dockerfile):
        confirmed = _confirmed(records, name, None)
        assets.append(
            {
                "name": name,
                "kind": "os-package",
                "version": None,
                "source": "Dockerfile",
                "license": "unknown" if confirmed is None else confirmed.get("license", "unknown"),
                "distribution": ["docker"],
                "cleared": confirmed is not None,
                "reviewer": None if confirmed is None else confirmed.get("reviewer"),
                "reviewed_on": None if confirmed is None else confirmed.get("reviewed_on"),
                "notice": None,
            }
        )
    assets.append(
        {
            "name": "tests/fixtures/documents",
            "kind": "fixture",
            "version": None,
            "source": "tests/fixtures/documents",
            "license": "unknown",
            "distribution": ["test"],
            "cleared": False,
            "reviewer": None,
            "reviewed_on": None,
            "redistributable": True,
            "notice": "合成試料です。保存済みの実資料は合成検査へ混ぜません。",
        }
    )
    return assets


def inventory(root: Path) -> dict:
    records = _clearance(root)
    packages = _python_packages(root / "requirements-lock.txt")
    packages.extend(
        _lock_packages(
            (root / "frontend/pnpm-lock.yaml").read_text(encoding="utf-8"),
            json.loads((root / "frontend/package.json").read_text(encoding="utf-8")),
            "npm",
            "frontend/pnpm-lock.yaml",
        )
    )
    packages.extend(
        _bun_packages(
            (root / "knowledge-api/bun.lock").read_text(encoding="utf-8"),
            json.loads((root / "knowledge-api/package.json").read_text(encoding="utf-8")),
            "knowledge-api/bun.lock",
        )
    )
    for package in packages:
        confirmed = _confirmed(records, package["name"], package["version"])
        package["cleared"] = confirmed is not None
        if confirmed is not None and confirmed.get("license"):
            package["declared_license"] = confirmed["license"]
    models = []
    for model in json.loads(
        (root / "src/docling_desk/resources/models/manifest.json").read_text(encoding="utf-8")
    ):
        confirmed = _confirmed(records, model["repo"], model["revision"])
        models.append(
            {
                "repo": model["repo"],
                "revision": model["revision"],
                "declared_license": model.get("declared_license"),
                "license_source": model.get("license_source"),
                "license": "unknown" if confirmed is None else confirmed.get("license", "unknown"),
                "cleared": confirmed is not None,
                "distribution": ["docker"],
            }
        )
    decisions_path = root / "supply/decisions.json"
    try:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        decisions = {"advisories": []}
    return {
        "packages": packages,
        "models": models,
        "assets": _assets(root, records),
        "decisions": decisions.get("advisories", []) if isinstance(decisions, dict) else [],
        "note": "declared_licenseは配布メタデータです。clearedがfalseの項目は利用権の確認済みではありません。",
    }


def assess_advisory(report: dict, advisory: dict) -> dict:
    """Explain whether a fixed advisory hits a resolved distribution. This is not a scan."""
    name = advisory.get("name")
    versions = set(advisory.get("versions") or [])
    ecosystem = advisory.get("ecosystem")
    matches = [
        package
        for package in report["packages"]
        if package["name"] == name
        and package["version"] in versions
        and (ecosystem is None or package["ecosystem"] == ecosystem)
    ]
    decision = next(
        (
            item
            for item in report.get("decisions", [])
            if isinstance(item, dict) and item.get("id") == advisory.get("id")
        ),
        None,
    )
    if not matches:
        return {
            "id": advisory.get("id"),
            "applies": False,
            "reason": "解決済みの配布物に、この名前と版はありません。",
            "packages": [],
            "decision": decision,
        }
    scopes = sorted({item["scope"] for item in matches})
    distributions = sorted({dist for item in matches for dist in item["distribution"]})
    return {
        "id": advisory.get("id"),
        "applies": True,
        "reason": "解決済み版が配布台帳にあります。到達範囲はscopeとdistributionです。",
        "packages": [
            {
                "name": item["name"],
                "version": item["version"],
                "ecosystem": item["ecosystem"],
                "scope": item["scope"],
                "direct": item["direct"],
                "distribution": item["distribution"],
            }
            for item in matches
        ],
        "scopes": scopes,
        "distributions": distributions,
        "decision": decision or {"status": "unreviewed"},
    }
