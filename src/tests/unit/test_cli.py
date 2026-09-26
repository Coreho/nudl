"""The command line: the part of nudl that runs on every platform.

`clean()` itself is specified by `test_clean.py`. What is tested here is the plumbing
around it — the parts a shell pipeline actually touches: what goes to stdout, what goes
to stderr, and the exit code.
"""

from __future__ import annotations

import io
import json

import pytest

from src import __version__, cli

UGLY = "https://www.amazon.com/dp/B08X?tag=aff-20&utm_source=nl&psc=1"
CLEAN = "https://www.amazon.com/dp/B08X?psc=1"


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


def test_a_url_argument_comes_back_clean(monkeypatch, capsys) -> None:
    assert _run(monkeypatch, capsys, [UGLY])[:2] == (0, CLEAN + "\n")


def test_a_pipe_is_cleaned_line_by_line(monkeypatch, capsys) -> None:
    """`Get-Clipboard | nudl | Set-Clipboard` — CRLFs and blank lines included."""
    code, out, _ = _run(monkeypatch, capsys, [], stdin=f"{UGLY}\r\n\r\nhttps://youtu.be/a?si=x\r\n")
    assert code == 0
    assert out.splitlines() == [CLEAN, "https://youtu.be/a"]


def test_a_link_with_nothing_to_strip_is_printed_exactly_as_it_came(monkeypatch, capsys) -> None:
    url = "https://example.com/a?id=42&page=2"
    assert _run(monkeypatch, capsys, [url])[1] == url + "\n"


def test_verbose_says_what_happened(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, ["--verbose", UGLY, CLEAN])[1]
    assert f"CLEANED:    {UGLY}  ->  {CLEAN}" in out
    assert f"UNCHANGED: {CLEAN}" in out


def test_an_exception_domain_is_left_alone(monkeypatch, capsys) -> None:
    out = _run(monkeypatch, capsys, ["--exceptions", "amazon.com", UGLY])[1]
    assert out == UGLY + "\n"


def test_nothing_to_read_on_a_terminal_explains_instead_of_hanging(monkeypatch, capsys) -> None:
    """Reading a TTY would wait forever for an EOF nobody knows to type."""
    code, out, err = _run(monkeypatch, capsys, [], tty=True)
    assert code == 2
    assert out == ""
    assert "pipe some in" in err


def test_an_empty_pipe_is_a_usage_error(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        _run(monkeypatch, capsys, [], stdin="   \n")
    assert exit_.value.code == 2


def test_a_broken_rules_file_warns_on_stderr_and_still_cleans(
    monkeypatch, capsys, tmp_path
) -> None:
    """stdout stays pure URLs even when something is wrong: it is feeding a pipe."""
    broken = tmp_path / "rules.json"
    broken.write_text("{ not json", encoding="utf-8")
    code, out, err = _run(monkeypatch, capsys, ["--rules", str(broken), UGLY])
    assert code == 0
    assert out == CLEAN + "\n"
    assert "using bundled rules instead" in err


def test_a_custom_rules_file_is_the_one_applied(monkeypatch, capsys, tmp_path) -> None:
    mine = tmp_path / "rules.json"
    mine.write_text(json.dumps({"global_tracker_keys": ["my_tracker"]}), encoding="utf-8")
    out = _run(monkeypatch, capsys, ["--rules", str(mine), "https://x.com/a?my_tracker=1&id=2"])[1]
    assert out == "https://x.com/a?id=2\n"


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
