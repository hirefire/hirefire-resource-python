import os

import pytest

HIREFIRE_TOKEN = "d2e39e50-82b1-478e-a457-5a53bfa153a1"


@pytest.fixture
def set_HIREFIRE_TOKEN():
    os.environ["HIREFIRE_TOKEN"] = HIREFIRE_TOKEN
    yield
    del os.environ["HIREFIRE_TOKEN"]
