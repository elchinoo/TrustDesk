import pytest
from fastapi.testclient import TestClient

from trustdesk import api


@pytest.fixture(scope="session")
def client():
    # The MCP session manager can only be started once per process, so share one client.
    with TestClient(api.app) as c:
        yield c
