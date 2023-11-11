from hirefire_resource.configuration import Configuration
from hirefire_resource import HireFire


def test_configure():
    assert None == HireFire.configuration

    with HireFire.configure() as config:
        pass

    assert isinstance(HireFire.configuration, Configuration)
