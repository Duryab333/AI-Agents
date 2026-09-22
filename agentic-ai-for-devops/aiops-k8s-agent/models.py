"""Shared data models for the AIOps CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


@dataclass
class Anomaly:
    id: str
    namespace: str
    pod_name: str
    container_name: Optional[str]
    category: str
    severity: str
    detected_at: datetime
    signals: dict
    related_events: list[str]
    workload_kind: Optional[str] = None
    workload_name: Optional[str] = None


class ProposedFix(BaseModel):
    tool_name: Literal[
        "restart_deployment",
        "delete_pod",
        "scale_deployment",
        "patch_resource_limits",
        "no_action",
    ]
    args: dict = Field(default_factory=dict)
    rationale: str


class RCAResult(BaseModel):
    root_cause: str
    evidence: list[str] = Field(default_factory=list)
    prevention: list[str] = Field(default_factory=list)
    proposed_fix: ProposedFix
    confidence: Literal["low", "medium", "high"]
    summary: str
