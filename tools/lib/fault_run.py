import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common
from fake_server import SCENARIOS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault", type=int, default=20)
    parser.add_argument("--heal", type=int, default=12)
    parser.add_argument("--parallel", type=int, default=5)
    parser.add_argument("--only")
    parser.add_argument("--env", default="py314-core")
    options = parser.parse_args()

    directory = common.results("faults")
    scenarios = options.only.split(",") if options.only else SCENARIOS
    python = common.ROOT / ".tox" / options.env / "bin" / "python"
    script = common.ROOT / "tools" / "lib" / "fault_harness.py"

    for index in range(0, len(scenarios), options.parallel):
        group = scenarios[index : index + options.parallel]
        processes = []
        for scenario in group:
            path = directory / f"{scenario}.json"
            path.unlink(missing_ok=True)
            log = open(directory / f"{scenario}.log", "w")
            processes.append(
                subprocess.Popen(
                    [
                        str(python),
                        "-B",
                        str(script),
                        scenario,
                        str(path),
                        str(options.fault),
                        str(options.heal),
                    ],
                    cwd=common.ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        for process in processes:
            process.wait()

    results = []
    for scenario in scenarios:
        path = directory / f"{scenario}.json"
        if path.exists():
            results.append(json.loads(path.read_text()))
        else:
            results.append({"scenario": scenario, "crashed": True})
    (directory / "results.json").write_text(json.dumps(results, indent=2) + "\n")

    print(
        "scenario            escaped  mw max ms  ingest  lease  log/min  running  threads  recover s  stop s"
    )
    for result in results:
        if result.get("crashed"):
            print(
                f"{result['scenario']:<19} harness crashed, see the log of the scenario"
            )
            continue
        recovered = result["recovered_after_seconds"]
        print(
            f"{result['scenario']:<19} {len(result['escaped']):>7} {result['middleware_max_ms']:>10.2f} "
            f"{result['ingest_requests']:>7} {result['lease_requests']:>6} {result['log_lines_per_minute']:>8}  "
            f"{str(result['dispatcher_running_after_fault']):<7} {result['threads_start']:>3}>{result['threads_after_fault']:<3} "
            f"{('n/a' if recovered is None else recovered):>10} {result['stop_seconds_under_fault']:>7.2f}"
        )


if __name__ == "__main__":
    main()
