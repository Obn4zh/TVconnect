"""LAN discovery of TV agents: mDNS/DNS-SD browsing plus UDP broadcast probing as a fallback."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
from dataclasses import dataclass
from typing import Callable

from ..protocol.constants import DEFAULT_PORT, DISCOVER_MAGIC, DISCOVERY_PORT, MDNS_TYPE

log = logging.getLogger(__name__)


@dataclass
class FoundTv:
    tv_id: str
    name: str
    host: str
    port: int
    model: str
    source: str  # "mdns" | "udp"


def local_ipv4_addresses() -> list[str]:
    addrs: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    # The address used for the default route (no packets are sent by connect() on UDP).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127.") and not a.startswith("169.254."))


class Discovery:
    def __init__(self, on_found: Callable[[FoundTv], None]) -> None:
        self.on_found = on_found
        self._zc = None
        self._browser = None
        self._stop = threading.Event()

    def start(self) -> None:
        try:
            from zeroconf import ServiceBrowser, Zeroconf

            self._zc = Zeroconf()
            self._browser = ServiceBrowser(self._zc, MDNS_TYPE, handlers=[self._on_mdns])
            log.info("mDNS browsing %s", MDNS_TYPE)
        except Exception as e:  # zeroconf can fail on hosts without multicast
            log.warning("mDNS unavailable: %s — only UDP broadcast and manual IP will work", e)
        self.probe()

    def stop(self) -> None:
        self._stop.set()
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception as e:
                log.debug("zeroconf close: %s", e)
            self._zc = None

    # ---- mDNS ----

    def _on_mdns(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange

        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            threading.Thread(target=self._resolve, args=(zeroconf, service_type, name), daemon=True).start()

    def _resolve(self, zc, service_type: str, name: str) -> None:
        try:
            info = zc.get_service_info(service_type, name, timeout=3000)
        except Exception as e:
            log.debug("mDNS resolve failed for %s: %s", name, e)
            return
        if not info:
            return
        props = {
            (k.decode(errors="replace") if isinstance(k, bytes) else k): (
                v.decode(errors="replace") if isinstance(v, bytes) else (v or "")
            )
            for k, v in (info.properties or {}).items()
        }
        addrs = [a for a in info.parsed_addresses() if ":" not in a]
        if not addrs:
            return
        tv = FoundTv(
            tv_id=props.get("id", ""),
            name=props.get("name") or name.split(".")[0],
            host=addrs[0],
            port=int(info.port or DEFAULT_PORT),
            model=props.get("model", ""),
            source="mdns",
        )
        log.info("mDNS found %s at %s:%d", tv.name, tv.host, tv.port)
        self.on_found(tv)

    # ---- UDP broadcast ----

    def probe(self) -> None:
        threading.Thread(target=self._udp_probe, name="udp-probe", daemon=True).start()

    def _udp_probe(self) -> None:
        targets = {"255.255.255.255"}
        for ip in local_ipv4_addresses():
            try:
                targets.add(str(ipaddress.ip_network(f"{ip}/24", strict=False).broadcast_address))
            except ValueError:
                pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(0.5)
                for _ in range(2):
                    for t in targets:
                        try:
                            s.sendto(DISCOVER_MAGIC, (t, DISCOVERY_PORT))
                        except OSError as e:
                            log.debug("broadcast to %s failed: %s", t, e)
                    deadline = 3
                    while deadline > 0 and not self._stop.is_set():
                        try:
                            data, addr = s.recvfrom(4096)
                        except socket.timeout:
                            deadline -= 1
                            continue
                        self._handle_announce(data, addr[0])
        except OSError as e:
            log.warning("UDP discovery failed: %s", e)

    def _handle_announce(self, data: bytes, host: str) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(msg, dict) or msg.get("type") != "tvc_announce":
            return
        try:
            port = int(msg.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            return
        tv = FoundTv(
            tv_id=str(msg.get("tv_id", ""))[:64],
            name=str(msg.get("name", host))[:64],
            host=host,
            port=port,
            model=str(msg.get("model", ""))[:64],
            source="udp",
        )
        log.info("UDP found %s at %s:%d", tv.name, tv.host, tv.port)
        self.on_found(tv)
