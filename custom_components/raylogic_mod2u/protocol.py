"""
Raylogic GO Protocol - MOD2U driver (2-channel WiFi smart switch, relay type).

Ye aapke H81 (8-channel dimmer) integration jaisa hi architecture use karta
hai (TCP socket, keep-alive, per-device sequence counter, listener-based
feedback sync) - lekin MOD2U alag command bytes use karta hai, jo aapne
khud Docklight se capture kiya:

    * K A = 1 <CR>                                    -> keep-alive
    101,559,*AR=001A0C0201<CR>   -> CHANNEL 1 ON
    101,560,*AR=001A0C0101<CR>   -> CHANNEL 1 OFF
    101,561,*AR=001A0C0202<CR>   -> CHANNEL 2 ON
    101,562,*AR=001A0C0102<CR>   -> CHANNEL 2 OFF

Frame format: <ID>,<SeqNo>,*AR=<AddrHigh><Cmd:1A><Area><Level><AddrLow><CR>

    AddrHigh = 00                  (MOD2U sirf 2 channels hai, high byte
                                     hamesha 00 - 256 se zyada channels ki
                                     zaroorat nahi)
    Cmd      = 1A                  (AREA CHANNEL DIRECT - H81 jaisa hi)
    Area     = 0C                  (MOD2U ka apna area byte - H81 (02) se
                                     ALAG hai, Docklight capture se confirmed)
    Level    = 01 = OFF, 02 = ON   (RELAY type channel - PDF "AREA CHANNEL
                                     DIRECT" section confirm karta hai:
                                     Switching/Relay channels ke liye
                                     0x01=OFF, 0x02=ON. Ye H81 ke dimmer
                                     wale 01=ON/FF=OFF se ULTA/ALAG hai -
                                     isliye H81 ka protocol.py copy-paste
                                     karke reuse nahi ho sakta, alag file
                                     zaroori thi)
    AddrLow  = Channel number       (Channel 1 = 01, Channel 2 = 02)

ID prefix (101) bhi H81 (002) se alag hai - ye MOD2U device khud ka ID hai,
jaisa Docklight capture mein dikha. Agar future mein aap dusra MOD2U device
add karo aur uska ID/area Docklight capture mein alag mile, to
configuration.yaml mein per-device override kar sakte ho (device_id, area).

Sequence number 001 se 999 tak counts hota hai, phir 001 pe wrap/restart ho
jaata hai (aapne confirm kiya) - H81 jaisa hi persist hota hai
(device_state/<device>_seq.json mein) taaki HA restart ke baad bhi seq
sequence na tute.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# ============================================================
# Protocol-level constants (MOD2U ke liye specific - Docklight capture se)
# ============================================================
CMD_CHANNEL_DIRECT = "1A"
LEVEL_ON = "02"             # RELAY: 0x02 = ON (PDF confirms, H81 dimmer se ALAG)
LEVEL_OFF = "01"            # RELAY: 0x01 = OFF (PDF confirms, H81 dimmer se ALAG)

CHANNELS_PER_DEVICE = 2     # MOD2U = 2-channel relay switch

DEFAULT_AREA = "0C"         # Docklight capture se confirmed
DEFAULT_DEVICE_ID = 101     # Docklight capture se confirmed ("101,559,...")

TIMEOUT = 3
KEEPALIVE_CMD = b"*KA=1\r"
KEEPALIVE_INTERVAL = 5      # seconds - H81 jaisa hi, idle-timeout avoid karne ke liye

SEQ_MIN = 1
SEQ_MAX = 999                # device sirf 3-digit sequence field accept karta hai (001-999, phir wrap)

_STATE_DIR = Path(__file__).parent / "device_state"
_STATE_DIR.mkdir(exist_ok=True)


def _channel_to_hex(channel: int) -> tuple[str, str]:
    """Channel number (1 ya 2) ko address high/low byte mein todo. high hamesha 00."""
    addr = channel & 0xFFFF
    high = (addr >> 8) & 0xFF
    low = addr & 0xFF
    return f"{high:02X}", f"{low:02X}"


def _level_hex(is_on: bool) -> str:
    return LEVEL_ON if is_on else LEVEL_OFF


def _parse_incoming(frame_text: str) -> tuple[int, bool] | None:
    """Ek raw incoming line parse karo - "*AR=..." ya "+AR=..." dono handle karta hai."""
    frame_text = frame_text.strip()
    if not frame_text:
        return None

    hex_part = None
    for prefix in ("*AR=", "+AR="):
        idx = frame_text.find(prefix)
        if idx == -1:
            continue
        candidate = frame_text[idx + len(prefix):].strip()
        if len(candidate) >= 10:
            hex_part = candidate[:10]
            break
    if hex_part is None:
        return None

    high, cmd, area, level, low = (
        hex_part[0:2],
        hex_part[2:4],
        hex_part[4:6],
        hex_part[6:8],
        hex_part[8:10],
    )
    if cmd.upper() != CMD_CHANNEL_DIRECT:
        return None
    try:
        channel = (int(high, 16) << 8) | int(low, 16)
    except ValueError:
        return None
    is_on = level.upper() == LEVEL_ON
    return channel, is_on


# ============================================================
# RaylogicMod2uDevice - ek physical MOD2U switch = ek TCP connection, apna
# sequence counter, apne listeners.
# ============================================================
class RaylogicMod2uDevice:
    def __init__(
        self,
        ip: str,
        port: int,
        area: str = DEFAULT_AREA,
        device_id: int = DEFAULT_DEVICE_ID,
        name: str = "",
    ) -> None:
        self.ip = ip
        self.port = port
        self.area = area
        self.device_id = device_id
        self.name = name or f"Raylogic MOD2U {ip}"
        self.key = f"{ip.replace('.', '_')}_{port}"

        self._sock: socket.socket | None = None
        self._conn_lock = threading.Lock()
        self._keepalive_started = False
        self._receiver_started = False
        self._recv_buf = b""

        self._seq_lock = threading.Lock()
        self._seq_file = _STATE_DIR / f"{self.key}_seq.json"

        self._listener_lock = threading.Lock()
        self._listeners: dict[int, list] = {}

    # -------------------- sequence number (per-device, 001-999 wrap) --------------------
    def _load_seq(self) -> int:
        try:
            data = json.loads(self._seq_file.read_text())
            seq = int(data.get("seq", SEQ_MIN))
            if seq < SEQ_MIN or seq > SEQ_MAX:
                seq = SEQ_MIN
            return seq
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return SEQ_MIN

    def _save_seq(self, seq: int) -> None:
        try:
            self._seq_file.write_text(json.dumps({"seq": seq}))
        except OSError as err:
            _LOGGER.warning("Raylogic MOD2U [%s]: sequence save fail: %s", self.name, err)

    def _next_seq(self) -> int:
        with self._seq_lock:
            seq = self._load_seq()
            next_seq = seq + 1 if seq < SEQ_MAX else SEQ_MIN
            self._save_seq(next_seq)
            return seq

    # -------------------- connection --------------------
    def _connect_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = socket.create_connection((self.ip, self.port), timeout=TIMEOUT)
        self._sock.settimeout(TIMEOUT)
        self._recv_buf = b""
        _LOGGER.debug("Raylogic MOD2U [%s]: TCP connection (re)established to %s:%s", self.name, self.ip, self.port)

    def _ensure_background_threads(self) -> None:
        if not self._keepalive_started:
            self._keepalive_started = True
            threading.Thread(target=self._keepalive_loop, daemon=True).start()
        if not self._receiver_started:
            self._receiver_started = True
            threading.Thread(target=self._receiver_loop, daemon=True).start()

    def ensure_started(self) -> None:
        """Connection + background threads ready karo (idempotent, safe to call multiple times)."""
        with self._conn_lock:
            if self._sock is None:
                try:
                    self._connect_locked()
                except OSError as err:
                    _LOGGER.warning("Raylogic MOD2U [%s]: initial connect fail (retry hoga): %s", self.name, err)
            self._ensure_background_threads()

    def _keepalive_loop(self) -> None:
        while True:
            threading.Event().wait(KEEPALIVE_INTERVAL)
            try:
                self.send(KEEPALIVE_CMD, is_keepalive=True)
            except OSError as err:
                _LOGGER.debug("Raylogic MOD2U [%s]: keep-alive failed: %s", self.name, err)

    def send(self, payload: bytes, is_keepalive: bool = False) -> None:
        with self._conn_lock:
            if self._sock is None:
                self._connect_locked()
                self._ensure_background_threads()
            try:
                self._sock.sendall(payload)
            except OSError as err:
                if not is_keepalive:
                    _LOGGER.warning("Raylogic MOD2U [%s]: send fail (%s), reconnecting", self.name, err)
                self._connect_locked()
                self._sock.sendall(payload)

    def _receiver_loop(self) -> None:
        while True:
            with self._conn_lock:
                if self._sock is None:
                    try:
                        self._connect_locked()
                    except OSError:
                        sock = None
                    else:
                        sock = self._sock
                else:
                    sock = self._sock

            if sock is None:
                threading.Event().wait(2)
                continue

            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as err:
                _LOGGER.debug("Raylogic MOD2U [%s]: receive error, reconnect: %s", self.name, err)
                with self._conn_lock:
                    if self._sock is sock:
                        try:
                            self._connect_locked()
                        except OSError:
                            pass
                threading.Event().wait(1)
                continue

            if not data:
                _LOGGER.debug("Raylogic MOD2U [%s]: connection closed (peer), reconnect", self.name)
                with self._conn_lock:
                    if self._sock is sock:
                        try:
                            self._connect_locked()
                        except OSError:
                            pass
                threading.Event().wait(1)
                continue

            self._recv_buf += data
            while b"\r" in self._recv_buf:
                frame, self._recv_buf = self._recv_buf.split(b"\r", 1)
                self._handle_incoming(frame)

    def _handle_incoming(self, frame_bytes: bytes) -> None:
        try:
            text = frame_bytes.decode("ascii", errors="ignore")
        except Exception:  # noqa: BLE001
            return
        parsed = _parse_incoming(text)
        if parsed is None:
            return
        channel, is_on = parsed
        _LOGGER.debug("Raylogic MOD2U [%s] <- channel %s is_on=%s", self.name, channel, is_on)
        self._dispatch(channel, is_on)

    # -------------------- commands --------------------
    def _build_command(self, channel: int, is_on: bool) -> tuple[str, bytes]:
        high, low = _channel_to_hex(channel)
        level = _level_hex(is_on)
        seq = self._next_seq()
        cmd = f"{self.device_id:03d},{seq:03d},*AR={high}{CMD_CHANNEL_DIRECT}{self.area}{level}{low}"
        return cmd, (cmd.encode("ascii") + b"\r")

    def set_channel(self, channel: int, is_on: bool) -> None:
        """switch.py yeh function call karega har ON/OFF pe."""
        cmd_str, payload = self._build_command(channel, is_on)
        _LOGGER.debug("Raylogic MOD2U [%s] -> %s", self.name, cmd_str)
        self.send(payload)

    # -------------------- listeners (mobile-app feedback sync) --------------------
    def register_listener(self, channel: int, callback) -> None:
        with self._listener_lock:
            self._listeners.setdefault(channel, []).append(callback)

    def unregister_listener(self, channel: int, callback) -> None:
        with self._listener_lock:
            callbacks = self._listeners.get(channel)
            if callbacks and callback in callbacks:
                callbacks.remove(callback)
                if not callbacks:
                    self._listeners.pop(channel, None)

    def _dispatch(self, channel: int, is_on: bool) -> None:
        with self._listener_lock:
            callbacks = list(self._listeners.get(channel, ()))
        for cb in callbacks:
            try:
                cb(is_on)
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception(
                    "Raylogic MOD2U [%s]: listener callback fail channel %s: %s", self.name, channel, err
                )


# ============================================================
# Device registry - IP:port ke hisaab se ek hi RaylogicMod2uDevice reuse
# hota hai (dono channels isi ek connection ko share karte hain).
# ============================================================
_devices: dict[str, RaylogicMod2uDevice] = {}
_devices_lock = threading.Lock()


def get_device(
    ip: str,
    port: int,
    area: str = DEFAULT_AREA,
    device_id: int = DEFAULT_DEVICE_ID,
    name: str = "",
) -> RaylogicMod2uDevice:
    """Is ip:port ke liye RaylogicMod2uDevice do - pehli baar call hone par naya banta hai."""
    key = f"{ip.replace('.', '_')}_{port}"
    with _devices_lock:
        dev = _devices.get(key)
        if dev is None:
            dev = RaylogicMod2uDevice(ip, port, area=area, device_id=device_id, name=name)
            _devices[key] = dev
        return dev
