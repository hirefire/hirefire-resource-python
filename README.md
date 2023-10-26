# Python Agent (Autoscale.app)

Provides [Autoscale.app] with the necessary metrics for autoscaling web and worker processes.

## Installation

Install the package:

    pip install autoscale-agent

## Usage

This package may be used as a stand-alone agent, or as middleware that integrates with [Django] and [Flask].

Installation instructions are provided during the autoscaler setup process on [Autoscale.app].

## Related Packages

The following packages are currently available.

#### Queues (Worker Metric Functions)

| Worker Library | Repository                                           |
|----------------|------------------------------------------------------|
| Celery         | https://github.com/autoscale-app/python-queue-celery |
| RQ             | https://github.com/autoscale-app/python-queue-rq     |

Let us know if your preferred worker library isn't available and we'll see if we can add support.

## Development

Prepare environment:

    pip install poetry
    poetry install

Boot the shell:

    poetry shell

See Paver for relevant tasks:

    paver --help

## Release

1. Update `pyproject.toml`
2. Update `hirefire_resource/__init__.py`
3. Update `CHANGELOG.md`
4. Create a new git tag (`v1.2.3`)
5. Push the new git tag

## Contributing

Bug reports and pull requests are welcome on GitHub at https://github.com/autoscale-app/python-agent

[Autoscale.app]: https://autoscale.app
[Django]: https://www.djangoproject.com
[Flask]: https://palletsprojects.com/p/flask/
[Celery]: https://docs.celeryq.dev/en/stable/
[RQ]: https://python-rq.org
