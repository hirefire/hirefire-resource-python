# Tools

Checks that go beyond the test suite. The suite shows that the code does what the tests say. These tools show whether the tests would catch a mistake, and how the client behaves when the server or the host misbehaves. Nothing here ships: the package holds `hirefire_resource/` and three documents only.

Run every script from the repository root, after `tools/bin/setup`. They are not part of `paver check` or CI, because mutation testing and the soak take long.

| Path                    | Holds                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------ |
| `bin/setup`             | Builds every tox env, starts the test services, and creates the virtual environment of the tools |
| `bin/sandbox`           | Copies the tree to a throwaway directory, with test services of its own on request               |
| `bin/matrix-run`        | Runs every tox env once, in random order and with coverage on request, and writes a summary      |
| `bin/matrix-repeat`     | Runs the matrix several times in random order                                                    |
| `bin/matrix-summary`    | Combines the runs and names every test that failed in any of them                                |
| `bin/coverage-report`   | Merges line and branch coverage of all envs per file                                             |
| `bin/public-api`        | Writes the public names and signatures by import                                                 |
| `bin/wire-goldens`      | Captures the ingest and lease requests, or checks them against the golden copies with `--check`  |
| `bin/mutate`            | Changes `hirefire_resource/` one small step at a time and runs the tests that cover the line     |
| `bin/mutation-report`   | Reports the score per file and fails on a surviving change without a verdict                     |
| `bin/fault-run`         | Runs the client against a local server that misbehaves in 21 ways                                |
| `bin/soak`              | Runs the client for a long time with faults and forks, against a server in its own process       |
| `bin/host-run`          | Boots the client under Gunicorn, Uvicorn, uWSGI, and Celery in a Linux container                 |
| `bin/host-race`         | Boots each preloading server ten times with a token and ten times without                        |
| `bin/free-threaded-run` | Runs the core tests on a Python build without the GIL                                            |
| `bin/evidence`          | Runs one script of `evidence/` in a tox env                                                      |
| `bin/check-audit`       | Checks the audit documents of the canon: findings, map, comparison table, lenses                 |
| `baseline/`             | The golden copies of the public API and the wire requests                                        |
| `evidence/`             | Scripts that reproduce one finding each                                                          |
| `hosts/`                | The image, the apps, and the drivers of the host runs                                            |
| `lib/`                  | The code behind the scripts                                                                      |
| `results/`              | Where every script writes. Ignored by git                                                        |

## Results

No result is committed to this repository. Every script writes under `tools/results/`, which git ignores. Results worth keeping are stored in the canon, under `hirefire-resource/python-audit/results/`, without paths or ports of the machine that produced them.
