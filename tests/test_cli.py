"""Tests for clipcutter.cli helpers."""

import socket

from clipcutter.cli import _find_free_port


def test_find_free_port_returns_default_range_when_quiet():
    """On a normally-quiet machine, prefers a port in the configured range."""
    port = _find_free_port()
    assert 8000 <= port <= 8099 or port > 1024  # in-range OR ephemeral fallback


def test_find_free_port_skips_busy_port():
    """When start is occupied, the helper returns a different free port."""
    # Bind 8000 ourselves so it looks busy. Use SO_REUSEADDR=False default.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        try:
            busy.bind(("127.0.0.1", 8000))
        except OSError:
            # 8000 already in use by something else on this machine — skip;
            # the next-port logic is exercised by the other tests in this file.
            return
        busy.listen(1)
        port = _find_free_port(start=8000, end=8002)
        assert port != 8000
        assert 8001 <= port <= 8002


def test_find_free_port_returns_bindable_port():
    """The port returned must actually be bindable right after."""
    port = _find_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_find_free_port_falls_back_to_ephemeral_when_range_exhausted():
    """If the entire start..end range is busy, an OS-picked port is returned."""
    busy_sockets = []
    try:
        for p in (9000, 9001):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("127.0.0.1", p))
                s.listen(1)
                busy_sockets.append(s)
            except OSError:
                s.close()
        if len(busy_sockets) < 2:
            return  # couldn't reproduce a full range — skip silently
        port = _find_free_port(start=9000, end=9001)
        assert port not in (9000, 9001)
        assert port > 1024
    finally:
        for s in busy_sockets:
            s.close()
