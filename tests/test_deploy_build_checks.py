from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_conflict_markers.py"
BUILD_SCRIPT = ROOT / "scripts" / "deploy-build.sh"


def run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_checker_reports_conflict_markers_with_file_and_line(tmp_path: Path) -> None:
    source = tmp_path / "app" / "main.py"
    source.parent.mkdir()
    source.write_text(
        "before = True\n<<<<<<< HEAD\nvalue = 1\n||||||| base\n"
        "=======\nvalue = 2\n>>>>>>> branch\n",
        encoding="utf-8",
    )

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "app/main.py:2: <<<<<<< HEAD" in result.stderr
    assert "app/main.py:4: ||||||| base" in result.stderr
    assert "app/main.py:5: =======" in result.stderr
    assert "app/main.py:7: >>>>>>> branch" in result.stderr
    assert "Remove the conflict markers before building." in result.stderr


def test_checker_ignores_non_source_files_and_builds_clean_source(tmp_path: Path) -> None:
    source = tmp_path / "app" / "main.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("<<<<<<< example\n", encoding="utf-8")

    result = run_checker(tmp_path)

    assert result.returncode == 0
    assert "No unresolved merge conflict markers found." in result.stdout


def run_deploy_build(
    tmp_path: Path,
    main_source: str,
    browser_source: str = "const value = 1;\n",
    browser_filename: str = "app.js",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    scripts = tmp_path / "scripts"
    app = tmp_path / "app"
    static = app / "static"
    fake_bin = tmp_path / "bin"
    scripts.mkdir()
    static.mkdir(parents=True)
    fake_bin.mkdir()
    (scripts / "deploy-build.sh").write_text(
        BUILD_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (scripts / "check_conflict_markers.py").write_text(
        CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (app / "main.py").write_text(main_source, encoding="utf-8")
    (static / browser_filename).write_text(browser_source, encoding="utf-8")
    python_calls = tmp_path / "python-calls"
    fake_python = fake_bin / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"scripts/check_conflict_markers.py\" ]; then\n"
        f"  exec {shlex.quote(sys.executable)} \"$@\"\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"compileall\" ]; then\n"
        f"  exec {shlex.quote(sys.executable)} \"$@\"\n"
        "fi\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(python_calls))}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        ["bash", str(scripts / "deploy-build.sh")],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return result, python_calls


def test_deploy_build_stops_before_installing_when_markers_exist(tmp_path: Path) -> None:
    result, python_calls = run_deploy_build(tmp_path, "<<<<<<< broken\n")

    assert result.returncode != 0
    assert "app/main.py:1: <<<<<<< broken" in result.stderr
    assert "Remove the conflict markers before building." in result.stderr
    assert not python_calls.exists()


def test_deploy_build_stops_before_installing_on_python_syntax_error(
    tmp_path: Path,
) -> None:
    result, python_calls = run_deploy_build(tmp_path, "if :\n")

    assert result.returncode != 0
    assert "SyntaxError" in result.stdout + result.stderr
    assert not python_calls.exists()


def test_deploy_build_accepts_valid_browser_javascript(tmp_path: Path) -> None:
    result, python_calls = run_deploy_build(
        tmp_path, "value = 1\n", "const value = 1;\n"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert python_calls.exists()
    assert "pip install" in python_calls.read_text(encoding="utf-8")


def test_deploy_build_reports_invalid_browser_javascript_before_installing(
    tmp_path: Path,
) -> None:
    result, python_calls = run_deploy_build(
        tmp_path,
        "value = 1\n",
        "const value = 1;\nfunction (\n",
        "broken.js",
    )

    assert result.returncode != 0
    assert "app/static/broken.js:2" in result.stderr
    assert not python_calls.exists()
