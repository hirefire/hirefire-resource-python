# Tools

Checks that go beyond the test suite. The suite shows that the code does what the tests say. These tools show whether the tests would catch a mistake, and how the client behaves when the server or the host misbehaves. Nothing here ships: the package holds `hirefire_resource/` and three documents only.

Run every script from the repository root, after `tools/bin/setup`. They are not part of `paver check` or CI, because mutation testing and the soak take long.

| Path                      | Holds                                                                                                                                         |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `bin/setup`               | Builds every tox env, starts the test services, and creates the virtual environment of the tools                                              |
| `bin/sandbox`             | Copies the tree to a throwaway directory, with test services of its own on request                                                            |
| `bin/matrix-run`          | Runs every tox env once, in random order and with coverage on request, and writes a summary. `--jobs 4` runs the Python versions side by side |
| `bin/matrix-repeat`       | Runs the matrix several times in random order                                                                                                 |
| `bin/matrix-summary`      | Combines the runs and names every test that failed in any of them                                                                             |
| `bin/coverage-report`     | Merges line and branch coverage of all envs per file, and fails below the floor                                                               |
| `bin/coverage [RUN]`      | Runs the matrix with coverage and prints the merged report. Fails below the floor in `lib/coverage_report.py`                                 |
| `bin/public-api`          | Writes the public names and signatures by import                                                                                              |
| `bin/wire-goldens`        | Captures the ingest and lease requests, or checks them against the golden copies with `--check`                                               |
| `bin/mutate`              | Changes `hirefire_resource/` one small step at a time and runs the tests that cover the line                                                  |
| `bin/mutation-report`     | Reports the score per file and fails on a surviving change without a verdict                                                                  |
| `bin/fault-run`           | Runs the client against a local server that misbehaves in 21 ways                                                                             |
| `bin/fault-compare`       | Prints each fault beside the Ruby results that the canon keeps                                                                                |
| `bin/soak`                | Runs the client for a long time with faults and forks, against a server in its own process                                                    |
| `bin/soak-variants`       | Runs the soak with and without the client, the forks, and the faults, and prints the memory growth of each                                    |
| `bin/host-run`            | Boots the client under Gunicorn, Uvicorn, uWSGI, and Celery in a Linux container                                                              |
| `bin/host-race`           | Boots each preloading server ten times with a token and ten times without                                                                     |
| `bin/host-summary`        | Prints the result per host of one or more host runs                                                                                           |
| `bin/free-threaded-run`   | Runs the core tests on a Python build without the GIL                                                                                         |
| `bin/evidence`            | Runs one script of `evidence/` in a tox env                                                                                                   |
| `bin/evidence-all [NAME]` | Runs every script of `evidence/` with its arguments and writes one output per finding to `results/evidence/`                                  |
| `bin/check-audit`         | Checks the audit documents of the canon: findings, map, comparison table, and each point of each lens                                         |
| `bin/check-fixes`         | Checks the status table of the fix plan of the canon against the commits that name a finding                                                  |
| `bin/gate`                | Runs `paver check` and the tox envs of the changed files, or every env with `--all`, before a commit                                          |
| `baseline/`               | The golden copies of the public API and the wire requests                                                                                     |
| `evidence/`               | Scripts that reproduce one finding each                                                                                                       |
| `hosts/`                  | The image, the apps, and the drivers of the host runs                                                                                         |
| `lib/`                    | The code behind the scripts                                                                                                                   |
| `results/`                | Where every script writes. Ignored by git                                                                                                     |

## Results

No result is committed to this repository. Every script writes under `tools/results/`, which git ignores. Results worth keeping are stored in the canon, under `hirefire-resource/python-audit/results/`, without paths or ports of the machine that produced them.

## Matrix lanes

`bin/matrix-run RUN --jobs N` runs up to four lanes, one or more Python versions each. Every lane after the first gets a Redis and a RabbitMQ of its own, in a Compose project named after that of the checkout with `-lane1` to `-lane3` appended, so two lanes never share a queue. The stacks stay up between runs. `bin/matrix-run --down` removes them. With `--jobs` the package is built once and every env installs that build. `bin/gate` runs with four lanes. The runs whose numbers are reported (`bin/matrix-repeat`) run one env at a time.

## Evidence

`bin/evidence-all` needs `bin/services up`, Docker, a built wheel in `dist/`, and the virtual environment that `bin/free-threaded-run` makes. It starts a RabbitMQ 4 and a Redis with TLS in containers of its own and removes them at its end. `TOOLS_EVIDENCE_OUT` names the directory under `results/`. A name as the argument runs that one output.

Two scripts reach outside the checkout. `lease_steal` also runs the Ruby client when `TOOLS_RUBY_ROOT` names a checkout of it with its bundle installed. `evidence/cedar_proc_cpu.py` is not part of `bin/evidence-all`: it uses the standard library only and runs on a one-off dyno, with `heroku run --no-tty -a APP -- python - < tools/evidence/cedar_proc_cpu.py`.

## Mutation run

`bin/mutate run` runs a changed line with the tests whose coverage names it, not with the whole env. Three steps keep a verdict tied to the change:

- A TLS context is made before the tests (`lib/mutation_warmup.py`), as an earlier test does in a full run. Without it the test that forks a web process fails by chance on the unchanged tree.
- Every distinct set of tests first runs on the unchanged tree. The run stops when one fails.
- A failing run counts as a kill when it fails a second time. The record of a kill names the test.

## Soak

`bin/soak` reads three optional variables: `SOAK_FORK_EVERY` (seconds between forks, 0 for none), `SOAK_PHASES` (the server modes in order, such as `healthy`), and `SOAK_CLIENT=0` (no token, so the client stays idle and the run measures the harness alone). `TOOLS_RUN` names the directory under `results/`.

## Host runs

`bin/host-run` and `bin/host-race` read four variables: `TOOLS_HOST_PYTHON` (the Python of the image, default 3.13), `TOOLS_HOST_UWSGI` (a requirement for pip such as `uwsgi==2.0.21`, default the newest release), `TOOLS_HOSTS_OUT` (the directory under `results/`, default `hosts`), and `HOSTS_ONLY` or `RACE_ONLY` (a part of a host name). uWSGI 2.0.31 enables Python threads by itself, so the case without thread support needs an older release.
