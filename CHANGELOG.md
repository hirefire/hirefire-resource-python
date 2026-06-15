## v1.1.0

Push-based metrics. The library now collects worker, web request-queue-time, and CPU metrics and pushes them outbound to `data.hirefire.io`, replacing the 1.x hybrid (HireFire polled the app's `/hirefire/<token>/info` endpoint for worker metrics; web request-queue-time was pushed to `logdrain.hirefire.io`). **This release is backwards-compatible: every existing 1.x configuration keeps working unchanged — upgrade by bumping the package.**

### Upgrading

* **No configuration change is required.** `config.dyno("web")` and `config.dyno("worker", callable)` parse and route exactly as before. The backend detects the agent version on the first push and transparently reads the pushed metrics instead of polling.
* **Restricted-egress networks must allowlist `data.hirefire.io` (outbound).** This is the one non-transparent part of the upgrade. The wire path changes: worker metrics flip from **inbound** (HireFire polled your app) to **outbound** (the library pushes to `data.hirefire.io`), so worker-only apps no longer need a reachable web process — but it is a new egress path. Web request-queue-time moves its destination from `logdrain.hirefire.io` to `data.hirefire.io`. If you allowlist outbound destinations (or previously whitelisted HireFire's inbound poller IPs), add `data.hirefire.io` before upgrading, or metrics will silently stop flowing while your config still looks correct. Open-egress apps (the vast majority) need no action.

### Added

* `config.service(name, tracking=...)` — a platform-neutral way to declare what a process tracks, for any platform (Heroku, Render, DigitalOcean, …). The name carries no meaning, so what to track is always explicit: `config.service("web", tracking="http")` for request metrics, `config.service("worker", callable)` for job metrics (the sampler is the signal), `config.service("clock", tracking="cpu")` for CPU. Passing both a `tracking` keyword and a sampler, or neither, raises. `config.dyno` is now exactly `config.service` plus the Heroku Procfile convention that the `web` name implies `tracking="http"`.
* `config.dyno` accepts an optional `tracking="cpu"` keyword — `config.dyno("web", tracking="cpu")`, `config.dyno("clock", tracking="cpu")` — to report CPU under that dyno name. The name still implies request metrics for `web` and job metrics when a sampler is given, so the 1.x forms are unchanged. `"cpu"` is the only value `config.dyno` accepts; use `config.service(name, tracking="http")` to declare an http process under a non-`web` name. `tracking` is keyword-only — the second positional argument is still the sampler, so the 1.x `config.dyno("worker", callable)` form keeps working.
* CPUActivity metrics (the `cpu` collector): self-samples the dyno's CPU utilization once per second and pushes it in the per-second samples format. CPU time is read from a cgroup counter where one exists (cgroup v2/v1 — Heroku Fir, Render, Docker, Kubernetes), else by summing `/proc/[pid]/stat` across the PID namespace (whole-dyno CPU on Heroku Cedar, which exposes no cpu cgroup), else the stdlib process clock (dev/macOS). Normalized by the cgroup CPU quota where present, else the Cedar shared-dyno entitlement inferred from the dyno's memory limit (512 MB → 1 core, 1 GB → 2 cores), else the processor count (dedicated dynos and dev machines). Gated by process identity (`HIREFIRE_SERVICE_NAME`, the Heroku `DYNO` name — both Cedar `web.1` and Fir pod-name formats — or `RENDER_SERVICE_NAME`) so a process only reports CPU under its own dyno name; unresolved identity disables CPU with a loud log rather than raising.
* Web liveness claims (heartbeats and backfilled empty seconds) are now gated by process identity: when the process's identity resolves and does not match the declared web dyno name, only real request samples are delivered and no liveness is synthesized. This prevents idle worker, one-off, and console processes from claiming web seconds — which could satisfy the RequestsPerMinute coverage check during a web outage and read it as zero traffic instead of missing data. When identity cannot be resolved, behavior is unchanged.
* Web metrics now claim every second between dispatches: seconds with no buffered samples are backfilled with explicit empty arrays (capped at 60 seconds, advancing only on successful delivery), so the server receives a complete per-second record — "alive with zero traffic" is reported as zero rather than left as a gap. Required for the RequestsPerMinute autoscaling strategy, whose coverage guard holds scaling when seconds go unreported.
* The dispatcher is now fork-aware: a forked worker (e.g. Gunicorn with `--preload`) detects that the inherited dispatcher thread did not survive the fork and starts a fresh one on the first request, instead of silently never dispatching.
* Dispatcher tick stages are isolated: a failing lease renewal or a raising job sampler no longer prevents the stages after it (CPU sampling, metric dispatch) from running. A raising sampler is logged and costs one sample window; a failed lease renewal is logged, revokes the local grant, and waits a full TTL before retrying.
* Worker samplers are validated: a raising sampler is isolated per worker, and non-numeric, negative, or non-finite return values are dropped with a logged error instead of being sent.
* Declaring a second http process now raises, under any name and across both `config.dyno` and `config.service` (request metrics come from this process's own http traffic, so only one http collector can exist per process). Duplicate-name detection spans both methods and is case-insensitive, matching the identity gates.
* `X-Request-Start` parsing now handles the nginx (`t=` + epoch seconds) and Apache (`t=` + epoch microseconds) formats in addition to Heroku's epoch milliseconds, and ignores unparseable or implausible values instead of producing an absurd queue-time sample.
* Timestamped buffers are bounded: when dispatch is starved (network outage), web/CPU seconds older than the 60-second server acceptance window are pruned at insert time, and worker samples keep only the latest value per name.
* A buffered payload that would exceed the server's 64 KB body limit — only reachable after a sustained delivery outage combined with a very high per-process request rate — is dropped, and dispatch resumes from the current second instead of retrying a payload that can never be delivered. The web watermark advances past the dropped seconds so they are left unclaimed (missing data) rather than backfilled as "alive with zero traffic".
* All transport errors (DNS, refused/reset connections, TLS) are mapped to a single `hirefire_resource.client.RequestError` and handled uniformly.

### Changed

* The library is **push-only**. The `/hirefire/<token>/info` (and `/hirefire`) pull endpoint is removed from the WSGI and ASGI middleware — it served the retired pull model. The middleware's sole job is now to read `X-Request-Start` and sample web request queue time; all other requests pass straight through.
* The push destination is `data.hirefire.io` (override with `HIREFIRE_DATA_URL`). The `HIREFIRE_DISPATCH_URL` environment variable (1.x web → logdrain override) is removed.
* The dispatcher now starts automatically when `HireFire.configure()` exits with a token present, so worker-only apps push without needing any web traffic. `HireFire.reset()` stops the dispatcher and replaces the configuration.
* Supported Python versions are now 3.11–3.14. Python 3.9 and 3.10 are no longer supported.

## v1.0.4

* Add `celery_app` parameter to `job_queue_size` and `async_job_queue_size` for priority queue support. This fixes RabbitMQ `PRECONDITION_FAILED` errors when querying queues configured with custom arguments like `x-max-priority`. The `celery_app` parameter allows extracting queue arguments from the app's `task_queues` configuration.

## v1.0.3

* Mitigate issue where measuring the Celery job queue size and job queue latency results in connection reset errors. If a connection is reset, the macro will attempt to reconnect and retry the operation up to 10 times over a span of 10 seconds before giving up. The ConnectionResetError typically resolves after the initial reconnection attempt, so this should help alleviate the issue.

## v1.0.2

* Fix issue where Django's HttpResponse object doesn't accept the headers keyword argument. Headers are now applied to the response object directly.

## v1.0.1

* Add support for dashes in `Worker` names to match the Procfile process naming format. `Worker` is implicitly used when configuring HireFire using the `Configuration#dyno` method.

## v1.0.0

* Initial release.
