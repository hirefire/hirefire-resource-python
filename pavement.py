from paver.easy import sh
from paver.tasks import needs, task


@task
@needs(["format", "test"])
def default():
    pass


@task
def test():
    sh("pytest --cov=hirefire_resource --cov-report=html tests/")


@task
def check():
    sh("autoflake --remove-all-unused-imports -r --check .")
    sh("isort --profile black --check . && poetry run black --check .")
    sh("black --check .")


@task
def format():
    sh("autoflake --remove-all-unused-imports -ri .")
    sh("isort --profile black .")
    sh("black .")


@task
def doc():
    sh(f"sphinx-build -M html docs docs/_build")
