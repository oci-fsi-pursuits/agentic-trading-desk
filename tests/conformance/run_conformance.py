from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.service import get_adapter

EXPECTED = json.loads((ROOT / 'tests/conformance/single-name-earnings.expected.json').read_text())
PARITY = json.loads((ROOT / 'tests/conformance/parity-profile.json').read_text())


def event_types(events):
    return {event['event_type'] for event in events}


def run_adapter(runtime_name: str):
    adapter = get_adapter(runtime_name)
    return adapter.execute('single_name_earnings')


def validate_expected(result):
    stages = result['summary']['stage_sequence']
    assert stages == EXPECTED['required_stage_sequence'], f"stage sequence mismatch: {stages}"
    assert set(EXPECTED['required_event_types']).issubset(event_types(result['events']))
    assert set(EXPECTED['required_object_types']).issubset(set(result['objects'].keys()))
    artifact_types = {item['artifact_type'] for item in result['objects']['artifact'].values()}
    assert set(EXPECTED['required_artifact_types']).issubset(artifact_types)


def validate_parity(left, right):
    assert left['summary']['stage_sequence'] == right['summary']['stage_sequence']
    assert event_types(left['events']) == event_types(right['events'])
    left_ticket = next(iter(left['objects']['trade_ticket'].values()))
    right_ticket = next(iter(right['objects']['trade_ticket'].values()))
    assert left_ticket['ticket_type'] == right_ticket['ticket_type']
    assert left_ticket['display_instrument'] == right_ticket['display_instrument']
    assert len(left_ticket['legs']) == len(right_ticket['legs'])
    left_primary_leg = next((leg for leg in left_ticket['legs'] if leg.get('role') == 'primary'), left_ticket['legs'][0])
    right_primary_leg = next((leg for leg in right_ticket['legs'] if leg.get('role') == 'primary'), right_ticket['legs'][0])
    assert left_primary_leg['side'] == right_primary_leg['side']
    assert left_primary_leg['size_bps'] == right_primary_leg['size_bps']
    assert left_ticket['exposure']['gross_bps'] == right_ticket['exposure']['gross_bps']
    assert left_ticket['exposure']['net_bps'] == right_ticket['exposure']['net_bps']
    left_decisions = sorted(item['outcome'] for item in left['objects']['decision'].values())
    right_decisions = sorted(item['outcome'] for item in right['objects']['decision'].values())
    assert left_decisions == right_decisions


def main() -> None:
    left = run_adapter('wayflow')
    right = run_adapter('langgraph')
    validate_expected(left)
    validate_expected(right)
    validate_parity(left, right)
    print(json.dumps({
        'status': 'ok',
        'parity_profile': PARITY,
        'wayflow_run': left['summary']['run_id'],
        'langgraph_run': right['summary']['run_id'],
    }, indent=2))


if __name__ == '__main__':
    main()
