"""The tray app's newer duties: what it refuses to read, what it counts, what it opens,
and what the Settings window is allowed to change.

Each of these is a promise the README makes out loud — "nudl doesn't read what your
password manager copies", "the count never leaves your machine", "the report form gets
nothing you didn't paste into it" — so each one is pinned here.
"""

from __future__ import annotations

import json

import pytest

from src import app as app_module
from src import autostart, config, html_clip, settings_ui, stats
from src.app import NudlApp
from src.clipboard import Snapshot
from src.tests.unit.test_undo import FakeClipboard

UGLY = "https://example.com/a?utm_source=x&id=1"
CLEAN = "https://example.com/a?id=1"


@pytest.fixture
def nudl(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake = FakeClipboard()
    monkeypatch.setattr(app_module, "clipboard", fake)
    instance = NudlApp()
    said: list[str] = []
    monkeypatch.setattr(instance.ui, "show_toast", lambda message, **_: said.append(message))
    return instance, fake, said


# -- what automatic mode refuses to read ---------------------------------------------------


def test_a_copy_marked_private_is_not_even_read(nudl, monkeypatch) -> None:
    """A password manager's copy: nudl must not look, let alone act."""
    instance, fake, _ = nudl
    instance.config["mode"] = "auto"
    fake.text = UGLY

    def private_snapshot(*, want_html: bool = False):
        return Snapshot(text=None, sequence=fake.sequence, private=True)

    monkeypatch.setattr(fake, "read_snapshot", private_snapshot)
    instance.on_clipboard_update()
    assert fake.text == UGLY


def test_the_hotkey_on_a_private_copy_says_why_nothing_happened(nudl, monkeypatch) -> None:
    instance, fake, said = nudl
    monkeypatch.setattr(
        fake,
        "read_snapshot",
        lambda **_: Snapshot(text=None, sequence=1, private=True),
    )
    instance.on_hotkey()
    assert said == ["nudl — the app you copied from marked it private"]


@pytest.mark.parametrize("configured", ["Code.exe", "code", "CODE.EXE"])
def test_copies_from_a_skipped_app_are_left_alone_in_automatic_mode(
    nudl, monkeypatch, configured
) -> None:
    instance, fake, _ = nudl
    instance.config.update(mode="auto", skip_apps=[configured])
    fake.text = UGLY
    monkeypatch.setattr(fake, "source_app", lambda: "Code.exe")

    def must_not_read(**_):
        raise AssertionError("read the clipboard of an app the user skips")

    monkeypatch.setattr(fake, "read_snapshot", must_not_read)
    instance.on_clipboard_update()
    assert fake.text == UGLY


def test_other_apps_are_still_cleaned(nudl, monkeypatch) -> None:
    instance, fake, _ = nudl
    instance.config.update(mode="auto", skip_apps=["Code.exe"])
    fake.text = UGLY
    monkeypatch.setattr(fake, "source_app", lambda: "chrome.exe")
    instance.on_clipboard_update()
    assert fake.text == CLEAN


def test_the_hotkey_ignores_the_skip_list(nudl, monkeypatch) -> None:
    """Pressing the hotkey is asking, whichever app the link came from."""
    instance, fake, _ = nudl
    instance.config.update(skip_apps=["Code.exe"])
    fake.text = UGLY
    monkeypatch.setattr(fake, "source_app", lambda: "Code.exe")
    instance.on_hotkey()
    assert fake.text == CLEAN


# -- the hotkey on text and on formatted text ----------------------------------------------


def test_the_hotkey_cleans_every_link_in_a_message_and_counts_them(nudl) -> None:
    instance, fake, said = nudl
    fake.text = f"two links: {UGLY} and https://youtu.be/v?si=abc — thanks"
    instance.on_hotkey()
    assert fake.text == f"two links: {CLEAN} and https://youtu.be/v — thanks"
    assert said[-1] == "nudl — removed 2 trackers from 2 links"
    assert instance.counter.stats.trackers == 2 and instance.counter.stats.links == 2


def _cf_html(fragment: str) -> bytes:
    from src.tests.unit.test_html_clip import cf_html

    return cf_html(fragment)


def test_formatted_text_keeps_its_formatting_and_loses_its_trackers(nudl) -> None:
    """ "the video" in plain text; the tracked URL only exists in the href."""
    instance, fake, _ = nudl
    fake.text = "the video"
    fake.html = _cf_html('<a href="https://youtu.be/v?si=abc">the video</a>')

    instance.on_hotkey()

    assert fake.text == "the video"
    assert b'href="https://youtu.be/v"' in fake.html


def test_undo_puts_the_formatting_back_too(nudl) -> None:
    instance, fake, _ = nudl
    original = _cf_html('<a href="https://youtu.be/v?si=abc">the video</a>')
    fake.text, fake.html = f"see {UGLY}", original

    instance.on_hotkey()
    assert instance.undo() is True

    assert fake.text == f"see {UGLY}"
    assert fake.html == original
    assert instance.counter.stats.links == 0, "undo left its clean on the count"


def test_unparseable_formatting_falls_back_to_plain_text(nudl, monkeypatch) -> None:
    instance, fake, _ = nudl
    fake.text, fake.html = f"see {UGLY}", b"garbage, not CF_HTML"
    instance.on_hotkey()
    assert fake.text == f"see {CLEAN}"
    assert fake.html is None


# -- the count ------------------------------------------------------------------------------


def test_the_count_survives_a_restart(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    stats.Counter().add(3, 2)
    again = stats.Counter()
    assert (again.stats.trackers, again.stats.links) == (3, 2)
    assert again.stats.describe() == "3 trackers removed from 2 links"


def test_a_mangled_count_file_costs_the_count_not_a_crash(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config.stats_path().parent.mkdir(parents=True, exist_ok=True)
    for junk in ("{not json", "[]", json.dumps({"trackers": -4, "links": "many"})):
        config.stats_path().write_text(junk, encoding="utf-8")
        assert stats.Counter().stats == stats.Stats()


def test_the_tooltip_says_what_nudl_has_done(nudl) -> None:
    instance, _, _ = nudl
    assert instance._tooltip() == "nudl — clean links"
    instance.counter.add(1, 1)
    assert instance._tooltip() == "nudl — 1 tracker removed from 1 link"


# -- the report link --------------------------------------------------------------------------


def test_reporting_opens_the_issue_form_and_sends_no_link(nudl, monkeypatch) -> None:
    instance, fake, _ = nudl
    fake.text = "https://private.example.com/share?token=secret"
    opened: list[str] = []
    monkeypatch.setattr(app_module.os, "startfile", opened.append, raising=False)

    instance._report_link()

    assert opened == [app_module.ISSUES_URL]
    assert "private" not in opened[0]


def test_only_nudls_own_issue_tracker_can_be_opened(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(app_module.os, "startfile", opened.append, raising=False)
    NudlApp._open_url("https://evil.example.com/")
    NudlApp._open_url("C:\\Windows\\System32\\calc.exe")
    assert opened == []


# -- the Settings window's rules ---------------------------------------------------------------


def _form(**overrides) -> settings_ui.SettingsForm:
    base = settings_ui.SettingsForm.from_config(config.DEFAULTS)
    return settings_ui.SettingsForm(**{**base.__dict__, **overrides})


def test_the_form_tidies_what_people_paste() -> None:
    updates, problem = settings_ui.parse_form(
        _form(
            hotkey="  Ctrl + Alt+V ",
            exceptions="https://www.MyBank.com/login?x=1\n\n*.intranet.corp\nmybank.com",
            skip_apps="code\nC:\\Program Files\\KeePass\\KeePass.exe\nCODE.exe",
        )
    )
    assert problem is None
    assert updates["exceptions"] == ["mybank.com", "intranet.corp"]
    assert updates["skip_apps"] == ["code.exe", "KeePass.exe"]


def test_the_form_refuses_what_would_not_work() -> None:
    assert settings_ui.parse_form(_form(hotkey="v"))[1].startswith("Hotkey:")
    assert settings_ui.parse_form(_form(hotkey="ctrl+c"))[1].startswith("Hotkey:")
    assert "isn't a website" in settings_ui.parse_form(_form(exceptions="my bank"))[1]
    assert "isn't an app" in settings_ui.parse_form(_form(skip_apps="a|b"))[1]


def test_saving_writes_the_config_and_asks_the_pump_to_rebind(nudl, monkeypatch) -> None:
    instance, _, _ = nudl
    asked: list[str] = []
    monkeypatch.setattr(instance, "_request_rebind", lambda: asked.append("rebind"))

    problem = instance._save_settings(_form(hotkey="ctrl+shift+l", exceptions="mybank.com"))

    assert problem is None
    saved = config.load()
    assert saved["hotkey"] == "ctrl+shift+l" and saved["exceptions"] == ["mybank.com"]
    assert asked == ["rebind"]


def test_a_refused_autostart_saves_nothing(nudl, monkeypatch) -> None:
    instance, _, _ = nudl

    def refuse() -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(autostart, "enable", refuse)
    problem = instance._save_settings(_form(run_at_startup=True, hotkey="ctrl+shift+l"))

    assert problem and "Nothing was saved" in problem
    assert config.load()["hotkey"] == config.DEFAULTS["hotkey"]


class _Window:
    hwnd = 1234


def test_a_shortcut_windows_refuses_keeps_the_old_one(nudl, monkeypatch) -> None:
    instance, _, said = nudl
    instance.window = _Window()
    instance._registered_hotkey = "ctrl+alt+v"
    instance.config["hotkey"] = "ctrl+shift+l"
    registered: list[str] = []

    def register(_hwnd, _id, combo):
        if combo == "ctrl+shift+l":
            raise app_module.hotkey.HotkeyUnavailable("ctrl+shift+l is taken by another app")
        registered.append(combo)

    monkeypatch.setattr(app_module.hotkey, "register", register)
    monkeypatch.setattr(app_module.hotkey, "unregister", lambda *_: None)

    instance._rebind_hotkey()

    assert registered == ["ctrl+alt+v"]
    assert instance.config["hotkey"] == "ctrl+alt+v"
    assert config.load()["hotkey"] == "ctrl+alt+v"
    assert said[-1] == "nudl — ctrl+shift+l is taken by another app. Kept ctrl+alt+v."


def test_a_shortcut_windows_accepts_is_live(nudl, monkeypatch) -> None:
    instance, _, said = nudl
    instance.window = _Window()
    instance._registered_hotkey = "ctrl+alt+v"
    instance.config["hotkey"] = "ctrl+shift+l"
    monkeypatch.setattr(app_module.hotkey, "register", lambda *_: None)
    monkeypatch.setattr(app_module.hotkey, "unregister", lambda *_: None)

    instance._rebind_hotkey()

    assert instance._registered_hotkey == "ctrl+shift+l"
    assert said[-1] == "nudl — shortcut is now ctrl+shift+l"


def test_html_clip_is_what_the_app_uses() -> None:
    """Guards the wiring: the app must clean formatted copies through html_clip."""
    assert app_module.html_clip is html_clip
