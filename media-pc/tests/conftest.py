"""The HA test plugin (auto-loaded from requirements_test.txt) blocks sockets and fails on leftover
threads. These stdlib tests run real loopback servers, so undo both for this directory only."""
import pytest
import pytest_socket


@pytest.fixture(autouse=True, scope="module")
def _sockets_for_setup_class():
    pytest_socket.enable_socket()  # runs before unittest's setUpClass fixture


@pytest.fixture(autouse=True)
def _sockets_per_test():
    pytest_socket.enable_socket()  # the HA plugin disables sockets again before every test


@pytest.fixture
def verify_cleanup():
    yield  # overrides the HA plugin's autouse leak check (daemon handler threads outlive a request)
