## HireFire Integration Library for Python Applications

This package integrates Python applications running on [Heroku] with [HireFire]'s autoscalers. It collects HTTP, CPU, and job metrics so HireFire can scale web and worker processes based on Request Queue Time, Requests Per Minute, CPU Activity, Job Queue Latency, and Job Queue Size.

**Supported runtimes:**

- Python 3.11+

**Supported web frameworks:**

- Django 4+
- Flask 2+
- Quart 0+
- FastAPI 0+
- Starlette 0+

**Supported worker libraries:**

- Celery 5+
- RQ 1+
- Dramatiq 2+

Django 6 requires Python 3.12+.

The test suite runs against these minimum versions and the current latest release of each runtime and library. Older versions may still work, but are not officially supported.

**Types:**

The package ships inline PEP 484 type hints and a `py.typed` marker (PEP 561).

**Documentation:**

Changelog lives in [CHANGELOG.md](CHANGELOG.md).

## Development

Requires [Docker](https://www.docker.com/) and [mise](https://mise.jdx.dev/). Redis and RabbitMQ for the macro tests run in containers, and mise installs the pinned Python versions from `.tool-versions`. `bin/services up` starts them on Docker-assigned free host ports recorded in a git-ignored `.env` (read by the test suite). `bin/services down` stops them and removes `.env`. Because the ports are assigned fresh at startup, multiple worktrees can run side by side without conflicting with each other or with any system-wide databases.

- Run `bin/setup` to prepare the environment.
- Run `bin/services up` / `bin/services down` to start / stop Redis and RabbitMQ.
- See `poetry run paver -h` for common tasks (`paver check`, `paver format`, `paver test`), or `poetry run tox` for the full version matrix.

## Release

1. Update the `version` property in `pyproject.toml` (prerelease: PEP 440, e.g. `2.0.0rc1`).
2. If `pyproject.toml` dependencies changed, refresh `poetry.lock` with `poetry lock`.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's date) and add a fresh empty `## [Unreleased]` above it.
4. Commit changes with `git commit`.
5. Create a `git tag` matching the new version (e.g., `v2.0.0` or `v2.0.0rc1`).
6. Push the new git tag. Continuous Integration will handle the distribution process. Prereleases are not installed by default (`pip` needs `--pre` or a pin).

## License

This package is licensed under the terms of the MIT license.

[HireFire]: https://hirefire.io/
[Heroku]: https://heroku.com/
