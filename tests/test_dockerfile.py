"""Tests for the root Dockerfile and container-readiness of the API."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"


class TestDockerfileStructure:
    """Validate that the Dockerfile exists and follows the expected conventions."""

    def test_dockerfile_exists(self):
        assert DOCKERFILE.is_file(), "Root Dockerfile must exist"

    def test_uses_multistage_build(self):
        content = DOCKERFILE.read_text()
        from_statements = re.findall(r"^FROM\s+", content, re.MULTILINE)
        assert len(from_statements) >= 2, "Dockerfile should use a multi-stage build (>= 2 FROM)"

    def test_builder_stage_uses_uv(self):
        content = DOCKERFILE.read_text()
        assert "uv" in content.lower(), "Builder stage should use uv"

    def test_runtime_stage_uses_python312_slim(self):
        content = DOCKERFILE.read_text()
        assert re.search(r"FROM\s+python:3\.12-slim", content), (
            "Runtime stage must be based on python:3.12-slim"
        )

    def test_exposes_port_8000(self):
        content = DOCKERFILE.read_text()
        assert re.search(r"EXPOSE\s+8000", content), "Dockerfile must EXPOSE 8000"

    def test_runs_uvicorn(self):
        content = DOCKERFILE.read_text()
        assert "uvicorn" in content, "Dockerfile must run uvicorn"
        assert "open_pulse_crawler.api:app" in content, (
            "Entrypoint must reference open_pulse_crawler.api:app"
        )

    def test_runs_as_nonroot_user(self):
        content = DOCKERFILE.read_text()
        assert re.search(r"^USER\s+\S+", content, re.MULTILINE), (
            "Dockerfile should switch to a non-root USER"
        )

    def test_dockerignore_exists(self):
        assert DOCKERIGNORE.is_file(), ".dockerignore must exist alongside Dockerfile"

    def test_dockerignore_excludes_secrets(self):
        content = DOCKERIGNORE.read_text()
        assert ".env" in content, ".dockerignore should exclude .env files"


class TestUvicornEntrypoint:
    """Verify that the uvicorn entrypoint resolves correctly (no import errors)."""

    def test_api_module_importable(self):
        from open_pulse_crawler.api import app  # noqa: F401

    def test_health_via_testclient(self):
        from fastapi.testclient import TestClient

        from open_pulse_crawler.api import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert "version" in body
