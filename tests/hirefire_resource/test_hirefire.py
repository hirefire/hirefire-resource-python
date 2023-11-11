from hirefire_resource import HireFire
from hirefire_resource.configuration import Configuration


def test_default_configuration():
    assert isinstance(HireFire.configuration, Configuration)


def test_configure():
    with HireFire.configure() as config:
        configuration = config
    assert configuration is HireFire.configuration
