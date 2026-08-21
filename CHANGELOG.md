# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Push job-queue and request-queue-time metrics to `https://data.hirefire.io` (lease plus nested ingest) so HireFire no longer polls the app.
- Always-on request queue time on the HTTP middleware path, and always-on CPU when process identity resolves (`DYNO`, `HIREFIRE_SERVICE_NAME`, or `RENDER_SERVICE_NAME`).
- `HireFire.boot()` for token-only zero-config. Local `config.dyno` job-queue samplers remain for custom probes and root installs.
- Job-queue working count (`job_queue_working` / `async_job_queue_working`) and nested `wrk` beside `jql`/`jqs` for RQ.
- Lease collection plans: the server grant can drive allowlisted macros (`celery`, `rq`, `dramatiq`). Strategy-only entries still run the matching local `config.dyno` sampler.
- Dramatiq first-party adapter (`dramatiq` plan key): waiting-only size and latency on Redis (live plus due delayed) and RabbitMQ (main-queue ready).

### Changed

- Job-queue macros count only the waiting set (live plus due scheduled plus due retry). In-flight jobs are no longer included in JQL or JQS.
- Celery size is broker-ready only (no active, reserved, or inspect-based scheduled). RQ size and latency exclude started, failed, deferred, and future scheduled jobs.
- Required Python is 3.11+.

### Deprecated

- `config.log_queue_metrics = True` still prints `[hirefire:router] queue=<N>ms` for Logplex QueueTime. Setting it once-warns to prefer HireFire Request Queue Time plus `HIREFIRE_TOKEN`.
- Bare `config.dyno("web")` (no sampler) is a once-warn no-op. Request queue time is armed by platform web identity and HTTP middleware traffic.

### Removed

- Serving `GET /hirefire/:token/info`. Job metrics are push-only.
- Official support for Python 3.9 and 3.10.

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
