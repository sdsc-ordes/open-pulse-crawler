# HuggingFace adapter

Open Pulse Crawler v3.4+ supports **HuggingFace** —
users, organizations, models, datasets, spaces, papers, and collections.
Anonymous reads work without a token.

## Supported entities

- **`HuggingFaceUser`** — a user account. Distinguished from a
  `HuggingFaceOrg` only by which API endpoint returned 200
  (`/users/<x>/overview` vs `/organizations/<x>/overview`) — URL form is
  identical (`huggingface.co/<name>`).
- **`HuggingFaceOrg`** — an organization (e.g. `meta-llama`, `openai`,
  `BigScience`).
- **`HuggingFaceRepo`** — a model, dataset, or space. Discriminated by
  the `repo_type` field (`"model" | "dataset" | "space"`). All three
  share git-repo plumbing (owner/name, sha, files, tags, downloads,
  likes, cardData); only the URL path prefix and a handful of typed
  fields differ.
- **`HuggingFacePaper`** — a paper on HuggingFace, keyed by arxiv ID
  (`huggingface.co/papers/2307.09288`). HF aggregates arxiv metadata
  plus HF-specific cross-references (`linkedModels` / `linkedDatasets` /
  `linkedSpaces` / `githubRepo`) that make papers the strongest
  cross-platform pivot in the graph.
- **`HuggingFaceCollection`** — a user-curated grouping ("bucket"): the
  owner picks N models / datasets / spaces / papers and gives the
  bundle a title + description.

## Edge kinds

| Source | Kind | Target |
|---|---|---|
| `HuggingFaceUser` | `owns` | `HuggingFaceRepo` (model/dataset/space) |
| `HuggingFaceUser` | `member_of` | `HuggingFaceOrg` |
| `HuggingFaceOrg` | `owns` | `HuggingFaceRepo` (model/dataset/space) |
| `HuggingFaceRepo` | `owned_by` | `HuggingFaceUser` OR `HuggingFaceOrg` |
| `HuggingFaceRepo` *(spaces only)* | `uses_model` | `HuggingFaceRepo` (model) |
| `HuggingFacePaper` | `related_to.IsIdenticalTo` | URL on any platform (arxiv.org) |
| `HuggingFacePaper` | `related_to.IsSupplementedBy` | URL on any platform (github.com) |
| `HuggingFacePaper` | `references_model` | `HuggingFaceRepo` (model) |
| `HuggingFacePaper` | `references_dataset` | `HuggingFaceRepo` (dataset) |
| `HuggingFacePaper` | `references_space` | `HuggingFaceRepo` (space) |
| `HuggingFaceCollection` | `owned_by` | `HuggingFaceUser` OR `HuggingFaceOrg` |
| `HuggingFaceCollection` | `contains` | `HuggingFaceRepo` OR `HuggingFacePaper` |

`related_to.IsIdenticalTo` and `related_to.IsSupplementedBy` reuse the
shared DataCite RelationType vocabulary: the URL synthesizer in
`platforms/datacite.py` handles the `arxiv` scheme for paper bridges,
producing the same canonical `https://arxiv.org/abs/<id>` URL that
Zenodo / Infoscience / DataCite records produce when they reference the
same arxiv ID. **This is what makes HF papers the cross-platform pivot.**

## Configuring tokens

Anonymous reads work for all entity endpoints. Tokens raise rate limits
and unlock gated content (gated models, private spaces if you have
access):

```bash
CRAWLER_PLATFORMS=huggingface.co
CRAWLER_TOKEN__HUGGINGFACE_CO=hf_<token>
# Or rotation pool:
CRAWLER_TOKEN_POOL__HUGGINGFACE_CO=hf_a,hf_b
```

Tokens are provisioned at <https://huggingface.co/settings/tokens>.
Read-only tokens suffice for the crawl-only workload.

## Seed forms accepted

- `https://huggingface.co/<owner>/<name>` — model
- `https://huggingface.co/datasets/<owner>/<name>` — dataset
- `https://huggingface.co/spaces/<owner>/<name>` — space
- `https://huggingface.co/papers/<arxiv-id>` — paper (modern arxiv IDs only)
- `https://huggingface.co/collections/<owner>/<slug>` — collection
- `https://huggingface.co/<username>` — user or org (disambiguated at fetch time)

**Not supported as seeds:**
- Legacy arxiv IDs (`papers/cond-mat/0303517` form) — HuggingFace only
  indexes modern arxiv IDs (`YYMM.NNNNN`).
- Static HF pages (`/blog`, `/learn`, `/pricing`, `/docs`, `/tasks`,
  `/enterprise`, `/inference-endpoints`).
- UI filter URLs (`/models?author=...`).

## Recipes

Each recipe lists the **command**, the **output** it produces, and **what to
look for**. All are anonymous — add `CRAWLER_TOKEN__HUGGINGFACE_CO` for higher
rate limits. Output is trimmed for clarity; counts are from live runs.

### Crawl a paper and its linked models, datasets, and spaces (Paper seed)

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/papers/2307.09288 \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: huggingface.co
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.4s, 18 nodes in queue (18 users)
  - Round 1 completed: 18 nodes processed in 0.6s, 4019 nodes in queue
  - Total nodes: 19
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **17 visited nodes**: 1 `HuggingFacePaper`, 4
`HuggingFaceRepo` (model), 4 `HuggingFaceRepo` (dataset), and 8
`HuggingFaceRepo` (space). Two additional nodes (`arxiv.org` and `github.com`
URLs) were discovered in round 1 but skipped — no adapter is registered for
those platforms in this invocation.

**What to look for:** the paper is keyed by
`https://huggingface.co/papers/2307.09288`; round 1 logs a warning
`Discovered github.com URI … but no github.com adapter is registered —
skipping` — that edge is the cross-platform bridge the paper adapter emits.
Pass `--platforms huggingface.co,github.com` (with a GitHub token) to follow
it into the source repository.

### Crawl a model and its owning organization (Model seed)

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/meta-llama/Llama-3.2-1B \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: huggingface.co
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.2s, 1 node in queue
  - Round 1 completed: 1 nodes processed in 0.6s, 80 nodes in queue
  - Total nodes: 2
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **2 nodes**: 1 `HuggingFaceRepo` (model) and 1
`HuggingFaceOrg` (`meta-llama`). 80 repos owned by the org are queued for
round 3 — add `--rounds 3` to pull them in.

**What to look for:** the `owned_by` edge connects the model to
`https://huggingface.co/meta-llama`; the org expansion (round 1) fetches
`/api/models?author=meta-llama`, `/api/datasets?…`, and `/api/spaces?…` and
places all discovered repos in the queue.

### Crawl a user's corpus and org memberships (User seed)

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/karpathy \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: huggingface.co
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.5s, 15 nodes in queue
  - Round 1 completed: 15 nodes processed in 1.0s, 1 node in queue
  - Total nodes: 16
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **16 nodes**: 1 `HuggingFaceUser` (karpathy), 2
`HuggingFaceOrg` (compvis-community, llmc), and 13 `HuggingFaceRepo`
(7 models + 6 datasets).

**What to look for:** `member_of` edges link the user to each org; each org
node is fully expanded in round 1 (its own repos are queued). Spaces owned by
karpathy (none at time of crawl) would appear as `HuggingFaceRepo` (space).

### Crawl a collection and its items (Collection seed)

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/collections/meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586 \
    --output-dir ./out --no-cache --no-csv
```

```text
✓ Registered adapters for: huggingface.co
✓ Added 1 seed nodes
  - Round 0 completed: 1 nodes processed in 0.3s, 15 nodes in queue
  - Round 1 completed: 15 nodes processed in 0.7s, 67 nodes in queue
  - Total nodes: 16
Exporting results... → ./out/<timestamp>.graph.json
```

The exported graph holds **16 nodes**: 1 `HuggingFaceCollection`, 1
`HuggingFaceOrg` (meta-llama), and 14 `HuggingFaceRepo` (10 models + 4
datasets).

**What to look for:** `contains` edges connect the collection to each of its
items; `owned_by` connects the collection to `huggingface.co/meta-llama`. The
org expansion in round 1 enumerates the full org catalogue (67 more repos
queued) — use `--rounds 2` to stay collection-scoped.

## Limitations
- **No `has_member` (Org → User) edges.** The
  `/api/organizations/<name>/members` endpoint is auth-gated. Same
  situation as Infoscience's removed `has_member` flow.
- **Paper authors are bare names without ORCID.** No `authored_by`
  edges to ORCID URLs (unlike DataCite). Cross-platform identity
  resolution stays downstream.
- **Model/dataset card README parsing not implemented.**
  `cardData.tags` sometimes contains `arxiv:<id>` references but card
  formats vary. Defer to v3.4.1 if useful.
- **Discussion / community / dataset-viewer / model-leaderboard data
  not crawled.** Read-only metadata only.
- **Crossref-issued DOIs in cards not resolved** — stays with the
  future Crossref adapter.
- **Legacy arxiv IDs not accepted** as paper seeds.
- **No deposit/upload/draft flows.** Read-only adapter.
