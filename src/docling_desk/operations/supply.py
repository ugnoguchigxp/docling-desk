"""List locked dependencies, pinned models and non-package assets by distribution path.

Declared metadata is not clearance to use a package. Unknown stays unknown.
`cleared` is true only for a ledger record whose status is "confirmed" with a reviewer
and date. An automated inventory record (status "inventoried") never clears anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from zipfile import ZipFile

import yaml

GENERIC_LICENSE = {"dual license", "license", "other", "unknown"}
REVIEW_FIELDS = ("reviewer", "reviewed_on", "observed_license", "conditions", "remaining")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _json_file(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return value if isinstance(value, dict) else default


def _declared_license(name: str) -> str | None:
    try:
        dist = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return None
    declared = dist.get("License-Expression")
    if declared:
        return declared
    text = dist.get("License")
    if text and len(text) <= 80 and "\n" not in text and text.lower() not in GENERIC_LICENSE:
        return text
    classifiers = [
        item.split("::")[-1].strip()
        for item in dist.get_all("Classifier") or []
        if item.startswith("License ::")
    ]
    if classifiers:
        return " / ".join(classifiers)
    return "(metadataに短い表記なし)" if text else None


def _classification(root: Path) -> dict:
    return _json_file(root / "supply/classification.json", {})


def _pyproject_direct(root: Path) -> set[str]:
    """Names the wheel metadata asks for, including optional extras."""
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return set()
    requirements = list(project.get("dependencies") or [])
    for extra in (project.get("optional-dependencies") or {}).values():
        requirements.extend(extra)
    names = set()
    for requirement in requirements:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        if match:
            names.add(_canonical(match.group(1)))
    return names


_DARWIN_ONLY = re.compile(r"""sys_platform\s*==\s*(["'])darwin\1""")


def _darwin_only(marker: str) -> bool:
    """True when a lock marker admits darwin but not the Linux of the Docker image."""
    marker = marker.strip()
    if not marker:
        return False
    try:
        from packaging.markers import InvalidMarker, Marker
    except ImportError:  # strict fallback: only the exact form the lock emits
        return _DARWIN_ONLY.fullmatch(marker) is not None
    try:
        parsed = Marker(marker)
        return parsed.evaluate({"sys_platform": "darwin", "platform_system": "Darwin"}) and (
            not parsed.evaluate({"sys_platform": "linux", "platform_system": "Linux"})
        )
    except InvalidMarker:  # an unreadable marker is not a darwin-only claim
        return False


def _python_packages(path: Path, root: Path | None = None) -> list[dict]:
    root = root or path.parent
    classes = (_classification(root).get("python")) or {}
    development = {_canonical(n) for n in (classes.get("development") or {}).get("names", [])}
    optional = {_canonical(n) for n in (classes.get("optional") or {}).get("names", [])}
    undeclared = {_canonical(n) for n in (classes.get("undeclared_runtime") or {}).get("names", [])}
    direct = _pyproject_direct(root)
    packages = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Hash options follow the requirement, on the line or on continuation lines.
        line = line.split(" --hash", 1)[0].removesuffix("\\").strip()
        if not line or line.startswith(("#", "-")) or "==" not in line:
            continue
        requirement, _, marker = line.partition(";")
        name, version = requirement.strip().split("==", 1)
        key = _canonical(name)
        # Docker installs Linux only. A darwin-only marker never reaches the image.
        darwin_only = _darwin_only(marker)
        if key in development:
            scope, distribution = "development", ["docker"]
            note = "開発・検査用。requirements-lock.txt を共有するためDockerイメージにも入る。"
        elif key in optional:
            scope, distribution = "optional", ["python", "docker"]
            note = "Blob/Azure OCR の extra。Dockerイメージには入る。"
        elif key in undeclared:
            scope, distribution = "runtime", ["docker"]
            note = (
                "実行に必要だがpyproject.tomlのextrasが要求しない。wheelだけの導入では不足しうる。"
            )
        else:
            scope, distribution = "runtime", ["python", "docker"]
            note = None
        if darwin_only:
            distribution = [item for item in distribution if item != "docker"] or ["python"]
        packages.append(
            {
                "name": name,
                "version": version,
                "ecosystem": "pypi",
                "source": "requirements-lock.txt",
                "scope": scope,
                "direct": key in direct,
                "depends_on": [],
                "distribution": distribution,
                "platform": "darwin" if darwin_only else None,
                "declared_license": _declared_license(name),
                "cleared": False,
                "note": note,
            }
        )
    for item in classes.get("unlocked_optional") or []:
        packages.append(
            {
                "name": item["name"],
                "version": item["version"],
                "ecosystem": "pypi",
                "source": item["source"],
                "scope": "optional",
                "direct": True,
                "depends_on": [],
                "distribution": list(item["distribution"]),
                "platform": None,
                "declared_license": _declared_license(item["name"]),
                "cleared": False,
                "note": item["evidence"],
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


def _lock_packages(
    lock_text: str,
    package_json: dict,
    ecosystem: str,
    source: str,
    build_only_roots: frozenset[str] = frozenset(),
) -> list[dict]:
    """Resolve a pnpm lock. Roots in build_only_roots are declared as dependencies
    but only produce build output, so they and their exclusive closure are not shipped."""
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
    shipped_roots = [root for root in prod_roots if root[0] not in build_only_roots]
    shipped_keys = _closure(shipped_roots, snapshots) if build_only_roots else prod_keys
    direct_prod = set(package_json.get("dependencies") or {})
    direct_dev = set(package_json.get("devDependencies") or {})
    # One package version can have several snapshots (peer variants). Any shipped
    # variant makes the version shipped.
    identities: dict[tuple[str, str], dict] = {}
    for key in sorted(set(prod_keys) | set(dev_keys)):
        name, version = key.split("(", 1)[0].rsplit("@", 1)
        item = identities.setdefault(
            (name, version), {"runtime": False, "shipped": False, "depends": set()}
        )
        item["runtime"] = item["runtime"] or key in prod_keys
        item["shipped"] = item["shipped"] or key in shipped_keys
        item["depends"] |= set((snapshots[key].get("dependencies") or {}).keys())
    packages = []
    for (name, version), item in identities.items():
        if item["runtime"] and not item["shipped"]:
            scope, distribution = "build", ["build"]
        elif item["runtime"]:
            scope, distribution = "runtime", ["frontend", "docker"]
        else:
            scope, distribution = "development", ["development"]
        packages.append(
            {
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "source": source,
                "scope": scope,
                "direct": name in direct_prod or name in direct_dev,
                "depends_on": sorted(item["depends"]),
                "distribution": distribution,
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
    matches = re.finditer(r"apt-get install -y --no-install-recommends\s+(.*?)&&", dockerfile, re.S)
    return list(
        dict.fromkeys(
            part
            for match in matches
            for part in match.group(1).split()
            if not part.startswith("\\")
        )
    )


def _base_images(root: Path) -> list[dict]:
    """Base images from every Dockerfile. Only the last stage is shipped; earlier stages
    are build tools whose output is copied in."""
    images = []
    for relative in ("Dockerfile", "deploy/Dockerfile.gateway", "knowledge-api/Dockerfile"):
        path = root / relative
        if not path.is_file():
            continue
        stages = re.findall(
            r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?",
            path.read_text(encoding="utf-8"),
            re.M | re.I,
        )
        for index, (image, stage) in enumerate(stages):
            final = index == len(stages) - 1
            images.append(
                {
                    "image": image,
                    "stage": stage or None,
                    "source": relative,
                    "final": final,
                    "digest_pinned": "@sha256:" in image,
                }
            )
    return images


def _clearance(root: Path) -> list[dict]:
    value = _json_file(root / "supply/clearance.json", {})
    records = value.get("records")
    return records if isinstance(records, list) else []


def _recorded(records: list[dict], name: str, version: str | None) -> dict | None:
    """The ledger record for an item, of any status. An exact version beats a versionless one."""
    loose = None
    for record in records:
        if not isinstance(record, dict) or record.get("name") != name:
            continue
        recorded = record.get("version")
        if recorded is None:
            loose = loose or record
        elif version is not None and recorded == version:
            return record
    return loose


def _review(record: dict | None) -> dict:
    """Review facts for an item. Any status other than "confirmed" is only an inventory note."""
    if record is None:
        return {"review_status": "unreviewed"}
    fields = {"review_status": record.get("status") or "unreviewed"}
    for key in REVIEW_FIELDS:
        if key in record:
            fields[key] = record[key]
    return fields


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


def _asset(records: list[dict], name: str, version: str | None, **fields) -> dict:
    confirmed = _confirmed(records, name, version)
    asset = {
        "name": name,
        "version": version,
        "license": "unknown" if confirmed is None else confirmed.get("license", "unknown"),
        "cleared": confirmed is not None,
        "reviewer": None if confirmed is None else confirmed.get("reviewer"),
        "reviewed_on": None if confirmed is None else confirmed.get("reviewed_on"),
        **fields,
    }
    # reviewer/reviewed_on name who last recorded the item; review_status says whether
    # that was a confirmed review. Only cleared reflects clearance.
    asset.update(_review(_recorded(records, name, version)))
    return asset


def _assets(root: Path, records: list[dict]) -> list[dict]:
    assets = []
    for model in json.loads(
        (root / "src/docling_desk/resources/models/manifest.json").read_text(encoding="utf-8")
    ):
        assets.append(
            _asset(
                records,
                model["repo"],
                model["revision"],
                kind="model",
                source="src/docling_desk/resources/models/manifest.json",
                declared_license=model.get("declared_license"),
                license_source=model.get("license_source"),
                distribution=["docker"],
                notice="static/frontend/licenses.txt は画面のpackage表示です。モデルの利用条件は別です。",
            )
        )
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    for name in _docker_packages(dockerfile):
        assets.append(
            _asset(
                records,
                name,
                None,
                kind="os-package",
                source="Dockerfile",
                distribution=["docker"],
                notice=None,
            )
        )
    for image in _base_images(root):
        assets.append(
            _asset(
                records,
                image["image"],
                None,
                kind="base-image" if image["final"] else "build-image",
                source=image["source"],
                stage=image["stage"],
                digest_pinned=image["digest_pinned"],
                distribution=["docker"] if image["final"] else ["build"],
                notice=None
                if image["final"]
                else "最終イメージには入らない。成果物だけがCOPYされる。",
            )
        )
    for item in _json_file(root / "supply/assets.json", {}).get("assets", []):
        assets.append(
            _asset(
                records,
                item["name"],
                item.get("version"),
                kind=item["kind"],
                revision=item.get("revision"),
                source=item["source"],
                paths=item.get("paths", []),
                distribution=list(item["distribution"]),
                notice=item.get("notice"),
            )
        )
    assets.append(
        _asset(
            records,
            "tests/fixtures/documents",
            None,
            kind="fixture",
            source="tests/fixtures/documents",
            distribution=["test"],
            redistributable=True,
            notice="合成試料です。保存済みの実資料は合成検査へ混ぜません。",
        )
    )
    return assets


def inventory(root: Path) -> dict:
    records = _clearance(root)
    classes = _classification(root)
    build_only = frozenset(
        ((classes.get("npm") or {}).get("build_only_roots") or {}).get("names", [])
    )
    packages = _python_packages(root / "requirements-lock.txt", root)
    packages.extend(
        _lock_packages(
            (root / "frontend/pnpm-lock.yaml").read_text(encoding="utf-8"),
            json.loads((root / "frontend/package.json").read_text(encoding="utf-8")),
            "npm",
            "frontend/pnpm-lock.yaml",
            build_only,
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
        review = _review(_recorded(records, package["name"], package["version"]))
        package.update(review)
        if package.get("declared_license") is None and review.get("observed_license"):
            package["declared_license"] = review["observed_license"]
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
    decisions = _json_file(root / "supply/decisions.json", {"advisories": []})
    return {
        "packages": packages,
        "models": models,
        "assets": _assets(root, records),
        "decisions": decisions.get("advisories", []),
        "note": "declared_licenseは配布メタデータです。clearedがfalseの項目は利用権の確認済みではありません。",
    }


def distributed(item: dict) -> bool:
    """True when the item reaches users through a wheel, an image or a static export."""
    scope = item.get("scope")
    if scope in {"development", "build"}:
        return False
    return bool(set(item.get("distribution") or []) - {"development", "build", "test"})


def unreviewed_distribution(report: dict) -> list[dict]:
    """Distributed items that lack a confirmed human review. These block release readiness."""
    rows = []
    for group in ("packages", "assets"):
        for item in report[group]:
            if distributed(item) and not item["cleared"]:
                rows.append(
                    {
                        "kind": item.get("kind", "package:" + str(item.get("ecosystem"))),
                        "name": item["name"],
                        "version": item.get("version"),
                        "review_status": item.get("review_status", "unreviewed"),
                    }
                )
    return rows


def artifact_problems(root: Path, wheel: Path) -> list[str]:
    """Check shipped vendor bytes/notices against the versioned source manifests."""
    problems = []
    resources = root / "src/docling_desk/resources"
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for manifest in (resources / "static/vendor").rglob("manifest.json"):
            files = json.loads(manifest.read_text())["files"]
            for name, recorded in files.items():
                digest = recorded["sha256"] if isinstance(recorded, dict) else recorded
                relative = (manifest.parent / name).relative_to(resources)
                packaged = "docling_desk/resources/" + relative.as_posix()
                if packaged not in names:
                    problems.append("missing:" + packaged)
                elif hashlib.sha256(archive.read(packaged)).hexdigest() != digest:
                    problems.append("hash:" + packaged)
        for relative in (
            "static/frontend/licenses.txt",
            "static/vendor/ag-grid-LICENSE.txt",
            "static/vendor/tabulens-LICENSE.txt",
            "static/vendor/drawio/LICENSE",
            "static/vendor/drawio/mathjax-LICENSE",
            "static/vendor/drawio/img-LICENSE",
            "static/vendor/drawio/shapes-LICENSE",
            "static/vendor/drawio/stencils-LICENSE",
        ):
            packaged = "docling_desk/resources/" + relative
            if packaged not in names or not archive.read(packaged).strip():
                problems.append("notice:" + packaged)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory distribution provenance and clearance")
    parser.add_argument("--check-release", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    report = inventory(root)
    rows = unreviewed_distribution(report)
    problems = artifact_problems(root, args.wheel) if args.wheel else None
    result = {
        "packages": len(report["packages"]),
        "assets": len(report["assets"]),
        "distributed_not_cleared": len(rows),
        "items": rows,
        "artifact_problems": problems,
        "artifact_check": "not_run" if problems is None else "failed" if problems else "passed",
        "clearance_ok": not rows,
        "note": "棚卸し・バイト照合は人による配布条件の確認を代替しません。",
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)
    return 1 if args.check_release and (rows or problems) else 0


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


if __name__ == "__main__":
    sys.exit(main())
