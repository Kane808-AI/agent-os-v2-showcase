"""Governed, version-controlled knowledge catalog contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


KNOWLEDGE_ROOT = Path(__file__).resolve().parents[2] / "knowledge"
CATALOG_PATH = KNOWLEDGE_ROOT / "catalog.json"
ALLOWED_KINDS = {"fact", "procedure", "strategy", "reference", "historical"}
ALLOWED_STATUSES = {
    "candidate",
    "evaluated",
    "verified",
    "approved-procedure",
    "rejected",
    "stale",
    "superseded",
    "archive-only",
}
ALLOWED_SCOPE_TYPES = {"platform", "reference-pack", "tenant"}
ALLOWED_FRESHNESS = {"stable", "periodic", "volatile"}
ALLOWED_PURPOSES = {"research", "fact", "procedure", "audit"}
TERMINAL_OR_HIDDEN = {"rejected", "superseded", "archive-only"}


class KnowledgeError(ValueError):
    """A knowledge record or catalog violates governance."""


def _identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]+", value):
        raise KnowledgeError(f"{name} must be a kebab-case identifier")
    return value


def _strings(name: str, value: Any, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise KnowledgeError(f"{name} must be a string list")
    if not allow_empty and not value:
        raise KnowledgeError(f"{name} cannot be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise KnowledgeError(f"{name} contains an invalid string")
    if len(value) != len(set(value)):
        raise KnowledgeError(f"{name} cannot contain duplicates")
    return tuple(value)


def _time(name: str, value: Any) -> datetime:
    if not isinstance(value, str):
        raise KnowledgeError(f"{name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeError(f"{name} must include a timezone")
    return parsed


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    source_system: str
    source_path: str
    sha256: str
    source_modified_at: datetime
    migration_use: str


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    scope_type: str
    tenant_key: str | None
    business_key: str | None

    def key(self) -> tuple[str, str | None, str | None]:
        return (self.scope_type, self.tenant_key, self.business_key)


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    knowledge_id: str
    version: str
    title: str
    kind: str
    status: str
    scope: KnowledgeScope
    topics: tuple[str, ...]
    content_path: str
    sources: tuple[KnowledgeSource, ...]
    migrated_at: datetime
    reviewed_by: str | None
    confidence: Decimal
    freshness_class: str
    observed_at: datetime
    review_by: datetime
    retrieval: Mapping[str, bool]
    claim_key: str | None
    claim_value: str | None
    conflicts_with: tuple[str, ...]
    supersedes: tuple[str, ...]
    tags: tuple[str, ...]
    content: str

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        content: str,
    ) -> "KnowledgeRecord":
        expected_fields = {
            "knowledge_id",
            "version",
            "title",
            "kind",
            "status",
            "scope",
            "topics",
            "content_path",
            "sources",
            "migrated_at",
            "reviewed_by",
            "confidence",
            "freshness",
            "retrieval",
            "claim_key",
            "claim_value",
            "conflicts_with",
            "supersedes",
            "tags",
        }
        if set(raw) != expected_fields:
            raise KnowledgeError("knowledge record fields do not match the contract")
        knowledge_id = _identifier("knowledge_id", raw.get("knowledge_id"))
        version = raw.get("version")
        if not isinstance(version, str) or not re.fullmatch(
            r"\d+\.\d+\.\d+", version
        ):
            raise KnowledgeError("version must use x.y.z")
        title = raw.get("title")
        if not isinstance(title, str) or len(title.strip()) < 5:
            raise KnowledgeError("title is required")
        kind = raw.get("kind")
        status = raw.get("status")
        if kind not in ALLOWED_KINDS:
            raise KnowledgeError("unsupported knowledge kind")
        if status not in ALLOWED_STATUSES:
            raise KnowledgeError("unsupported knowledge status")

        scope_raw = raw.get("scope")
        if not isinstance(scope_raw, dict) or set(scope_raw) != {
            "scope_type",
            "tenant_key",
            "business_key",
        }:
            raise KnowledgeError("invalid knowledge scope")
        if scope_raw["scope_type"] not in ALLOWED_SCOPE_TYPES:
            raise KnowledgeError("unsupported scope type")
        for key in ("tenant_key", "business_key"):
            if scope_raw[key] is not None:
                _identifier(key, scope_raw[key])
        if scope_raw["scope_type"] == "platform" and (
            scope_raw["tenant_key"] is not None
            or scope_raw["business_key"] is not None
        ):
            raise KnowledgeError("platform knowledge cannot name a tenant or business")
        if scope_raw["scope_type"] != "platform" and scope_raw["tenant_key"] is None:
            raise KnowledgeError("non-platform knowledge requires tenant_key")
        scope = KnowledgeScope(**scope_raw)

        sources_raw = raw.get("sources")
        if not isinstance(sources_raw, list) or not sources_raw:
            raise KnowledgeError("sources cannot be empty")
        sources = []
        for source in sources_raw:
            if not isinstance(source, dict) or set(source) != {
                "source_system",
                "source_path",
                "sha256",
                "source_modified_at",
                "migration_use",
            }:
                raise KnowledgeError("invalid source metadata")
            if source.get("source_system") not in {
                "openclaw-legacy",
                "agent-os-v1",
                "human",
                "external",
            }:
                raise KnowledgeError("unsupported source system")
            path = source.get("source_path")
            digest = source.get("sha256")
            use = source.get("migration_use")
            if not isinstance(path, str) or not path.strip():
                raise KnowledgeError("source_path is required")
            if not isinstance(digest, str) or not re.fullmatch(
                r"[a-f0-9]{64}", digest
            ):
                raise KnowledgeError("source sha256 is invalid")
            if use not in {"synthesized", "evidence-only", "review-only"}:
                raise KnowledgeError("invalid migration_use")
            sources.append(
                KnowledgeSource(
                    source_system=source["source_system"],
                    source_path=path,
                    sha256=digest,
                    source_modified_at=_time(
                        "source_modified_at", source.get("source_modified_at")
                    ),
                    migration_use=use,
                )
            )

        confidence = Decimal(str(raw.get("confidence")))
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise KnowledgeError("confidence must be between 0 and 1")
        freshness = raw.get("freshness")
        if not isinstance(freshness, dict) or set(freshness) != {
            "class",
            "observed_at",
            "review_by",
        }:
            raise KnowledgeError("invalid freshness metadata")
        if freshness["class"] not in ALLOWED_FRESHNESS:
            raise KnowledgeError("unsupported freshness class")
        observed_at = _time("freshness.observed_at", freshness["observed_at"])
        review_by = _time("freshness.review_by", freshness["review_by"])
        if review_by <= observed_at:
            raise KnowledgeError("review_by must be after observed_at")

        retrieval = raw.get("retrieval")
        if (
            not isinstance(retrieval, dict)
            or set(retrieval) != {"research", "fact", "procedure"}
            or any(not isinstance(value, bool) for value in retrieval.values())
        ):
            raise KnowledgeError("invalid retrieval policy")
        reviewed_by = raw.get("reviewed_by")
        if reviewed_by is not None and (
            not isinstance(reviewed_by, str) or not reviewed_by.strip()
        ):
            raise KnowledgeError("reviewed_by must be null or a non-empty string")
        if status in {"verified", "approved-procedure"} and not reviewed_by:
            raise KnowledgeError("promoted knowledge requires a reviewer")
        if status == "verified" and not retrieval["fact"]:
            raise KnowledgeError("verified records must permit fact retrieval")
        if status == "approved-procedure" and not retrieval["procedure"]:
            raise KnowledgeError(
                "approved procedures must permit procedure retrieval"
            )
        if status == "verified" and kind != "fact":
            raise KnowledgeError("verified operational facts must use fact kind")
        if status == "approved-procedure" and kind != "procedure":
            raise KnowledgeError(
                "approved-procedure status requires procedure kind"
            )
        if retrieval["fact"] and (
            kind != "fact" or status != "verified"
        ):
            raise KnowledgeError(
                "fact retrieval requires verified fact records"
            )
        if retrieval["procedure"] and (
            kind != "procedure" or status != "approved-procedure"
        ):
            raise KnowledgeError(
                "procedure retrieval requires approved procedure records"
            )
        if status not in {"verified", "approved-procedure"} and (
            retrieval["fact"] or retrieval["procedure"]
        ):
            raise KnowledgeError(
                "unpromoted knowledge cannot permit fact or procedure retrieval"
            )

        claim_key = raw.get("claim_key")
        claim_value = raw.get("claim_value")
        if (claim_key is None) != (claim_value is None):
            raise KnowledgeError("claim_key and claim_value must appear together")
        if claim_key is not None:
            _identifier("claim_key", claim_key)
            if not isinstance(claim_value, str) or not claim_value.strip():
                raise KnowledgeError("claim_value is invalid")

        if not content.strip().startswith("# "):
            raise KnowledgeError("knowledge content must start with a heading")
        if len(content.strip()) < 120:
            raise KnowledgeError("knowledge content is incomplete")
        content_path = raw.get("content_path")
        if not isinstance(content_path, str) or not content_path.endswith(".md"):
            raise KnowledgeError("content_path must name a Markdown file")

        return cls(
            knowledge_id=knowledge_id,
            version=version,
            title=title.strip(),
            kind=kind,
            status=status,
            scope=scope,
            topics=_strings("topics", raw.get("topics")),
            content_path=content_path,
            sources=tuple(sources),
            migrated_at=_time("migrated_at", raw.get("migrated_at")),
            reviewed_by=reviewed_by,
            confidence=confidence,
            freshness_class=freshness["class"],
            observed_at=observed_at,
            review_by=review_by,
            retrieval=dict(retrieval),
            claim_key=claim_key,
            claim_value=claim_value,
            conflicts_with=_strings(
                "conflicts_with", raw.get("conflicts_with"), allow_empty=True
            ),
            supersedes=_strings(
                "supersedes", raw.get("supersedes"), allow_empty=True
            ),
            tags=_strings("tags", raw.get("tags")),
            content=content.strip(),
        )

    def is_stale(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise KnowledgeError("now must include a timezone")
        return self.status == "stale" or current >= self.review_by


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    record: KnowledgeRecord
    stale: bool
    conflicted: bool


class KnowledgeCatalog:
    def __init__(self, records: Sequence[KnowledgeRecord]) -> None:
        self.records = tuple(records)
        identifiers = [record.knowledge_id for record in self.records]
        if len(identifiers) != len(set(identifiers)):
            raise KnowledgeError("knowledge IDs must be unique")
        known = set(identifiers)
        for record in self.records:
            if record.knowledge_id in record.conflicts_with:
                raise KnowledgeError("a knowledge record cannot conflict with itself")
            if record.knowledge_id in record.supersedes:
                raise KnowledgeError("a knowledge record cannot supersede itself")
            unresolved = (
                set(record.conflicts_with) | set(record.supersedes)
            ) - known
            if unresolved:
                raise KnowledgeError(
                    f"{record.knowledge_id} references unknown records: "
                    f"{sorted(unresolved)}"
                )
        by_id = {record.knowledge_id: record for record in self.records}
        for record in self.records:
            for superseded_id in record.supersedes:
                if by_id[superseded_id].status != "superseded":
                    raise KnowledgeError(
                        "superseded records must have superseded lifecycle status"
                    )

    def conflict_ids(self) -> set[str]:
        conflict_ids: set[str] = set()
        by_id = {record.knowledge_id: record for record in self.records}
        for record in self.records:
            for other_id in record.conflicts_with:
                conflict_ids.update({record.knowledge_id, other_id})
                other = by_id[other_id]
                if record.knowledge_id not in other.conflicts_with:
                    raise KnowledgeError(
                        "explicit conflict relationships must be symmetric"
                    )
        claims: dict[
            tuple[tuple[str, str | None, str | None], str],
            list[KnowledgeRecord],
        ] = {}
        for record in self.records:
            if record.claim_key and record.status not in TERMINAL_OR_HIDDEN:
                claims.setdefault(
                    (record.scope.key(), record.claim_key), []
                ).append(record)
        for records in claims.values():
            if len({record.claim_value for record in records}) > 1:
                conflict_ids.update(record.knowledge_id for record in records)
        return conflict_ids

    def query(
        self,
        text: str,
        *,
        purpose: str,
        tenant_key: str | None = None,
        business_key: str | None = None,
        now: datetime | None = None,
        minimum_confidence: Decimal = Decimal("0"),
    ) -> list[KnowledgeHit]:
        if purpose not in ALLOWED_PURPOSES:
            raise KnowledgeError("unsupported retrieval purpose")
        terms = {
            term
            for term in re.findall(r"[a-z0-9-]+", text.lower())
            if len(term) > 1
        }
        conflicts = self.conflict_ids()
        hits: list[tuple[int, KnowledgeHit]] = []
        for record in self.records:
            if record.scope.scope_type != "platform":
                if tenant_key is None or record.scope.tenant_key != tenant_key:
                    continue
                if (
                    record.scope.business_key is not None
                    and record.scope.business_key != business_key
                ):
                    continue
            stale = record.is_stale(now=now)
            conflicted = record.knowledge_id in conflicts
            if purpose != "audit":
                if record.status in TERMINAL_OR_HIDDEN:
                    continue
                if not record.retrieval[purpose]:
                    continue
                if record.confidence < minimum_confidence:
                    continue
                if purpose in {"fact", "procedure"} and (stale or conflicted):
                    continue
            haystack = " ".join(
                (
                    record.title,
                    " ".join(record.topics),
                    " ".join(record.tags),
                    record.content,
                )
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if terms and score == 0:
                continue
            hits.append(
                (
                    score,
                    KnowledgeHit(
                        record=record,
                        stale=stale,
                        conflicted=conflicted,
                    ),
                )
            )
        return [
            hit
            for _, hit in sorted(
                hits,
                key=lambda item: (
                    -item[0],
                    -item[1].record.confidence,
                    item[1].record.knowledge_id,
                ),
            )
        ]


def load_catalog(path: str | Path = CATALOG_PATH) -> KnowledgeCatalog:
    catalog_path = Path(path)
    raw = json.loads(catalog_path.read_text())
    if raw.get("schema_version") != 1 or not isinstance(raw.get("records"), list):
        raise KnowledgeError("invalid knowledge catalog")
    root = catalog_path.parent.resolve()
    records = []
    for item in raw["records"]:
        content_path = item.get("content_path")
        if not isinstance(content_path, str):
            raise KnowledgeError("content_path is required")
        content_file = (root / content_path).resolve()
        if root not in content_file.parents:
            raise KnowledgeError("content_path escapes the knowledge root")
        if not content_file.is_file():
            raise KnowledgeError(f"missing knowledge content: {content_path}")
        records.append(
            KnowledgeRecord.from_mapping(
                item,
                content=content_file.read_text(),
            )
        )
    return KnowledgeCatalog(records)


def validate_source_inventory(
    path: str | Path,
    *,
    catalog: KnowledgeCatalog,
) -> tuple[dict[str, Any], ...]:
    raw = json.loads(Path(path).read_text())
    if raw.get("schema_version") != 1 or not isinstance(raw.get("sources"), list):
        raise KnowledgeError("invalid source inventory")
    sources = tuple(raw["sources"])
    if any(not isinstance(source, dict) for source in sources):
        raise KnowledgeError("source inventory entries must be objects")
    paths = [source.get("source_path") for source in sources]
    if any(not isinstance(path, str) or not path.strip() for path in paths):
        raise KnowledgeError("source inventory path is invalid")
    if len(paths) != len(set(paths)):
        raise KnowledgeError("source inventory paths must be unique")
    allowed_decisions = {
        "synthesize",
        "evidence-only",
        "reference-only-needs-refresh",
        "superseded",
        "hold-for-live-verification",
        "exclude-sensitive",
        "exclude-wrapper-specific",
    }
    inventory_by_path = {}
    for source in sources:
        if source.get("decision") not in allowed_decisions:
            raise KnowledgeError("unsupported inventory decision")
        digest = source.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise KnowledgeError("inventory source digest is invalid")
        if not source.get("reason"):
            raise KnowledgeError("inventory source decision needs a reason")
        inventory_by_path[source["source_path"]] = source
    for record in catalog.records:
        for source in record.sources:
            inventory = inventory_by_path.get(source.source_path)
            if inventory is None:
                raise KnowledgeError(
                    f"catalog source missing from inventory: {source.source_path}"
                )
            if inventory["sha256"] != source.sha256:
                raise KnowledgeError("catalog and inventory source hashes differ")
            if inventory["decision"] not in {"synthesize", "evidence-only"}:
                raise KnowledgeError(
                    "catalog cannot migrate a source excluded by the inventory"
                )
    return sources
