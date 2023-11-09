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
def test_py312():
    sh(
        "tox -e py312-core,py312-django4,py312-django3,py312-flask3,py312-flask2,py312-quart,py312-fastapi,py312-starlette,py312-rq"
    )


@task
def lint():
    sh("autoflake --remove-all-unused-imports -r --check .")
    sh("isort --profile black --check . && poetry run black --check .")
    sh("black --check .")


@task
def format():
    sh("autoflake --remove-all-unused-imports -ri .")
    sh("isort --profile black .")
    sh("black .")


@task
def coverage_report():
    sh("coverage report")


@task
def coverage_html():
    sh("coverage html")
    sh("open htmlcov/index.html")
