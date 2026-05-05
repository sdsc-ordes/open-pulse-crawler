"""Streamlit GUI for the Open Pulse Crawler."""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx
import streamlit as st

_API_BASE = "http://api:8000"

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_ACTIVE_STATUSES = {"pending", "running", "paused"}


def _check_password() -> None:
    """Block GUI access until the configured password is entered.

    Reads ``GUI_PASSWORD`` from the environment.  If unset, the gate is
    open (with a visible warning) so dev runs without a configured
    password don't silently lock the user out.

    Compared with :func:`secrets.compare_digest` to avoid timing-leak
    side-channels.
    """
    expected = os.environ.get("GUI_PASSWORD", "")
    if not expected:
        st.warning(
            "GUI_PASSWORD is not set; the interface is unprotected. "
            "Set the env var on the gui container to require a password.",
            icon="⚠️",
        )
        return

    if st.session_state.get("_gui_authed"):
        return

    st.title("Open Pulse Crawler")
    entered = st.text_input(
        "Password",
        type="password",
        key="_gui_password_input",
        help="Set on the server via the GUI_PASSWORD environment variable.",
    )
    if not entered:
        st.info("Enter the password to continue.")
        st.stop()
    if not secrets.compare_digest(entered, expected):
        st.error("Incorrect password.")
        st.stop()
    st.session_state["_gui_authed"] = True
    st.rerun()


def _headers() -> dict[str, str]:
    token = st.session_state.get("api_token", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api_call(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    timeout: float = 10.0,
) -> dict | None:
    """Wrapper around httpx that surfaces errors via st.error and returns the
    JSON body on success. Returns ``None`` on any failure."""
    try:
        resp = httpx.request(
            method,
            f"{_API_BASE}{path}",
            headers=_headers(),
            json=json,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        # Surface the API's detail message when present (e.g. 409 conflicts).
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        st.error(f"API {method} {path} failed ({exc.response.status_code}): {detail}")
        return None
    except httpx.HTTPError as exc:
        st.error(f"API {method} {path} failed: {exc}")
        return None


def _poll_job(job_id: str) -> dict | None:
    return _api_call("GET", f"/api/v1/crawl/{job_id}")


def _sidebar() -> None:
    with st.sidebar:
        st.header("Settings")
        st.text_input(
            "API Token",
            type="password",
            key="api_token",
            help="Bearer token used to authenticate with the crawler API.",
        )


# ── Crawl form ──────────────────────────────────────────────────────────────


def _crawl_form() -> None:
    st.subheader("Start a Crawl")

    with st.form("crawl_form"):
        seeds_raw = st.text_area(
            "Seed nodes",
            placeholder="octocat\norg:github\nrepo:streamlit/streamlit",
            help=(
                "One seed per line. Accepts usernames, org/repo identifiers, "
                "or full GitHub URLs."
            ),
        )
        max_rounds = st.slider(
            "BFS rounds",
            min_value=1,
            max_value=10,
            value=2,
            help="How many breadth-first rounds to run.",
        )

        with st.expander("Dependency crawling", expanded=False):
            crawl_dependencies = st.checkbox(
                "Crawl dependencies (downstream / SBOM)",
                value=False,
                help="Discover repos this seed depends on.",
            )
            crawl_dependents = st.checkbox(
                "Crawl dependents (upstream / 'Used by')",
                value=False,
                help="Discover repos that depend on this seed.",
            )
            col_a, col_b = st.columns(2)
            min_stars = col_a.number_input(
                "Min stars",
                min_value=0,
                value=0,
                step=10,
                help="Filter dependents/dependencies by minimum star count.",
            )
            max_dependents = col_b.number_input(
                "Max dependents (0 = all)",
                min_value=0,
                value=0,
                step=50,
                help="Cap dependents fetched per repo. 0 means unlimited.",
            )

        with st.expander("Performance & filtering", expanded=False):
            col_c, col_d = st.columns(2)
            max_contributors = col_c.number_input(
                "Max contributors (0 = unlimited)",
                min_value=0,
                value=0,
                step=50,
                help=(
                    "Skip contributor expansion for repos above this threshold. "
                    "The repo node still lands in the graph; only its contributors "
                    "are not queued. Useful for avoiding mega-projects."
                ),
            )
            batch_size = col_d.number_input(
                "Batch size (0 = server default)",
                min_value=0,
                value=0,
                step=1,
                help="Concurrent nodes per round.",
            )

        submitted = st.form_submit_button("Crawl")

    if not submitted:
        return

    seeds = [s.strip() for s in seeds_raw.splitlines() if s.strip()]
    if not seeds:
        st.warning("Please enter at least one seed node.")
        return
    if not st.session_state.get("api_token"):
        st.warning("Please enter an API token in the sidebar.")
        return

    # Build the request body, omitting fields that mean "use server default".
    body: dict[str, Any] = {
        "seeds": seeds,
        "max_rounds": max_rounds,
        "crawl_dependencies": crawl_dependencies,
        "crawl_dependents": crawl_dependents,
        "min_stars": int(min_stars),
    }
    if max_dependents > 0:
        body["max_dependents"] = int(max_dependents)
    if max_contributors > 0:
        body["max_contributors"] = int(max_contributors)
    if batch_size > 0:
        body["batch_size"] = int(batch_size)

    with st.spinner("Submitting crawl job…"):
        data = _api_call("POST", "/api/v1/crawl", json=body)
    if data:
        st.session_state["last_job_id"] = data["job_id"]
        st.session_state.pop("last_result", None)
        st.success(f"Job submitted: `{data['job_id']}`")


# ── Results / progress ──────────────────────────────────────────────────────


def _format_eta(result: dict) -> str:
    """Render the ETA / elapsed window for a running job."""
    started = result.get("started_at")
    eta = result.get("estimated_completion_at")
    if eta:
        return f"ETA {eta}"
    if started:
        return f"Running since {started}"
    return "—"


def _job_controls(job_id: str, status: str) -> None:
    """Pause / resume / cancel / delete buttons, gated by current status."""
    cols = st.columns(4)

    pause_disabled = status not in {"running"}
    resume_disabled = status != "paused"
    cancel_disabled = status not in _ACTIVE_STATUSES
    delete_disabled = status not in _TERMINAL_STATUSES

    if cols[0].button("⏸ Pause", disabled=pause_disabled, use_container_width=True):
        if _api_call("POST", f"/api/v1/crawl/{job_id}/pause"):
            st.toast("Pause requested.")
            st.rerun()
    if cols[1].button("▶ Resume", disabled=resume_disabled, use_container_width=True):
        if _api_call("POST", f"/api/v1/crawl/{job_id}/resume"):
            st.toast("Resumed.")
            st.rerun()
    if cols[2].button("🛑 Cancel", disabled=cancel_disabled, use_container_width=True):
        if _api_call("POST", f"/api/v1/crawl/{job_id}/cancel"):
            st.toast("Cancellation requested.")
            st.rerun()
    if cols[3].button("🗑 Delete", disabled=delete_disabled, use_container_width=True):
        if _api_call("DELETE", f"/api/v1/crawl/{job_id}"):
            st.toast("Job deleted.")
            st.session_state.pop("last_job_id", None)
            st.session_state.pop("last_result", None)
            st.rerun()


def _results_area() -> None:
    st.subheader("Results")

    job_id = st.session_state.get("last_job_id")
    if not job_id:
        st.info("No crawl job submitted yet. Use the form above to start one.")
        return

    st.write(f"**Last job:** `{job_id}`")

    if st.button("🔄 Refresh status"):
        result = _poll_job(job_id)
        if result:
            st.session_state["last_result"] = result

    result = st.session_state.get("last_result")
    if not result:
        st.info("Click **Refresh status** to check the job.")
        return

    status = result.get("status", "unknown")
    st.metric("Status", status)

    # Lifecycle controls
    _job_controls(job_id, status)

    # Live progress for running / paused jobs.
    if status in _ACTIVE_STATUSES:
        prog_cols = st.columns(3)
        prog_cols[0].metric("Round", result.get("current_round") or "—")
        prog_cols[1].metric("Nodes processed", result.get("nodes_processed", 0))
        prog_cols[2].metric("Queue", result.get("nodes_in_queue", 0))
        st.caption(_format_eta(result))

    # Summary counts (populated mid-flight too once at least one node is done).
    cols = st.columns(3)
    cols[0].metric("Users", result.get("users", 0))
    cols[1].metric("Orgs", result.get("orgs", 0))
    cols[2].metric("Repos", result.get("repos", 0))

    if result.get("detail"):
        st.caption(result["detail"])

    # Download the graph once the job has completed.
    if status == "completed":
        if st.button("⬇ Download graph (JSON)"):
            graph = _api_call("GET", f"/api/v1/graph/{job_id}", timeout=60.0)
            if graph:
                import json

                st.download_button(
                    "Save graph.json",
                    data=json.dumps(graph, indent=2),
                    file_name=f"{job_id}.graph.json",
                    mime="application/json",
                )


def main() -> None:
    st.set_page_config(
        page_title="Open Pulse Crawler",
        page_icon="🔍",
        layout="wide",
    )
    _check_password()
    st.title("Open Pulse Crawler")
    st.caption("Discover GitHub users, organisations, and repositories via BFS crawling.")

    _sidebar()
    _crawl_form()
    st.divider()
    _results_area()


if __name__ == "__main__":
    main()
