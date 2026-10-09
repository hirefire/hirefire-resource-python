import ssl


def pytest_sessionstart(session):
    ssl.create_default_context()
