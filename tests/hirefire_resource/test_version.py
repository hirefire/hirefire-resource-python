from hirefire_resource.version import VERSION

def test_version():
    assert VERSION
    assert isinstance(VERSION, str)
    assert "." in VERSION
    assert VERSION.count(".") == 2
    assert VERSION.split(".")[0].isdigit()
    assert VERSION.split(".")[1].isdigit()
    assert VERSION.split(".")[2].isdigit()
