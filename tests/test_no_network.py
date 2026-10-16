"""The suite never reaches the network: the conftest guard refuses every connection."""

import socket

import pytest


def test_name_lookup_is_refused() -> None:
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("example.com", 443)


def test_connecting_is_refused() -> None:
    with socket.socket() as sock, pytest.raises(AssertionError, match="network"):
        sock.connect(("127.0.0.1", 9))
