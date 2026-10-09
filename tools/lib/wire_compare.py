import json
import sys
from pathlib import Path


def load(directory, name):
    return json.loads((Path(directory) / f"{name}.json").read_text())["requests"]


def main():
    mine = Path(__file__).resolve().parents[1] / "baseline" / "wire"
    other = Path(sys.argv[1])
    names = sorted(path.stem for path in mine.glob("*.json"))
    differences = 0
    kinds = {}

    def count(kind, counted=True):
        nonlocal differences
        differences += counted
        kinds[kind] = kinds.get(kind, 0) + 1

    requests = 0
    for name in names:
        ours, theirs = load(mine, name), load(other, name)
        print(f"{name}: {len(ours)} requests here, {len(theirs)} there")
        for index, (a, b) in enumerate(zip(ours, theirs)):
            requests += 1
            label = f"  request {index + 1} {a['verb']} {a['path']}"
            if (a["verb"], a["path"]) != (b["verb"], b["path"]):
                count("verb or path")
                print(f"{label}: DIFFERENT verb or path ({b['verb']} {b['path']})")
            ha = {k.lower(): v for k, v in a["headers"]}
            hb = {k.lower(): v for k, v in b["headers"]}
            for key in sorted(set(ha) | set(hb)):
                if ha.get(key) != hb.get(key):
                    count(f"value of the header {key}")
                    print(
                        f"{label}: header {key}: here {ha.get(key)!r}, there {hb.get(key)!r}"
                    )
            case = sorted(
                (k1, k2)
                for k1, _ in a["headers"]
                for k2, _ in b["headers"]
                if k1.lower() == k2.lower() and k1 != k2
            )
            if case:
                count("spelling of header names (not counted)", counted=False)
                print(f"{label}: header names spelled differently: {case}")
            order_a = [k.lower() for k, _ in a["headers"]]
            order_b = [k.lower() for k, _ in b["headers"] if k.lower() in order_a]
            if [k for k in order_a if k in order_b] != order_b:
                count("order of the headers (not counted)", counted=False)
                print(f"{label}: header order differs")
            if a["body"] != b["body"]:
                count("body")
                print(
                    f"{label}: body differs\n    here:  {a['body']}\n    there: {b['body']}"
                )
            else:
                print(f"{label}: body identical ({len(a['body'])} bytes)")
    print(f"{len(names)} scenarios, {requests} requests")
    for kind, number in kinds.items():
        print(f"{number:>3} of {requests} requests differ in: {kind}")
    print(f"{differences} differences in headers and bodies")


if __name__ == "__main__":
    main()
