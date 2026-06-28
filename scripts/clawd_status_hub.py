#!/usr/bin/env python3
"""Local Clawd hook hub with a small visual dashboard.

The hub accepts Codex/Claude hook deliveries on /hook, keeps transport state,
and forwards animations to the ESP32 by BLE or serial.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import importlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LOG_DIR = Path.home() / ".clawd-mochi"
LOG_PATH = LOG_DIR / "status-hub.log"
PID_PATH = LOG_DIR / "status-hub.pid"
WATCH_PID_PATH = LOG_DIR / "session-watch.pid"
RUN_LOCK_PATH = LOG_DIR / "status-hub.run.lock"
EVENTS_LIMIT = 300
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
EFFECTS_PATH = LOG_DIR / "status-effects.json"

STATUS_LABELS = {
    "idle": "空闲",
    "working": "工作中",
    "waiting": "等待确认",
    "complete": "完成",
    "error": "错误",
    "waiting_connection": "等待连接",
    "sleeping": "休眠",
}

DEFAULT_STATUS_EFFECTS: dict[str, dict[str, Any]] = {
    "idle": {"leds": "001"},
    "working": {"effect": {"pattern": "chase", "mask": "111", "period": 220}},
    "waiting": {"effect": {"pattern": "blink", "mask": "010", "period": 420}},
    "complete": {"leds": "001"},
    "error": {"effect": {"pattern": "blink", "mask": "100", "period": 220}},
    "waiting_connection": {"effect": {"pattern": "chase", "mask": "010", "period": 180}},
    "sleeping": {"leds": "000"},
}

ANIM_STATUS_MAP = {
    "idle": "idle",
    "typing": "working",
    "thinking": "working",
    "building": "working",
    "debugger": "working",
    "wizard": "working",
    "conducting": "working",
    "juggling": "working",
    "sweeping": "working",
    "walking": "working",
    "confused": "waiting",
    "alert": "waiting",
    "happy": "complete",
    "dizzy": "error",
    "beacon": "waiting_connection",
    "disconnected": "waiting_connection",
    "sleeping": "sleeping",
    "going_away": "sleeping",
}


def import_bridge():
    for name in ("codex_clawd_hook", "claude_clawd_hook"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise RuntimeError("could not import codex_clawd_hook or claude_clawd_hook")


bridge = import_bridge()


def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {message}\n")
    except OSError:
        pass


def write_pid() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def acquire_file_lock(path: Path, stale_seconds: float = 30.0) -> bool:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {time.time():.6f}\n")
        return True
    except FileExistsError:
        try:
            parts = path.read_text(encoding="utf-8", errors="replace").split()
            pid = int(parts[0]) if parts else 0
            if pid and not pid_is_running(pid):
                path.unlink(missing_ok=True)
                return acquire_file_lock(path, stale_seconds)
            if time.time() - path.stat().st_mtime > stale_seconds:
                path.unlink(missing_ok=True)
                return acquire_file_lock(path, stale_seconds)
        except OSError:
            pass
        return False
    except OSError:
        return False


def release_file_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def file_contains(path: Path, needle: str) -> bool:
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def normalized_status_effects(data: object | None = None) -> dict[str, dict[str, Any]]:
    result = json.loads(json.dumps(DEFAULT_STATUS_EFFECTS, ensure_ascii=False))
    if isinstance(data, dict):
        for key in STATUS_LABELS:
            value = data.get(key)
            if isinstance(value, dict):
                result[key] = value
    return result


def read_status_effects() -> dict[str, dict[str, Any]]:
    try:
        return normalized_status_effects(json.loads(EFFECTS_PATH.read_text(encoding="utf-8")))
    except Exception:
        return normalized_status_effects()


def write_status_effects(effects: dict[str, dict[str, Any]]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    EFFECTS_PATH.write_text(json.dumps(effects, ensure_ascii=False, indent=2), encoding="utf-8")


def status_for_anim(anim: str) -> str:
    return ANIM_STATUS_MAP.get(str(anim or "").strip(), "working")


def fmt_age(ts: float | None) -> str:
    if not ts:
        return ""
    age = max(0, int(time.time() - ts))
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    return f"{age // 3600}h ago"


def process_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    return kwargs


def stop_pid(pid: int) -> None:
    if not pid_is_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


class BleSession:
    def __init__(self, name: str, address: str | None) -> None:
        self.name = name
        self.address = address
        self.client: Any = None
        self.target: str | None = address
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def send(self, command: str | dict, timeout: float = 8.0) -> tuple[bool, str]:
        fut = asyncio.run_coroutine_threadsafe(self._send(command), self.loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as exc:
            return False, str(exc)

    def scan(self, timeout: float = 6.0) -> tuple[bool, list[dict[str, Any]] | str]:
        fut = asyncio.run_coroutine_threadsafe(self._scan(), self.loop)
        try:
            return True, fut.result(timeout=timeout)
        except Exception as exc:
            return False, str(exc)

    def select(self, address: str, name: str | None = None) -> None:
        self.target = address.strip() or None
        self.address = self.target
        if name:
            self.name = name
        self.client = None

    def connect(self, timeout: float = 8.0) -> tuple[bool, str]:
        fut = asyncio.run_coroutine_threadsafe(self._connect(), self.loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as exc:
            return False, str(exc)

    def disconnect(self, timeout: float = 4.0) -> tuple[bool, str]:
        fut = asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as exc:
            return False, str(exc)

    def reset(self, clear_target: bool = False) -> None:
        self.disconnect()
        self.client = None
        if clear_target:
            self.target = None
            self.address = None

    def status(self) -> dict[str, Any]:
        connected = bool(self.client is not None and getattr(self.client, "is_connected", False))
        return {
            "name": self.name,
            "address": self.target,
            "connected": connected,
        }

    async def _scan(self) -> list[dict[str, Any]]:
        try:
            from bleak import BleakScanner  # type: ignore
        except ImportError:
            raise RuntimeError("bleak missing; install with: python -m pip install bleak")
        devices = await BleakScanner.discover(timeout=4.0, return_adv=True)
        rows: list[dict[str, Any]] = []
        for key, value in devices.items():
            device, adv = value
            name = device.name or getattr(adv, "local_name", "") or ""
            rows.append(
                {
                    "address": device.address,
                    "name": name,
                    "rssi": getattr(adv, "rssi", None),
                    "selected": device.address == self.target,
                    "suggested": bool(name and name.startswith(self.name)),
                }
            )
        rows.sort(key=lambda item: (not item["suggested"], item["name"] or "", item["address"]))
        return rows

    async def _send(self, command: str | dict) -> tuple[bool, str]:
        try:
            from bleak import BleakClient, BleakScanner  # type: ignore
        except ImportError:
            return False, "bleak missing; install with: python -m pip install bleak"

        try:
            ok, message = await self._connect()
            if not ok:
                return False, message
            await self.client.write_gatt_char(
                bridge.BLE_RX_UUID,
                bridge.command_payload(command).encode("utf-8"),
                response=False,
            )
            return True, f"BLE {self.target}"
        except Exception as exc:
            self.client = None
            return False, f"BLE failed: {exc}"

    async def _connect(self) -> tuple[bool, str]:
        try:
            from bleak import BleakClient, BleakScanner  # type: ignore
        except ImportError:
            return False, "bleak missing; install with: python -m pip install bleak"

        if self.client is not None and self.client.is_connected:
            return True, f"BLE connected {self.target}"

        if not self.target:
            devices = await BleakScanner.discover(timeout=2.5)
            for device in devices:
                if (device.name or "").startswith(self.name):
                    self.target = device.address
                    self.address = self.target
                    break
        if not self.target:
            return False, f"BLE device not found name={self.name!r}"

        try:
            self.client = BleakClient(self.target, timeout=5.0)
            await self.client.connect()
            return True, f"BLE connected {self.target}"
        except Exception as exc:
            self.client = None
            return False, f"BLE connect failed: {exc}"

    async def _disconnect(self) -> tuple[bool, str]:
        if self.client is None:
            return True, "BLE disconnected"
        try:
            if self.client.is_connected:
                await self.client.disconnect()
            self.client = None
            return True, "BLE disconnected"
        except Exception as exc:
            self.client = None
            return False, f"BLE disconnect failed: {exc}"


class SerialSession:
    def __init__(self, port: str | None, baud: int | None) -> None:
        self.port = port
        self.baud = baud
        self.ser: Any = None
        self.lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "connected": bool(self.ser is not None and getattr(self.ser, "is_open", False)),
        }

    def select(self, port: str | None, baud: int | None = None) -> None:
        port = (port or "").strip() or None
        if port != self.port:
            self.disconnect()
        self.port = port
        if baud is not None:
            self.baud = baud

    def connect(self, port: str | None = None, baud: int | None = None) -> tuple[bool, str]:
        with self.lock:
            if port or baud is not None:
                self.select(port or self.port, baud)
            serial_port = self.port or bridge.discover_serial_port()
            if not serial_port:
                return False, "no serial port found"
            self.port = serial_port

            try:
                import serial  # type: ignore
            except ImportError:
                return False, "pyserial missing; install with: python -m pip install pyserial"

            try:
                if self.ser is not None and getattr(self.ser, "is_open", False):
                    return True, f"serial connected {self.port}"
                ser = serial.Serial()
                ser.port = self.port
                ser.baudrate = self.baud or int(os.environ.get("CLAWD_TANK_SERIAL_BAUD", bridge.DEFAULT_BAUD))
                ser.timeout = 0.4
                ser.write_timeout = 1.0
                ser.rtscts = False
                ser.dsrdtr = False
                ser.dtr = False
                ser.rts = False
                ser.open()
                ser.dtr = False
                ser.rts = False
                ser.reset_input_buffer()
                bridge.wait_for_serial_ready(ser)
                self.ser = ser
                return True, f"serial connected {self.port}"
            except Exception as exc:
                self._close_locked()
                return False, f"serial connect failed {self.port}: {exc}"

    def disconnect(self) -> tuple[bool, str]:
        with self.lock:
            self._close_locked()
            return True, "serial disconnected"

    def send(self, command: str | dict) -> tuple[bool, str]:
        with self.lock:
            if self.ser is None or not getattr(self.ser, "is_open", False):
                ok, message = self.connect(self.port, self.baud)
                if not ok:
                    return False, message
            try:
                self.ser.write(bridge.command_payload(command).encode("utf-8"))
                self.ser.flush()
                deadline = time.time() + 0.15
                while time.time() < deadline:
                    line = self.ser.readline().decode("utf-8", errors="replace").strip()
                    if "{" in line and "\"anim\"" in line:
                        return True, f"serial delivered {self.port}"
                return True, f"serial delivered {self.port} (no ack)"
            except Exception as exc:
                self._close_locked()
                return False, f"serial failed {self.port}: {exc}"

    def _close_locked(self) -> None:
        if self.ser is not None:
            try:
                if getattr(self.ser, "is_open", False):
                    self.ser.close()
            except Exception:
                pass
        self.ser = None


class HubState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.hooks: dict[str, dict[str, Any]] = {}
        self.clients: dict[str, dict[str, Any]] = {}
        self.transports: dict[str, dict[str, Any]] = {}
        self.status_effects = read_status_effects()
        self.use_status_effects = os.environ.get("CLAWD_TANK_USE_STATUS_EFFECTS", "0").lower() in {"1", "true", "yes", "on"}
        self.state: dict[str, Any] = {
            "started_at": now_iso(),
            "current_anim": None,
            "current_source": None,
            "current_client_id": None,
            "current_client_kind": None,
            "current_event": None,
            "current_tool": None,
            "current_status": None,
            "transport": None,
            "transport_status": "idle",
            "transport_message": "",
            "last_error": None,
            "last_hook_at": None,
            "last_send_ms": None,
            "delivered_count": 0,
            "failed_count": 0,
        }
        self.ble = BleSession(args.ble_name, args.ble_address)
        self.serial = SerialSession(args.port, args.baud)

    def scan_serial(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            from serial.tools import list_ports  # type: ignore
            for info in list_ports.comports():
                device = str(getattr(info, "device", "") or "")
                rows.append(
                    {
                        "device": device,
                        "description": str(getattr(info, "description", "") or ""),
                        "hwid": str(getattr(info, "hwid", "") or ""),
                        "manufacturer": str(getattr(info, "manufacturer", "") or ""),
                        "product": str(getattr(info, "product", "") or ""),
                        "selected": device == (self.args.port or ""),
                        "suggested": bool(bridge.port_score(info) >= 90),
                    }
                )
        except Exception as exc:
            return [{"error": str(exc)}]
        rows.sort(key=lambda item: (not item.get("suggested"), item.get("device") or ""))
        return rows

    def scan_ble(self) -> dict[str, Any]:
        ok, result = self.ble.scan()
        if ok:
            return {"ok": True, "devices": result}
        return {"ok": False, "error": result, "devices": []}

    def config(self) -> dict[str, Any]:
        ble_status = self.ble.status()
        serial_status = self.serial.status()
        return {
            "transport": self.args.transport,
            "serial_port": serial_status["port"],
            "baud": self.args.baud,
            "serial_connected": serial_status["connected"],
            "ble_name": ble_status["name"],
            "ble_address": ble_status["address"],
            "ble_connected": ble_status["connected"],
            "use_status_effects": self.use_status_effects,
        }

    def status_effect_config(self) -> dict[str, Any]:
        with self.lock:
            return {
                "labels": STATUS_LABELS,
                "effects": self.status_effects,
                "defaults": DEFAULT_STATUS_EFFECTS,
            }

    def update_status_effects(self, data: dict[str, Any]) -> dict[str, Any]:
        incoming = data.get("effects") if isinstance(data.get("effects"), dict) else data
        effects = normalized_status_effects(incoming)
        write_status_effects(effects)
        with self.lock:
            self.status_effects = effects
        return self.status_effect_config()

    def update_config(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "serial_port" in data:
                port = str(data.get("serial_port") or "").strip()
                self.serial.select(port or None, self.args.baud)
                self.args.port = self.serial.port
                self.transports["serial"] = {
                    "status": "selected" if port else "idle",
                    "message": f"{port} selected; connect to hold it" if port else "auto serial detection",
                    "last_at": time.time(),
                }
            if "ble_address" in data:
                address = str(data.get("ble_address") or "").strip()
                name = str(data.get("ble_name") or "").strip()
                self.ble.select(address, name or None)
                self.transports["ble"] = {
                    "status": "selected" if address else "idle",
                    "message": address or "auto BLE discovery",
                    "last_at": time.time(),
                }
            if "use_status_effects" in data:
                self.use_status_effects = bool(data.get("use_status_effects"))
            if "transport" in data:
                transport = str(data.get("transport") or "").strip().lower()
                if transport:
                    self.args.transport = transport
            return self.config()

    def connect_transport(self, data: dict[str, Any]) -> dict[str, Any]:
        transport = str(data.get("transport") or "").strip().lower()
        if transport == "serial":
            port = str(data.get("serial_port") or data.get("port") or "").strip()
            ok, message = self.serial.connect(port or None, self.args.baud)
            with self.lock:
                self.args.port = self.serial.port
                self.transports["serial"] = {
                    "status": "connected" if ok else "failed",
                    "message": message,
                    "last_at": time.time(),
                }
            return {"ok": ok, "transport": "serial", "message": message, "config": self.config()}

        if transport == "ble":
            address = str(data.get("ble_address") or data.get("address") or "").strip()
            name = str(data.get("ble_name") or data.get("name") or "").strip()
            if address or name:
                self.ble.select(address, name or None)
            ok, message = self.ble.connect()
            with self.lock:
                self.transports["ble"] = {
                    "status": "connected" if ok else "failed",
                    "message": message,
                    "last_at": time.time(),
                }
            return {"ok": ok, "transport": "ble", "message": message, "config": self.config()}

        return {"ok": False, "error": "expected transport serial or ble"}

    def disconnect_transport(self, data: dict[str, Any]) -> dict[str, Any]:
        transport = str(data.get("transport") or "").strip().lower()
        if transport == "serial":
            clear = bool(data.get("clear"))
            ok, message = self.serial.disconnect()
            with self.lock:
                if clear:
                    self.serial.select(None, self.args.baud)
                self.args.port = self.serial.port
                self.transports["serial"] = {
                    "status": "idle" if ok else "failed",
                    "message": message,
                    "last_at": time.time(),
                }
            return {"ok": ok, "transport": "serial", "message": message, "config": self.config()}

        if transport == "ble":
            clear = bool(data.get("clear"))
            ok, message = self.ble.disconnect()
            if clear:
                self.ble.reset(clear_target=True)
            with self.lock:
                self.transports["ble"] = {
                    "status": "idle" if ok else "failed",
                    "message": message,
                    "last_at": time.time(),
                }
            return {"ok": ok, "transport": "ble", "message": message, "config": self.config()}

        return {"ok": False, "error": "expected transport serial or ble"}

    def restart_watcher(self) -> dict[str, Any]:
        watcher = Path(__file__).with_name("codex_session_watch.py")
        if not watcher.exists():
            watcher = Path.home() / ".codex" / "skills" / "codex-clawd-status" / "scripts" / "codex_session_watch.py"
        if not watcher.exists():
            return {"ok": False, "error": "codex_session_watch.py not found"}

        old_pid = read_pid(WATCH_PID_PATH)
        stop_pid(old_pid)
        time.sleep(0.3)
        proc = subprocess.Popen([sys.executable, str(watcher), "--follow-latest"], **process_kwargs())
        log(f"module restart codex-watcher old_pid={old_pid} new_pid={proc.pid}")
        return {"ok": True, "module": "codex-watcher", "pid": proc.pid}

    def restart_ble(self) -> dict[str, Any]:
        with self.lock:
            self.ble.reset()
            self.transports["ble"] = {
                "status": "idle",
                "message": "BLE connection reset",
                "last_at": time.time(),
            }
        log("module restart transport-ble")
        return {"ok": True, "module": "transport-ble"}

    def restart_module(self, module: str, server: ThreadingHTTPServer | None = None) -> dict[str, Any]:
        if module == "codex-watcher":
            return self.restart_watcher()
        if module == "transport-ble":
            return self.restart_ble()
        if module == "hub":
            if server is None:
                return {"ok": False, "error": "server handle unavailable"}
            self.schedule_hub_restart(server)
            return {"ok": True, "module": "hub", "message": "Hub restarting"}
        return {"ok": False, "error": f"module {module!r} is not restartable"}

    def schedule_hub_restart(self, server: ThreadingHTTPServer) -> None:
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--host",
            str(self.args.host),
            "--port",
            str(self.args.hub_port),
            "--transport",
            str(self.args.transport),
        ]
        if self.args.port:
            cmd += ["--serial-port", str(self.args.port)]
        if self.args.baud is not None:
            cmd += ["--baud", str(self.args.baud)]
        if self.ble.target:
            cmd += ["--ble-address", str(self.ble.target)]
        if self.ble.name:
            cmd += ["--ble-name", str(self.ble.name)]

        helper = (
            "import subprocess,time;"
            "time.sleep(1.2);"
            f"subprocess.Popen({cmd!r}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True"
            + (", creationflags=0x00000008|0x08000000" if os.name == "nt" else ", start_new_session=True")
            + ")"
        )
        subprocess.Popen([sys.executable, "-c", helper], **process_kwargs())

        def stop_server() -> None:
            time.sleep(0.2)
            log("module restart hub")
            server.shutdown()

        threading.Thread(target=stop_server, daemon=True).start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                **self.state,
                "status_labels": STATUS_LABELS,
                "status_effects": self.status_effects,
                "hooks": self.hooks,
                "clients": self.clients,
                "transports": self.transports,
                "modules": self.modules_locked(),
                "events_count": len(self.events),
                "hub_pid": os.getpid(),
            }

    def modules_locked(self) -> dict[str, dict[str, Any]]:
        codex_hooks = Path.home() / ".codex" / "hooks.json"
        claude_settings = Path.home() / ".claude" / "settings.json"
        watcher_pid = read_pid(WATCH_PID_PATH)
        serial_port = ""
        try:
            serial_port = bridge.discover_serial_port() or ""
        except Exception:
            serial_port = ""

        def client_module(client_id: str, label: str, configured: bool) -> dict[str, Any]:
            client = self.clients.get(client_id, {})
            last_at = client.get("last_at")
            if client:
                status = client.get("status") or "seen"
                detail = f"last {client.get('last_anim') or ''} {fmt_age(last_at)}".strip()
            else:
                status = "configured" if configured else "missing"
                detail = "waiting for first event" if configured else "hook config not found"
            return {"label": label, "status": status, "detail": detail, "last_at": last_at}

        transport_modules = {}
        serial_status = self.serial.status()
        for name in bridge.transport_list(self.args.transport):
            t = self.transports.get(name, {})
            selected_serial = serial_status["port"] or self.args.port or serial_port
            if name == "serial" and serial_status["connected"]:
                status = "connected"
            else:
                status = t.get("status") or ("available" if name == "serial" and selected_serial else "idle")
            detail = t.get("message") or ""
            if name == "serial" and selected_serial:
                detail = detail if str(detail).startswith(str(selected_serial)) else f"{selected_serial} {detail}".strip()
            if name == "ble" and self.ble.target:
                detail = f"{self.ble.target} {detail}".strip()
            transport_modules[f"transport-{name}"] = {
                "label": f"Transport {name.upper()}",
                "status": status,
                "detail": detail,
                "last_at": t.get("last_at"),
                "restartable": name == "ble",
            }

        device_status = self.state.get("transport_status") or "idle"
        device_detail = self.state.get("transport_message") or "waiting for delivery"
        modules = {
            "hub": {
                "label": "Hook Hub",
                "status": "online",
                "detail": f"pid {os.getpid()} / transport {self.args.transport}",
                "last_at": time.time(),
                "restartable": True,
            },
            "codex-hook": client_module(
                "codex-code",
                "Codex native hook",
                file_contains(codex_hooks, "codex_clawd_hook.py"),
            ),
            "codex-vscode": client_module("codex-vscode", "Codex VS Code watcher", True),
            "codex-desktop": client_module("codex-desktop", "Codex Desktop watcher", True),
            "codex-watcher": {
                "label": "Codex session watcher",
                "status": "online" if pid_is_running(watcher_pid) else "offline",
                "detail": f"pid {watcher_pid}" if watcher_pid else "pid file missing",
                "last_at": None,
                "restartable": True,
            },
            "claude-hook": client_module(
                "claude-code",
                "Claude Code hook",
                file_contains(claude_settings, "claude_clawd_hook.py"),
            ),
            "esp32": {
                "label": "ESP32 display",
                "status": device_status,
                "detail": device_detail,
                "last_at": self.state.get("last_hook_at"),
            },
        }
        modules.update(transport_modules)
        return modules

    def recent_events(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.events)

    def add_event(self, item: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(item)
            if len(self.events) > EVENTS_LIMIT:
                del self.events[: len(self.events) - EVENTS_LIMIT]

    def deliver(self, delivery: dict[str, Any]) -> dict[str, Any]:
        payload = delivery.get("payload") if isinstance(delivery.get("payload"), dict) else {}
        source = str(delivery.get("source") or "manual")
        client_id = str(delivery.get("client_id") or source or "manual")
        client_kind = str(delivery.get("client_kind") or source or "manual")
        event = str(delivery.get("event") or payload.get("hook_event_name") or payload.get("event") or "")
        tool = str(delivery.get("tool") or payload.get("tool_name") or payload.get("toolName") or "")

        original_command = self.device_command(delivery)
        original_anim = str(original_command.get("anim") or delivery.get("anim") or "").strip()
        semantic_status = str(delivery.get("status") or status_for_anim(original_anim))
        command = original_command
        if (
            source != "manual"
            and self.use_status_effects
            and semantic_status in self.status_effects
        ):
            command = self.device_command(self.status_effects[semantic_status])
            command.setdefault("status", semantic_status)

        anim = str(command.get("anim") or delivery.get("anim") or "").strip()
        if not anim:
            if "effect" in command or "steps" in command or "pattern" in command:
                anim = "custom"
            elif any(key in command for key in ("leds", "led", "mask")):
                anim = "manual"
        if not anim:
            return {"ok": False, "error": "missing anim or effect"}

        ts = time.time()
        hook_key = f"{client_id}:{event}" if event else client_id

        with self.lock:
            client = self.clients.setdefault(
                client_id,
                {
                    "client_id": client_id,
                    "kind": client_kind,
                    "source": source,
                    "status": "idle",
                    "hooks": {},
                    "delivered_count": 0,
                    "failed_count": 0,
                    "last_at": None,
                },
            )
            client.update({"kind": client_kind, "source": source, "status": "sending", "last_at": ts})
            self.state.update(
                {
                    "current_anim": anim,
                    "current_source": source,
                    "current_client_id": client_id,
                    "current_client_kind": client_kind,
                    "current_event": event,
                    "current_tool": tool,
                    "current_status": semantic_status,
                    "transport_status": "sending",
                    "last_hook_at": ts,
                    "last_error": None,
                }
            )
            if event:
                hook_state = {
                    "status": "sending",
                    "last_anim": anim,
                    "last_tool": tool,
                    "last_source": source,
                    "last_client_id": client_id,
                    "last_client_kind": client_kind,
                    "last_at": ts,
                }
                self.hooks[hook_key] = {"event": event, **hook_state}
                client["hooks"][event] = hook_state

        started = time.perf_counter()
        results = []
        sent = False
        for transport in bridge.transport_list(self.args.transport):
            ok, message = self.send_by_transport(command, transport)
            results.append({"transport": transport, "ok": ok, "message": message})
            if ok:
                sent = True
                if self.args.transport != "parallel":
                    break

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        status = "delivered" if sent else "failed"
        event_item = {
            "at": now_iso(),
            "source": source,
            "client_id": client_id,
            "client_kind": client_kind,
            "event": event,
            "tool": tool,
            "anim": anim,
            "semantic_status": semantic_status,
            "original_anim": original_anim,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "results": results,
        }
        self.add_event(event_item)

        transport_message = "; ".join(r["message"] for r in results)
        with self.lock:
            for result in results:
                self.transports[result["transport"]] = {
                    "status": "delivered" if result["ok"] else "failed",
                    "message": result["message"],
                    "last_at": ts,
                }
            self.state.update(
                {
                    "transport": next((r["transport"] for r in results if r["ok"]), None),
                    "transport_status": status,
                    "transport_message": transport_message,
                    "last_send_ms": elapsed_ms,
                    "last_error": None if sent else transport_message,
                }
            )
            self.state["delivered_count" if sent else "failed_count"] += 1
            client = self.clients.setdefault(client_id, {"hooks": {}})
            client["status"] = status
            client["last_anim"] = anim
            client["last_event"] = event
            client["last_tool"] = tool
            client["last_at"] = ts
            client["delivered_count"] = int(client.get("delivered_count", 0)) + (1 if sent else 0)
            client["failed_count"] = int(client.get("failed_count", 0)) + (0 if sent else 1)
            if event:
                self.hooks[hook_key]["status"] = status
                client.setdefault("hooks", {}).setdefault(event, {})["status"] = status

        log(f"{status} client={client_id} source={source} event={event!r} tool={tool!r} anim={anim} results={results}")
        return {"ok": sent, "status": status, "elapsed_ms": elapsed_ms, "results": results}

    def device_command(self, delivery: dict[str, Any]) -> dict[str, Any]:
        command: dict[str, Any] = {"auto": False}
        for key in ("anim", "id", "leds", "led", "mask", "speed", "backlight", "effect", "steps", "pattern", "period", "period_ms"):
            if key in delivery:
                command[key] = delivery[key]
        if "effect" in command and isinstance(command["effect"], dict):
            command.setdefault("anim", "custom")
        return command

    def send_by_transport(self, command: str | dict, transport: str) -> tuple[bool, str]:
        if transport == "ble":
            return self.ble.send(command)
        if transport == "serial":
            ok, message = self.serial.send(command)
            self.args.port = self.serial.port
            return ok, message
        return False, f"unknown transport {transport!r}"


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>红绿灯控制台</title>
<style>
:root{--bg:#0d0f13;--panel:#15181f;--raised:#1b1f28;--line:#272d38;--text:#eef1f5;--muted:#8c95a3;--accent:#d97757;--accent2:#e8927c;--ok:#5fd39a;--bad:#ff7c6c;--warn:#f3c552}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 72% -12%,#1a1d26 0%,var(--bg) 55%);color:var(--text);font-family:"Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:980px;margin:0 auto;padding:0 24px 44px}
.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:13px;padding:16px 0;margin-bottom:6px;background:linear-gradient(180deg,var(--bg) 72%,transparent)}
.logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),#b85a3e);display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;box-shadow:0 4px 14px rgba(217,119,87,.4)}
.title{display:flex;flex-direction:column;line-height:1.15}
.title b{font-size:19px;letter-spacing:.2px}
.title span{font-size:12px;color:var(--muted)}
.spacer{flex:1}
.grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:20px}
.panel{border:1px solid var(--line);border-radius:16px;padding:20px;background:linear-gradient(180deg,var(--panel),#12151b);box-shadow:0 1px 0 rgba(255,255,255,.02) inset,0 10px 26px rgba(0,0,0,.28);transition:transform 0.2s,box-shadow 0.2s}
.panel:hover{transform:translateY(-2px);box-shadow:0 12px 32px rgba(0,0,0,.4)}
.panel.wide{grid-column:1/-1}
.ph{display:flex;align-items:center;gap:9px;margin:0 0 13px;font-size:11.5px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;color:var(--muted)}
.ph::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 9px var(--accent)}
.hero{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.hero-anim{font-size:36px;font-weight:800;letter-spacing:.3px;background:linear-gradient(90deg,#fff,var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
.metas{display:grid;grid-template-columns:1fr 1fr;gap:9px 16px}
.meta{display:flex;flex-direction:column;gap:2px;min-width:0}
.ml{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.mv{font-size:14px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 11px;border-radius:999px;background:#22262f;color:var(--muted);border:1px solid var(--line)}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.pill.ok{color:var(--ok);background:rgba(95,211,154,.10);border-color:rgba(95,211,154,.28)}
.pill.bad{color:var(--bad);background:rgba(255,124,108,.10);border-color:rgba(255,124,108,.28)}
.pill.send{color:var(--warn);background:rgba(243,197,82,.10);border-color:rgba(243,197,82,.28)}
.pill.muted{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.chip{font-size:12px;font-weight:600;background:var(--raised);color:#cfd5de;border:1px solid var(--line);border-radius:8px;padding:7px 11px;cursor:pointer;transition:.15s}
.chip:hover{border-color:var(--accent);color:#fff;transform:translateY(-1px)}
.chip.active{background:linear-gradient(135deg,var(--accent),#b85a3e);border-color:transparent;color:#fff;box-shadow:0 4px 12px rgba(217,119,87,.32)}
.btn{font-size:12px;font-weight:600;background:var(--raised);color:#cfd5de;border:1px solid var(--line);border-radius:8px;padding:6px 11px;margin:2px 2px 2px 0;cursor:pointer;transition:.15s}
.btn:hover{border-color:var(--accent);color:#fff}
.btn.ok{border-color:rgba(95,211,154,.5);color:var(--ok)}
.controls{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}
.control-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.field{width:100%;background:#10131a;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:12px "Cascadia Code",Consolas,ui-monospace,monospace;outline:none}
.field:focus{border-color:var(--accent)}
.signal-wrap{display:grid;grid-template-columns:112px 1fr;gap:18px;align-items:center;margin-bottom:16px}
.signal{width:112px;padding:12px;border-radius:18px;background:#080a0d;border:1px solid #252b35;box-shadow:inset 0 0 18px rgba(0,0,0,.75),0 12px 26px rgba(0,0,0,.28)}
.lamp{width:72px;height:72px;margin:9px auto;border-radius:50%;background:#222831;border:1px solid #333b48;box-shadow:inset 0 3px 12px rgba(0,0,0,.75);transition:.12s}
.lamp.red.on{background:#ff4a3d;box-shadow:0 0 24px rgba(255,74,61,.72),inset 0 2px 10px rgba(255,255,255,.25)}
.lamp.yellow.on{background:#ffd24c;box-shadow:0 0 24px rgba(255,210,76,.65),inset 0 2px 10px rgba(255,255,255,.3)}
.lamp.green.on{background:#40d979;box-shadow:0 0 24px rgba(64,217,121,.65),inset 0 2px 10px rgba(255,255,255,.28)}
.signal-status{min-width:0}
.signal-status b{display:block;font-size:20px;margin-bottom:8px}
.status-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:0 0 18px}
.status-card{border:1px solid var(--line);border-radius:8px;padding:10px;background:#10131a;min-width:0}
.status-card-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px}
.status-card-title{font-size:12px;font-weight:700;color:var(--text);white-space:nowrap}
.status-card-actions{display:flex;align-items:center;gap:6px;min-width:0}
.btn.mini{padding:4px 7px;font-size:11px;line-height:1}
.status-card-detail{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.map-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.map-item{border:1px solid var(--line);border-radius:8px;padding:10px;background:#10131a}
.map-title{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
.select{width:100%;background:var(--raised);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px;outline:none}
.select:focus{border-color:var(--accent)}
.custom-panel{grid-column:1/-1}
.custom-panel details{border:1px solid var(--line);border-radius:8px;background:#10131a;padding:10px}
.custom-panel summary{cursor:pointer;color:var(--text);font-weight:700;font-size:13px}
.custom-panel summary::marker{color:var(--accent)}
.custom-panel #statusMapper{margin-top:10px}
.sub{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:15px 0 7px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:8px;border-bottom:1px solid rgba(39,45,56,.55);color:#dfe4ea}
tr:last-child td{border-bottom:none}
tbody tr:hover td,table tr:hover td{background:rgba(217,119,87,.05)}
.mono{font-family:"Cascadia Code",Consolas,ui-monospace,monospace;font-size:12px;color:var(--muted)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.send{color:var(--warn)}.muted,.k{color:var(--muted)}
.empty{color:var(--muted);font-size:13px;padding:10px 2px;text-align:center}
p{margin:7px 0}
@media(max-width:760px){main{padding:0 14px 28px}.grid,.status-strip{grid-template-columns:1fr}.controls{grid-template-columns:1fr}.hero-anim{font-size:28px}.signal-wrap{grid-template-columns:1fr}.signal{margin:auto}}
::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb{background:#2a313c;border-radius:6px}
</style></head><body><main>
<header class="top">
<div class="logo">灯</div>
<div class="title"><b>红绿灯控制台</b><span>三色灯控制器</span></div>
<div class="spacer"></div>
<span id="status" class="pill muted">加载中</span>
</header>
<div id="statusBar" class="status-strip"></div>
<div class="grid">
<section class="panel"><div class="ph">红绿灯</div><div id="signalPreview"></div><div id="current"></div><div class="sub">快捷控制</div><div id="buttons" class="chips"></div><div id="effectControls" class="controls"></div></section>
<section class="panel"><div class="ph">设备</div><div id="transport"></div><div class="sub">连接</div><div id="selectors"></div></section>
<section class="panel custom-panel"><div class="ph">自定义</div><details open><summary>Hook 状态灯效映射</summary><div id="statusMapper"></div></details></section>
</div>
<script>
const statusOrder=[
  ["idle","空闲"],["working","工作中"],["waiting","等待确认"],["complete","完成"],["error","错误"],["waiting_connection","等待连接"],["sleeping","休眠"]
];
const effectOptions=[
  {id:"red",label:"红灯常亮",command:{leds:"100"}},
  {id:"yellow",label:"黄灯常亮",command:{leds:"010"}},
  {id:"green",label:"绿灯常亮",command:{leds:"001"}},
  {id:"off",label:"全灭",command:{leds:"000"}},
  {id:"all",label:"全亮",command:{leds:"111"}},
  {id:"red_blink",label:"红灯闪烁",command:{effect:{pattern:"blink",mask:"100",period:220}}},
  {id:"yellow_blink",label:"黄灯闪烁",command:{effect:{pattern:"blink",mask:"010",period:420}}},
  {id:"green_blink",label:"绿灯闪烁",command:{effect:{pattern:"blink",mask:"001",period:300}}},
  {id:"all_chase",label:"全灯轮巡",command:{effect:{pattern:"chase",mask:"111",period:220}}},
  {id:"yellow_chase",label:"黄灯等待轮巡",command:{effect:{pattern:"chase",mask:"010",period:180}}},
  {id:"pair_chase",label:"双灯轮巡",command:{effect:{pattern:"pair_chase",mask:"111",period:260}}}
];
const presets=[
  ["红灯","100"],["黄灯","010"],["绿灯","001"],["全灭","000"],["全亮","111"]
];
buttons.innerHTML=presets.map(([name,mask])=>`<button class="chip" data-mask="${mask}" onclick="sendPreset('${mask}')">${name}</button>`).join("")+
  `<button class="chip" onclick="sendPattern('blink')">闪烁</button><button class="chip" onclick="sendPattern('chase')">轮巡</button>`;
async function send(anim){await fetch('/send',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({anim})}); refresh();}
async function sendPreset(mask){ledMask=mask; previewMode={type:"manual"}; renderEffectControls(); renderSignal(); await post('/send',{leds:mask}); refresh();}
let ledMask="111";
let customPeriod=250;
let customSpeed=2;
let backlightOn=true;
let previewMode={type:"manual"};
let previewStartedAt=Date.now();
let statusEffects={};
function clone(v){return JSON.parse(JSON.stringify(v))}
function normalizeCommand(c){const copy=clone(c||{}); delete copy.auto; delete copy.anim; delete copy.status; return JSON.stringify(copy)}
function effectIdForCommand(command){
  const target=normalizeCommand(command);
  const found=effectOptions.find(o=>normalizeCommand(o.command)===target);
  return found?found.id:"all_chase";
}
function commandForEffect(id){
  const found=effectOptions.find(o=>o.id===id)||effectOptions[0];
  return clone(found.command);
}
function applyPreviewFromCommand(command){
  const cmd=command||{};
  if(cmd.leds||cmd.led||cmd.mask){ledMask=String(cmd.leds||cmd.led||cmd.mask).padStart(3,"0").slice(0,3); previewMode={type:"manual"};}
  if(cmd.effect&&cmd.effect.pattern){ledMask=String(cmd.effect.mask||"111").padStart(3,"0").slice(0,3); customPeriod=Math.max(20,parseInt(cmd.effect.period||cmd.effect.period_ms||customPeriod)||customPeriod); previewMode={type:"pattern",pattern:cmd.effect.pattern}; previewStartedAt=Date.now();}
  if(cmd.effect&&Array.isArray(cmd.effect.steps)){previewMode={type:"steps",steps:cmd.effect.steps}; previewStartedAt=Date.now();}
  renderEffectControls(); renderSignal();
}
async function loadStatusEffects(){
  const data=await (await fetch('/status-effects')).json();
  statusEffects=data.effects||{};
  renderStatusMapper();
}
function renderStatusMapper(){
  const target=document.getElementById("statusMapper");
  if(!target)return;
  target.innerHTML=`<div class="control-row" style="margin-bottom:10px"><button class="btn ok" onclick="applyStatusEffects()">应用自定义灯效</button><span class="muted">按下后，自动状态会使用下面的映射</span></div><div class="map-grid">`+statusOrder.map(([key,label])=>{
    const selected=effectIdForCommand(statusEffects[key]);
    return `<div class="map-item"><div class="map-title"><b>${label}</b><button class="btn" onclick="testStatusEffect('${key}')">测试</button></div><select class="select" onchange="setStatusEffect('${key}',this.value)">`+
      effectOptions.map(o=>`<option value="${o.id}" ${o.id===selected?"selected":""}>${o.label}</option>`).join("")+
      `</select></div>`;
  }).join("")+`</div>`;
}
async function applyStatusEffects(){
  const result=await post('/config',{use_status_effects:true});
  if(!result.use_status_effects){alert('应用失败');return}
  await refresh();
}
async function setStatusEffect(statusKey,effectId){
  statusEffects[statusKey]=commandForEffect(effectId);
  await post('/status-effects',{effects:statusEffects});
  renderStatusMapper();
}
async function testStatusEffect(statusKey){
  const command=statusEffects[statusKey]||commandForEffect(effectIdForCommand(statusEffects[statusKey]));
  applyPreviewFromCommand(command);
  await post('/send',command);
  refresh();
}
function renderEffectControls(){
  effectControls.innerHTML=
    `<div><div class="sub">灯位</div><div class="control-row">`+
    ["红","黄","绿"].map((name,i)=>`<button class="btn ${ledMask[i]==="1"?"ok":""}" onclick="toggleLed(${i})">${name}</button>`).join("")+
    `<button class="btn" onclick="sendMask()">应用</button></div></div>`+
    `<div><div class="sub">输出</div><div class="control-row">`+
    [1,2,3].map(v=>`<button class="btn ${customSpeed===v?"ok":""}" onclick="setSpeed(${v})">速度 ${v}</button>`).join("")+
    `<button class="btn ${backlightOn?"ok":""}" onclick="toggleBacklight()">电源</button></div></div>`+
    `<div><div class="sub">灯效</div><div class="control-row">`+
    [["steady","常亮"],["blink","闪烁"],["chase","轮巡"],["alternate","交替"],["pair_chase","双灯轮巡"]].map(([p,label])=>`<button class="btn" onclick="sendPattern('${p}')">${label}</button>`).join("")+
    `</div></div>`+
    `<div><div class="sub">周期 ms</div><input id="periodField" class="field" value="${customPeriod}" onchange="customPeriod=Math.max(20,Math.min(10000,parseInt(this.value||250)||250)); renderSignal();"></div>`+
    `<div style="grid-column:1/-1"><div class="sub">自定义步骤 JSON</div><input id="stepsField" class="field" value='[{"mask":"100","ms":120},{"mask":"010","ms":120},{"mask":"001","ms":120},{"mask":"000","ms":80}]'><button class="btn" onclick="sendSteps()">发送步骤</button></div>`;
}
function toggleLed(i){ledMask=ledMask.split("").map((v,n)=>n===i?(v==="1"?"0":"1"):v).join(""); renderEffectControls(); renderSignal();}
async function sendMask(){previewMode={type:"manual"}; renderSignal(); await post('/send',{leds:ledMask}); refresh();}
async function setSpeed(v){customSpeed=v; await post('/send',{speed:v,anim:"custom"}); renderEffectControls(); renderSignal(); refresh();}
async function toggleBacklight(){backlightOn=!backlightOn; await post('/send',{backlight:backlightOn,anim:"custom"}); renderEffectControls(); renderSignal(); refresh();}
async function sendPattern(pattern){previewMode={type:"pattern",pattern}; previewStartedAt=Date.now(); renderSignal(); await post('/send',{effect:{pattern,mask:ledMask,period:customPeriod}}); refresh();}
async function sendSteps(){
  try{const steps=JSON.parse(stepsField.value); previewMode={type:"steps",steps}; previewStartedAt=Date.now(); renderSignal(); await post('/send',{effect:{steps}}); refresh();}
  catch(e){alert("步骤 JSON 格式不正确");}
}
async function post(url,body){return await (await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body||{})})).json();}
function cls(s){return ["delivered","online","available","configured","selected","connected","已连接","已选择","可用","在线"].includes(s)?"ok":["failed","offline","missing","失败","离线","缺失"].includes(s)?"bad":["sending","发送中"].includes(s)?"send":"muted"}
function statusText(s){return {"delivered":"已送达","sending":"发送中","failed":"失败","idle":"空闲","online":"在线","offline":"离线","connected":"已连接","selected":"已选择","available":"可用","configured":"已配置","missing":"缺失"}[s]||s||"空闲"}
function pill(s){return `<span class="pill ${cls(s)}">${statusText(s)}</span>`}
function meta(l,v){const t=(v===0?"0":v||"").toString().replace(/"/g,"");return `<div class="meta"><span class="ml">${l}</span><span class="mv" title="${t}">${(v===0?"0":v)||""}</span></div>`}
function none(t){return `<tr><td class="empty" colspan="9">${t}</td></tr>`}
function q(s){return JSON.stringify(s||"")}
function selected(a,b){return (a||"")===(b||"")}
function modeLabel(mode){return mode==="ble"?"仅 BLE":mode==="serial"?"仅串口":"自动：BLE -> 串口"}
function maskName(mask){return mask==="100"?"红灯":mask==="010"?"黄灯":mask==="001"?"绿灯":mask==="000"?"全灭":mask==="111"?"全亮":mask}
function bit(mask,i){return mask[i]==="1"}
function activePreviewMask(){
  if(!backlightOn)return "000";
  const now=Date.now();
  if(previewMode.type==="steps"&&Array.isArray(previewMode.steps)&&previewMode.steps.length){
    const steps=previewMode.steps.filter(s=>s&&s.mask!=null&&s.ms!=null).map(s=>({mask:String(s.mask).padStart(3,"0").slice(0,3),ms:Math.max(20,parseInt(s.ms)||20)}));
    const total=steps.reduce((n,s)=>n+s.ms,0); let t=(now-previewStartedAt)%Math.max(total,1);
    for(const s of steps){if(t<s.ms)return s.mask;t-=s.ms}
    return steps[0]?.mask||"000";
  }
  if(previewMode.type==="pattern"){
    const p=previewMode.pattern, period=Math.max(20,customPeriod), tick=Math.floor((now-previewStartedAt)/period);
    if(p==="steady")return ledMask;
    if(p==="blink")return tick%2===0?ledMask:"000";
    if(p==="chase"){const order=[0,1,2].filter(i=>bit(ledMask,i)); if(!order.length)return "000"; return ["0","0","0"].map((v,i)=>i===order[tick%order.length]?"1":"0").join("");}
    if(p==="alternate")return tick%2===0?["1","0","1"].map((v,i)=>bit(ledMask,i)?v:"0").join(""):["0",bit(ledMask,1)?"1":"0","0"].join("");
    if(p==="pair_chase"){const pairs=["110","011","101"]; return pairs[tick%3].split("").map((v,i)=>v==="1"&&bit(ledMask,i)?"1":"0").join("");}
  }
  return ledMask;
}
function renderSignal(){
  const mask=activePreviewMask();
  signalPreview.innerHTML=`<div class="signal-wrap"><div class="signal"><div class="lamp green ${bit(mask,2)?"on":""}"></div><div class="lamp yellow ${bit(mask,1)?"on":""}"></div><div class="lamp red ${bit(mask,0)?"on":""}"></div></div><div class="signal-status"><b>${maskName(mask)}</b><div class="metas">`+meta("当前灯位",mask)+meta("选择灯位",ledMask)+meta("周期",customPeriod+" ms")+meta("电源",backlightOn?"开启":"关闭")+`</div></div></div>`;
}
function compactDetail(v){return (v&&v.detail?v.detail:"").replace(/"/g,"")}
function renderStatusBar(s){
  const m=s.modules||{};
  const items=[
    ["Hub",m.hub||{status:"offline",detail:""},""],
    ["Hook",m["codex-hook"]||{status:"missing",detail:""},""],
    ["Watcher",m["codex-watcher"]||{status:"offline",detail:""},"codex-watcher"],
    ["设备",m.esp32||{status:s.transport_status||"idle",detail:s.transport_message||""},""]
  ];
  statusBar.innerHTML=items.map(([label,v,restartName])=>{
    const restart=restartName?`<button class="btn mini" onclick="restartModule('${restartName}')">Restart</button>`:"";
    return `<div class="status-card"><div class="status-card-head"><span class="status-card-title">${label}</span><span class="status-card-actions">${restart}${pill(v.status)}</span></div><div class="status-card-detail" title="${compactDetail(v)}">${compactDetail(v)||"等待状态"}</div></div>`;
  }).join("");
}
let selectorView="home";
async function setTransport(mode){await post('/config',{transport:mode}); refresh();}
function backSelectors(){selectorView="home"; refresh();}
async function connectSerial(port){
  const result=await post('/connect',{transport:'serial',serial_port:port});
  if(!result.ok){alert(result.error||result.message||'串口连接失败');}
  refresh();
}
async function disconnectSerial(){await post('/disconnect',{transport:'serial'}); refresh();}
async function clearSerial(){await post('/disconnect',{transport:'serial',clear:true}); refresh();}
async function connectBle(address,name){
  const result=await post('/connect',{transport:'ble',ble_address:address,ble_name:name});
  if(!result.ok){alert(result.error||result.message||'BLE 连接失败');}
  refresh();
}
async function disconnectBle(clear){await post('/disconnect',{transport:'ble',clear:!!clear}); refresh();}
function renderSelectors(cfg){
  const mode=cfg.transport||"auto";
  selectors.innerHTML=
    `<div class="sub">模式</div>`+
    `<button class="btn ${mode==="auto"?"ok":""}" onclick="setTransport('auto')">自动 BLE -> 串口</button>`+
    `<button class="btn ${mode==="ble"?"ok":""}" onclick="setTransport('ble')">仅 BLE</button>`+
    `<button class="btn ${mode==="serial"?"ok":""}" onclick="setTransport('serial')">仅串口</button>`+
    `<div class="sub">串口</div>`+
    `<p>${pill(cfg.serial_connected?"已连接":(cfg.serial_port?"已选择":"空闲"))} <span class="mono">${cfg.serial_port||"自动检测"}</span></p>`+
    `<button class="btn" onclick="scanSerial()">搜索串口</button>`+
    `<button class="btn" onclick="connectSerial(null)">自动连接串口</button>`+
    `<button class="btn" onclick="disconnectSerial()">断开串口</button>`+
    `<button class="btn" onclick="clearSerial()">清除选择</button>`+
    `<div class="sub">BLE</div>`+
    `<p>${pill(cfg.ble_connected?"已连接":(cfg.ble_address||cfg.ble_name?"已选择":"空闲"))} <span class="mono">${cfg.ble_name||"ClawdTank"} ${cfg.ble_address||""}</span></p>`+
    `<button class="btn" onclick="scanBle()">搜索 BLE</button>`+
    `<button class="btn" onclick="connectBle(null,null)">连接 BLE</button>`+
    `<button class="btn" onclick="disconnectBle(false)">断开 BLE</button>`;
}
async function scanSerial(){
  selectorView="serial";
  selectors.innerHTML='<p class="muted">正在搜索串口...</p>';
  const rows=await (await fetch('/scan/serial')).json();
  const cfg=await (await fetch('/config')).json();
  selectors.innerHTML='<div class="sub">串口</div>'+
    rows.map(r=>r.error?`<p class=bad>${r.error}</p>`:
      `<p><button class="btn" onclick="connectSerial(${q(r.device)})">连接并保持</button> ${selected(cfg.serial_port,r.device)?pill(cfg.serial_connected?"已连接":"已选择"):""} <b>${r.device}</b> ${r.suggested?'<span class=ok>ESP32</span> ':''}<span class="mono">${r.description||''} ${r.hwid||''}</span></p>`).join('')+
    '<button class="btn" onclick="connectSerial(null)">自动连接串口</button> <button class="btn" onclick="disconnectSerial()">断开串口</button> <button class="btn" onclick="clearSerial()">清除选择</button> <button class="btn" onclick="backSelectors()">返回</button>';
}
async function scanBle(){
  selectorView="ble";
  selectors.innerHTML='<p class="muted">正在搜索 BLE...</p>';
  const data=await (await fetch('/scan/ble')).json();
  if(!data.ok){selectors.innerHTML=`<p class=bad>${data.error}</p>`;return}
  const cfg=await (await fetch('/config')).json();
  selectors.innerHTML='<div class="sub">BLE</div>'+
    data.devices.map(r=>`<p><button class="btn" onclick="connectBle(${q(r.address)},${q(r.name||'')})">连接</button> ${selected(cfg.ble_address,r.address)?pill(cfg.ble_connected?"已连接":"已选择"):""} <b>${r.name||'(未命名)'}</b> <span class="mono">${r.address} ${r.rssi??''}</span> ${r.suggested?'<span class=ok>目标设备</span>':''}</p>`).join('')+
    '<button class="btn" onclick="connectBle(null,null)">自动连接 BLE</button> <button class="btn" onclick="disconnectBle(false)">断开 BLE</button> <button class="btn" onclick="backSelectors()">返回</button>';
}
async function selectSerial(port){await post('/config',{serial_port:port}); refresh();}
async function selectBle(address,name){await post('/config',{ble_address:address,ble_name:name}); refresh();}
async function restartModule(name){
  const result=await post('/module/restart',{module:name});
  if(!result.ok){alert(result.error||'重启失败');return}
  if(name==='hub'){status.textContent='重启中'; setTimeout(refresh,2500); return}
  refresh();
}
async function refresh(){
  const s=await (await fetch('/state')).json();
  const cfg=await (await fetch('/config')).json();
  status.textContent=s.transport_status||"idle"; status.className="pill "+cls(s.transport_status);
  renderStatusBar(s);
  current.innerHTML=`<div class="hero"><div class="hero-anim">${s.current_anim||"待机"}</div>${pill(s.transport_status)}</div>`;
  document.querySelectorAll('#buttons .chip').forEach(b=>b.classList.toggle('active',b.dataset.mask===ledMask));
  transport.innerHTML=`<div class="metas">`+
    meta("模式",modeLabel(cfg.transport))+
    meta("串口",cfg.serial_connected?`已连接 ${cfg.serial_port||""}`:(cfg.serial_port?`已选择 ${cfg.serial_port}`:"自动检测"))+
    meta("BLE",`${cfg.ble_connected?"已连接 ":""}${cfg.ble_name||"ClawdTank"} ${cfg.ble_address||""}`)+
    meta("自定义灯效",cfg.use_status_effects?"已应用":"未应用")+
    meta("最近通道",s.transport)+meta("消息",s.transport_message)+meta("延迟",s.last_send_ms!=null?s.last_send_ms+" ms":null)+`</div>`;
  if(selectorView==="home"){renderSelectors(cfg)}
}
setInterval(refresh,1000); refresh();
setInterval(renderSignal,120);
renderEffectControls();
renderSignal();
loadStatusEffects();
</script></main></body></html>"""


class HubHandler(BaseHTTPRequestHandler):
    hub: HubState

    def log_message(self, fmt: str, *args: Any) -> None:
        log(fmt % args)

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/state":
            self.send_json(self.hub.snapshot())
        elif path == "/events":
            self.send_json(self.hub.recent_events())
        elif path == "/modules":
            with self.hub.lock:
                self.send_json(self.hub.modules_locked())
        elif path == "/scan/serial":
            self.send_json(self.hub.scan_serial())
        elif path == "/scan/ble":
            self.send_json(self.hub.scan_ble())
        elif path == "/config":
            self.send_json(self.hub.config())
        elif path == "/status-effects":
            self.send_json(self.hub.status_effect_config())
        elif path == "/health":
            self.send_json({"ok": True, "pid": os.getpid()})
        else:
            self.send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            data = self.read_json()
            if path == "/hook":
                self.send_json(self.hub.deliver(data))
            elif path == "/send":
                data.setdefault("source", "manual")
                data.setdefault("client_id", "manual")
                data.setdefault("client_kind", "manual")
                self.send_json(self.hub.deliver(data))
            elif path == "/config":
                self.send_json(self.hub.update_config(data))
            elif path == "/status-effects":
                self.send_json(self.hub.update_status_effects(data))
            elif path == "/connect":
                self.send_json(self.hub.connect_transport(data))
            elif path == "/disconnect":
                self.send_json(self.hub.disconnect_transport(data))
            elif path == "/module/restart":
                module = str(data.get("module") or "")
                self.send_json(self.hub.restart_module(module, self.server))
            else:
                self.send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:
            log(f"request failed: {exc}")
            self.send_json({"ok": False, "error": str(exc)}, 500)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("CLAWD_TANK_HUB_HOST", DEFAULT_HOST))
    parser.add_argument("--port", dest="hub_port", type=int, default=int(os.environ.get("CLAWD_TANK_HUB_PORT", DEFAULT_PORT)))
    parser.add_argument("--transport", default=os.environ.get("CLAWD_TANK_TRANSPORT", "auto"))
    parser.add_argument("--serial-port", dest="port_override", default=None)
    parser.add_argument("--baud", type=int, default=None)
    parser.add_argument("--ble-address", default=os.environ.get("CLAWD_TANK_BLE_ADDRESS"))
    parser.add_argument("--ble-name", default=os.environ.get("CLAWD_TANK_BLE_NAME", bridge.DEFAULT_BLE_NAME))
    args = parser.parse_args()
    args.port = args.port_override

    try:
        if not acquire_file_lock(RUN_LOCK_PATH):
            log("hub start skipped; run lock is active")
            return 0
        HubHandler.hub = HubState(args)
        try:
            server = ThreadingHTTPServer((args.host, args.hub_port), HubHandler)
        except OSError as exc:
            log(f"hub bind skipped http://{args.host}:{args.hub_port}: {exc}")
            return 0
        write_pid()
        log(f"hub listening http://{args.host}:{args.hub_port} transport={args.transport}")
        print(f"Clawd Hook Hub: http://{args.host}:{args.hub_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
    finally:
        release_file_lock(RUN_LOCK_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

