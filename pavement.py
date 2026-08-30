from paver.easy import sh
from paver.tasks import needs, task


@task
@needs(["format", "test"])
def default():
    pass


@task
def test():
    sh("tox")


@task
def check():
    sh("poetry run ruff check .")
    sh("poetry run ruff format --check .")
    sh("poetry run mypy")


@task
def format():
    sh("poetry run ruff check --fix .")
    sh("poetry run ruff format .")
