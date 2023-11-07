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
def lint():
    sh("autoflake --remove-all-unused-imports -r --check .")
    sh("isort --check . && poetry run black --check .")
    sh("black --check .")


@task
def format():
    sh("autoflake --remove-all-unused-imports -ri .")
    sh("isort .")
    sh("black .")


@task
def coverage_report():
    sh("coverage report")


@task
def coverage_html():
    sh("coverage html")
    sh("open htmlcov/index.html")
