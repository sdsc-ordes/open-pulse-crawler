"""Parsing helpers for gimie JSON-LD payloads.

The gimie endpoint output is not a classic JSON-LD graph, but it uses an
`output: [...]` list containing schema.org-typed entities with `@id`, `@type`
and properties like `http://schema.org/name`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple


GITHUB_LOGIN_RE = re.compile(r"^https?://github\.com/([^/?#]+)/?$")
GITHUB_REPO_URL_RE = re.compile(r"^https?://github\.com/([^/?#]+)/([^/?#]+)/?$")


def _extract_github_login(github_id: str) -> Optional[str]:
    if not github_id:
        return None
    m = GITHUB_LOGIN_RE.match(github_id.strip())
    if not m:
        return None
    return m.group(1)


def _extract_github_repo_full_name_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = GITHUB_REPO_URL_RE.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    return f"{owner}/{repo}"


def _extract_first_value(x: Any) -> Optional[str]:
    """
    Extract a string from a gimie schema.org property list.

    Common shapes in the fixture:
      - "http://schema.org/name": [{ "@value": "..." }]
      - "http://schema.org/author": [{ "@id": "https://github.com/<login>" }, ...]
    """
    if isinstance(x, list) and x:
        item = x[0]
        if isinstance(item, dict):
            if "@value" in item:
                return item.get("@value")
            if "@id" in item:
                return item.get("@id")
        if isinstance(item, str):
            return item
    return None


def _extract_all_ids(x: Any) -> List[str]:
    if not isinstance(x, list):
        return []
    out: List[str] = []
    for item in x:
        if isinstance(item, dict) and "@id" in item and isinstance(item["@id"], str):
            out.append(item["@id"])
        elif isinstance(item, str):
            out.append(item)
    return out


def _types_to_login_kinds(types: List[str]) -> Optional[str]:
    type_set = set(types)
    if "http://schema.org/Person" in type_set:
        return "user"
    if "http://schema.org/Organization" in type_set:
        return "org"
    return None


@dataclass(frozen=True)
class ParsedRepoFromGimie:
    repo_full_name: str
    owner_login: str
    contributor_logins: List[str]
    # Mapping: github_login -> "user" | "org"
    login_type_map: Dict[str, str]


def parse_gimie_repo_jsonld(payload: Dict[str, Any]) -> ParsedRepoFromGimie:
    """
    Parse gimie JSON-LD payload and extract:
      - repo full name (owner/repo)
      - repo owner login
      - contributor github logins
      - github login type mapping (user/org) based on schema.org Person/Organization
    """
    output = payload.get("output")
    if not isinstance(output, list):
        raise ValueError("Invalid gimie payload: missing output list")

    # Build login -> user|org mapping.
    login_type_map: Dict[str, str] = {}
    for entity in output:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("@id")
        if not isinstance(entity_id, str):
            continue
        login = _extract_github_login(entity_id)
        if not login:
            continue
        types = entity.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list):
            continue
        kind = _types_to_login_kinds([t for t in types if isinstance(t, str)])
        if kind:
            login_type_map[login] = kind

    # Find repository SoftwareSourceCode entity.
    repo_entity: Optional[Dict[str, Any]] = None
    for entity in output:
        if not isinstance(entity, dict):
            continue
        types = entity.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list):
            continue
        type_set = {t for t in types if isinstance(t, str)}
        if "http://schema.org/SoftwareSourceCode" in type_set:
            repo_entity = entity
            break

    if not repo_entity:
        raise ValueError("Could not find SoftwareSourceCode entity in gimie payload")

    # Derive repo_full_name.
    repo_full_name: Optional[str] = None
    # Prefer codeRepository @id when it is a github repo URL.
    code_repo_ids = _extract_all_ids(repo_entity.get("http://schema.org/codeRepository"))
    for rid in code_repo_ids:
        repo_full_name = _extract_github_repo_full_name_from_url(rid)
        if repo_full_name:
            break

    if not repo_full_name:
        # Fallback to name/@value which in the fixture is `owner/repo`.
        name_val = _extract_first_value(repo_entity.get("http://schema.org/name"))
        if isinstance(name_val, str) and "/" in name_val:
            # Accept first two path segments to be tolerant to accidental prefixes.
            parts = [p for p in name_val.split("/") if p]
            if len(parts) >= 2:
                repo_full_name = f"{parts[0]}/{parts[1]}"

    if not repo_full_name:
        # Last fallback: entity @id may contain repo URL.
        entity_id = repo_entity.get("@id")
        if isinstance(entity_id, str):
            repo_full_name = _extract_github_repo_full_name_from_url(entity_id)

    if not repo_full_name:
        raise ValueError("Could not extract repo_full_name from gimie payload")

    owner_login = repo_full_name.split("/", 1)[0]

    # Extract github contributors from schema.org/contributor.
    contributor_ids = _extract_all_ids(repo_entity.get("http://schema.org/contributor"))
    contributor_logins: Set[str] = set()
    for cid in contributor_ids:
        login = _extract_github_login(cid)
        if login:
            contributor_logins.add(login)

    return ParsedRepoFromGimie(
        repo_full_name=repo_full_name,
        owner_login=owner_login,
        contributor_logins=sorted(contributor_logins),
        login_type_map=login_type_map,
    )

