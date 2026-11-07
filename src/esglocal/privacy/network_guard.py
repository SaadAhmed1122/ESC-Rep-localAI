"""Process-wide outbound network guard.

When installed, any attempt by *this Python process* (our code or any
third-party library) to resolve or connect to a non-local host raises
:class:`NetworkBlockedError`. Loopback (127.0.0.0/8, ::1) and Unix sockets are
always allowed so the app can reach the local model runtime. Additional hosts
(e.g. an Ollama container on a private Docker network) can be allow-listed.

This is defence in depth on top of the app simply never making external calls.
It does not sandbox other processes (e.g. the Ollama daemon itself) — see
docs/PRIVACY.md for OS-level firewall guidance.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from collections.abc import Iterable

from esglocal.config import is_loopback_host


class NetworkBlockedError(ConnectionRefusedError):
    """Raised when offline mode blocks a non-local connection."""


_lock = threading.Lock()
_installed = False
_allowed_hosts: set[str] = set()
_allowed_ips: set[str] = set()
_blocked_attempts: list[str] = []
_orig_getaddrinfo = socket.getaddrinfo
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_sendto = socket.socket.sendto
_orig_sendmsg = getattr(socket.socket, "sendmsg", None)


def _host_str(host) -> str:
    if isinstance(host, bytes):
        host = host.decode("idna", errors="ignore")
    return str(host or "").strip("[]").lower()


def _ip_allowed(ip: str) -> bool:
    ip = ip.split("%", 1)[0]  # strip IPv6 zone id
    if ip in _allowed_ips:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    # IPv4-mapped loopback (::ffff:127.0.0.1)
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _record(target: str) -> None:
    if len(_blocked_attempts) < 100:
        _blocked_attempts.append(target)


def _guarded_getaddrinfo(host, *args, **kwargs):
    h = _host_str(host)
    if not h or is_loopback_host(h) or _ip_allowed(h):
        return _orig_getaddrinfo(host, *args, **kwargs)
    if h in _allowed_hosts:
        infos = _orig_getaddrinfo(host, *args, **kwargs)
        for info in infos:
            _allowed_ips.add(str(info[4][0]))
        return infos
    _record(h)
    raise NetworkBlockedError(f"Offline mode: DNS lookup of non-local host {h!r} blocked")


def _check_address(sock: socket.socket, address) -> None:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return
    if isinstance(address, tuple) and address:
        host = _host_str(address[0])
        if is_loopback_host(host) or _ip_allowed(host):
            return
        _record(host)
        raise NetworkBlockedError(f"Offline mode: connection to non-local address {host!r} blocked")


def _guarded_connect(self, address):
    _check_address(self, address)
    return _orig_connect(self, address)


def _guarded_connect_ex(self, address):
    _check_address(self, address)
    return _orig_connect_ex(self, address)


def _guarded_sendto(self, data, *args):
    # sendto(data, address) or sendto(data, flags, address) — unconnected UDP egress
    if args:
        _check_address(self, args[-1])
    return _orig_sendto(self, data, *args)


def _guarded_sendmsg(self, buffers, ancdata=(), flags=0, address=None):
    if address is not None:
        _check_address(self, address)
        return _orig_sendmsg(self, buffers, ancdata, flags, address)
    return _orig_sendmsg(self, buffers, ancdata, flags)


def install(allowed_hosts: Iterable[str] = ()) -> None:
    """Block all non-local outbound connections from this process."""
    global _installed
    with _lock:
        _allowed_hosts.update(h.lower() for h in allowed_hosts)
        if _installed:
            return
        socket.getaddrinfo = _guarded_getaddrinfo
        socket.socket.connect = _guarded_connect
        socket.socket.connect_ex = _guarded_connect_ex
        socket.socket.sendto = _guarded_sendto
        if _orig_sendmsg is not None:
            socket.socket.sendmsg = _guarded_sendmsg
        _installed = True


def uninstall() -> None:
    global _installed
    with _lock:
        socket.getaddrinfo = _orig_getaddrinfo
        socket.socket.connect = _orig_connect
        socket.socket.connect_ex = _orig_connect_ex
        socket.socket.sendto = _orig_sendto
        if _orig_sendmsg is not None:
            socket.socket.sendmsg = _orig_sendmsg
        _allowed_hosts.clear()
        _allowed_ips.clear()
        _installed = False


def is_installed() -> bool:
    return _installed


def blocked_attempts() -> list[str]:
    return list(_blocked_attempts)
