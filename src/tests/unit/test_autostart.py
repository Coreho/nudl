"""Start with Windows: the Run value, and whether nudl tells the truth about it.

The failure this guards is the quiet one. A tray tool that does not come back after a
reboot is forgotten, and a Run value pointing at an exe that has since moved looks, from
the tray, exactly like one that works.

These tests use their OWN value name under the real HKCU Run key. Sharing the production
name would rewrite the developer's actual autostart on every run of the suite.
"""

from __future__ import annotations

import sys
import uuid
import winreg

import pytest

from src import autostart
from src import config as config_module
from src.app import NudlApp


@pytest.fixture
def value_name():
    name = f"nudl-test-{uuid.uuid4().hex[:8]}"
    yield name
    autostart.disable(name)


def test_the_production_name_is_not_what_the_tests_write(value_name) -> None:
    assert value_name != autostart.VALUE_NAME


def test_enable_writes_this_nudls_command(value_name) -> None:
    autostart.enable(value_name)
    assert autostart.registered(value_name) == autostart.command()


def test_disable_removes_it_and_is_happy_to_do_so_twice(value_name) -> None:
    autostart.enable(value_name)
    autostart.disable(value_name)
    autostart.disable(value_name)  # already off is the state we wanted, not an error
    assert autostart.registered(value_name) is None


def test_sync_off_removes_a_value_left_behind(value_name) -> None:
    autostart.enable(value_name)
    assert autostart.sync(False, value_name) is True
    assert autostart.registered(value_name) is None


def test_sync_on_writes_a_missing_value(value_name) -> None:
    assert autostart.sync(True, value_name) is True
    assert autostart.registered(value_name) == autostart.command()


def test_sync_on_repairs_a_value_whose_exe_has_gone(value_name, tmp_path) -> None:
    """The portable zip, unzipped somewhere new. The old path starts nothing at logon."""
    _write(value_name, f'"{tmp_path / "moved-away" / "nudlw.exe"}"')
    autostart.sync(True, value_name)
    assert autostart.registered(value_name) == autostart.command()


def test_sync_on_leaves_another_real_nudl_alone(value_name) -> None:
    """A checkout must not steal autostart from the installed copy on every launch."""
    elsewhere = f'"{sys.executable}" --some-other-nudl'
    _write(value_name, elsewhere)
    autostart.sync(True, value_name)
    assert autostart.registered(value_name) == elsewhere


def test_the_frozen_build_starts_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\nudl\nudlw.exe")
    assert autostart.command() == r'"C:\Apps\nudl\nudlw.exe"'


def test_a_checkout_uses_pythonw_so_no_console_opens_at_logon(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(autostart.sysconfig, "get_path", lambda _name: str(tmp_path))
    cmd = autostart.command()
    assert cmd.endswith(" -m src.app")
    if (autostart.Path(sys.executable).with_name("pythonw.exe")).exists():
        assert "pythonw.exe" in cmd


@pytest.mark.parametrize(
    ("cmd", "target"),
    [
        (r'"C:\Program Files\nudl\nudlw.exe"', r"C:\Program Files\nudl\nudlw.exe"),
        (r'"C:\Py\pythonw.exe" -m src.app', r"C:\Py\pythonw.exe"),
        (r"C:\nudl\nudlw.exe --flag", r"C:\nudl\nudlw.exe"),
    ],
)
def test_the_target_is_the_first_token(cmd: str, target: str) -> None:
    assert str(autostart._target(cmd)) == target


# -- the app: first run asks, the tray tick tells the truth -----------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    instance = NudlApp()
    monkeypatch.setattr(instance.ui, "show_toast", lambda *_a, **_k: None)
    return instance


@pytest.mark.parametrize("answer", [True, False])
def test_first_run_remembers_the_answer(app, monkeypatch: pytest.MonkeyPatch, answer) -> None:
    monkeypatch.setattr(app.ui, "ask_first_run", lambda **_: ("auto", answer))
    app._first_run()
    saved = config_module.load()
    assert saved["run_at_startup"] is answer
    assert saved["mode"] == "auto"
    assert saved["first_run_complete"] is True


def test_a_refused_toggle_leaves_the_setting_alone(app, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the registry says no, the tick must not claim nudl will start at logon."""

    def refuse() -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(autostart, "enable", refuse)
    app._toggle_run_at_startup()
    assert app.config["run_at_startup"] is False


def test_a_toggle_that_works_is_saved(app, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(autostart, "enable", lambda: calls.append("on"))
    monkeypatch.setattr(autostart, "disable", lambda: calls.append("off"))

    app._toggle_run_at_startup()
    app._toggle_run_at_startup()

    assert calls == ["on", "off"]
    assert config_module.load()["run_at_startup"] is False


def _write(value_name: str, cmd: str) -> None:
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, autostart.RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, cmd)
