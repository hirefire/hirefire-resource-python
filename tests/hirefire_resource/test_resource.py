from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration


def test_configure():
    assert None == HireFire.configuration

    with HireFire.configure() as config:
        pass

    assert isinstance(HireFire.configuration, Configuration)
