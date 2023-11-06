from hirefire_resource.configuration import Configuration
from hirefire_resource.resource import Resource


def test_configure():
    assert None == Resource.configuration

    with Resource.configure() as config:
        pass

    assert isinstance(Resource.configuration, Configuration)
