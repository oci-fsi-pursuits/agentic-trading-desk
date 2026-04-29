from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from runtime.common.contract_validation import validate_object


@dataclass
class RunContext:
    run_id: str
    scenario_id: str
    runtime: str
    active_seat_ids: list[str]
    ticker: str
    tickers: list[str] = field(default_factory=list)
    demo_mode: bool = True


@dataclass
class EventEnvelope:
    schema_version: str
    event_id: str
    event_type: str
    run_id: str
    stage_id: str
    emitted_at: str
    producer: str
    payload: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "stage_id": self.stage_id,
            "emitted_at": self.emitted_at,
            "producer": self.producer,
            "payload": self.payload,
        }


@dataclass
class RunArtifacts:
    event_log: List[Dict[str, Any]] = field(default_factory=list)
    objects: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    stage_sequence: List[str] = field(default_factory=list)

    def upsert(self, object_type: str, object_id: str, value: Dict[str, Any]) -> None:
        validate_object(object_type, value)
        bucket = self.objects.setdefault(object_type, {})
        bucket[object_id] = value
