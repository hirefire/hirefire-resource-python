from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.source.http import HTTP
from tests.helpers import at


def test_name():
    assert HTTP("api").name == "api"


def test_name_normalized_to_string():
    assert HTTP(123).name == "123"


def test_sample_buffers_request_queue_time():
    http = HTTP("web")

    with freeze_time(at(100)):
        http.sample(25)

    data = HireFire.configuration.buffer.flush()
    assert data["web"]["rqt"][100] == {"sum": 25.0, "count": 1}
