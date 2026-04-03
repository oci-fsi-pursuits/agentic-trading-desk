from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.common.types import RunArtifacts
from runtime.common.utils import ROOT, RUNS_ROOT, append_jsonl, ensure_runs_root, write_json

AUDIT_ROOT = ROOT / "var" / "audit"
AUDIT_LOG_PATH = AUDIT_ROOT / "audit-log.jsonl"
AUDIT_EVENT_TYPES = {
    "run.started",
    "approval.requested",
    "approval.resolved",
    "ticket.updated",
    "run.completed",
    "run.failed",
}


class RunStore:
    def __init__(self, run_id: str) -> None:
        ensure_runs_root()
        self.run_id = run_id
        self.run_dir = RUNS_ROOT / run_id
        self.event_log_path = self.run_dir / "event-log.jsonl"
        self.object_store_path = self.run_dir / "object-store.json"
        self.summary_path = self.run_dir / "summary.json"

    def append_event(self, event: dict[str, Any]) -> None:
        append_jsonl(self.event_log_path, event)
        if event.get("event_type") in AUDIT_EVENT_TYPES:
            audit_record = {
                "emitted_at": event.get("emitted_at"),
                "run_id": event.get("run_id"),
                "event_type": event.get("event_type"),
                "stage_id": event.get("stage_id"),
                "producer": event.get("producer"),
                "payload": event.get("payload", {}),
            }
            append_jsonl(AUDIT_LOG_PATH, audit_record)

    def write_objects(self, artifacts: RunArtifacts) -> None:
        payload = {
            "run_id": self.run_id,
            "stage_sequence": artifacts.stage_sequence,
            "objects": artifacts.objects,
        }
        write_json(self.object_store_path, payload)

    def write_summary(self, payload: dict[str, Any]) -> None:
        write_json(self.summary_path, payload)

    @classmethod
    def load_run(cls, run_id: str) -> dict[str, Any]:
        store = cls(run_id)
        if not store.summary_path.exists() or not store.object_store_path.exists():
            raise FileNotFoundError(run_id)
        return {
            "summary": json.loads(store.summary_path.read_text()),
            "objects": json.loads(store.object_store_path.read_text()),
            "event_log_path": str(store.event_log_path),
            "event_log_url": "/" + str(store.event_log_path.relative_to(ROOT)).replace("\\", "/"),
        }

    @classmethod
    def list_runs(cls, limit: int = 20) -> list[dict[str, Any]]:
        ensure_runs_root()
        runs = []
        for run_dir in sorted(RUNS_ROOT.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            summary_path = run_dir / "summary.json"
            if not summary_path.exists():
                continue
            try:
                runs.append(json.loads(summary_path.read_text()))
            except json.JSONDecodeError:
                continue
            if len(runs) >= limit:
                break
        return runs

    @classmethod
    def list_audit(cls, limit: int = 50) -> list[dict[str, Any]]:
        if not AUDIT_LOG_PATH.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            lines = AUDIT_LOG_PATH.read_text().splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(entries) >= limit:
                break
        return entries
