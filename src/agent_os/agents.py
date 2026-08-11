"""Versioned agent-constitution loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


AGENT_ROOT = Path(__file__).resolve().parents[2] / "agents"
CONSTITUTION_FILENAME = "CONSTITUTION.json"
SOUL_FILENAME = "SOUL.md"
ALLOWED_LAYERS = {
    "portfolio",
    "business",
    "department",
    "specialist",
    "assurance",
    "platform",
}
ALLOWED_REASONING_TIERS = {"utility", "standard", "advanced"}
ALLOWED_CONTEXT_CLASSES = {"small", "medium", "large"}
ALLOWED_MODALITIES = {"text", "code", "vision", "image", "audio", "video"}
ALLOWED_DATA_CLASSES = {
    "public",
    "internal",
    "confidential",
    "restricted-financial",
}
REQUIRED_LIST_FIELDS = (
    "accountabilities",
    "capabilities",
    "inputs",
    "outputs",
    "decision_rights",
    "prohibited_actions",
    "startup_checks",
    "operating_loop",
    "escalation_triggers",
    "success_metrics",
    "evaluation_scenarios",
)
PROVIDER_OR_MODEL_KEYS = {"provider", "provider_id", "model", "model_id"}


class ConstitutionError(ValueError):
    """A role constitution violates the product contract."""


def _require_identifier(name: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]+", value):
        raise ConstitutionError(f"{name} must be a kebab-case identifier")
    return value


def _require_nonempty_strings(name: str, value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ConstitutionError(f"{name} must be a non-empty string list")
    if len(value) != len(set(value)):
        raise ConstitutionError(f"{name} cannot contain duplicates")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class AgentConstitution:
    constitution_id: str
    version: str
    role_name: str
    layer: str
    reports_to: str | None
    mission: str
    accountabilities: tuple[str, ...]
    capabilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    autonomy: Mapping[str, Any]
    tool_policy: Mapping[str, Any]
    decision_rights: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    startup_checks: tuple[str, ...]
    operating_loop: tuple[str, ...]
    handoff_targets: tuple[str, ...]
    escalation_triggers: tuple[str, ...]
    success_metrics: tuple[str, ...]
    model_requirements: Mapping[str, Any]
    memory_policy: Mapping[str, Any]
    evaluation_scenarios: tuple[str, ...]
    soul: str

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        soul: str,
    ) -> "AgentConstitution":
        if raw.get("schema_version") != 1:
            raise ConstitutionError("schema_version must be 1")
        constitution_id = _require_identifier(
            "constitution_id", raw.get("constitution_id")
        )
        version = raw.get("version")
        if not isinstance(version, str) or not re.fullmatch(
            r"\d+\.\d+\.\d+", version
        ):
            raise ConstitutionError("version must use semantic x.y.z format")
        role_name = raw.get("role_name")
        mission = raw.get("mission")
        if not isinstance(role_name, str) or len(role_name.strip()) < 3:
            raise ConstitutionError("role_name is required")
        if not isinstance(mission, str) or len(mission.strip()) < 20:
            raise ConstitutionError("mission must be specific")
        layer = raw.get("layer")
        if layer not in ALLOWED_LAYERS:
            raise ConstitutionError(f"unsupported layer: {layer}")
        reports_to = raw.get("reports_to")
        if reports_to is not None:
            reports_to = _require_identifier("reports_to", reports_to)

        lists = {
            name: _require_nonempty_strings(name, raw.get(name))
            for name in REQUIRED_LIST_FIELDS
        }
        handoff_targets_raw = raw.get("handoff_targets")
        if not isinstance(handoff_targets_raw, list):
            raise ConstitutionError("handoff_targets must be a list")
        handoff_targets = tuple(
            _require_identifier("handoff target", target)
            for target in handoff_targets_raw
        )
        if len(handoff_targets) != len(set(handoff_targets)):
            raise ConstitutionError("handoff_targets cannot contain duplicates")

        autonomy = raw.get("autonomy")
        if not isinstance(autonomy, dict):
            raise ConstitutionError("autonomy is required")
        expected_autonomy = {
            "work_discovery",
            "may_delegate",
            "may_execute",
            "may_verify_own_work",
            "external_action_default",
        }
        if set(autonomy) != expected_autonomy:
            raise ConstitutionError("autonomy fields do not match the contract")
        if any(
            not isinstance(autonomy[key], bool)
            for key in (
                "work_discovery",
                "may_delegate",
                "may_execute",
                "may_verify_own_work",
            )
        ):
            raise ConstitutionError("autonomy flags must be boolean")
        if autonomy["may_verify_own_work"]:
            raise ConstitutionError("an agent cannot independently verify its own work")
        if autonomy["external_action_default"] != "policy-engine-required":
            raise ConstitutionError("external actions must use the policy engine")

        tool_policy = raw.get("tool_policy")
        if not isinstance(tool_policy, dict) or set(tool_policy) != {
            "requestable",
            "forbidden",
            "runtime_allowlist_required",
        }:
            raise ConstitutionError("tool_policy fields do not match the contract")
        requestable = _require_nonempty_strings(
            "tool_policy.requestable", tool_policy["requestable"]
        )
        forbidden = _require_nonempty_strings(
            "tool_policy.forbidden", tool_policy["forbidden"]
        )
        if set(requestable) & set(forbidden):
            raise ConstitutionError("a tool class cannot be requestable and forbidden")
        if tool_policy["runtime_allowlist_required"] is not True:
            raise ConstitutionError("runtime tool allowlisting is mandatory")

        model = raw.get("model_requirements")
        if not isinstance(model, dict):
            raise ConstitutionError("model_requirements is required")
        if PROVIDER_OR_MODEL_KEYS & set(model):
            raise ConstitutionError("constitutions cannot name a provider or model")
        expected_model = {
            "reasoning_tier",
            "tool_use",
            "structured_output",
            "modalities",
            "context_class",
            "data_classes",
            "independent_evaluator",
        }
        if set(model) != expected_model:
            raise ConstitutionError(
                "model_requirements fields do not match the contract"
            )
        if model["reasoning_tier"] not in ALLOWED_REASONING_TIERS:
            raise ConstitutionError("unsupported reasoning tier")
        if model["context_class"] not in ALLOWED_CONTEXT_CLASSES:
            raise ConstitutionError("unsupported context class")
        modalities = set(
            _require_nonempty_strings("model_requirements.modalities", model["modalities"])
        )
        if not modalities <= ALLOWED_MODALITIES:
            raise ConstitutionError("unsupported modality")
        data_classes = set(
            _require_nonempty_strings(
                "model_requirements.data_classes", model["data_classes"]
            )
        )
        if not data_classes <= ALLOWED_DATA_CLASSES:
            raise ConstitutionError("unsupported data class")
        for key in ("tool_use", "structured_output", "independent_evaluator"):
            if not isinstance(model[key], bool):
                raise ConstitutionError(f"model_requirements.{key} must be boolean")

        memory = raw.get("memory_policy")
        if not isinstance(memory, dict) or set(memory) != {
            "read_scopes",
            "may_propose_candidates",
            "may_promote",
        }:
            raise ConstitutionError("memory_policy fields do not match the contract")
        _require_nonempty_strings("memory_policy.read_scopes", memory["read_scopes"])
        if not isinstance(memory["may_propose_candidates"], bool):
            raise ConstitutionError("candidate-memory flag must be boolean")
        if memory["may_promote"] is not False:
            raise ConstitutionError("agents cannot promote their own memory")

        if not soul.strip().startswith("# "):
            raise ConstitutionError("SOUL.md must start with a role heading")
        if len(soul.strip()) < 160:
            raise ConstitutionError("SOUL.md is too generic or incomplete")

        scenarios = lists["evaluation_scenarios"]
        suffixes = {scenario.rsplit(".", 1)[-1] for scenario in scenarios}
        if not {"happy", "boundary"} <= suffixes:
            raise ConstitutionError(
                "each constitution needs happy and boundary evaluations"
            )

        return cls(
            constitution_id=constitution_id,
            version=version,
            role_name=role_name.strip(),
            layer=layer,
            reports_to=reports_to,
            mission=mission.strip(),
            accountabilities=lists["accountabilities"],
            capabilities=lists["capabilities"],
            inputs=lists["inputs"],
            outputs=lists["outputs"],
            autonomy=dict(autonomy),
            tool_policy={
                "requestable": requestable,
                "forbidden": forbidden,
                "runtime_allowlist_required": True,
            },
            decision_rights=lists["decision_rights"],
            prohibited_actions=lists["prohibited_actions"],
            startup_checks=lists["startup_checks"],
            operating_loop=lists["operating_loop"],
            handoff_targets=handoff_targets,
            escalation_triggers=lists["escalation_triggers"],
            success_metrics=lists["success_metrics"],
            model_requirements=dict(model),
            memory_policy=dict(memory),
            evaluation_scenarios=scenarios,
            soul=soul.strip(),
        )


@dataclass(frozen=True, slots=True)
class AgentAssignment:
    actor_id: str
    display_name: str
    constitution_id: str
    business_keys: tuple[str, ...]


def load_constitution(
    constitution_id: str,
    *,
    agent_root: Path = AGENT_ROOT,
) -> AgentConstitution:
    safe_id = _require_identifier("constitution_id", constitution_id)
    role_root = agent_root / "roles" / safe_id
    raw = json.loads((role_root / CONSTITUTION_FILENAME).read_text())
    soul = (role_root / SOUL_FILENAME).read_text()
    constitution = AgentConstitution.from_mapping(raw, soul=soul)
    if constitution.constitution_id != safe_id:
        raise ConstitutionError("folder and constitution IDs do not match")
    return constitution


def load_registry(*, agent_root: Path = AGENT_ROOT) -> tuple[dict[str, Any], ...]:
    raw = json.loads((agent_root / "registry.json").read_text())
    if raw.get("schema_version") != 1 or not isinstance(raw.get("roles"), list):
        raise ConstitutionError("invalid agent registry")
    roles = tuple(raw["roles"])
    identifiers = [role.get("constitution_id") for role in roles]
    if len(identifiers) != len(set(identifiers)):
        raise ConstitutionError("agent registry contains duplicate roles")
    return roles


def load_all_constitutions(
    *,
    agent_root: Path = AGENT_ROOT,
) -> dict[str, AgentConstitution]:
    constitutions: dict[str, AgentConstitution] = {}
    registry = load_registry(agent_root=agent_root)
    for entry in registry:
        constitution_id = _require_identifier(
            "registry constitution_id", entry.get("constitution_id")
        )
        if entry.get("activation") not in {"core", "department", "specialist"}:
            raise ConstitutionError("invalid activation class")
        constitution = load_constitution(
            constitution_id,
            agent_root=agent_root,
        )
        constitutions[constitution_id] = constitution
    known = set(constitutions)
    for constitution in constitutions.values():
        if constitution.reports_to and constitution.reports_to not in known:
            raise ConstitutionError(
                f"{constitution.constitution_id} reports to an unknown role"
            )
        unknown_targets = set(constitution.handoff_targets) - known
        if unknown_targets:
            raise ConstitutionError(
                f"{constitution.constitution_id} has unknown handoff targets: "
                f"{sorted(unknown_targets)}"
            )
    return constitutions


def load_assignment_manifest(
    path: str | Path,
    *,
    constitutions: Mapping[str, AgentConstitution] | None = None,
) -> tuple[AgentAssignment, ...]:
    """Validate an inert tenant assignment manifest."""
    raw = json.loads(Path(path).read_text())
    if raw.get("schema_version") != 1:
        raise ConstitutionError("assignment schema_version must be 1")
    if raw.get("status") != "defined-not-activated":
        raise ConstitutionError("assignment manifests cannot activate agents")
    businesses_raw = raw.get("businesses")
    assignments_raw = raw.get("assignments")
    if not isinstance(businesses_raw, list) or not businesses_raw:
        raise ConstitutionError("assignment manifest requires businesses")
    if not isinstance(assignments_raw, list) or not assignments_raw:
        raise ConstitutionError("assignment manifest requires assignments")
    business_keys = {
        _require_identifier("business_key", business.get("business_key"))
        for business in businesses_raw
    }
    if len(business_keys) != len(businesses_raw):
        raise ConstitutionError("business keys must be unique")

    known = constitutions or load_all_constitutions()
    assignments: list[AgentAssignment] = []
    seen_actors: set[str] = set()
    owner_scopes: list[str] = []
    for item in assignments_raw:
        actor_id = _require_identifier("actor_id", item.get("actor_id"))
        if actor_id in seen_actors:
            raise ConstitutionError("actor IDs must be unique")
        seen_actors.add(actor_id)
        display_name = item.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ConstitutionError("assignment display_name is required")
        constitution_id = _require_identifier(
            "assignment constitution_id", item.get("constitution_id")
        )
        if constitution_id not in known:
            raise ConstitutionError("assignment references an unknown constitution")
        scopes = _require_nonempty_strings(
            "assignment business_keys", item.get("business_keys")
        )
        if not set(scopes) <= business_keys:
            raise ConstitutionError("assignment references an unknown business")
        if constitution_id == "business-owner":
            if len(scopes) != 1:
                raise ConstitutionError(
                    "each business-owner assignment must own exactly one business"
                )
            owner_scopes.extend(scopes)
        assignments.append(
            AgentAssignment(
                actor_id=actor_id,
                display_name=display_name.strip(),
                constitution_id=constitution_id,
                business_keys=scopes,
            )
        )
    if set(owner_scopes) != business_keys or len(owner_scopes) != len(business_keys):
        raise ConstitutionError(
            "every business requires exactly one business-owner assignment"
        )
    return tuple(assignments)


def render_agent_prompt(
    constitution: AgentConstitution,
    *,
    agent_root: Path = AGENT_ROOT,
) -> str:
    """Compose the stable role prompt; runtime state is appended elsewhere."""
    shared = (agent_root / "AGENTS.md").read_text().strip()
    role_contract = {
        "constitution_id": constitution.constitution_id,
        "version": constitution.version,
        "mission": constitution.mission,
        "accountabilities": constitution.accountabilities,
        "capabilities": constitution.capabilities,
        "autonomy": dict(constitution.autonomy),
        "decision_rights": constitution.decision_rights,
        "prohibited_actions": constitution.prohibited_actions,
        "startup_checks": constitution.startup_checks,
        "operating_loop": constitution.operating_loop,
        "escalation_triggers": constitution.escalation_triggers,
        "success_metrics": constitution.success_metrics,
        "memory_policy": dict(constitution.memory_policy),
    }
    return (
        f"{shared}\n\n"
        f"{constitution.soul}\n\n"
        "## Machine-readable role contract\n\n"
        f"```json\n{json.dumps(role_contract, indent=2, sort_keys=True)}\n```"
    )
