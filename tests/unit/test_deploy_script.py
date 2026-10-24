"""`scripts/deploy` argument handling and its dry run. Every tool it could call
(`gcpx`, `uv`, `npm`, `gcloud`) is a stub on PATH that records the call and
fails, so a dry run that executed anything would show up here."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy"
PREFIX = "+ gcpx exec example -p example-project -- gcloud "


@pytest.fixture
def stubs(tmp_path: Path) -> tuple[dict[str, str], Path]:
    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("gcpx", "gcloud", "uv", "npm", "gsutil", "bq"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\nexit 1\n')
        stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
           "HOME": str(tmp_path), "GSHEET_DEPLOY_ENV_FILE": os.devnull,
           "GCP_PROJECT": "example-project", "GCPX_ALIAS": "example"}
    return env, calls


def _deploy(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True,
                          timeout=60)


def test_dry_run_prints_every_command_and_runs_none(stubs):
    env, calls = stubs
    result = _deploy(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert not calls.exists(), calls.read_text()
    assert "+ uv run pytest" in result.stdout
    assert "+ uv run ruff check src tests" in result.stdout


def test_the_release_gate_comes_before_any_gcloud_call(stubs):
    env, _ = stubs
    lines = _deploy(env, "--dry-run").stdout.splitlines()
    first_gcloud = next(i for i, line in enumerate(lines) if "gcloud" in line)
    assert lines.index("+ uv run pytest") < first_gcloud
    assert lines.index("+ uv run ruff check src tests") < first_gcloud


def test_every_gcloud_call_goes_through_the_gcpx_alias(stubs):
    env, _ = stubs
    lines = [line for line in _deploy(env, "--dry-run").stdout.splitlines() if "gcloud" in line]
    assert lines
    for line in lines:
        assert line.startswith(PREFIX) or f"| {PREFIX[2:]}" in line, line


def test_the_service_runs_as_one_instance_sized_per_the_spec(stubs):
    env, _ = stubs
    (deploy,) = [line for line in _deploy(env, "--dry-run").stdout.splitlines()
                 if line.startswith(PREFIX + "run deploy")]
    for flag in ("--max-instances=1", "--cpu=1", "--memory=1Gi", "--concurrency=20",
                 "--timeout=300", "--allow-unauthenticated"):
        assert flag in deploy.split(), flag


def test_database_and_bucket_are_created_only_if_missing(stubs):
    env, _ = stubs
    out = _deploy(env, "--dry-run").stdout
    for check, create in (
        ("firestore databases describe --database=gsheet-mcp-auth",
         "firestore databases create --database=gsheet-mcp-auth"),
        ("storage buckets describe gs://example-project-gsheet-mcp-state",
         "storage buckets create gs://example-project-gsheet-mcp-state"),
    ):
        assert out.index(PREFIX + check) < out.index(PREFIX + create)
        assert "only if this fails" in out.split(PREFIX + check)[1].splitlines()[0]


def test_the_bucket_gets_a_one_day_delete_rule(stubs):
    env, _ = stubs
    out = _deploy(env, "--dry-run").stdout
    assert '{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}' in out
    assert PREFIX + "storage buckets update gs://example-project-gsheet-mcp-state" in out


def test_the_cli_tests_join_the_gate_only_when_cli_exists(stubs):
    env, _ = stubs
    out = _deploy(env, "--dry-run").stdout
    assert ("+ npm --prefix cli test" in out) == (ROOT / "cli").is_dir()


def test_a_new_client_id_and_secret_version_reach_the_service(stubs):
    env, _ = stubs
    env |= {"GSHEET_MCP_GOOGLE_CLIENT_ID": "123.apps.googleusercontent.com",
            "GSHEET_SECRET_VERSION": "2", "GSHEET_MCP_BASE_URL": "https://gsheet.example"}
    out = _deploy(env, "--dry-run").stdout
    assert "GSHEET_MCP_GOOGLE_CLIENT_ID=123.apps.googleusercontent.com" in out
    assert "GSHEET_MCP_GOOGLE_CLIENT_SECRET=gsheet-mcp-google-client-secret:2" in out
    assert "GSHEET_MCP_BASE_URL=https://gsheet.example" in out
    assert "projects describe" not in out


@pytest.mark.parametrize("unset", ["GCP_PROJECT", "GCPX_ALIAS"])
def test_a_missing_project_or_alias_stops_before_anything_runs(stubs, unset):
    env, calls = stubs
    del env[unset]
    result = _deploy(env, "--dry-run")
    assert result.returncode == 2
    assert unset in result.stderr
    assert not result.stdout and not calls.exists()


def test_an_unknown_argument_is_refused(stubs):
    env, calls = stubs
    result = _deploy(env, "--yes")
    assert result.returncode == 2
    assert "unknown argument: --yes" in result.stderr
    assert not calls.exists()


def test_help_runs_nothing(stubs):
    env, calls = stubs
    result = _deploy(env, "--help")
    assert result.returncode == 0 and "--dry-run" in result.stdout
    assert not calls.exists()


def test_project_and_alias_are_read_from_the_env_file(stubs, tmp_path):
    env, calls = stubs
    dotenv = tmp_path / ".env"
    dotenv.write_text("SAMPLE_SHEET_URL=https://x.example/?a=1&b=2#gid=0\n"
                      "GCPX_ALIAS=from-file\nGCP_PROJECT=file-project\n")
    del env["GCP_PROJECT"], env["GCPX_ALIAS"]
    env["GSHEET_DEPLOY_ENV_FILE"] = str(dotenv)
    result = _deploy(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "+ gcpx exec from-file -p file-project -- gcloud " in result.stdout
    assert not calls.exists()
