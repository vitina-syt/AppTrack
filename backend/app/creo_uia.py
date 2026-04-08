"""
Creo UIA Monitor — Windows UI Automation event capture.

Listens for UIA InvokePattern, FocusChanged, and WindowOpened events
from a target process (default: Creo Parametric → xtop.exe / creo_parametric.exe).

Falls back to window-title polling if comtypes / UIAutomation is unavailable.

Public API
----------
    monitor = CreoUiaMonitor(target_app="xtop.exe")
    monitor.start(callback)   # callback(event_dict) called from monitor thread
    monitor.stop()
"""
import threading
import time
import logging
import queue
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger("app.creo_uia")

# ── optional UIA via comtypes ────────────────────────────────────────────────

_UIA_OK = False
try:
    import comtypes
    import comtypes.client

    _uia_module = comtypes.client.GetModule("UIAutomationCore.dll")
    import comtypes.gen.UIAutomationClient as _UIAC

    _UIA_OK = True
except Exception as _e:
    logger.info("comtypes/UIAutomationCore not available (%s) — will use title-polling fallback", _e)

# ── optional Win32 for process-name → PID lookup ────────────────────────────

_WIN32 = False
try:
    import win32gui
    import win32process
    import psutil
    _WIN32 = True
except ImportError:
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_foreground_info():
    """(app_name, window_title) of current foreground window."""
    if not _WIN32:
        return None, None
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name(), title
    except Exception:
        return None, None


# ── UIA event sink (comtypes COM callback) ───────────────────────────────────

if _UIA_OK:
    class _FocusChangedHandler(comtypes.COMObject):
        """Fires when keyboard focus moves to a new UIA element."""
        _com_interfaces_ = [_UIAC.IUIAutomationFocusChangedEventHandler]

        def __init__(self, cb, target_pids):
            self._cb = cb
            self._pids = target_pids

        def HandleFocusChangedEvent(self, sender):
            try:
                pid = sender.CurrentProcessId
                if self._pids and pid not in self._pids:
                    return
                name = sender.CurrentName or ""
                ctrl = sender.CurrentLocalizedControlType or ""
                aid  = sender.CurrentAutomationId or ""
                if name:
                    self._cb({
                        "event_type": "uia_focus",
                        "timestamp": _utcnow(),
                        "uia_element_name": name,
                        "uia_element_type": ctrl,
                        "uia_automation_id": aid,
                    })
            except Exception:
                pass

    class _InvokeHandler(comtypes.COMObject):
        """Fires when a Button/MenuItem etc. is activated (InvokedEvent)."""
        _com_interfaces_ = [_UIAC.IUIAutomationEventHandler]

        def __init__(self, cb, target_pids):
            self._cb = cb
            self._pids = target_pids

        def HandleAutomationEvent(self, sender, eventId):
            try:
                pid = sender.CurrentProcessId
                if self._pids and pid not in self._pids:
                    return
                name = sender.CurrentName or ""
                ctrl = sender.CurrentLocalizedControlType or ""
                aid  = sender.CurrentAutomationId or ""
                self._cb({
                    "event_type": "uia_invoke",
                    "timestamp": _utcnow(),
                    "uia_element_name": name,
                    "uia_element_type": ctrl,
                    "uia_automation_id": aid,
                })
            except Exception:
                pass


# ── Main monitor class ───────────────────────────────────────────────────────

class CreoUiaMonitor:
    """
    Monitor UIA events from a target application.

    Parameters
    ----------
    target_app : str
        Process name to monitor (e.g. 'xtop.exe', 'creo_parametric.exe').
        Empty string = monitor all processes.
    poll_interval : float
        Seconds between title-polling cycles (fallback mode only).
    """

    def __init__(self, target_app: str = "xtop.exe", poll_interval: float = 1.0):
        self.target_app = target_app.lower()
        self.poll_interval = poll_interval
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._event_queue: queue.Queue = queue.Queue()
        self._callback: Optional[Callable] = None

    # ── public ───────────────────────────────────────────────────────────

    def start(self, callback: Callable[[dict], None]) -> None:
        """Start monitoring. *callback* is called from the monitor thread."""
        with self._lock:
            if self._running:
                return
            self._callback = callback
            self._running = True

        if _UIA_OK:
            self._thread = threading.Thread(
                target=self._run_uia, name="CreoUIA", daemon=True
            )
        else:
            self._thread = threading.Thread(
                target=self._run_polling, name="CreoUIA-poll", daemon=True
            )
        self._thread.start()
        logger.info("UIA monitor started (target=%s, uia=%s)", self.target_app, _UIA_OK)

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("UIA monitor stopped")

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # ── UIA mode ─────────────────────────────────────────────────────────

    def _target_pids(self):
        """Return set of PIDs whose process name matches target_app (empty = all)."""
        if not self.target_app or not _WIN32:
            return set()
        pids = set()
        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info["name"] and proc.info["name"].lower() == self.target_app:
                pids.add(proc.info["pid"])
        return pids

    def _run_uia(self) -> None:
        """Register COM event handlers and pump messages until stopped."""
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=_UIAC.IUIAutomation,
            )

            pids = self._target_pids()

            focus_handler = _FocusChangedHandler(self._dispatch, pids)
            uia.AddFocusChangedEventHandler(None, focus_handler)

            # InvokedEvent = 0x000119C3 (UIA_Invoke_InvokedEventId)
            INVOKE_EVENT_ID = 0x000119C3
            invoke_handler = _InvokeHandler(self._dispatch, pids)
            uia.AddAutomationEventHandler(
                INVOKE_EVENT_ID,
                uia.GetRootElement(),
                _UIAC.TreeScope_Subtree,
                None,
                invoke_handler,
            )

            # Pump COM messages
            import comtypes.messageloop
            while self.running:
                comtypes.messageloop.spin(timeout_ms=200)

            uia.RemoveFocusChangedEventHandler(focus_handler)
            uia.RemoveAllEventHandlers()
        except Exception as exc:
            logger.warning("UIA thread error: %s — falling back to polling", exc)
            self._run_polling()
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    # ── Polling fallback ─────────────────────────────────────────────────

    def _run_polling(self) -> None:
        """Simple foreground-window title polling when UIA is unavailable."""
        last_title = None
        last_app = None

        while True:
            with self._lock:
                if not self._running:
                    break

            app, title = _get_foreground_info()

            # Filter to target app if specified
            if self.target_app and app and app.lower() != self.target_app:
                app, title = None, None

            if title and title != last_title:
                last_title = title
                last_app = app
                self._dispatch({
                    "event_type": "uia_focus",
                    "timestamp": _utcnow(),
                    "app_name": app,
                    "uia_element_name": title,
                    "uia_element_type": "window",
                    "uia_automation_id": "",
                })

            time.sleep(self.poll_interval)

    # ── dispatch ─────────────────────────────────────────────────────────

    def _dispatch(self, event: dict) -> None:
        if self._callback:
            try:
                self._callback(event)
            except Exception as exc:
                logger.debug("UIA callback error: %s", exc)