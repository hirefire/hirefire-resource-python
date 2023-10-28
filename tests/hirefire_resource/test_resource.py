from hirefire_resource import Configuration, Resource


def test_configure():
    assert None == Resource.configuration

    with Resource.configure() as config:
        pass

    assert isinstance(Resource.configuration, Configuration)
