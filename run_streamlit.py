from __future__ import annotations

import platform
import sys
from typing import Any


def _patch_platform_for_broken_wmi() -> None:
    """
    Some Windows setups have broken WMI (WinError 0x80041032), and Python 3.13's
    `platform.win32_ver()` may raise "not supported". Streamlit imports
    `platform.system()` during startup, which triggers this path.

    This wrapper patches `platform.win32_ver` to a safe fallback before importing
    Streamlit, so `streamlit run` works reliably.
    """

    def safe_win32_ver(*args: Any, **kwargs: Any) -> tuple[str, str, str, str]:
        return ("10", "10.0.0", "", "")

    try:
        platform.win32_ver = safe_win32_ver  # type: ignore[assignment]
    except Exception:
        pass


def main() -> None:
    _patch_platform_for_broken_wmi()
    from streamlit.web import cli as stcli  # noqa: WPS433 (runtime import)

    # Equivalent to: python -m streamlit run app.py --server.port 8502
    sys.argv = [
        sys.argv[0],
        "run",
        "app.py",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8502",
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()

