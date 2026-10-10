import json
import sys
from pathlib import Path

REPORTS = Path.home() / "Library" / "Logs" / "DiagnosticReports"


def main():
    since = float(sys.argv[1])
    reports = sorted(
        (
            path
            for path in REPORTS.glob("python3*.ips")
            if path.stat().st_mtime >= since
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not reports:
        print(json.dumps({"crash_reports_of_python_since_the_start": 0}))
        return
    report = json.loads(reports[-1].read_text().partition("\n")[2])
    thread = report["threads"][report["faultingThread"]]
    print(
        json.dumps(
            {
                "crash_reports_of_python_since_the_start": len(reports),
                "crash_report_of_a_killed_child": {
                    "exception": report["exception"]["type"],
                    "signal": report["exception"]["signal"],
                    "reason": [
                        line for lines in report["asi"].values() for line in lines
                    ],
                    "top_frames_of_the_crashed_thread": [
                        frame.get("symbol") for frame in thread["frames"][:9]
                    ],
                },
            }
        )
    )


if __name__ == "__main__":
    main()
