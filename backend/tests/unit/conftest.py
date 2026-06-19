import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Override the root DB setup fixture — unit tests need no Postgres."""
    yield
