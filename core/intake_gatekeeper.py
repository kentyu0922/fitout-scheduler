"""Compatibility facade over the sole project input contract."""
from dataclasses import dataclass, field
from typing import Any, Dict, List
from core.project_input import normalize_project_inputs, REQUIRED, SUPPORTED, CHOICES, VALUE_ALIASES, DEFAULTS
CHOICE_ALIASES = VALUE_ALIASES
@dataclass
class FieldDefinition:
    key: str
    name: str
    category: str
    field_type: str
    choices: Any = None
    default_value: Any = None
    engineering_impact: str = ""
    prompt_guidance: str = ""
INTAKE_SCHEMA = {
    k: FieldDefinition(k, k, "MANDATORY" if k in REQUIRED else "OPTIONAL_INFERRED",
                       "choice" if k in CHOICES else "str", sorted(CHOICES.get(k, [])) or None,
                       DEFAULTS.get(k)) for k in sorted(SUPPORTED)}
@dataclass
class BlockerItem:
    key: str
    name: str
    engineering_impact: str
    suggested_fix: str
    preset_choice: Any = None
@dataclass
class InferredItem:
    key: str
    name: str
    inferred_value: Any
    rationale: str
@dataclass
class TriageResult:
    status: str
    resolved_facts: Dict[str, Any] = field(default_factory=dict)
    blockers: List[BlockerItem] = field(default_factory=list)
    inferred_assumptions: List[InferredItem] = field(default_factory=list)
    field_evidence: dict = field(default_factory=dict)
    def is_blocked(self):
        return self.status == "PREFLIGHT_BLOCKER"
def evaluate_intake(raw_facts):
    result = normalize_project_inputs([("intake", raw_facts)])
    resolved = dict(result.project)
    resolved["site_calendar"] = resolved["calendar_profile"]
    if "target_date" in resolved:
        resolved["target_movein_date"] = resolved["target_date"]
    return TriageResult(
        "PREFLIGHT_BLOCKER" if result.blockers else "PASS", resolved,
        [BlockerItem(b["field"], b["field"], b["reason"], b["reason"]) for b in result.blockers],
        [InferredItem(a["field"], a["field"], a["value"], a["basis"]) for a in result.payload()["assumptions"]],
        result.evidence)
def format_blocker_report(triage):
    return "\n".join(["项目准入前置门禁检查：核心工程边界参数缺失或冲突",
                       "已有资料优先；只需补充或确认以下核心字段（非关键工程参数已静默注入 标杆定额并在假设清单中注明）："] +
                      [f"- {b.key}: {b.suggested_fix}" for b in triage.blockers])
