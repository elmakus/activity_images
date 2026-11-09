"""Run calendar lifecycle checks without test_behavior's global HA module stubs."""
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from unittest import SkipTest


def test_calendar_startup_real_ha_subprocess():
    try:
        installed = version("homeassistant")
    except PackageNotFoundError:
        raise SkipTest("Run smoke_calendar_startup_ha.py with HA Core 2026.11.0 installed") from None
    if installed != "2026.11.0":
        raise SkipTest(f"Calendar lifecycle smoke targets HA Core 2026.11.0, not {installed}")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("smoke_calendar_startup_ha.py"))],
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Calendar startup/lifecycle smoke passed" in result.stdout
