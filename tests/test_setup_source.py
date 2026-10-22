import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup_module_code():
    spec = importlib.util.spec_from_file_location("source_setup", ROOT / "scripts/setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_plan_uses_pinned_local_sources_without_mutation(tmp_path, monkeypatch, capsys):
    setup = setup_module_code()
    monkeypatch.setattr(
        setup.shutil, "which", lambda name: "/tools/corepack" if name == "corepack" else None
    )
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan executed")),
    )
    destination = tmp_path / "new-venv"
    assert setup.main(["--plan", "--venv", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "--frozen-lockfile" in output and "pnpm@10.24.0" in output
    assert "./docling" in output and "./packages/docling-azure-ocr" in output
    assert not destination.exists()


def test_missing_package_manager_fails_before_venv_creation(tmp_path, monkeypatch):
    setup = setup_module_code()
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    destination = tmp_path / "new-venv"
    assert setup.main(["--venv", str(destination)]) == 1
    assert not destination.exists()


def test_new_install_initializes_configured_state_before_diagnosis(tmp_path, monkeypatch):
    setup = setup_module_code()
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/tools/corepack")
    steps = setup.commands(sys.executable, Path(sys.prefix), models=False)
    initialization = next(i for i, (_, command) in enumerate(steps) if command[1] == "-c")
    assert initialization < len(steps) - 1
    assert steps[-1][1][-1] == "--diagnose"
    state = tmp_path / "new" / "documents"
    subprocess.run(
        steps[initialization][1],
        cwd=ROOT,
        env=dict(os.environ, DOCLING_DATA_DIR=str(state), DOCLING_ENV_FILE=os.devnull),
        check=True,
    )
    assert state.is_dir() and list(state.iterdir()) == []
