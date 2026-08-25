## HireFire Integration Library for Python Applications

This library integrates Python applications with HireFire's autoscalers on Heroku, Render, and other platforms.
It reports app metrics so HireFire can autoscale web and worker processes. That
unlocks strategies such as Request Queue Time, Requests Per Minute, CPU Activity,
Job Queue Latency, and Job Queue Size. Set `HIREFIRE_TOKEN` for the library to run.

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

The test suite runs against these minimum versions and the current latest release of each runtime and library. Older versions may still work, but are not officially supported.

**Types:**

The package ships inline PEP 484 type hints and a `py.typed` marker (PEP 561). No separate stubs package is needed.

**Documentation:**

Public API prose is Google-style docstrings on the consumer-facing surface. Changelog lives in [CHANGELOG.md](CHANGELOG.md).

---

Since 2011, HireFire has helped over 1,500 companies autoscale more than 5,000 [Heroku] applications across 10,000+ web and worker dynos.

HireFire autoscales both web and worker dynos, on all dyno tiers, using whichever signal fits the workload: request queue time or requests per minute for web dynos, job queue latency or job queue size for worker dynos, and CPU Activity for compute-bound web or worker dynos. Each tracks real demand, so dynos are added when you need them and removed when you don't. You pay only for what you use.

Learn more at the [home page][HireFire].

---

## Development

Requires [Docker](https://www.docker.com/) and [mise](https://mise.jdx.dev/). Redis and RabbitMQ for the macro tests run in containers, and mise installs the pinned Python versions from `.tool-versions`. `bin/services up` starts them on Docker-assigned free host ports recorded in a git-ignored `.env` (read by the test suite). `bin/services down` stops them and removes `.env`. Because the ports are assigned fresh at startup, multiple worktrees can run side by side without conflicting with each other or with any system-wide Redis/RabbitMQ.

- Run `bin/setup` to prepare the environment.
- Run `bin/services up` / `bin/services down` to start / stop Redis and RabbitMQ.
- See `poetry run paver -h` for common tasks (`paver check`, `paver format`, `paver test`), or `poetry run tox` for the full version matrix.

## Release

1. Update the `version` property in `pyproject.toml`.
2. If `pyproject.toml` dependencies changed, refresh `poetry.lock` with `poetry lock`.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's date) and add a fresh empty `## [Unreleased]` above it.
4. Commit changes with `git commit`.
5. Create a `git tag` matching the new version (e.g., `v1.0.0`).
6. Push the new git tag. Continuous Integration will handle the distribution process.

## License

This package is licensed under the terms of the MIT license.

[HireFire]: https://hirefire.io/
[Heroku]: https://heroku.com/
