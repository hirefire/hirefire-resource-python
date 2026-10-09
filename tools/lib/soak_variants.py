import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common

SETTLE_SECONDS = 120


def per_minute(rows, key):
    rows = [row for row in rows if int(row["second"]) >= SETTLE_SECONDS]
    minutes = [int(row["second"]) / 60 for row in rows]
    values = [float(row[key]) for row in rows]
    mean_minute = sum(minutes) / len(minutes)
    mean_value = sum(values) / len(values)
    spread = sum((minute - mean_minute) ** 2 for minute in minutes)
    return round(
        sum(
            (minute - mean_minute) * (value - mean_value)
            for minute, value in zip(minutes, values)
        )
        / spread,
        1,
    )


def every_ten_minutes(rows, key):
    values = []
    boundary = 0
    for row in rows:
        if int(row["second"]) >= boundary:
            values.append(int(row[key]))
            boundary += 600
    return values


def main():
    directory = common.RESULTS / "soak-variants"
    for path in sorted(directory.glob("*/soak.json")):
        summary = json.loads(path.read_text())
        with open(path.with_name("soak.csv")) as file:
            rows = list(csv.DictReader(file))
        print(
            json.dumps(
                {
                    "variant": path.parent.name,
                    "client": summary["client"],
                    "forks": summary["forks"],
                    "server_modes": len(set(summary["phases"])),
                    "seconds": summary["seconds"],
                    "rss_kb_first_last": [
                        summary["rss_kb"]["first"],
                        summary["rss_kb"]["last"],
                    ],
                    "rss_kb_per_minute": per_minute(rows, "rss_kb"),
                    "rss_kb_every_ten_minutes": every_ten_minutes(rows, "rss_kb"),
                    "allocated_blocks_first_last": [
                        summary["allocated_blocks"]["first"],
                        summary["allocated_blocks"]["last"],
                    ],
                    "allocated_blocks_per_minute": per_minute(rows, "allocated_blocks"),
                    "ingest_requests": summary["ingest_requests"],
                }
            )
        )


if __name__ == "__main__":
    main()
