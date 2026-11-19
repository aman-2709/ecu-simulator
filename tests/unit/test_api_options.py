import pytest

from ecu_simulator.api.options import ApiOptions, ApiStartupError, allowed_hosts, parse_api


@pytest.mark.parametrize(
    ("value", "host", "port"),
    [("127.0.0.1:8765", "127.0.0.1", 8765), ("localhost:8765", "127.0.0.1", 8765),
     ("::1:8765", "::1", 8765), ("[::1]:8765", "::1", 8765), ("127.0.0.1:0", "127.0.0.1", 0)],
)
def test_loopback_forms_are_accepted(value, host, port):
    assert parse_api(value, "p.yaml", "1.0") == ApiOptions(host, port, "p.yaml", "1.0")


@pytest.mark.parametrize(
    "value",
    ["0.0.0.0:8765", "192.168.1.20:8765", "127.0.0.2:8765", "example.com:8765", "[::]:8765"],
)
def test_non_loopback_hosts_are_refused(value):
    with pytest.raises(ApiStartupError, match="loopback"):
        parse_api(value, "p.yaml", "1.0")


@pytest.mark.parametrize("value", ["127.0.0.1", "127.0.0.1:", "127.0.0.1:x", "127.0.0.1:70000", "127.0.0.1:-1", ":8765", "127.0.0.1:²", "127.0.0.1:٣", "127.0.0.1:+80", "127.0.0.1: 80"])
def test_malformed_values_are_refused(value):
    with pytest.raises(ApiStartupError):
        parse_api(value, "p.yaml", "1.0")


def test_allowed_hosts():
    assert allowed_hosts("127.0.0.1", 8765) == {"127.0.0.1:8765", "localhost:8765"}
    assert allowed_hosts("::1", 8765) == {"[::1]:8765", "localhost:8765"}
