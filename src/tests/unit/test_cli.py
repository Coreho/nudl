"""The command line: the part of nudl that runs on every platform.

`clean()` itself is specified by `test_clean.py` and `clean_text()` by
`test_clean_text.py`. What is tested here is the plumbing around them — the parts a
shell pipeline or a key binding actually touches: what goes to stdout, what goes to
stderr, what happens to the clipboard, and the exit code.
"""

from __future__ import annotations

import io
import json

import pytest

from src import __version__, cli, config, os_clipboard

UGLY = "https://www.amazon.com/dp/B08X?tag=aff-20&utm_source=nl&psc=1"
CLEAN = "https://www.amazon.com/dp/B08X?psc=1"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Every test gets its own empty config dir; the developer's real one never leaks in."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return config.config_dir()


class _Stdin(io.StringIO):
    def __init__(self, text: str, tty: bool = False) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _run(monkeypatch, capsys, argv, stdin: str = "", tty: bool = False):
    monkeypatch.setattr("sys.stdin", _Stdin(stdin, tty))
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


# -- arguments and pipes ------------------------------------------------------------------


def test_a_link_argument_comes_back_clean(monkeypatch, capsys) -> None:
    assert _run(monkeypatch, capsys, [UGLY])[:2] == (0, CLEAN + "\n")


def test_a_link_inside_an_argument_is_cleaned_in_place(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, [f"buy it here: {UGLY} (really)"])[1]
    assert out == f"buy it here: {CLEAN} (really)\n"


def test_a_pipe_comes_out_exactly_as_it_went_in_minus_the_trackers(monkeypatch, capsys) -> None:
    """`Get-Clipboard | nudl | Set-Clipboard`, or a whole Markdown file: blank lines, CRLFs
    and the absence of a final newline all survive. A filter that reflowed its input would
    be mangling the file it was asked to leave alone."""
    text = f"# Notes\r\n\r\n- {UGLY}\r\n- [video](https://youtu.be/a?si=x)"
    code, out, _ = _run(monkeypatch, capsys, [], stdin=text)
    assert code == 0
    assert out == f"# Notes\r\n\r\n- {CLEAN}\r\n- [video](https://youtu.be/a)"


def test_a_link_with_nothing_to_strip_is_printed_exactly_as_it_came(monkeypatch, capsys) -> None:
    url = "https://example.com/a?id=42&page=2"
    assert _run(monkeypatch, capsys, [url])[1] == url + "\n"


def test_verbose_reports_on_stderr_so_the_pipe_stays_clean(monkeypatch, capsys) -> None:
    code, out, err = _run(monkeypatch, capsys, ["--verbose", UGLY])
    assert out == CLEAN + "\n"
    assert UGLY in err and CLEAN in err and "tag, utm_source" in err


def test_an_exception_domain_is_left_alone(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, ["--exceptions", "amazon.com", UGLY])[1]
    assert out == UGLY + "\n"


def test_nothing_to_read_on_a_terminal_explains_instead_of_hanging(monkeypatch, capsys) -> None:
    """Reading a TTY would wait forever for an EOF nobody knows to type."""
    code, out, err = _run(monkeypatch, capsys, [], tty=True)
    assert code == 2
    assert out == ""
    assert "--clipboard" in err


def test_an_empty_pipe_is_a_usage_error(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        _run(monkeypatch, capsys, [], stdin="   \n")
    assert exit_.value.code == 2


# -- the same config and rules as the tray ----------------------------------------------


def test_the_users_rules_file_applies_without_being_asked_for(
    monkeypatch, capsys, isolated_config
) -> None:
    """One rules file for the tray and the CLI: a rule added once applies everywhere."""
    isolated_config.mkdir(parents=True, exist_ok=True)
    (isolated_config / "rules.json").write_text(
        json.dumps({"global_tracker_keys": ["my_tracker"]}), encoding="utf-8"
    )
    out = _run(monkeypatch, capsys, ["https://x.com/a?my_tracker=1&fbclid=2&id=3"])[1]
    assert out == "https://x.com/a?id=3\n", "the user's rule or a bundled one did not apply"


def test_the_configs_exceptions_apply_too(monkeypatch, capsys) -> None:
    config.save({**config.DEFAULTS, "exceptions": ["amazon.com"]})
    assert _run(monkeypatch, capsys, [UGLY])[1] == UGLY + "\n"


def test_a_broken_rules_file_warns_on_stderr_and_still_cleans(
    monkeypatch, capsys, tmp_path
) -> None:
    """stdout stays pure output even when something is wrong: it is feeding a pipe."""
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, ["--rules", str(broken), UGLY])
    assert code == 0
    assert out == CLEAN + "\n"
    assert "using the bundled rules" in err


def test_no_rules_file_at_all_is_not_worth_a_warning(monkeypatch, capsys) -> None:
    assert _run(monkeypatch, capsys, [UGLY])[2] == ""


# -- --clipboard --------------------------------------------------------------------------


@pytest.fixture
def fake_clipboard(monkeypatch: pytest.MonkeyPatch):
    board: dict[str, str | None] = {"text": None, "writes": 0}

    def write(text: str) -> None:
        board["text"] = text
        board["writes"] += 1

    monkeypatch.setattr(os_clipboard, "read", lambda: board["text"])
    monkeypatch.setattr(os_clipboard, "write", write)
    return board


def test_clipboard_mode_cleans_in_place_and_says_what_it_did(
    monkeypatch, capsys, fake_clipboard
) -> None:
    fake_clipboard["text"] = f"look {UGLY}"
    code, out, err = _run(monkeypatch, capsys, ["--clipboard"])
    assert code == 0
    assert fake_clipboard["text"] == f"look {CLEAN}"
    assert out == ""
    assert "removed 2 trackers: tag, utm_source" in err


def test_clipboard_mode_does_not_rewrite_a_clipboard_with_nothing_to_clean(
    monkeypatch, capsys, fake_clipboard
) -> None:
    """Rewriting it anyway would throw away whatever else was on it, for nothing."""
    fake_clipboard["text"] = CLEAN
    code, _, err = _run(monkeypatch, capsys, ["-c"])
    assert code == 0
    assert fake_clipboard["writes"] == 0
    assert "nothing to clean" in err


def test_clipboard_mode_with_no_text_says_so(monkeypatch, capsys, fake_clipboard) -> None:
    code, _, err = _run(monkeypatch, capsys, ["-c"])
    assert code == 1
    assert "no text on the clipboard" in err


def test_clipboard_mode_with_no_clipboard_tool_says_what_to_install(monkeypatch, capsys) -> None:
    def unavailable():
        raise os_clipboard.Unavailable("no clipboard tool found — install xclip (or xsel)")

    monkeypatch.setattr(os_clipboard, "read", unavailable)
    code, _, err = _run(monkeypatch, capsys, ["-c"])
    assert code == 1
    assert "install xclip" in err


# -- odds and ends ------------------------------------------------------------------------


def test_version(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit):
        _run(monkeypatch, capsys, ["--version"])
    assert capsys.readouterr().out.strip() == f"nudl {__version__}"


def test_the_tray_entry_point_refuses_politely_off_windows(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(SystemExit) as exit_:
        cli.tray()
    assert "Windows-only" in str(exit_.value.code)


def test_a_double_click_is_only_ever_detected_in_the_packaged_build(monkeypatch) -> None:
    """From source there is no nudlw.exe beside the interpreter to hand over to."""
    monkeypatch.delattr("sys.frozen", raising=False)
    assert cli._double_clicked() is False


# -- the clipboard backends off Windows ----------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "wayland", "tools", "expected_read"),
    [
        ("darwin", None, set(), ["pbpaste"]),
        ("linux", "wayland-0", {"wl-paste", "wl-copy", "xclip"}, ["wl-paste", "--no-newline"]),
        ("linux", None, {"xclip", "xsel"}, ["xclip", "-selection", "clipboard", "-o"]),
        ("linux", None, {"xsel"}, ["xsel", "--clipboard", "--output"]),
    ],
)
def test_each_os_uses_the_clipboard_tool_it_ships(
    monkeypatch, platform, wayland, tools, expected_read
) -> None:
    monkeypatch.setattr("sys.platform", platform)
    if wayland:
        monkeypatch.setenv("WAYLAND_DISPLAY", wayland)
    else:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(os_clipboard.shutil, "which", lambda name: name if name in tools else None)
    assert os_clipboard._commands()[0] == expected_read


def test_a_linux_box_with_no_clipboard_tool_is_told_what_to_install(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(os_clipboard.shutil, "which", lambda _name: None)
    with pytest.raises(os_clipboard.Unavailable, match="wl-clipboard"):
        os_clipboard._commands()
