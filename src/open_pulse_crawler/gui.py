"""Streamlit GUI for the Open Pulse Crawler."""

from __future__ import annotations

import os
import secrets

import httpx
import streamlit as st

_API_BASE = "http://api:8000"


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


def _poll_job(job_id: str) -> dict | None:
    try:
        resp = httpx.get(
            f"{_API_BASE}/api/v1/crawl/{job_id}",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        st.error(f"Failed to poll job: {exc}")
        return None


def _sidebar() -> None:
    with st.sidebar:
        st.header("Settings")
        st.text_input(
            "API Token",
            type="password",
            key="api_token",
            help="Bearer token used to authenticate with the crawler API.",
        )


def _crawl_form() -> None:
    st.subheader("Start a Crawl")

    with st.form("crawl_form"):
        seeds_raw = st.text_area(
            "Seed nodes",
            placeholder="octocat\norg:github\nrepo:streamlit/streamlit",
            help="One seed per line. Prefix with org: or repo: for non-user seeds.",
        )
        max_rounds = st.slider("BFS rounds", min_value=1, max_value=10, value=2)
        submitted = st.form_submit_button("Crawl")

    if submitted:
        seeds = [s.strip() for s in seeds_raw.splitlines() if s.strip()]
        if not seeds:
            st.warning("Please enter at least one seed node.")
            return
        if not st.session_state.get("api_token"):
            st.warning("Please enter an API token in the sidebar.")
            return

        with st.spinner("Submitting crawl job…"):
            try:
                resp = httpx.post(
                    f"{_API_BASE}/api/v1/crawl",
                    json={"seeds": seeds, "max_rounds": max_rounds},
                    headers=_headers(),
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                st.session_state["last_job_id"] = data["job_id"]
                st.success(f"Job submitted: `{data['job_id']}`")
            except httpx.HTTPError as exc:
                st.error(f"API request failed: {exc}")


def _results_area() -> None:
    st.subheader("Results")

    job_id = st.session_state.get("last_job_id")
    if not job_id:
        st.info("No crawl job submitted yet. Use the form above to start one.")
        return

    st.write(f"**Last job:** `{job_id}`")

    if st.button("Refresh status"):
        result = _poll_job(job_id)
        if result:
            st.session_state["last_result"] = result

    result = st.session_state.get("last_result")
    if result:
        status = result.get("status", "unknown")
        st.metric("Status", status)
        cols = st.columns(3)
        cols[0].metric("Users", result.get("users", 0))
        cols[1].metric("Orgs", result.get("orgs", 0))
        cols[2].metric("Repos", result.get("repos", 0))
        if result.get("detail"):
            st.caption(result["detail"])
    else:
        st.info("Click **Refresh status** to check the job.")


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
