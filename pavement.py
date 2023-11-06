from paver.easy import sh
from paver.tasks import needs, task


@task
@needs(["format", "test"])
def default():
    pass


@task
def lint():
    sh("poetry run autoflake --remove-all-unused-imports -r --check .")
    sh("poetry run isort --check . && poetry run black --check .")
    sh("poetry run black --check .")


@task
def format():
    sh("poetry run autoflake --remove-all-unused-imports -ri .")
    sh("poetry run isort .")
    sh("poetry run black .")


@task
def coverage_report():
    sh("poetry run coverage report")


@task
def coverage_html():
    sh("poetry run coverage html")
    sh("open htmlcov/index.html")
