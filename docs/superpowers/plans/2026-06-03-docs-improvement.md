# Docs Improvement Plan — examples + overall reading

**Date:** 2026-06-03
**Status:** P0–P3 implemented (`opc` alias; index links + de-versioned headers;
all 7 guide recipes + README quickstart rewritten with real live output;
cross-link footers + index hub table; anti-rot guard at
`tools/check_doc_examples.py`). Heavy template-uniformity (P2-1) intentionally
skipped — guides were already aligned; platform-specific sections kept.
**Scope:** improve the examples in the docs and the overall reading experience. No product code changes except one optional CLI alias (see P0-1).

## Why now

The docs grew organically across 7 platform adapters. A survey of `docs/*.md` +
`README.md` (3,300 lines, 16 files) found concrete, fixable problems — several
of which make copy-pasted examples fail outright.

## Findings (grounded in the survey)

1. **`opc` is not a real command.** `pyproject.toml` defines only the
   `open-pulse-crawler` console script, but the docs use `opc` **~38 times
   across 10 files** (README, OPENALEX, DATACITE, HUGGINGFACE, ZENODO,
   INFOSCIENCE, GITLAB, CROSSREF, API, DEPLOYMENT). Every CLI example a reader
   pastes fails with “command not found.” **Highest-impact bug.**
2. **`docs/index.md` is stale.** Its “Where to start” list links GitLab,
   Zenodo, Infoscience, DataCite, HuggingFace — but **not** OpenAlex or
   Crossref. New platforms are undiscoverable from the hub.
3. **Examples never show expected output.** Every platform guide’s
   “Manual-test recipes” shows the command but not what it returns, so a reader
   can’t tell success from failure. (We captured real outputs this session —
   e.g. a 2-round OpenAlex crawl = “80 nodes: 74 works, 5 authors, 1 source” —
   ideal example output.)
4. **Template drift.** The platform guides *mostly* share a skeleton
   (Supported entities → Edge kinds → Configuring tokens → Seed forms →
   Manual-test recipes → Limitations) but OpenAlex/Crossref diverge (different
   headers, “Out of scope” vs “Limitations”). Hard to scan across guides.
5. **Version-stamped headers rot.** `## Limitations (v3.3)`, `(v3.4)`, `(v3.1)`
   are inconsistent and go stale as versions move.
6. **Placeholder seeds.** Recipes use throwaway DOIs rather than a small set of
   known-good canonical seeds with described, reproducible results.

## Goals

- Every example is **copy-paste runnable** and **shows what success looks
  like**.
- **One consistent structure** across platform guides so readers build a mental
  template once.
- A clear reading path: **README → index hub → platform guide → API/NODE_IDS**.
- Examples can’t silently rot again (a lightweight guard).

## Workstreams

### P0 — Correctness sweep (high impact, low effort)
- **P0-1 — Fix the `opc` command.** **Recommended:** add `opc` as a second
  console-script alias in `pyproject.toml`
  (`opc = "open_pulse_crawler.cli:main"`). One line makes all ~38 existing
  examples correct *and* gives users a nicer short command. (Alternative:
  rewrite 38 occurrences to `open-pulse-crawler` — more churn, worse UX.
  Decision needed; recommendation is the alias.)
- **P0-2 — Fix `docs/index.md`** to link `OPENALEX.md` and `CROSSREF.md`
  (and confirm every guide in `docs/` is linked from the hub).
- **P0-3 — De-version section headers**: `## Limitations (v3.3)` → `## Limitations`.

### P1 — The examples standard (the core ask)
- **P1-1 — Define one recipe format** and apply it everywhere. Each recipe =
  1. a one-line *intent* ("Crawl a paper and its citation neighborhood");
  2. the **command** (correct binary, real seed);
  3. an **expected-output** fenced block (trimmed real output);
  4. a **"what to look for"** line (the 1–2 signals that prove it worked).
- **P1-2 — Curate canonical seeds.** A small, stable, public seed set reused
  across guides (e.g. the OpenAlex/Glia DOI, an ORCID, a ROR, a Zenodo record,
  a HF paper) with reproducible described results. Capture real outputs (we
  already have several from this session’s live testing).
- **P1-3 — A copy-paste Quickstart that actually runs end to end** in README:
  install → `doctor` (with sample output) → one anonymous crawl (no token) →
  where the output lands → how to read it.

### P2 — Structure & reading flow
- **P2-1 — Canonical platform-guide template** (see below); align all 7 guides.
- **P2-2 — `index.md` as a real hub**: a "pick your platform" table
  (platform → seed example → guide link) mirroring the README table, plus a
  short "how the pieces fit" diagram/paragraph.
- **P2-3 — Cross-linking**: every platform guide links to `NODE_IDS.md`
  (keying) and `API.md` (programmatic use); README links the hub.
- **P2-4 — README progressive disclosure**: lead with the 3-line value prop +
  one runnable example; push the full platform/key tables below the fold.

### P3 — Keep examples from rotting
- **P3-1 — Example-extraction smoke check**: a small script (and CI/`make`
  target) that extracts fenced ```bash blocks tagged for testing from the docs
  and runs the safe, fast, anonymous ones (e.g. `--help`, `doctor`, a 1-round
  anonymous crawl), failing the build if a documented command errors. This is
  what would have caught the `opc` and `--platforms` issues automatically.

## Proposed canonical platform-guide template

```
# <Platform> adapter
<1-paragraph: what it is + the one thing it uniquely adds>
## Rationale            (why this adapter exists / what it unlocks)
## Supported entities   (table: subkind → canonical key)
## Edge kinds           (table: source subkind → kind → target)
## Configuring access   (tokens or "anonymous — no token needed" + env vars)
## Seed forms accepted
## Recipes              (each: intent → command → expected output → what to look for)
## Limitations & out of scope   (un-versioned)
```

## Sequencing & effort

| Phase | Effort | Payoff |
|---|---|---|
| **P0** | ~1 hr | Examples stop failing; new guides discoverable |
| **P1** | ~half day | The actual "better examples" deliverable |
| **P2** | ~half day | Consistent, scannable, well-linked |
| **P3** | ~2–3 hrs | Examples verified in CI; no future rot |

## Suggested execution

Do **P0 first as one small PR** (immediate correctness win). Then P1+P2 per
guide via the subagent-driven flow — **one subagent per platform guide**, each
given the canonical template + the curated seed/output for that platform +
spec/quality review — so the 7 guides are rewritten consistently in parallel.
P3 last as a standalone CI guard.

## Out of scope
- Rewriting `API.md` reference content (already comprehensive) beyond the
  example-format + cross-link pass.
- New product features. The only code change is the optional `opc` alias (P0-1).
