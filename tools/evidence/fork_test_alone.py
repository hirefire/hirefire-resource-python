import json
import os
import platform
import subprocess
import sys

TEST = (
    "tests/hirefire_resource/test_fork.py"
    "::test_real_fork_restarts_child_and_stops_parent"
)
RUNS = 20


def failures(*options):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HIREFIRE_") and key not in ("DYNO", "FORCE_COLOR")
    }
    count = 0
    lines = set()
    for _ in range(RUNS):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-x",
                "-q",
                "-p",
                "no:cacheprovider",
                *options,
                TEST,
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            count += 1
            lines.update(
                line.strip()
                for line in result.stdout.splitlines()
                if line.startswith("E  ") and "WIFEXITED>(" in line
            )
    return count, sorted(lines)


def main():
    alone, lines = failures()
    after_context, _ = failures("-p", "mutation_warmup")
    print(
        json.dumps(
            {
                "platform": platform.system(),
                "python": platform.python_version(),
                "runs": RUNS,
                "failed_alone": alone,
                "failed_after_a_tls_context_was_made": after_context,
                "failure": lines,
            }
        )
    )


if __name__ == "__main__":
    main()
