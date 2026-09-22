"""Shared data models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

FixTool = Literal[
    "restart_deployment",
    "delete_pod",
    "scale_deployment",
    "patch_resource_limits",
    "no_action",
]


@dataclass
class Anomaly:
    namespace: str
    pod_name: str
    container_name: Optional[str]
    category: str
    severity: str
    detected_at: datetime
    signals: dict
    related_events: list[str] = field(default_factory=list)
    workload_kind: Optional[str] = None
    workload_name: Optional[str] = None

    @property
    def workload(self) -> str:
        return self.workload_name or self.pod_name

    @property
    def key(self) -> str:
        """Stable identity across pod restarts/recreations: namespace/workload/category."""
        return f"{self.namespace}/{self.workload}/{self.category}"

    @property
    def incident_id(self) -> str:
        return "inc-" + hashlib.sha1(self.key.encode()).hexdigest()[:6]


class ProposedFix(BaseModel):
    tool_name: FixTool
    args: dict = Field(default_factory=dict)
    rationale: str


class RCAResult(BaseModel):
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    manual_fix_steps: list[str] = Field(
        default_factory=list,
        description="Concrete steps (with kubectl commands where possible) a human runs to fix it.",
    )
    prevention: list[str] = Field(default_factory=list)
    proposed_fix: ProposedFix
    confidence: Literal["low", "medium", "high"]
    summary: str
    source: str = Field(default="llm", description="'llm' or 'heuristic' (LLM unavailable/failed).")
