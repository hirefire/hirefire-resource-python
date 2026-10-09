FROM python:3.13-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends redis-server procps >/dev/null && rm -rf /var/lib/apt/lists/*
RUN pip install -q --no-cache-dir gunicorn gevent uvicorn uwsgi flask starlette 'celery[redis]' redis
COPY dist/*.whl /tmp/
RUN pip install -q --no-cache-dir /tmp/*.whl
WORKDIR /hosts
COPY tools/lib/fake_server.py tools/hosts/*.py /hosts/
CMD ["python", "run_hosts.py"]
