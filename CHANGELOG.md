# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The library now pushes metrics to `https://data.hirefire.io`.
- Request queue time is sampled from HTTP traffic through the middleware. A web `dyno` line is not required.
- CPU activity is sampled automatically.
- Automatic request queue time and CPU sampling need a process identity (`HIREFIRE_SERVICE_NAME` or `DYNO`).
- Optional token-only setup with `HireFire.boot()`. Existing `config.dyno` job queue blocks still work.
- `HireFire.reset` and `Configuration.stop_dispatcher` stop the background dispatcher.
- `HIREFIRE_CELERY_BROKER_URL`, `HIREFIRE_RQ_URL`, and `HIREFIRE_DRAMATIQ_URL` (optional `HIREFIRE_DRAMATIQ_NAMESPACE`) set the broker URL for job queue samples.
- Count of jobs still being processed (`job_queue_working` / `async_job_queue_working`) for RQ.
- Dramatiq adapter: job queue size and job queue latency (Redis queued plus delayed jobs that are due, RabbitMQ on the main queue only).
- Support Python 3.13 and 3.14.
- Support Django 5 and 6, Starlette 1, and RQ 2.
- The package now ships type hints.

### Changed

- Metrics are sent only when `HIREFIRE_TOKEN` is set.
- Job queue metrics are sampled by one process at a time.
- RQ and Dramatiq Redis job queue macros count queued jobs plus scheduled or retry jobs that are due. Jobs already being processed are no longer included in job queue size or job queue latency.
- Celery job queue size counts only ready messages in the broker. Active, reserved, and inspect-based scheduled tasks are not included.
- Required Python is 3.11+. Official Django support is 4+.
- A Celery connection reset is retried once immediately. The sample no longer sleeps up to 9 seconds.
- Process names allow any non-empty string up to 128 bytes. The 1.x letter-start charset and 30-character cap are gone.
- `config.dyno` without a sampler raises `MissingSamplerError` (1.x raised `MissingDynoProcError`).
- `HIREFIRE_VERBOSE` still prints dispatch diagnostics and now also prints sample-path timings.

### Deprecated

- Bare `config.dyno("web")` (no sampler) is deprecated. It does nothing. Request queue time is sampled automatically from HTTP traffic. You can remove the line. Leaving it does not break anything.

### Removed

- Serving `GET /hirefire/:token/info`.
- `POST` of request queue time JSON to `logdrain.hirefire.io`.
- `HIREFIRE_DISPATCH_URL` no longer overrides ingest. The internal override is `HIREFIRE_DATA_URL`.
- In-source docstrings on the public API. See the README and changelog.
- Official support for Python 3.9 and 3.10.
- Official support for Django 3.

### Fixed

- A forked child no longer closes the parent's HTTP keep-alive connection.
- Dramatiq RabbitMQ samples now fail within five seconds when the broker does not complete the handshake, and the sample is dropped instead of recorded as an empty queue.
- A forked child no longer deadlocks when a lease lock was held across `fork`.
- A reused HTTP connection that returns a truncated body is retried once.
- Sampler error logs redact passwords in `user:pass@` connection URLs.
- An oversized numeric lease header is treated as malformed instead of raising into the host.
- Celery queue samples time out after 5 seconds when the broker does not respond.
- RQ job queue latency skips an unreadable job timestamp instead of dropping the whole sample.
- RQ and Dramatiq Redis samples time out after 5 seconds when the broker does not respond.

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
