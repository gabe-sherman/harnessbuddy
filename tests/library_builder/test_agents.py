from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from harnessbuddy.core.repos import RepoSource
from harnessbuddy.library_builder.agents import _build_prompt, _ensure_provenance, agent_generate
from harnessbuddy.library_builder.analysis import analyze

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "repos"
_FAKE_URL = "https://github.com/example/mylib.git"


def _analysis(fixture_name: str):  # type: ignore[no-untyped-def]
    source = RepoSource(
        source_path=_FIXTURES / fixture_name,
        clone_url=_FAKE_URL,
        project_name="mylib",
        repo_ref=None,
    )
    return analyze(source)


def _fake_completed(returncode: int = 0) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.returncode = returncode
    return mock


class TestAgentGenerate:
    def test_command_includes_dangerously_skip_permissions(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)) as mock_run:
            agent_generate(analysis, output_path)

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd

    def test_command_uses_claude(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)) as mock_run:
            agent_generate(analysis, output_path)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"

    def test_command_uses_p_flag(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)) as mock_run:
            agent_generate(analysis, output_path)

        cmd = mock_run.call_args[0][0]
        assert "-p" in cmd

    def test_cwd_is_source_path(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)) as mock_run:
            agent_generate(analysis, output_path)

        kwargs = mock_run.call_args[1]
        assert kwargs["cwd"] == analysis.source_path

    def test_succeeded_when_exit_code_zero(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)):
            result = agent_generate(analysis, output_path)

        assert result.succeeded is True
        assert result.exit_code == 0

    def test_failed_when_exit_code_nonzero(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(1)):
            result = agent_generate(analysis, output_path)

        assert result.succeeded is False
        assert result.exit_code == 1

    def test_files_collected_from_output_path(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()
        (output_path / "Dockerfile").write_text("FROM scratch\n")
        (output_path / "project.yaml").write_text("homepage: x\n")

        with patch("subprocess.run", return_value=_fake_completed(0)):
            result = agent_generate(analysis, output_path)

        assert len(result.files) == 3  # Dockerfile + project.yaml + provenance.json
        file_names = {f.name for f in result.files}
        assert "Dockerfile" in file_names
        assert "project.yaml" in file_names

    def test_files_empty_when_output_path_missing(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(1)):
            result = agent_generate(analysis, output_path)

        assert result.files == []

    def test_output_path_in_result(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)):
            result = agent_generate(analysis, output_path)

        assert result.output_path == output_path

    def test_duration_recorded(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)):
            result = agent_generate(analysis, output_path)

        assert result.duration_seconds >= 0.0

    def test_timeout_forwarded(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        output_path = tmp_path / "mylib"
        output_path.mkdir()

        with patch("subprocess.run", return_value=_fake_completed(0)) as mock_run:
            agent_generate(analysis, output_path, timeout=42)

        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 42


class TestBuildPrompt:
    def test_prompt_includes_project_name(self) -> None:
        analysis = _analysis("cmake_repo")
        prompt = _build_prompt(analysis, Path("/tmp/out"))
        assert "mylib" in prompt

    def test_prompt_includes_clone_url(self) -> None:
        analysis = _analysis("cmake_repo")
        prompt = _build_prompt(analysis, Path("/tmp/out"))
        assert _FAKE_URL in prompt

    def test_prompt_includes_build_system(self) -> None:
        analysis = _analysis("cmake_repo")
        prompt = _build_prompt(analysis, Path("/tmp/out"))
        assert "cmake" in prompt

    def test_prompt_includes_output_path(self) -> None:
        analysis = _analysis("cmake_repo")
        out = Path("/tmp/out/mylib")
        prompt = _build_prompt(analysis, out)
        assert str(out) in prompt

    def test_prompt_includes_repo_ref_null(self) -> None:
        analysis = _analysis("cmake_repo")
        prompt = _build_prompt(analysis, Path("/tmp/out"))
        assert "null" in prompt

    def test_prompt_includes_repo_ref_when_set(self) -> None:
        source = RepoSource(
            source_path=_FIXTURES / "cmake_repo",
            clone_url=_FAKE_URL,
            project_name="mylib",
            repo_ref="v1.2.3",
        )
        analysis = analyze(source)
        prompt = _build_prompt(analysis, Path("/tmp/out"))
        assert "v1.2.3" in prompt


class TestEnsureProvenance:
    def test_writes_provenance_when_missing(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        _ensure_provenance(tmp_path, analysis)
        provenance_path = tmp_path / "provenance.json"
        assert provenance_path.exists()

    def test_provenance_contains_project_name(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        _ensure_provenance(tmp_path, analysis)
        data = json.loads((tmp_path / "provenance.json").read_text())
        assert data["project_name"] == "mylib"

    def test_provenance_records_generation_method(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        _ensure_provenance(tmp_path, analysis)
        data = json.loads((tmp_path / "provenance.json").read_text())
        assert data["generation_method"] == "agent"

    def test_does_not_overwrite_existing_provenance(self, tmp_path: Path) -> None:
        analysis = _analysis("cmake_repo")
        existing = {"custom": "data"}
        (tmp_path / "provenance.json").write_text(json.dumps(existing))
        _ensure_provenance(tmp_path, analysis)
        data = json.loads((tmp_path / "provenance.json").read_text())
        assert data == existing
