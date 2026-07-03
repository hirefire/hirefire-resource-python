# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Inline type hints (PEP 484) on the public API plus a `py.typed` marker (PEP 561), so editors and type checkers (mypy, Pyright, Pylance) give consumers autocomplete and type checking. `config.service`/`config.dyno` carry literal `tracking` values, the queue macros type their return (`job_queue_size` an `int`, `job_queue_latency` a `float`), and `HireFire.configure()` is typed as a context manager yielding the configuration. Framework middleware request objects stay untyped, since precise types would require each framework as a dependency.
- `config.service(name, tracking=...)`, a way to declare what a process tracks. The name carries no meaning, so what to track is always explicit: `config.service("web", tracking="http")` for request metrics, `config.service("worker", callable)` for job metrics (the sampler is the signal), `config.service("encoder", tracking="cpu")` for CPU. Passing both a `tracking` keyword and a sampler, or neither, raises. `config.dyno` is now exactly `config.service` plus the Procfile convention that the `web` name implies `tracking="http"`.
- `config.dyno` accepts an optional `tracking="cpu"` keyword (`config.dyno("web", tracking="cpu")`, `config.dyno("encoder", tracking="cpu")`) to report CPU under that dyno name. The name still implies request metrics for `web` and job metrics when a sampler is given, so the 1.x forms are unchanged. `"cpu"` is the only value `config.dyno` accepts. Use `config.service(name, tracking="http")` to declare an http process under a non-`web` name. `tracking` is keyword-only: the second positional argument is still the sampler, so the 1.x `config.dyno("worker", callable)` form keeps working.
- CPUActivity metrics (the `cpu` collector): self-samples the dyno's CPU utilization once per second and pushes it in the per-second samples format. CPU time is read from a cgroup counter where one exists (cgroup v2/v1), else by summing `/proc/[pid]/stat` across the PID namespace (whole-dyno CPU where no cpu cgroup is exposed), else the stdlib process clock (dev/macOS). Normalized by the cgroup CPU quota where present, else the Cedar shared-dyno entitlement inferred from the dyno's memory limit (512 MB → 1 core, 1 GB → 2 cores), else the processor count (dedicated dynos and dev machines). Gated by process identity (`HIREFIRE_SERVICE_NAME`, the Heroku `DYNO` name in both Cedar `web.1` and Fir pod-name formats, or `RENDER_SERVICE_NAME`) so a process only reports CPU under its own dyno name. Unresolved identity disables CPU with a loud log rather than raising.
- Web liveness claims (heartbeats and backfilled empty seconds) are now gated by process identity: when the process's identity resolves and does not match the declared web dyno name, only real request samples are delivered and no liveness is synthesized. This prevents idle worker, one-off, and console processes from claiming web seconds, which could satisfy the RequestsPerMinute coverage check during a web outage and read it as zero traffic instead of missing data. When identity cannot be resolved, behavior is unchanged.
- Web metrics now claim every second between dispatches: seconds with no buffered samples are backfilled with explicit empty arrays (capped at 60 seconds, advancing only on successful delivery), so the server receives a complete per-second record: "alive with zero traffic" is reported as zero rather than left as a gap. Required for the RequestsPerMinute autoscaling strategy, whose coverage guard holds scaling when seconds go unreported.
- The dispatcher is now fork-aware: a forked worker (e.g. Gunicorn with `--preload`) detects that the inherited dispatcher thread did not survive the fork and starts a fresh one on the first request, instead of silently never dispatching. Locks that the fork copied in a locked state are re-created in the child (via `os.register_at_fork`), so a worker cannot deadlock on a mutex the parent happened to hold at the instant of the fork. The same `os.register_at_fork` handler re-issues each child's lease identity and drops any inherited grant, so forked workers do not all poll the job queues under the parent's identity.
- The dispatcher runs web/CPU dispatch and worker sampling on separate loops, so a slow or hung worker sampler (a job backend blocking with no timeout) can no longer stall metric delivery. Lease renewal and worker sampling run on their own loop, dispatch on another. A hung sampler stops renewing its lease, so the server hands worker sampling to a healthy process. Within each loop, stages stay isolated: a raising sampler is logged and costs one sample window, and a failed lease renewal is logged, revokes the local grant, and waits a full TTL before retrying.
- The middleware and dispatcher start are crash-safe: the per-request bookkeeping is wrapped so an internal failure (including the OS refusing a thread when the dispatcher starts) is logged and swallowed instead of raising into the host application's request or aborting boot from `HireFire.configure()`. A failed dispatcher-thread spawn leaves the dispatcher retryable rather than latched as "running" with no loop, and the downstream app call stays outside the guard so the host app's own exceptions still propagate.
- Metric dispatch and lease requests reuse a single persistent HTTPS connection (keep-alive) instead of opening a fresh TCP and TLS handshake per request. On the roughly once-per-second dispatch path this removes most of the per-request round-trips and the handshake CPU spent on the host process. A keep-alive socket the peer closes while idle is transparently reconnected and retried once (both endpoints are idempotent, so the retry is safe).
- Worker samplers are validated: a raising sampler is isolated per worker, and non-numeric, negative, or non-finite return values are dropped with a logged error instead of being sent.
- Declaring a second http process now raises, under any name and across both `config.dyno` and `config.service` (request metrics come from this process's own http traffic, so only one http collector can exist per process). Duplicate-name detection spans both methods and is case-insensitive, matching the identity gates.
- `X-Request-Start` parsing now handles the nginx (`t=` + epoch seconds) and Apache (`t=` + epoch microseconds) formats in addition to Heroku's epoch milliseconds, and ignores unparseable or implausible values instead of producing an absurd queue-time sample.
- Timestamped buffers are bounded: when dispatch is starved (network outage), web/CPU seconds older than the 60-second server acceptance window are pruned at insert time, and worker samples keep only the latest value per name.
- A buffered payload that would exceed the server's 64 KB body limit (only reachable after a sustained delivery outage combined with a very high per-process request rate) is dropped, and dispatch resumes from the current second instead of retrying a payload that can never be delivered. The web watermark advances past the dropped seconds so they are left unclaimed (missing data) rather than backfilled as "alive with zero traffic".
- All transport errors (DNS, refused/reset connections, TLS) are mapped to a single `hirefire_resource.client.RequestError` and handled uniformly.

### Changed

- The library is **push-only**. The `/hirefire/<token>/info` (and `/hirefire`) pull endpoint is removed from the WSGI and ASGI middleware: it served the retired pull model. The middleware's sole job is now to read `X-Request-Start` and sample web request queue time. All other requests pass straight through.
- The push destination is `data.hirefire.io` (override with `HIREFIRE_DATA_URL`). The `HIREFIRE_DISPATCH_URL` environment variable (1.x web → logdrain override) is removed. Restricted-egress networks must allowlist `data.hirefire.io` (outbound) or metrics silently stop.
- The dispatcher now starts automatically when `HireFire.configure()` exits with a token present, so worker-only apps push without needing any web traffic. `HireFire.reset()` stops the dispatcher and replaces the configuration.
- Supported Python versions are now 3.11 to 3.14. Python 3.9 and 3.10 are no longer supported.

### Fixed

- `job_queue_size`/`async_job_queue_size` now work against RabbitMQ 4.3+, which denies transient non-exclusive queues by default. The Celery worker inspection sets `control_queue_exclusive` and `event_queue_exclusive` before inspecting (matching Celery 5.7's own default) so the pidbox reply/event queues it relies on are accepted instead of failing with `INTERNAL_ERROR`.

## [1.0.4] - 2026-01-09

### Added

- Add `celery_app` parameter to `job_queue_size` and `async_job_queue_size` for priority queue support. This fixes RabbitMQ `PRECONDITION_FAILED` errors when querying queues configured with custom arguments like `x-max-priority`. The `celery_app` parameter allows extracting queue arguments from the app's `task_queues` configuration.

## [1.0.3] - 2024-10-18

### Fixed

- Mitigate issue where measuring the Celery job queue size and job queue latency results in connection reset errors. If a connection is reset, the macro will attempt to reconnect and retry the operation up to 10 times over a span of 10 seconds before giving up. The ConnectionResetError typically resolves after the initial reconnection attempt, so this should help alleviate the issue.

## [1.0.2] - 2024-07-26

### Fixed

- Fix issue where Django's HttpResponse object doesn't accept the headers keyword argument. Headers are now applied to the response object directly.

## [1.0.1] - 2024-03-12

### Added

- Add support for dashes in `Worker` names to match the Procfile process naming format. `Worker` is implicitly used when configuring HireFire using the `Configuration#dyno` method.

## [1.0.0] - 2024-01-23

### Added

- Initial release.
