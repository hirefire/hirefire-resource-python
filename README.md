## HireFire Integration Library for Python Applications

This library integrates Python applications with HireFire's autoscalers. It pushes
request queue time, CPU, and job-queue metrics to `data.hirefire.io`. Setup steps
for each web framework and worker library are shown in the HireFire dashboard
during install.

**Zero-config (common path):** set `HIREFIRE_TOKEN`, mount the framework middleware
early, and call `HireFire.boot()` (or empty `with HireFire.configure():`) in every
process that should report. Request queue time arms from traffic and platform web-role
hints (`DYNO` type `web`, `RENDER_SERVICE_TYPE=web`). CPU is always-on when process
identity resolves (`HIREFIRE_SERVICE_NAME`, Heroku `DYNO`, or `RENDER_SERVICE_NAME`).
Job queues are driven by lease collection plans from the dashboard (Celery and RQ), or
optional local `config.dyno("worker", sampler)` callables. There is no `service` or
`tracking` API.

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

The test suite runs against these minimum versions and the current latest release of
each runtime and library. Older versions may still work, but are not officially
supported.

**Types:**

The package ships inline PEP 484 type hints and a `py.typed` marker (PEP 561). No
separate stubs package is needed.

**Documentation:**

Public surface is documented with Google-style docstrings on consumer-facing APIs
(`HireFire`, `Configuration`, middleware, macros). There is no Sphinx site.

**Prefork servers (Gunicorn/uWSGI):** the library registers fork hooks. Prefer
`--preload` only when you understand parent stop / child restart. Job workers that
fork per job abandon inherited dispatcher state in the child so they do not steal
the lease.

---

Since 2011, HireFire has helped over 1,500 companies autoscale more than 5,000 [Heroku]
applications across 10,000+ web and worker dynos.

HireFire autoscales both web and worker dynos, on all dyno tiers, using whichever signal
fits the workload: request queue time or requests per minute for web dynos, job queue
latency or job queue size for worker dynos, and CPU utilization for compute-bound web or
worker dynos. Each tracks real demand, so dynos are added when you need them and removed
when you don't. You pay only for what you use.

Learn more at the [home page][HireFire].

---

## Development

Requires [Docker](https://www.docker.com/) and [mise](https://mise.jdx.dev/). Redis and
RabbitMQ for the macro tests run in containers, and mise installs the pinned Python
versions (3.11 through 3.14) from `.tool-versions`. `bin/services up` starts the
containers on Docker-assigned free host ports recorded in a git-ignored `.env` (read by
the test suite). `bin/services down` stops them and removes `.env`. Because the ports are
assigned fresh at startup, multiple worktrees, and any system-wide Redis/RabbitMQ, run
side by side without conflicts.

- Run `bin/setup` to prepare the environment.
- Run `bin/services up` / `bin/services down` to start / stop the Redis and RabbitMQ
  containers.
- Run `poetry run paver <task>` for common tasks (`format`, `check`, `test`), or
  `poetry run tox` for the full version matrix.

## Release

1. Update the `version` property in `pyproject.toml`.
2. Ensure that `CHANGELOG.md` is up-to-date.
3. Commit changes with `git commit`.
4. Create a `git tag` matching the new version (e.g., `v1.0.0`).
5. Push the new git tag. Continuous Integration will handle the distribution process.

## License

This package is licensed under the terms of the MIT license.

[HireFire]: https://hirefire.io/
[Heroku]: https://heroku.com/
