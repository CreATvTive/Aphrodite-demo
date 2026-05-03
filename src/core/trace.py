from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class PresenceTrace:
    raw_input: str
    event_version: int
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    interpreted_event: dict = field(default_factory=dict)
    dominant_response_mode: str = "minimal_ack"
    secondary_modes: list[str] = field(default_factory=list)
    suppressed_modes: list[str] = field(default_factory=list)
    mind_delta: dict = field(default_factory=dict)
    relationship_delta: dict = field(default_factory=dict)
    memory_candidates: list[dict] = field(default_factory=list)
    memory_write_decisions: list[dict] = field(default_factory=list)
    body_influence: dict = field(default_factory=dict)
    action_basis_weights: dict = field(default_factory=dict)
    mixer_result: dict = field(default_factory=dict)
    trajectory: dict = field(default_factory=dict)
    language_density: str = "minimal_ack"
    persona_firewall_result: dict = field(default_factory=dict)
    latency_tier: str = "tier_1"
    final_output: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()
