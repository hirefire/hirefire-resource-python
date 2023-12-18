## HireFire: Advanced Autoscaling for Heroku-hosted Applications

[HireFire] is the oldest autoscaling service for applications running on [Heroku]. Since 2011, we've assisted more than 1,000 companies in autoscaling upwards of 5,000 applications, with over 10,000 dynos.

This package collects metrics from Python applications running on Heroku and provides them to HireFire in order to autoscale web and worker dynos.

## Guides & Documentation

Please refer to our [Python Guide] for instructions on setting up HireFire with your Python application.

## Development

Run `bin/setup` to prepare the environment.

Run `poetry shell` to boot up the development environment.

See `paver --help` for common tasks.

## Release

1. Update the `version` property in `pyproject.toml`.
2. Ensure that `CHANGELOG.md` is up-to-date.
3. Commit changes with `git commit`.
4. Create a `git tag` matching the new version (e.g., `v1.0.0`).
5. Push the new git tag. Continuous Integration will handle the distribution process.

## License

This package is licensed under the MIT license. See LICENSE.

[HireFire]: https://www.hirefire.io/
[Heroku]: https://www.heroku.com/
[Python Guide]: https://help.hirefire.io/TODO
