"""Streamlit GUI for the Open Pulse Crawler."""

from __future__ import annotations

import httpx
import streamlit as st

_DEFAULT_API_BASE = "http://api:8000"


def _api_base() -> str:
    return st.session_state.get("api_base", _DEFAULT_API_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    token = st.session_state.get("api_token", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _poll_job(job_id: str) -> dict | None:
    try:
        resp = httpx.get(
            f"{_api_base()}/api/v1/crawl/{job_id}",
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
        st.text_input(
            "API Base URL",
            value=_DEFAULT_API_BASE,
            key="api_base",
            help="Base URL of the FastAPI backend (e.g. http://localhost:8000).",
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
                    f"{_api_base()}/api/v1/crawl",
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
    st.title("Open Pulse Crawler")
    st.caption("Discover GitHub users, organisations, and repositories via BFS crawling.")

    _sidebar()
    _crawl_form()
    st.divider()
    _results_area()


if __name__ == "__main__":
    main()
