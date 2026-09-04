# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Job metrics are pushed to HireFire instead of being read from a poll of the app.
- CPU activity is sampled automatically on supported platforms when the process is identified.
- `HireFire.boot()` starts metric collection when a token is set. `HireFire.reset()` stops the dispatcher and clears configuration.
- `config.token` can set the HireFire token in code. 1.x read only `HIREFIRE_TOKEN`.
- `HIREFIRE_SERVICE_NAME` sets the process name only on platforms that do not detect it automatically. On Heroku, `DYNO` is used.
- `HIREFIRE_CELERY_BROKER_URL`, `HIREFIRE_RQ_URL`, and `HIREFIRE_DRAMATIQ_URL` (optional `HIREFIRE_DRAMATIQ_NAMESPACE`) set the broker URL for job queue samples.
- `job_queue_working` / `async_job_queue_working` report how many jobs are currently in progress for RQ.
- Dramatiq adapter: job queue size and job queue latency (Redis queued plus delayed jobs that are due, RabbitMQ on the main queue only).
- Support Python 3.13 and 3.14.
- Support Django 5 and 6, Starlette 1, and RQ 2.
- The package now ships PEP 561 type hints (`py.typed`).

### Changed

- Request queue time is sampled automatically from HTTP traffic. `config.dyno("web")` is not required.
- Celery `job_queue_size` counts only ready messages in the broker. Active, reserved, and inspect-based scheduled tasks are not included.
- Official Python support is 3.11+. Official Django support is 4+.
- A Celery connection reset is retried once immediately. The sample no longer sleeps up to 9 seconds.
- Process names may be any non-empty string up to 128 bytes. The 1.x letter-start charset and 30-character cap are gone.
- `config.dyno` without a sampler raises `MissingSamplerError` except when the name is `"web"` (1.x raised `MissingDynoProcError`). Duplicate dyno names raise `DuplicateDynoError`.

### Deprecated

- Bare `config.dyno("web")` (no sampler) is deprecated. It does nothing. Request queue time is sampled automatically from HTTP traffic. The line can be removed. Leaving it does not break anything.

### Removed

- Serving `GET /hirefire/:token/info` and `GET /hirefire` when the token matched.
- Official support for Python 3.9 and 3.10.
- Official support for Django 3.

### Fixed

- Request queue time ignores samples older than 60 seconds.
- Celery queue samples time out after 5 seconds when the broker does not respond.
- A Celery broker that is down no longer reports job queue size or latency as 0.
- Celery Redis latency skips corrupt JSON instead of raising. Celery RabbitMQ latency always requeues the peeked message, even when the header parse fails.
- RQ job queue latency skips an unreadable job timestamp instead of failing the whole sample.
- RQ Redis samples time out after 5 seconds when the broker does not respond.
- RQ samples with no queue names use RQ's registered queue set instead of scanning Redis with `KEYS`.

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
