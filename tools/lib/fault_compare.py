import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common
from fake_server import SCENARIOS

COLUMNS = [
    ("escaped", lambda row: len(row["escaped"])),
    ("ingest", lambda row: row["ingest_requests"]),
    ("lease", lambda row: row["lease_requests"]),
    ("log/min", lambda row: row["log_lines_per_minute"]),
    ("recover s", lambda row: row["recovered_after_seconds"]),
    ("stop s", lambda row: row["stop_seconds_under_fault"]),
]


def text(value):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main():
    ruby = Path(sys.argv[1])
    python = {
        row["scenario"]: row
        for row in json.loads((common.results("faults") / "results.json").read_text())
    }
    header = f"{'fault':<19}" + "".join(f"{name:>24}" for name, _ in COLUMNS)
    print("each cell: Python / Ruby as audited / Ruby after its fix pass")
    print(header)
    same = 0
    for scenario in SCENARIOS:
        rows = [
            python[scenario],
            json.loads((ruby / "faults" / f"{scenario}.json").read_text()),
            json.loads((ruby / "fixes" / "faults" / f"{scenario}.json").read_text()),
        ]
        cells = [" / ".join(text(read(row)) for row in rows) for _, read in COLUMNS]
        same += all(read(rows[0]) == read(rows[1]) for _, read in COLUMNS[:4])
        print(f"{scenario:<19}" + "".join(f"{cell:>24}" for cell in cells))
    print(f"faults: {len(SCENARIOS)}")
    print(
        "Python equals Ruby as audited in escaped errors, ingest requests, lease "
        f"requests, and log lines per minute: {same} of {len(SCENARIOS)}"
    )
    print(
        "errors that escaped into the host, Python: "
        f"{sum(len(row['escaped']) for row in python.values())}"
    )
    print(
        "slowest middleware call, Python: "
        f"{max(row['middleware_max_ms'] for row in python.values())} ms"
    )


if __name__ == "__main__":
    main()
