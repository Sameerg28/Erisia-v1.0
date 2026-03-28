import copy
import ctypes
import ctypes.wintypes
import json
import os
import threading
import time
from datetime import datetime, timezone


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except Exception:
        return fallback


class WorldStateTracker:
    """
    Lightweight desktop tracker for Windows.
    Polls only foreground-window identity and cursor coordinates at a short interval.
    Runs optional heavy dump callback strictly when foreground window changes.
    """

    def __init__(
        self,
        state_file_path,
        poll_interval_seconds=0.25,
        write_interval_seconds=1.0,
        heavy_dump_callback=None,
    ):
        self.state_file_path = str(state_file_path)
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.write_interval_seconds = max(0.1, float(write_interval_seconds))
        self.heavy_dump_callback = heavy_dump_callback if callable(heavy_dump_callback) else None

        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._last_hwnd = None
        self._last_write_ts = 0.0
        self._last_heavy_dump = None
        self._state = {
            "tracker_mode": "win32_lightweight",
            "captured_at_utc": _utc_now_iso(),
            "active_window": {"hwnd": 0, "title": "", "pid": 0},
            "cursor": {"x": 0, "y": 0},
            "last_window_change_utc": None,
            "staleness_ms": 0,
            "heavy_dump": None,
            "errors": [],
        }

        self._user32 = ctypes.windll.user32

    @property
    def thread(self):
        return self._thread

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WorldStateTracker")
        self._thread.start()

    def stop(self, join_timeout=2.0):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def get_state(self):
        with self._lock:
            return copy.deepcopy(self._state)

    def _get_foreground_hwnd(self):
        try:
            return _safe_int(self._user32.GetForegroundWindow(), 0)
        except Exception:
            return 0

    def _get_window_title(self, hwnd):
        if not hwnd:
            return ""
        try:
            length = _safe_int(self._user32.GetWindowTextLengthW(hwnd), 0)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            copied = _safe_int(self._user32.GetWindowTextW(hwnd, buffer, length + 1), 0)
            if copied <= 0:
                return ""
            return buffer.value.strip()
        except Exception:
            return ""

    def _get_window_pid(self, hwnd):
        if not hwnd:
            return 0
        pid = ctypes.wintypes.DWORD(0)
        try:
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return _safe_int(pid.value, 0)
        except Exception:
            return 0

    def _get_cursor_position(self):
        point = ctypes.wintypes.POINT()
        try:
            ok = self._user32.GetCursorPos(ctypes.byref(point))
            if not ok:
                return {"x": 0, "y": 0}
            return {"x": _safe_int(point.x, 0), "y": _safe_int(point.y, 0)}
        except Exception:
            return {"x": 0, "y": 0}

    def _run_heavy_dump(self, active_window):
        if not self.heavy_dump_callback:
            return self._last_heavy_dump
        try:
            result = self.heavy_dump_callback(copy.deepcopy(active_window))
            if isinstance(result, dict):
                return result
            if result is None:
                return None
            return {"result": str(result)}
        except Exception as e:
            return {"error": str(e)}

    def _write_state_atomic(self, payload):
        state_dir = os.path.dirname(self.state_file_path)
        if state_dir and not os.path.exists(state_dir):
            os.makedirs(state_dir, exist_ok=True)

        tmp_path = f"{self.state_file_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)
        os.replace(tmp_path, self.state_file_path)

    def _run_loop(self):
        while not self._stop_event.is_set():
            started = time.time()
            errors = []

            hwnd = self._get_foreground_hwnd()
            title = self._get_window_title(hwnd)
            pid = self._get_window_pid(hwnd)
            cursor = self._get_cursor_position()

            active_window = {
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
            }

            window_changed = hwnd != self._last_hwnd
            if window_changed:
                self._last_hwnd = hwnd
                self._last_heavy_dump = self._run_heavy_dump(active_window)
                last_window_change_utc = _utc_now_iso()
            else:
                with self._lock:
                    last_window_change_utc = self._state.get("last_window_change_utc")

            snapshot = {
                "tracker_mode": "win32_lightweight",
                "captured_at_utc": _utc_now_iso(),
                "active_window": active_window,
                "cursor": cursor,
                "last_window_change_utc": last_window_change_utc,
                "staleness_ms": 0,
                "heavy_dump": self._last_heavy_dump,
                "errors": errors,
            }

            should_persist = window_changed or (started - self._last_write_ts) >= self.write_interval_seconds

            with self._lock:
                self._state = snapshot
                if should_persist:
                    try:
                        self._write_state_atomic(snapshot)
                        self._last_write_ts = started
                    except Exception as e:
                        self._state.setdefault("errors", []).append(str(e))

            elapsed = time.time() - started
            sleep_for = max(0.01, self.poll_interval_seconds - elapsed)
            self._stop_event.wait(sleep_for)
