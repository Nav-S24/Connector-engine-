"""Tests for Phase 6 packaging: Dockerfile, Docker Compose, CI workflow, README, and configs."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from connector.cli import app as cli_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_and_compose_manifest() -> None:
    """Dockerfile and docker-compose.yml exist, parse cleanly, and contain no hardcoded secrets."""
    dockerfile = REPO_ROOT / "Dockerfile"
    compose_file = REPO_ROOT / "docker-compose.yml"
    assert dockerfile.is_file()
    assert compose_file.is_file()

    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    assert "python:3.11" in dockerfile_text
    assert "mock_server.app:app" in dockerfile_text

    compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert isinstance(compose_data, dict)
    services = compose_data["services"]
    assert "mock-server" in services
    assert "connector" in services
    assert "8000:8000" in services["mock-server"]["ports"]
    assert "healthcheck" in services["mock-server"]
    assert services["connector"]["depends_on"]["mock-server"]["condition"] == "service_healthy"
    assert services["connector"]["environment"]["GEMINI_API_KEY"] == "${GEMINI_API_KEY:-}"


def test_github_actions_ci_workflow() -> None:
    """GitHub Actions workflow validates configs, runs pytest, and checks AI evals."""
    ci_file = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_file.is_file()

    ci_data = yaml.safe_load(ci_file.read_text(encoding="utf-8"))
    assert isinstance(ci_data, dict)
    assert "jobs" in ci_data
    job = next(iter(ci_data["jobs"].values()))
    steps_run = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "pytest" in steps_run
    assert "connector validate" in steps_run
    assert "benchmarks.eval_ai_layer" in steps_run


def test_readme_contains_architecture_mermaid_and_metrics() -> None:
    """README.md documents architecture via Mermaid, CLI/Docker usage, and benchmark results."""
    readme = REPO_ROOT / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "connector run" in text
    assert "connector generate" in text
    assert "connector repair" in text
    assert "docker compose" in text
    assert "26,091.28" in text
    assert "100.0%" in text


def test_all_shipped_connector_configs_validate_via_cli() -> None:
    """Every YAML file under connectors/ passes connector validate cleanly."""
    runner = CliRunner()
    configs = sorted((REPO_ROOT / "connectors").glob("*.yaml"))
    assert len(configs) == 3
    for cfg in configs:
        res = runner.invoke(cli_app, ["validate", str(cfg)])
        assert res.exit_code == 0, f"{cfg.name}: {res.output}"
