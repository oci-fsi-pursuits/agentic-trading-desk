from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import validate_run_request


def main() -> None:
    required_seats = [
        "market_analyst",
        "news_analyst",
        "fundamentals_analyst",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "quant_analyst",
        "risk_manager",
        "portfolio_manager",
        "trader",
    ]

    validate_run_request("wayflow", "single_name_earnings", required_seats)

    try:
        validate_run_request("wayflow", "single_name_earnings", [*required_seats, "geopolitical_analyst"])
    except ValueError as exc:
        assert "scenario-suppressed seats" in str(exc)
    else:
        raise AssertionError("/api/run/start preflight must reject scenario-suppressed seats")

    try:
        validate_run_request("wayflow", "single_name_earnings", required_seats[:-1])
    except ValueError as exc:
        assert "required scenario seats" in str(exc)
    else:
        raise AssertionError("/api/run/start preflight must reject missing required seats")

    print(json.dumps({"status": "ok", "validated": "run_start_preflight"}, indent=2))


if __name__ == "__main__":
    main()
