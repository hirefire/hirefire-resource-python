from freezegun import freeze_time

from hirefire_resource import HireFire
from hirefire_resource.buffer import Buffer
from hirefire_resource.source.http import HTTP
from tests.helpers import at


def test_name():
    assert HTTP("api", HireFire.configuration.buffer).name == "api"


def test_sample_buffers_request_queue_time():
    http = HTTP("web", HireFire.configuration.buffer)

    with freeze_time(at(100)):
        http.sample(25)

    data = HireFire.configuration.buffer.flush()
    assert data["web"]["rqt"][100] == {"sum": 25.0, "count": 1}


def test_name_normalized_to_string():
    assert HTTP(123, HireFire.configuration.buffer).name == "123"


def test_samples_go_to_the_buffer_it_was_given():
    buffer = Buffer()

    with freeze_time(at(100)):
        HTTP("web", buffer).sample(25)

    assert HireFire.configuration.buffer.flush() == {}
    assert buffer.flush() == {"web": {"rqt": {100: {"sum": 25.0, "count": 1}}}}
