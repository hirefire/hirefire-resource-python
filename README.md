## HireFire Integration Library for Python Applications

This library integrates Python applications with HireFire's Dyno Managers (Heroku Dyno Autoscalers). Instructions specific to supported web frameworks and worker libraries are provided during the setup process.

**Supported web frameworks:**

- Django
- Flask
- Quart
- FastAPI
- Starlette

**Supported worker libraries:**

- Celery
- RQ

---

Since 2011, over 1,000 companies have trusted [HireFire] to autoscale more than 5,000 applications hosted on [Heroku], managing over 10,000 web and worker dynos.

HireFire is distinguished by its support for both web and worker dynos, extending autoscaling capabilities to Standard-tier dynos. It provides fine-grained control over scaling behavior and improves scaling accuracy by monitoring more reliable metrics at the application level. These metrics include request queue time (web), job queue latency (worker), and job queue size (worker), which contribute to making more effective scaling decisions.

For more information, visit our [home page][HireFire].

---

## Development

- Run `bin/setup` to prepare the environment.
- Run `poetry shell` to boot up the development environment.
- See `paver --help` for common tasks.

## Release

1. Update the `version` property in `pyproject.toml`.
2. Ensure that `CHANGELOG.md` is up-to-date.
3. Commit changes with `git commit`.
4. Create a `git tag` matching the new version (e.g., `v1.0.0`).
5. Push the new git tag. Continuous Integration will handle the distribution process.

## License

This library is licensed under the terms of the MIT license.

[HireFire]: https://hirefire.io/
[Heroku]: https://heroku.com/
