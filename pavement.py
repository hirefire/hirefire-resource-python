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
    sh("poetry run autoflake --jobs 1 --remove-all-unused-imports -r --check .")
    sh("poetry run isort --profile black --check .")
    sh("poetry run black --check .")
    sh("poetry run mypy")


@task
def format():
    sh("poetry run autoflake --jobs 1 --remove-all-unused-imports -ri .")
    sh("poetry run isort --profile black .")
    sh("poetry run black .")
