"""
AutoCAD COM Automation Monitor.

Captures real AutoCAD operations via the AutoCAD ActiveX/COM API:
  • Application events: BeginCommand / EndCommand / BeginSave / OpenDrawing …
  • Document events: ObjectAdded / ObjectModified / LayoutSwitched …

Falls back to UIA title-polling when AutoCAD is not running or COM is unavailable.

Usage
-----
    mon = AutoCADMonitor()
    mon.start(callback)   # callback(event_dict)  called from monitor thread
    mon.stop()
"""
import os
import sys
import threading
import time
import logging
import queue
from datetime import datetime, timezone
from typing import Optional, Callable, Set

logger = logging.getLogger("app.autocad_monitor")

# ── AutoCAD command categories ────────────────────────────────────────────────

COMMAND_CATEGORIES: dict[str, Set[str]] = {
    "draw":     {"LINE","CIRCLE","ARC","PLINE","POLYLINE","SPLINE","ELLIPSE",
                 "RECTANGLE","POLYGON","DONUT","POINT","RAY","XLINE","REGION",
                 "BOUNDARY","SOLID","REVCLOUD","WIPEOUT"},
    "edit":     {"MOVE","COPY","ROTATE","SCALE","STRETCH","TRIM","EXTEND",
                 "FILLET","CHAMFER","MIRROR","OFFSET","ARRAY","EXPLODE","PEDIT",
                 "JOIN","BREAK","LENGTHEN","DIVIDE","MEASURE","ERASE",
                 "UNDO","REDO","MATCHPROP","CHANGE","CHPROP"},
    "3d":       {"EXTRUDE","REVOLVE","SWEEP","LOFT","UNION","SUBTRACT",
                 "INTERSECT","THICKEN","PRESSPULL","BOX","SPHERE","CYLINDER",
                 "CONE","TORUS","WEDGE","PYRAMID","SLICE","SECTION","HELIX",
                 "SHELL","3DROTATE","3DMOVE","3DSCALE","3DALIGN","3DMIRROR","3DARRAY"},
    "annotate": {"MTEXT","TEXT","DTEXT","DIMLINEAR","DIMALIGNED","DIMANGULAR",
                 "DIMRADIUS","DIMDIAMETER","DIMORDINATE","DIMBASELINE",
                 "DIMCONTINUE","LEADER","MLEADER","TOLERANCE","HATCH",
                 "BHATCH","GRADIENT","TABLE","DIMSPACE","DIMBREAK"},
    "view":     {"ZOOM","PAN","3DORBIT","VIEW","VPOINT","REGEN","REGENALL",
                 "REDRAW","VSCURRENT","VISUALSTYLES","RENDER","SHADE","HIDE",
                 "CAMERA","PLAN","NAVVCUBE","NAVSWHEEL"},
    "layer":    {"LAYER","LAYMCH","LAYON","LAYOFF","LAYFREZ","LAYTHW",
                 "LAYISO","LAYUNISO","LAYDEL","LAYERP","COPYTOLAYER",
                 "LAYCUR","LAYWALK","LAYERSTATE"},
    "block":    {"BLOCK","WBLOCK","INSERT","XREF","XATTACH","XCLIP","XBIND",
                 "BEDIT","REFEDIT","REFCLOSE","ATTDEF","ATTEDIT","ATTSYNC",
                 "EATTEDIT","DATAEXTRACTION"},
    "file":     {"NEW","OPEN","CLOSE","SAVE","SAVEAS","PUBLISH","PLOT",
                 "PRINT","EXPORT","EXPORTPDF","IMPORT","RECOVER","AUDIT",
                 "PURGE","QSAVE"},
}

# Build reverse map: command_name → category
_CMD_TO_CAT: dict[str, str] = {}
for _cat, _cmds in COMMAND_CATEGORIES.items():
    for _c in _cmds:
        _CMD_TO_CAT[_c] = _cat


def categorize_command(cmd: str) -> str:
    """Return the category string for an AutoCAD command, or 'other'."""
    return _CMD_TO_CAT.get(cmd.upper().strip(), "other")


# ── optional win32com / pythoncom ─────────────────────────────────────────────

_COM_OK = False
try:
    import win32com.client
    import win32event
    import pythoncom
    _COM_OK = True
except ImportError:
    logger.info("pywin32 not available — AutoCAD COM events disabled; will use title-polling")

# ── optional Win32 for title polling fallback ─────────────────────────────────

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


def _get_acad_foreground():
    """(app_name, window_title) when AutoCAD is the foreground window."""
    if not _WIN32:
        return None, None
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = psutil.Process(pid).name()
        if "acad" in name.lower():
            return name, title
        return None, None
    except Exception:
        return None, None


# ── COM event sink factories ──────────────────────────────────────────────────
#
# win32com.client.WithEvents(obj, EventClass) instantiates EventClass with NO
# arguments internally, so the class must have a no-arg constructor.
# We use factory functions that close over the callback, producing a fresh
# class each time — this is the standard pywin32 pattern for this scenario.

def _make_app_event_class(callback):
    """Return a fresh AcadApplication event class with *callback* baked in."""

    class _AcadAppEvents:
        def OnBeginCommand(self, CommandName):
            cat = categorize_command(CommandName)
            callback({
                "event_type": "uia_invoke",
                "timestamp": _utcnow(),
                "uia_element_name": CommandName.upper(),
                "uia_element_type": f"acad_cmd:{cat}",
                "uia_automation_id": f"BeginCommand:{CommandName}",
            })

        def OnEndCommand(self, CommandName):
            callback({
                "event_type": "uia_invoke",
                "timestamp": _utcnow(),
                "uia_element_name": f"{CommandName.upper()} ✓",
                "uia_element_type": f"acad_cmd:{categorize_command(CommandName)}",
                "uia_automation_id": f"EndCommand:{CommandName}",
            })

        def OnBeginSave(self, FileName):
            callback({
                "event_type": "uia_invoke",
                "timestamp": _utcnow(),
                "uia_element_name": "SAVE",
                "uia_element_type": "acad_cmd:file",
                "uia_automation_id": f"BeginSave:{FileName}",
                "window_title": FileName,
            })

        def OnEndSave(self, FileName):
            callback({
                "event_type": "uia_invoke",
                "timestamp": _utcnow(),
                "uia_element_name": "SAVE ✓",
                "uia_element_type": "acad_cmd:file",
                "uia_automation_id": f"EndSave:{FileName}",
                "window_title": FileName,
            })

        def OnNewDrawing(self, TemplateName):
            callback({
                "event_type": "app_open",
                "timestamp": _utcnow(),
                "app_name": "acad.exe",
                "window_title": f"New drawing (template: {TemplateName})",
                "uia_element_name": "NEW",
                "uia_element_type": "acad_cmd:file",
            })

        def OnOpenDrawing(self, FileName):
            callback({
                "event_type": "app_open",
                "timestamp": _utcnow(),
                "app_name": "acad.exe",
                "window_title": FileName,
                "uia_element_name": "OPEN",
                "uia_element_type": "acad_cmd:file",
                "uia_automation_id": f"OpenDrawing:{FileName}",
            })

        def OnWindowChanged(self, Window):
            try:
                cap = getattr(Window, "Caption", "") or ""
                if cap:
                    callback({
                        "event_type": "uia_focus",
                        "timestamp": _utcnow(),
                        "app_name": "acad.exe",
                        "window_title": cap,
                        "uia_element_name": cap,
                        "uia_element_type": "window",
                    })
            except Exception:
                pass

        def OnBeginQuit(self):
            callback({
                "event_type": "uia_invoke",
                "timestamp": _utcnow(),
                "uia_element_name": "QUIT",
                "uia_element_type": "acad_cmd:file",
            })

    return _AcadAppEvents


def _make_doc_event_class(callback):
    """Return a fresh AcadDocument event class with *callback* baked in."""

    class _AcadDocEvents:
        def OnObjectAdded(self, Object):
            try:
                entity_type = getattr(Object, "ObjectName", "") or ""
                layer       = getattr(Object, "Layer", "") or ""
                callback({
                    "event_type": "uia_invoke",
                    "timestamp": _utcnow(),
                    "uia_element_name": entity_type or "Entity",
                    "uia_element_type": "acad_object:added",
                    "uia_automation_id": f"layer={layer}",
                })
            except Exception:
                pass

        def OnObjectModified(self, Object):
            pass  # too frequent — skip

        def OnLayoutSwitched(self, LayoutName):
            callback({
                "event_type": "uia_focus",
                "timestamp": _utcnow(),
                "app_name": "acad.exe",
                "uia_element_name": LayoutName,
                "uia_element_type": "acad_layout",
            })

        def OnSelectionChanged(self):
            pass  # too frequent — skip

    return _AcadDocEvents


# ── AutoCADMonitor ────────────────────────────────────────────────────────────

class AutoCADMonitor:
    """
    Monitor AutoCAD operations via COM Automation events.

    Falls back to foreground-window title polling when COM is unavailable
    or AutoCAD is not running.

    Parameters
    ----------
    poll_interval : float
        Seconds between reconnection attempts / title-polling cycles.
    com_pump_interval : float
        Seconds between pythoncom.PumpWaitingMessages() calls.
        Smaller = more responsive events; larger = less CPU / less AutoCAD impact.
        Default 0.1 s (100 ms) is a good balance.
    min_event_interval : float
        Minimum seconds between two events with the same key (rate-limiting).
        Prevents event floods from ARRAY / HATCH operations.
    """

    ACAD_PROG_IDS = [
        "AutoCAD.Application",          # generic / latest
        "AutoCAD.Application.25",       # 2025
        "AutoCAD.Application.24",       # 2024
        "AutoCAD.Application.23",       # 2023
        "AutoCAD.Application.22",       # 2022
    ]

    def __init__(
        self,
        poll_interval: float = 2.0,
        com_pump_interval: float = 0.1,
        min_event_interval: float = 0.05,
    ):
        self.poll_interval      = poll_interval
        self.com_pump_interval  = com_pump_interval
        self.min_event_interval = min_event_interval
        self._lock              = threading.Lock()
        self._running           = False
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable]       = None
        # Rate-limiter: last dispatch time per event key
        self._last_ts: dict[str, float] = {}

    # ── public ───────────────────────────────────────────────────────────

    def start(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if self._running:
                return
            self._callback = callback
            self._running  = True

        if _COM_OK:
            self._thread = threading.Thread(
                target=self._run_com, name="AutoCAD-COM", daemon=True
            )
        else:
            self._thread = threading.Thread(
                target=self._run_polling, name="AutoCAD-poll", daemon=True
            )
        self._thread.start()
        logger.info("AutoCAD monitor started (com=%s)", _COM_OK)

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=6)
        logger.info("AutoCAD monitor stopped")

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    # ── COM mode ──────────────────────────────────────────────────────────

    def _get_acad_com(self):
        """Try to connect to a running AutoCAD instance via COM (early-bound)."""
        # In a PyInstaller frozen bundle sys.prefix points to the read-only bundle
        # dir, so win32com.gencache.EnsureDispatch fails trying to write the
        # generated type-library wrappers there.  Redirect to a writable AppData
        # directory *before* the first EnsureDispatch call.
        if getattr(sys, "frozen", False):
            import win32com as _win32com
            _gen = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "StepCast", "gen_py",
            )
            os.makedirs(_gen, exist_ok=True)
            _win32com.__gen_path__ = _gen

        for prog_id in self.ACAD_PROG_IDS:
            try:
                late = win32com.client.GetActiveObject(prog_id)
                # Upgrade to early-bound dispatch so WithEvents can find the
                # event interface — plain GetActiveObject returns a late-bound
                # object that lacks the type-library metadata WithEvents needs.
                return win32com.client.gencache.EnsureDispatch(late)
            except Exception:
                pass
        return None

    def _run_com(self) -> None:
        pythoncom.CoInitialize()
        stop_event = win32event.CreateEvent(None, 0, 0, None)
        try:
            while self.running:
                acad = self._get_acad_com()
                if acad is None:
                    logger.info("AutoCAD not running — waiting …")
                    time.sleep(self.poll_interval)
                    continue

                logger.info("Connected to AutoCAD via COM")
                self._dispatch({
                    "event_type": "app_open",
                    "timestamp": _utcnow(),
                    "app_name": "acad.exe",
                    "window_title": "AutoCAD connected",
                    "uia_element_name": "AutoCAD",
                    "uia_element_type": "connection",
                })

                # Register application-level events.
                # WithEvents instantiates the class with no args, so we use
                # factory functions that close over self._dispatch.
                AppEvtClass = _make_app_event_class(self._dispatch)
                app_conn = win32com.client.WithEvents(acad, AppEvtClass)

                # Document events disabled: OnObjectAdded passes a live COM object
                # reference into the callback. Accessing its properties (.ObjectName,
                # .Layer) from our thread while AutoCAD is still constructing the
                # object causes re-entrant COM calls → Access Violation crash in AutoCAD.
                doc_conn = None

                # ── COM message pump ──────────────────────────────────────
                # IMPORTANT: Do NOT use MsgWaitForMultipleObjects(QS_ALLINPUT).
                # That flag intercepts ALL mouse/keyboard messages system-wide,
                # stealing input from AutoCAD and causing it to freeze.
                # Instead: sleep briefly and pump only pending COM messages.
                alive_check_counter = 0
                while self.running:
                    time.sleep(self.com_pump_interval)
                    pythoncom.PumpWaitingMessages()   # process pending COM callbacks only

                    # Check AutoCAD is still alive every ~2 s (not every 100 ms)
                    alive_check_counter += 1
                    if alive_check_counter >= int(2.0 / self.com_pump_interval):
                        alive_check_counter = 0
                        try:
                            _ = acad.Name
                        except Exception:
                            logger.info("AutoCAD closed — will reconnect")
                            break

        except Exception as exc:
            logger.warning("COM monitor error: %s — switching to polling", exc)
            self._run_polling()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    # ── Polling fallback ──────────────────────────────────────────────────

    def _run_polling(self) -> None:
        last_title = None
        while True:
            with self._lock:
                if not self._running:
                    break
            app, title = _get_acad_foreground()
            if title and title != last_title:
                last_title = title
                self._dispatch({
                    "event_type": "uia_focus",
                    "timestamp": _utcnow(),
                    "app_name": app or "acad.exe",
                    "window_title": title,
                    "uia_element_name": title,
                    "uia_element_type": "window",
                })
            time.sleep(self.poll_interval)

    # ── dispatch (with rate-limiter) ──────────────────────────────────────

    def _dispatch(self, event: dict) -> None:
        event.setdefault("app_name", "acad.exe")

        # Rate-limit: drop events of the same type that arrive faster than
        # min_event_interval seconds (prevents HATCH/ARRAY flooding).
        key = f"{event.get('event_type')}:{event.get('uia_element_name','')}"
        now = time.monotonic()
        last = self._last_ts.get(key, 0.0)
        if now - last < self.min_event_interval:
            return
        self._last_ts[key] = now

        if self._callback:
            try:
                self._callback(event)
            except Exception as exc:
                logger.debug("AutoCAD callback error: %s", exc)