# Open Pulse Crawler

A multi-platform crawler for the open-science graph. BFS-walks public APIs across GitHub, GitLab, Zenodo, Infoscience, DataCite, and HuggingFace and emits a unified graph keyed by canonical URLs.

Powers <https://openpulse.science> — this crawler is the data plane behind the open-science observability platform there.

## What it does

Seed a public URL (a GitHub repo, a Zenodo record, an EPFL Infoscience handle, a DOI, an ORCID, a HuggingFace paper, …) and the crawler walks outward N rounds, discovering related entities and emitting typed edges between them. Output is a single graph (JSON / CSV / JSON-LD) with the same node-key contract across every platform: **every node is identified by its full `https://...` URL**, so a record on Zenodo and a paper on HuggingFace that reference the same arxiv ID naturally converge at the *same canonical edge target*.

## Platforms

| Platform | Hosts | Anonymous? | Guide |
|---|---|---|---|
| **GitHub** | `github.com` | Token required (60/hr anon is too low) | — |
| **GitLab** | `gitlab.com`, `gitlab.epfl.ch`, `gitlab.ethz.ch`, `*.renkulab.io`, … | Yes — public projects/users/groups | [docs/GITLAB.md](docs/GITLAB.md) |
| **Zenodo** | `zenodo.org`, `sandbox.zenodo.org` | Yes | [docs/ZENODO.md](docs/ZENODO.md) |
| **Infoscience** | `infoscience.epfl.ch` (DSpace 7 + CRIS) | Yes | [docs/INFOSCIENCE.md](docs/INFOSCIENCE.md) |
| **DataCite Commons** | `doi.org`, `ror.org`, `orcid.org`, `api.datacite.org`, `commons.datacite.org` | Yes | [docs/DATACITE.md](docs/DATACITE.md) |
| **HuggingFace** | `huggingface.co` | Yes | [docs/HUGGINGFACE.md](docs/HUGGINGFACE.md) |

Cross-platform edges work out of the box: a Zenodo record, an Infoscience publication, a DataCite work, and a HuggingFace paper that all reference the same arxiv paper produce edges to the *same* `https://arxiv.org/abs/<id>` URL — the convergence is automatic via the shared DataCite RelationType vocabulary.

## Installation

```bash
# Install the package
pip install -e .

# With visualization support
pip install -e ".[visualization]"

# With development tools
pip install -e ".[dev]"
```

## Quickstart

### Configure tokens (anonymous-friendly platforms can skip this)

Copy `.env.dist` to `.env` and fill in what you need. Only GitHub strictly requires a token; everything else is optional (anonymous works, tokens raise rate limits):

```bash
CRAWLER_PLATFORMS=github.com,zenodo.org,huggingface.co
CRAWLER_TOKEN__GITHUB_COM=ghp_…
# Optional — anonymous works without these:
# CRAWLER_TOKEN__ZENODO_ORG=…
# CRAWLER_TOKEN__HUGGINGFACE_CO=hf_…
```

Run `opc doctor` to verify which platforms are wired up — it shows tri-state per host: **OK** (token configured), **ANONYMOUS** (no token, public reads work), **MISSING** (token required but absent).

### Crawl

```bash
# Single seed, two BFS rounds
opc crawl --rounds 2 https://huggingface.co/papers/2307.09288

# Multi-platform crawl from a seed file
opc crawl --seed-file seeds.txt --rounds 3 --output-dir ./results

# Resume from saved state
opc crawl --resume --state-file state.json
```

Seeds accept short forms (`torvalds`, `owner/repo`) and full URLs across any supported platform. See `opc crawl --help` for the full option list (rate-limit knobs, dependency graphs, issue/PR activity, visualization).

## Node identifiers

Every node in the exported graph is keyed by its **canonical public URL** — always with the `https://` scheme. The URL is the single ID used as the dict key in JSON output, the `id` column in nodes CSV, and the `source`/`target` columns in edges CSV.

| Platform | Canonical graph key |
|---|---|
| GitHub user / repo / team | `https://github.com/torvalds`, `https://github.com/torvalds/linux`, `https://github.com/orgs/acme/teams/core` |
| GitLab (any instance) | `https://gitlab.epfl.ch/users/bovel`, `https://gitlab.renkulab.io/<group>/<project>` |
| Zenodo record / community / user | `https://zenodo.org/records/<id>`, `https://zenodo.org/communities/<slug>`, `https://zenodo.org/users/<id>` |
| Infoscience handle | `https://infoscience.epfl.ch/handle/20.500.14299/<id>` |
| DataCite work via DOI | `https://doi.org/<doi>` |
| DataCite organization (ROR) | `https://ror.org/<id>` |
| DataCite person (ORCID) | `https://orcid.org/<id>` |
| DataCite repository | `https://commons.datacite.org/repositories/<id>` |
| HuggingFace model / dataset / space | `https://huggingface.co/<owner>/<name>`, `…/datasets/<owner>/<name>`, `…/spaces/<owner>/<name>` |
| HuggingFace paper / collection | `https://huggingface.co/papers/<arxiv-id>`, `…/collections/<owner>/<slug>` |

**Why full URLs?** Browser-resolvable, JSON-LD `@id`-compatible (downstream linked-data tooling expects IRIs), and they make the cross-platform pivot work automatically — a Zenodo record, an Infoscience publication, a DataCite work, and a HuggingFace paper that all reference the same arxiv paper produce edges to the *same* canonical `https://arxiv.org/abs/<id>` URL.

**Dual storage of bare identifiers.** Where it adds value, the scheme-native form of the identifier lives alongside the URL as a typed metadata field — not as a separate node:

```
DataCiteWork:         url=https://doi.org/10.6084/m9.figshare.99   doi="10.6084/m9.figshare.99"
DataCiteOrganization: url=https://ror.org/02s376052                ror_id="02s376052"
DataCitePerson:       url=https://orcid.org/0000-0002-1825-0097    orcid="0000-0002-1825-0097"
HuggingFacePaper:     url=https://huggingface.co/papers/2307.09288 arxiv_id="2307.09288"
```

Downstream tools that want to query by scheme-native form use the typed field; the graph itself stays URL-keyed.

DOI URLs matching the prefix table (`10.5281/zenodo.*`, `10.5072/zenodo.*`) are rewritten to the platform's canonical URL before BFS dispatch — so a seed of `https://doi.org/10.5281/zenodo.42` enters the graph as `https://zenodo.org/records/42`.

## REST API

The crawler ships a FastAPI service with two routers:

- `/api/v1/*` — the legacy GitHub-only endpoints.
- `/api/v2/*` — the unified multi-platform endpoints (recommended). Swagger UI at `/api/v1/docs` exposes ~23 example request bodies across all five platforms, including cross-platform demos (DOI prefix routing, ORCID URL canonical seed, HuggingFace collection walks, multi-platform single-POST jobs).

`POST /api/v2/crawl` accepts a list of seed URLs and returns a job ID; `GET /api/v2/graph/{job_id}` returns the discovered graph. `GET /api/v2/platforms` lists configured hosts + token status.

See [`docs/API.md`](docs/API.md) for the full endpoint reference.

## Docker

The repository ships a three-container stack (FastAPI + Streamlit GUI + Nginx):

```bash
cp .env.dist .env
docker compose -f infra/docker-compose.yml up -d --build
```

Then open:
- <http://localhost/> — Streamlit GUI
- <http://localhost/api/v1/docs> — Swagger UI
- <http://localhost/api/v1/health> — health check

If you set `OPC_PORT=8080`, swap the port accordingly. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for full deployment options.

## Output formats

The crawler emits its graph in three formats from the same in-memory representation:

- **JSON** — full graph as nested dicts of node-URL → node payload.
- **CSV nodes** — one row per discovered node (URL, type, is_seed, is_explored, timestamp).
- **CSV edges** — one row per discovered edge (source URL, target URL, edge kind, source/target subkind).
- **JSON-LD** — same graph re-emitted with `@id`/`@type` per node; downstream linked-data tooling consumes this directly.

Edge kinds are stable across platforms where the semantics overlap (`owned_by`, `contributor_of`, `related_to.<RelationType>`, `member_of`, …) and platform-specific where they don't (`uses_model` only for HuggingFace spaces, `parent_of` for GitHub teams + Infoscience org-units, `references_model` / `references_dataset` / `references_space` for HuggingFace papers, etc.). See each platform's guide for the full table.

## Development

```bash
# Install with dev tools
pip install -e ".[dev]"

# Run unit tests (live integration tests are deselected by default)
pytest -m "not integration"

# Live integration tests against real APIs (anonymous, polite)
CRAWLER_SKIP_INTEGRATION=0 pytest -m integration

# Skip integration tests explicitly
CRAWLER_SKIP_INTEGRATION=1 pytest
```

Specs and plans for each platform live under `docs/superpowers/` — useful as architectural reference when adding a new adapter.

## License

Apache 2.0

## Author

Carlos Vivar Rios (`@caviri`) — Swiss Data Science Center (SDSC), EPFL.

Part of the [Open Pulse](https://openpulse.science) project.
