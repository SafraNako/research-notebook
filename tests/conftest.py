import pytest

from arastirma_defteri import fakeworld
from arastirma_defteri import http as httpmod
from arastirma_defteri import library as libmod


def make_client(retries=1, timeout=5.0, **kwargs):
    """A client that can only talk to the loopback interface and never sleeps between retries."""
    return httpmod.HttpClient(allow_hosts={"127.0.0.1"}, sleep=lambda s: None, retries=retries, timeout=timeout, **kwargs)


@pytest.fixture(scope="session")
def world():
    return fakeworld.PlantedScholarship()


@pytest.fixture(scope="session")
def parsed(world):
    """Every planted record as the real parsers read it: {(source, id): Paper}."""
    return fakeworld.parse_all(world)


@pytest.fixture
def lib():
    from datetime import datetime, timezone
    clock = [datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)]
    library = libmod.Library(":memory:", clock=lambda: clock[0])
    library.clock_box = clock
    yield library
    library.close()
