import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools_common as common
from wire_scenarios import ENVS


def main():
    check = "--check" in sys.argv[1:]
    directory = common.ROOT / "tools" / "baseline" / "wire"
    directory.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("TOOLS_REDIS_URL"):
        sys.exit("set TOOLS_REDIS_URL to a Redis database that this script may flush")

    failed = []
    with tempfile.TemporaryDirectory(prefix="wire-goldens") as scratch:
        for name, env in ENVS.items():
            fresh = Path(scratch) / f"{name}.json"
            python = common.ROOT / ".tox" / env / "bin" / "python"
            script = common.ROOT / "tools" / "lib" / "wire_scenarios.py"
            result = subprocess.run(
                [str(python), str(script), name, str(fresh)],
                cwd=common.ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not fresh.exists():
                failed.append(name)
                tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
                print(f"FAIL {name}: {' | '.join(tail)}")
                continue
            path = directory / f"{name}.json"
            if check:
                same = path.exists() and path.read_text() == fresh.read_text()
                if not same:
                    failed.append(name)
                print(f"{'same' if same else 'DIFF'} {name}")
            else:
                path.write_text(fresh.read_text())
                print(f"wrote {name}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
