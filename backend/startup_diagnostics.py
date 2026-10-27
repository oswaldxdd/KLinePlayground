"""Visible startup diagnostics for the windowed Windows release builds."""

import ctypes
import logging
import os
import sys
from pathlib import Path


_LOG_PATH = None
_LOG_STREAM = None
_FAILURE_DIALOG_SHOWN = False
_DIAGNOSTICS_DISABLED = False


def configure_startup_diagnostics(app_name, app_version, project_root):
    """Write stdout/stderr and Python logs to a portable log, with a user fallback."""
    global _LOG_PATH, _LOG_STREAM, _DIAGNOSTICS_DISABLED
    if "--smoke" in sys.argv:
        _DIAGNOSTICS_DISABLED = True
        return None

    candidates = [
        Path(project_root) / "data" / "logs" / f"{app_name}-startup.log",
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "KLinePlayground-US" / "logs" / f"{app_name}-startup.log"
        )
    else:
        candidates.append(
            Path.home() / "AppData" / "Local" / "KLinePlayground-US" / "logs" /
            f"{app_name}-startup.log"
        )

    for log_path in candidates:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                backup_path = log_path.with_suffix(log_path.suffix + ".1")
                if backup_path.exists():
                    backup_path.unlink()
                log_path.replace(backup_path)
            _LOG_STREAM = log_path.open("a", encoding="utf-8", buffering=1)
            _LOG_PATH = log_path
            sys.stdout = _LOG_STREAM
            sys.stderr = _LOG_STREAM
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            root_logger.handlers.clear()
            handler = logging.StreamHandler(_LOG_STREAM)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root_logger.addHandler(handler)
            logging.captureWarnings(True)
            logging.info(
                "Starting %s %s; executable=%s; project_root=%s",
                app_name,
                app_version,
                sys.executable,
                project_root,
            )
            return _LOG_PATH
        except OSError:
            continue
    return None


def report_startup_failure(message, app_title, error=None):
    """Persist a traceback and show a native Windows error dialog when possible."""
    global _FAILURE_DIALOG_SHOWN
    if _DIAGNOSTICS_DISABLED:
        return
    if error is not None:
        logging.exception("Startup failed: %s", message)
    else:
        logging.error("Startup failed: %s", message)
    if _FAILURE_DIALOG_SHOWN:
        return
    _FAILURE_DIALOG_SHOWN = True

    log_location = str(_LOG_PATH) if _LOG_PATH else "日志目录不可写"
    dialog_text = f"{message}\n\n诊断日志：{log_location}"
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, dialog_text, app_title, 0x10)
        except Exception:
            pass
