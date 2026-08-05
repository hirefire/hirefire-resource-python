# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- RQ **working** count via `job_queue_working` / `async_job_queue_working` (`ZCARD rq:wip:{name}`). Plan path samples nested strategy **`wrk`** unconditionally next to each RQ `jql`/`jqs` entry (same queues and connection options). Failures drop only `wrk`. Not folded into waiting JQL/JQS. Other plan adapters without `job_queue_working` are unchanged.
- Plan sample-wave lifecycle aligned with Ruby: `plan.around_job_queue_sample` brackets each dispatcher job-queue sample, every allowlisted macro exposes no-op `before_sample_job_queues` / `after_sample_job_queues` / `reinit_after_fork`, and `plan.reinit_macros_after_fork` runs next to buffer reinit on fork and abandon-inherited paths.
- Dramatiq first-party job-queue adapter (`dramatiq` plan wire key): `job_queue_size` / `job_queue_latency` (and async wrappers) for Redis and RabbitMQ. Redis waiting set is live ready list plus due `.DQ` messages (`options.eta ≤ now_ms`). RabbitMQ is main-queue ready only (`.DQ` / `.XQ` not counted in v1). Queues required. Connection via `broker=` XOR `broker_url`, plan `HIREFIRE_DRAMATIQ_URL` / optional `HIREFIRE_DRAMATIQ_NAMESPACE`, then the Celery-family AMQP then Redis env ladder. Working/acks never included (no `do_qsize`).
- Zero-config install: set `HIREFIRE_TOKEN`, mount middleware, and call `HireFire.boot()` (or empty `HireFire.configure()`). Request queue time and CPU report without local declarations. Job queues follow lease collection plans for Celery, RQ, and Dramatiq.
- Always-on CPU under resolved process identity. Always-on RQT when platform web role is known (Heroku `DYNO` type `web`, Render `RENDER_SERVICE_TYPE=web`), when middleware sees traffic, or when `config.dyno("web")` is declared.
- Lease plan body parsing (`job_queues`), hold/refuse with process id rotate, demote with epoch fence, and dispatcher plan sampling (`jql`/`jqs`) for Celery, RQ, and Dramatiq.
- Prefork web handoff via `os.register_at_fork`: parent stops without flush, child starts with token, handoff without token is a no-op, job-only children abandon inherited state.
- Compact nested ingest wire: RQT as `[mean, n]` or `[]`, bare non-RQT numbers, 32 KB payload limit.

### Changed

- Local configuration is dyno-only: `config.dyno(name, sampler=None)`. Multi-kind same name is allowed. Soft identity re-resolves every call.
- Middleware samples with token only (no predeclared web collector). Dispatcher re-reads live configuration each tick and supports `stop(flush=...)`.
- Nested metric buffer and ingest payload use the compact 32 KB wire format.
- Celery `job_queue_size` / `async_job_queue_size` count broker-ready messages only (Redis `LLEN` / RabbitMQ `message_count`). Active, reserved, and due scheduled tasks from Celery inspect are no longer included, so size tracks waiting backlog rather than worker load or prefetch.
- RQ `job_queue_size` / `job_queue_latency` (and async variants) are waiting-only: live queue plus due scheduled (`score ≤ now`). Delayed retries share the scheduled registry (no separate retry set). Started/WIP, failed, deferred, and future scheduled jobs are excluded. Scheduled due bound is inclusive on both size and latency. Redis clients opened by the macros are closed after each sample.

### Removed

- `config.service` and all `tracking=` parameters (greenfield cutover, no migration shims).
- Construct-time lease `enabled=` freeze and sample-all-local-jql-on-grant behavior.

### Fixed

- Sample-wave token fencing on the lifecycle ports above: a raised `before_sample_job_queues` skips that adapter's `after_sample_job_queues` (successful `None` still gets after). Hook errors log and do not abort the wave. Soft-missing optional macros (`ImportError` → skip) leave no token and do not log as hook failures. Known adapters that fail to load log as unloadable rather than "unknown".
- Celery and RQ macros trim and de-duplicate queue names before sampling, matching the adapter contract so `job_queue_size("a", "a")` and padded names no longer double-count.
- RQ all-queues enumeration uses RQ's registered queue set (`SMEMBERS rq:queues`) instead of `KEYS rq:queue:*` / `KEYS rq:scheduled:*`, so each poll is not an O(keyspace) scan and queue names that contain `:` are no longer truncated.
- Lease renewal re-issues the process identity and drops any inherited grant when the process id changes (for example after a fork when the at-fork reinit did not run), matching Ruby.
- Dispatcher loop threads bind to a start generation, so a hung loop that outlives `stop()`'s join cannot resume work after a later `start()`.
- Celery RabbitMQ latency measurement always requeues the peeked message, even when parsing `run_at` fails, so a bad payload cannot leave a message unacked.
- An empty token (`""` in code or `HIREFIRE_TOKEN=""`) is treated as absent: the dispatcher does not start, middleware does not sample, and no empty `HireFire-Token` is sent. Assigning `config.token = ""` also forces reporting off when the env var is set.
- RQ and Celery Redis macros no longer call `.decode()` on replies that may already be `str` when the Redis URL sets `decode_responses=true` (or when the client otherwise returns strings). Keys, job ids, `enqueued_at`, and Celery queue payloads are normalized with a `bytes | str` helper first.
- A zero cgroup v2 CPU quota (`cpu.max` of `0 <period>`) now falls through to the next divisor source instead of reporting zero available CPUs and disabling CPU sampling.
- Internal dispatch pacing, lease renewal, and the CPU utilization delta now measure elapsed time on a monotonic clock, so a system clock adjustment (e.g. an NTP step) no longer skews the dispatch cadence, lease renewal, or a CPU reading. The metric timestamps themselves stay wall-clock, as the server requires.
- `X-Request-Start` parsing reads the leading numeric value and ignores any trailing content (matching the Ruby and Node clients), so a request behind a proxy chain that sets the header at two hops (folding it to `"<ts>, <ts>"`) still yields a queue-time sample instead of being dropped.
- A logger assigned to `config.logger` that raises from its logging method is now caught rather than propagated, so it can no longer escape a dispatcher or worker guard and halt metric reporting, or abort boot from `HireFire.configure()`.

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
