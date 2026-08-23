# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The library now pushes metrics to `https://data.hirefire.io`. HireFire no longer polls the app.
- Request queue time is sampled automatically from HTTP traffic. You do not need a web `dyno` line.
- CPU activity is sampled automatically.
- Optional token-only setup with `HireFire.boot()`. Existing `config.dyno` job queue blocks still work.
- Count of jobs still being processed (`job_queue_working` / `async_job_queue_working`) for RQ.
- Dramatiq adapter: job queue size and job queue latency. Redis counts ready jobs plus due delayed jobs. RabbitMQ counts ready messages on the main queue only.
- Support Python 3.13 and 3.14.
- Support Django 5 and 6, Starlette 1, and RQ 2.
- The package now ships type hints.

### Changed

- Job queue macros count queued jobs plus scheduled or retry jobs that are due. Jobs already being processed are no longer included in job queue size or job queue latency.
- Celery job queue size counts only ready messages in the broker. Active, reserved, and inspect-based scheduled tasks are not included.
- Required Python is 3.11+. Official support is Django 4+, Flask 2+, Celery 5+, RQ 1+, and Dramatiq 2+.

### Deprecated

- Bare `config.dyno("web")` (no sampler) is deprecated. It does nothing. Request queue time is sampled automatically from HTTP traffic. You can remove the line. Leaving it does not break anything.

### Removed

- Serving `GET /hirefire/:token/info`.
- Official support for Python 3.9 and 3.10.
- Official support for Django 3.

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
