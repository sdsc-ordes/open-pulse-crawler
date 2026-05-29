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

## Manual-test recipes

### Cross-platform paper bridge

```bash
opc crawl --platforms huggingface.co,github.com --rounds 2 \
    https://huggingface.co/papers/2307.09288
```

Round 0 fetches the Llama 2 paper. Round 1 emits:
- `related_to.IsIdenticalTo` → `https://arxiv.org/abs/2307.09288`
- `related_to.IsSupplementedBy` → `https://github.com/facebookresearch/llama`
- `references_model` → 8 Llama 2 models on HF
- `references_dataset` / `references_space` → linked items

Round 2 follows the GitHub edge into the source repository.

### Single model with owner fan-out

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/meta-llama/Llama-3.2-1B
```

Round 1 emits `owned_by` → `huggingface.co/meta-llama`. Round 2 fetches
the meta-llama org and walks every model / dataset / space it owns.

### Author corpus

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/karpathy
```

Walks every model / dataset / space by Andrej Karpathy, plus `member_of`
edges to each of his orgs.

### Collection walk

```bash
opc crawl --platforms huggingface.co --rounds 2 \
    https://huggingface.co/collections/meta-llama/metas-llama-32-language-models-and-evals-675bfd70e574a62dd0e40586
```

Walks every model / dataset / space / paper in the collection's `items[]`.

## Limitations (v3.4)

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
