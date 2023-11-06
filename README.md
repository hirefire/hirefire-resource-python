## HireFire: Advanced Autoscaling for Heroku Applications

[HireFire] is the oldest and a leading autoscaling service for applications hosted on [Heroku]. Since 2011, we've assisted more than 1,000 companies in autoscaling upwards of 5,000 applications, involving over 10,000 dynos.

This package streamlines the integration of HireFire with Python applications running on Heroku, offering companies substantial cost savings while maintaining optimal performance.

---

### Supported Python Versions:

|    | Python |
|----|--------|
| ✅ | 3.12   |
| ✅ | 3.11   |
| ✅ | 3.10   |
| ✅ | 3.9    |
| ✅ | 3.8    |

Older versions might work, but aren't officially supported.

---

### Supported Python Web Frameworks:

HireFire comes with WSGI and ASGI middleware integration, making it compatible with the following frameworks:

| Python Web Framework | WSGI | ASGI |
|----------------------|------|------|
| Django               | ✅   | ✅   |
| Flask                | ✅   | ❌   |
| Quart                | ❌   | ✅   |
| FastAPI              | ❌   | ✅   |
| Starlette            | ❌   | ✅   |

---

### Supported Python Worker Libraries:

If your preferred library isn't listed, or if you need further support, please contact us.

| Python Worker Library | Job Queue Latency | Job Queue Size |
|-----------------------|:-----------------:|:--------------:|
| RQ                    | ✅                | ✅             |
| Celery                | ❌                | ❌             |

---

### Integration Demonstration

To easily integrate HireFire with an existing Python application (e.g., Django and RQ), follow these steps:

1. Install the package:

```sh
pip install hirefire-resource
```

2. Configure HireFire in Django's `settings.py`:

```python
from hirefire_resource import Resource
from hirefire_resource.macro.rq import job_queue_latency

with Resource.configure() as config:
    # To collect Request Queue Time metrics for autoscaling `web` dynos:
    config.dyno("web")
    # To collect Job Queue Latency metrics for autoscaling `worker` dynos:
    config.dyno("worker", lambda: job_queue_latency("default"))

MIDDLEWARE = [
    # Inject as high up in the stack as possible
    # for accurate Request Queue Time measurement.
    "hirefire_resource.middleware.django.Middleware"
]
```

After completing these steps, deploy your application to Heroku. Then, [sign into HireFire] to complete your autoscaling setup by adding the web and worker dyno managers.

---

## Development

### Setup Environment

Execute `bin/setup` to install the necessary dependencies. Inspect `bin/setup` before running to see what operations will be executed.

### Running Tests

Use `tox` to run the tests. See `tox.ini`.

### Local Installation

Install this package on your local machine using `pip install .`.

### Releasing a new version

1. Bump the `version` property in `pyproject.toml`.
2. Update `CHANGELOG.md` for the next version.
3. Commit changes with `git commit`.
4. Create a new git tag matching the version (e.g., `v1.0.0`) with `git tag`.
5. Push the new tag. GitHub Actions will handle the package publishing process from there.

---

### Questions?

Feel free to [contact us] for support and inquiries.

---

### License

`hirefire-resource` is licensed under the MIT license. See LICENSE.

[HireFire]: https://www.hirefire.io/
[Heroku]: https://www.heroku.com/
[sign into HireFire]: https://manager.hirefire.io/login
[contact us]: mailto:support@hirefire.io
