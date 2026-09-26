"""nudl — wiring, threading, and the undo contract.

Threading layout:

  main thread      pystray's icon loop. Owns the tray menu. Blocks in `Tray.run()`.
  "nudl-pump"      the Win32 message pump on the hidden message-only window. Owns the
                   hotkey registration and every clipboard write, because
                   `RegisterHotKey` must be called on the thread that pumps it.
  "nudl-ui"        Tk's loop, for the toast and the first-run dialog. Tk is
                   thread-affine, so it gets a thread of its own.

Shared state (the last clean, for undo) is guarded by a lock.

The rule that outranks everything here: **a no-op is silent, and every real change is
loud and reversible.** If nudl did not change the link, the user must not be able to
tell nudl ran at all.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import pywintypes
import win32api
import win32con
import win32event
import win32security
import winerror

from src import autostart, clean, clipboard, config, hotkey
from src.hidden_window import MessageWindow
from src.toast import OverlayUI
from src.tray import Tray

logger = logging.getLogger(__name__)

HOTKEY_ID = 1

#: Only one nudl may run per user session. A second instance would put a second icon in
#: the tray, fail to claim the (already-held) hotkey, and — once auto-watch lands —
#: run a second clipboard watcher that sees the first one's writes and cleans them
#: again. Two processes taking turns rewriting the clipboard is exactly the silent
#: mangling this product exists to prevent.
#:
#: A named mutex is the right primitive: the kernel creates it atomically, so there is
#: no window between "check" and "claim" for a second instance to slip through, and it
#: is released automatically if nudl crashes (unlike a PID file, which would be left
#: behind and lock the user out).
SINGLE_INSTANCE_MUTEX = "nudl-single-instance-mutex"

#: Press the hotkey again within this many seconds of a clean to undo it.
UNDO_WINDOW_SECONDS = 3.0

#: A JSON error carries a line and a column and can run to a full sentence. The toast is
#: a few words wide, so it gets a truncated copy; the untruncated reason is in the log,
#: which is where a user goes to actually fix the file.
RULES_ERROR_TOAST_CHARS = 90

#: Rotate the audit log at this size.
LOG_MAX_BYTES = 512 * 1024

#: The log exists so a suspicious user can audit exactly what nudl did — which means a
#: human has to be able to read it. It opens in Notepad, so it is laid out for Notepad:
#: one block per change, the before and the after stacked so the difference is obvious
#: at a glance. Plain ASCII throughout, because a stray "·" renders as mojibake in half
#: the editors on Windows.
LOG_HEADER = """\
# nudl - every link nudl has changed on this machine.
#
# Links nudl left alone are not recorded. Nothing in this file has ever left
# your computer.
#

"""


# Params whose VALUE is a secret. This is a LOGGING concern and nothing else: nudl never
# strips these from your link — doing that is how you turn a password-reset link into a
# 403 — but the audit log is an append-only plaintext file that lives forever, and
# writing an OAuth code or a session key into it verbatim would quietly turn nudl's trust
# feature into a credential store. The key is kept, only the value is masked, so the log
# still tells you what the link was carrying.
SECRET_LOG_KEYS = frozenset(
    {
        "code",
        "access_token",
        "id_token",
        "refresh_token",
        "auth",
        "authorization",
        "session",
        "sessionid",
        "sid",
        "key",
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "pwd",
        "otp",
        "pin",
        "client_secret",
        "oauth_token",
        "oauth_token_secret",
        "auth_token",
        "authtoken",
        "bearer",
        "id_token_hint",
        "assertion",
        "code_verifier",
        "reset_token",
        "reset_password_token",
        "confirmation_token",
        "activation_token",
        "invitation_token",
        "verification_code",
        "magic",
        "login_token",
        "email_token",
        "unsubscribe_token",
        "jwt",
        "ticket",
        "samlresponse",
        "sso",
        "token_id",
        "csrf_token",
        "nonce",
        "state",
        "passcode",
        "totp",
        "mfa",
        "invite",
        "invitation",
        "activation",
        "confirm",
        "email",
    }
    | set(clean.SIGNED_EXACT_KEYS)
)
SECRET_LOG_PREFIXES = clean.SIGNED_KEY_PREFIXES + ("oauth_", "x-api-", "aws_")
_SECRET_SUBSTRINGS = ("token", "secret", "password", "credential", "signature", "apikey")
_MASK = "***"


def _is_secret_key(key: str) -> bool:
    key_norm = unquote(key).strip().lower().replace("-", "_").replace(".", "_")
    prefixes = tuple(p.replace("-", "_") for p in SECRET_LOG_PREFIXES)
    return (
        key_norm in SECRET_LOG_KEYS
        or key_norm.startswith(prefixes)
        or any(s in key_norm for s in _SECRET_SUBSTRINGS)
    )


def _redact_query(query: str) -> str:
    out = []
    for token in re.split(r"([&;])", query):
        if token in ("&", ";"):
            out.append(token)
            continue
        key, sep, _ = token.partition("=")
        out.append(f"{key}={_MASK}" if sep and _is_secret_key(key) else token)
    return "".join(out)


def redact(url: str) -> str:
    """Mask credentials and secret-bearing params before a URL is written to disk.

    Three things get masked: the `user:hunter2@host` userinfo, the values of any
    param in SECRET_LOG_KEYS (in both query and fragment), and any key matching
    known secret prefixes or substrings. Everything else is left exactly as it is
    — the log has to stay useful, or nobody will trust it.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    netloc = parts.netloc
    if netloc and "@" in netloc:
        _credentials, _, host = netloc.rpartition("@")
        netloc = f"***@{host}"

    query = _redact_query(parts.query) if parts.query else parts.query
    fragment = _redact_query(parts.fragment) if "=" in parts.fragment else parts.fragment

    # Only rebuild if something actually changed: urlunsplit does not always round-trip
    # exotic inputs byte-for-byte, and the log should show the link the user really had.
    if netloc == parts.netloc and query == parts.query and fragment == parts.fragment:
        return url
    return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))


def format_log_entry(result: clean.CleanResult, stamp: str) -> str:
    """One human-readable block per change.

    [2026-07-13 01:09:16]  removed 2 trackers: tag, ref_
        before  https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1
        after   https://www.amazon.com/dp/B08X?psc=1
    """
    count = len(result.params_removed)
    if count == 1:
        headline = f"removed 1 tracker: {result.params_removed[0]}"
    elif count > 1:
        headline = f"removed {count} trackers: {', '.join(result.params_removed)}"
    elif urlsplit(result.original).netloc != urlsplit(result.result).netloc:
        # The host moved, so this was a redirect wrapper being unwrapped.
        headline = "unwrapped a redirect"
    else:
        # Changed, same host, no params named: a rawRule rewrote the link in place.
        # Calling that "unwrapped a redirect" is a lie in an audit log, which is the one
        # place a lie is least affordable.
        headline = "rewrote the link"

    before, after = redact(result.original), redact(result.result)
    return f"[{stamp}]  {headline}\n    before  {before}\n    after   {after}\n\n"


@dataclass(frozen=True)
class LastClean:
    original: str
    cleaned: str
    at: float


class NudlApp:
    def __init__(self) -> None:
        self.config = config.load()
        # Honour the configured rules_path. It was previously advertised in config.json
        # and then ignored, which is worse than not offering it at all.
        #
        # The whole RulesLoad is kept, not just the dict: `run()` needs to know whether
        # what is loaded is what the user asked for, so it can say so out loud.
        self._rules_load = self._load_rules()
        self.rules = self._rules_load.rules
        self.ui = OverlayUI()
        self.window: MessageWindow | None = None
        self._window_released = False

        self._lock = threading.Lock()
        self._last_clean: LastClean | None = None
        self._own_write = clipboard.OwnWriteGuard()

        # Clipboard writes come from two threads: the pump (a clean) and the Tk thread
        # (the toast's Undo button). arm -> write -> confirm must be atomic as a unit, or
        # an interleaved clean and undo can arm the guard with one text and confirm it
        # with the other's sequence number — and the auto-watcher would then re-clean the
        # link the user just undid.
        self._write_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------------

    def run(self) -> None:
        self.ui.start()

        # Not in `__init__`: there is no toast until the Tk thread exists.
        self._warn_about_rules()

        if not self.config["first_run_complete"]:
            self._first_run()
        # Every launch, not just the first: the exe may have moved since the Run value was
        # written, and a value pointing at nothing is autostart silently switched off.
        autostart.sync(self.config["run_at_startup"])

        pump = threading.Thread(target=self._pump, name="nudl-pump", daemon=True)
        pump.start()

        tray = Tray(
            get_mode=lambda: self.config["mode"],
            set_mode=self._set_mode,
            get_run_at_startup=lambda: self.config["run_at_startup"],
            toggle_run_at_startup=self._toggle_run_at_startup,
            undo=self.undo,
            can_undo=self.can_undo,
            open_settings=self._open_settings,
            open_rules=self._open_rules,
            validate_rules=self._validate_rules,
            open_log=self._open_log,
            show_about=self._show_about,
            on_exit=self._shutdown,
        )
        tray.run()  # blocks the main thread until Exit

    def _first_run(self) -> None:
        """Ask once, remember forever (FR-010)."""
        mode, start_with_windows = self.ui.ask_first_run(default=self.config["mode"])
        self.config["mode"] = mode
        self.config["run_at_startup"] = start_with_windows
        self.config["first_run_complete"] = True
        config.save(self.config)

    def _pump(self) -> None:
        """Own the Win32 window, the hotkey and the clipboard listener, for their lifetime.

        Registration AND cleanup both happen here, on this thread, deliberately:
        `UnregisterHotKey` only frees a hotkey registered by the *calling* thread, so
        tearing it down from the tray thread would fail silently and leak the chord.
        """
        try:
            self.window = MessageWindow()
            self.window.on(win32con.WM_HOTKEY, lambda _w, _l: self.on_hotkey())
            self.window.on(clipboard.WM_CLIPBOARDUPDATE, lambda _w, _l: self.on_clipboard_update())

            # Cleanup runs HERE, on WM_CLOSE, and not in a finally after the pump returns.
            # `stop()` posts WM_CLOSE; DefWindowProc answers it by destroying the window;
            # only then does PumpMessages() return. So by the time any finally-block runs,
            # the hwnd is already dead and unregistering against it is unregistering
            # against nothing. WM_CLOSE is the last moment the window still exists.
            self.window.on(win32con.WM_CLOSE, lambda _w, _l: self._release_window())

            try:
                hotkey.register(self.window.hwnd, HOTKEY_ID, self.config["hotkey"])
            except (hotkey.InvalidHotkey, hotkey.HotkeyUnavailable) as exc:
                # Losing the hotkey is survivable — the tray still works — but the user
                # has to be told, or nudl looks silently broken.
                logger.error("hotkey unavailable: %s", exc)
                self.ui.show_toast(f"nudl — {exc}. Pick another in Settings.", seconds=10)

            # The listener is registered once and left in place for the life of the
            # process; the handler simply does nothing when the mode is "hotkey".
            if not clipboard.add_format_listener(self.window.hwnd):
                # Auto-watch is now dead and nothing else will say so: the tray icon sits
                # there looking healthy while every copy goes uncleaned. Say it out loud.
                logger.error("could not register the clipboard listener; auto mode is dead")
                if self.config["mode"] == "auto":
                    self.ui.show_toast(
                        "nudl — could not watch the clipboard. Use hotkey mode.", seconds=10
                    )

            self.window.pump()  # blocks until stop()
        except Exception:  # noqa: BLE001
            # Without this, an unexpected failure kills the pump thread silently and both
            # the hotkey and auto-watch stop working while the tray icon sits there
            # looking perfectly healthy.
            logger.exception("the message pump died")
            self.ui.show_toast("nudl — stopped responding. Restart it.", seconds=10)
        finally:
            # A fallback, not the main path: if the pump died before it ever saw WM_CLOSE,
            # the window may still be alive and still holding the chord. `_release_window`
            # checks the hwnd, so when the normal shutdown already ran this does nothing.
            self._release_window()

    def _release_window(self) -> None:
        """Give the hotkey and the clipboard listener back. Pump thread only; idempotent.

        `UnregisterHotKey` only frees a hotkey registered by the *calling* thread, which is
        why this cannot move to the tray thread: it would fail silently and leak the chord
        to every other application on the system until reboot.
        """
        window = self.window
        if window is None or not window.hwnd or self._window_released:
            return
        self._window_released = True
        hotkey.unregister(window.hwnd, HOTKEY_ID)
        clipboard.remove_format_listener(window.hwnd)

    def _shutdown(self) -> None:
        # Only ASK the pump to stop. It gives back its own hotkey and listener on the
        # thread that owns them (see the WM_CLOSE handler in _pump).
        if self.window is not None:
            self.window.stop()
        self.ui.stop()

    # -- the rule set --------------------------------------------------------------

    def _load_rules(self) -> clean.RulesLoad:
        """Pick the live rule set, and put which one it is in the log.

        That log line is the only thing that tells "nudl is running on the bundled rules
        because your file is broken" apart from "nudl has stopped working". From the
        outside the two are identical: the icon sits there looking healthy while links
        come back with trackers still on them.
        """
        path = config.rules_path(self.config)
        load = clean.load_rules_verbose(path, backup=config.rules_backup_path(self.config))
        logger.info("rules: using the %s set from %s", load.source, load.path)
        if load.error is not None:
            logger.error("rules: %s %s", path, load.error)
        if load.source == "custom":
            # The file has just parsed and passed the shape check, so it is known-good by
            # definition. That is the only moment at which copying it is worth anything.
            self._back_up_rules()
        return load

    def _back_up_rules(self) -> None:
        """Keep the last rule file that WORKED beside the live one, as `<name>.bak`.

        Only ever called once the active file has already loaded cleanly — that is what
        makes the backup trustworthy. nudl never copies a file it could not parse.

        Deliberately not a rename-then-restore dance. The rules file belongs to the user;
        nudl writing over an edit they are halfway through, in order to "roll back", would
        destroy work nobody asked it to touch. The backup is a spare nudl can RUN on for a
        session, never something written back over the original.

        Losing the backup costs a future fallback, not this run, so every failure is
        logged and swallowed: a tool that refuses to start because it could not make a
        copy of a file is worse than one running without a spare.
        """
        active = config.rules_path(self.config)
        backup = config.rules_backup_path(self.config)
        try:
            data = active.read_bytes()
            # Starting nudl should not rewrite a file on disk every single time; almost
            # every launch finds the backup already identical.
            if backup.exists() and backup.read_bytes() == data:
                return
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(data)
        except OSError:
            logger.warning("could not back up %s", active, exc_info=True)

    def _warn_about_rules(self) -> None:
        """Say out loud when nudl is not running the rules the user asked for.

        Silent fallback is the exact failure this feature exists to remove: the user edits
        rules.json, nothing complains, and the rule they added never fires.
        """
        error = self._rules_load.error
        if error is None:
            return

        # A rules file that was never created is the DEFAULT install, not a fallback worth
        # interrupting anyone about: `rules_path` points into the config dir, and nothing
        # puts a file there until the user asks for one via Rules…. Toasting "rules.json
        # does not exist" on every single launch is how a user learns to dismiss nudl's
        # toasts unread — and the next one is the one that matters. The log still has it.
        if not config.rules_path(self.config).exists():
            return

        if len(error) > RULES_ERROR_TOAST_CHARS:
            error = error[: RULES_ERROR_TOAST_CHARS - 1].rstrip() + "…"
        name = config.rules_path(self.config).name
        if self._rules_load.source == "backup":
            message = f"nudl — {name} {error}. Using the last version that worked."
        else:
            message = f"nudl — {name} {error}. Using the bundled rules."
        self.ui.show_toast(message, seconds=10)

    def _open_rules(self) -> None:
        """Open the live rules file, seeding it from the bundled set when it is absent.

        Seeded rather than created empty, and that matters: the bundled file is heavily
        commented and lists every param nudl deliberately leaves alone, so a user who
        opens it can see both what a rule looks like and why some obvious-looking
        tracking keys are not stripped. A blank file invites reinventing all of that,
        badly, and breaking links in the process.
        """
        path = config.rules_path(self.config)
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(clean.BUNDLED_RULES_PATH, path)
            except OSError:
                logger.exception("could not seed %s from the bundled rules", path)
                self.ui.show_toast(
                    f"nudl — could not create {path.name}. Check the log.", seconds=6
                )
                return
        self._open(path)

    def _validate_rules(self) -> None:
        """Re-read the rules file on demand: report what is in it, or what is wrong with it.

        No `backup=` here, unlike startup. The question being asked is "is MY file valid",
        and quietly answering it from the backup would hide the answer behind a file the
        user did not just write.

        On success the rules are ADOPTED, not merely reported on. Telling a user their
        file is valid and then carrying on with the old set would leave nudl running rules
        the user has already replaced — precisely the looks-healthy-does-nothing failure
        this project refuses.

        Threading: this runs on the tray thread while `apply_clean` runs on the pump
        thread. `self.rules` is rebound in one atomic assignment and `clean_result` takes
        the rule set by argument, so a clean already in flight finishes against whichever
        set it had already read. A lock would buy nothing.
        """
        path = config.rules_path(self.config)
        if not path.exists():
            # Not a failure — it is the stock install. Answering "does not exist" and
            # stopping there leaves the user with no idea that the file is theirs to
            # create, or how.
            self.ui.show_toast(
                f"nudl — no {path.name} yet, so nudl is on the bundled rules. "
                f"Use Rules… to make your own.",
                seconds=8,
            )
            return

        load = clean.load_rules_verbose(path)
        if load.error is not None:
            logger.error("validate: %s %s", path, load.error)
            self.ui.show_toast(
                f"nudl — {path.name} {load.error}. Still using the rules already loaded.",
                seconds=10,
            )
            return

        self._rules_load = load
        self.rules = load.rules
        self._back_up_rules()
        summary = clean.summarize(load.rules)
        logger.info("validate: adopted %s (%s)", load.path, summary.describe())
        self.ui.show_toast(f"nudl — rules reloaded: {summary.describe()}", seconds=6)

    # -- auto-watch ----------------------------------------------------------------

    def on_clipboard_update(self) -> None:
        """Something changed the clipboard. Was it us? Was it a link? Should we act?"""
        if self.config["mode"] != "auto":
            logger.debug("clipboard changed, but mode is %r", self.config["mode"])
            return

        try:
            # Read both under one clipboard lock. Fetching them separately leaves a
            # window for another process to change the clipboard in between, which would
            # pair a stale sequence number with fresh text.
            text, sequence = clipboard.get_text_and_sequence()
        except clipboard.ClipboardBusy:
            logger.warning("clipboard update: locked by another process, skipping")
            return

        # The echo of our own write. Ignoring this is what stops the infinite loop.
        if self._own_write.is_own_write(sequence, text):
            logger.debug("clipboard update seq=%s was our own write; ignoring", sequence)
            return

        url = clipboard.as_single_url(text)
        if url is None:
            logger.debug("clipboard update: not a single URL; leaving it alone")
            return  # a paragraph, a file, an image, plain text — never touched

        logger.info("auto-watch: cleaning a copied link")
        self.apply_clean(url)

    # -- the hotkey ----------------------------------------------------------------

    def on_hotkey(self) -> None:
        """Clean what's on the clipboard — or undo the last clean."""
        logger.info("hotkey pressed")
        if self._undo_if_pending():
            logger.info("hotkey: within the undo window -> undid the last clean")
            return

        try:
            text = clipboard.get_text()
        except clipboard.ClipboardBusy:
            # The user pressed the hotkey and something has to happen. Silence here reads
            # as "nudl is broken".
            logger.warning("hotkey: clipboard is locked by another process")
            self.ui.show_toast("nudl — clipboard busy, try again", seconds=3)
            return

        url = clipboard.as_single_url(text)
        if url is None:
            logger.info("hotkey: clipboard holds no single URL")
            self.ui.show_toast("nudl — no link on the clipboard", seconds=3)
            return

        self.apply_clean(url)

    def apply_clean(self, url: str) -> None:
        """Clean `url` and, only if that changed something, write it back loudly."""
        result = clean.clean_result(
            url,
            rules=self.rules,
            exceptions=self.config["exceptions"],
            strip_referral=self.config["strip_referral"],
        )
        if not result.changed:
            # A no-op is silent: no toast, no clipboard write. But it is NOT invisible to
            # the debug log — "nudl stopped working" and "nudl decided there was nothing
            # to do" look identical from the outside, and this is how you tell them apart.
            logger.info("no change (%s)", result.reason_noop or "nothing to strip")
            return

        if not self._write_clipboard(result.result):
            self.ui.show_toast("nudl — clipboard busy, link left alone", seconds=3)
            return

        with self._lock:
            self._last_clean = LastClean(result.original, result.result, time.monotonic())

        self._log(result)
        self.ui.show_toast(self._describe(result), on_undo=self.undo)

    def _write_clipboard(self, text: str, expect_sequence: int | None = None) -> bool:
        """The ONLY way nudl writes the clipboard. Always through the own-write guard.

        Arm before the write, confirm after: a clipboard write nudl doesn't recognise as
        its own would be re-cleaned by the auto-watcher, forever.

        If the write FAILS, the guard must be disarmed. Otherwise it stays armed with
        text that never reached the clipboard — and the next time the user copies that
        same link by hand, nudl would mistake it for its own echo and refuse to clean it.
        A failed write must not poison the next one.

        `expect_sequence` refuses the write if the clipboard moved since the caller read
        it. Undo passes it; cleaning does not, because cleaning acts on the copy that
        just happened rather than on something the user might have replaced.
        """
        with self._write_lock:
            token = self._own_write.arm(text)
            try:
                seq = clipboard.set_text(text, expect_sequence=expect_sequence)
                self._own_write.confirm(token, seq)
            except Exception:  # noqa: BLE001 — busy clipboard, changed clipboard, any win32 failure
                logger.warning("could not write the clipboard", exc_info=True)
                self._own_write.disarm()
                return False
        return True

    @staticmethod
    def _describe(result: clean.CleanResult) -> str:
        count = len(result.params_removed)
        if count == 1:
            return "nudl — removed 1 tracker"
        if count > 1:
            return f"nudl — removed {count} trackers"
        return "nudl — unwrapped the redirect"  # changed, but nothing was stripped

    # -- undo ----------------------------------------------------------------------

    def _undo_if_pending(self) -> bool:
        """Hotkey pressed again within the undo window → undo instead of cleaning.

        The time limit belongs to the HOTKEY, not to undo itself. Pressing the shortcut
        again is ambiguous — you might mean "undo", you might mean "clean this new
        thing" — so it only means undo in the moments right after a clean. The tray's
        Undo entry is unambiguous, so it carries no clock.
        """
        with self._lock:
            last = self._last_clean
        if last is None or time.monotonic() - last.at > UNDO_WINDOW_SECONDS:
            return False
        return self.undo()

    def can_undo(self) -> bool:
        """Is there a clean to undo, and is undoing it still safe?

        Drives the tray entry's enabled state, so the menu tells the truth instead of
        offering an action that would do nothing (or worse).
        """
        with self._lock:
            last = self._last_clean
        if last is None:
            return False
        try:
            return clipboard.get_text() == last.cleaned
        except clipboard.ClipboardBusy:
            return False

    def undo(self) -> bool:
        """Restore the exact original clipboard content. Returns whether it happened.

        Undo will only fire if the clipboard STILL holds what nudl put there. If you have
        copied something else since, restoring the old link would silently destroy what
        you just copied — an undo that eats your clipboard is a far worse bug than the
        one it was undoing. Every entry point (hotkey, toast button, tray) goes through
        this check.

        In auto mode this is also the subtlest write in the program: restoring the ugly
        original puts a dirty link back on the clipboard, which the auto-watcher is
        listening for. Without the own-write guard it would immediately re-clean it and
        undo would be impossible. Hence `_write_clipboard`, never a raw `set_text`.
        """
        with self._lock:
            last = self._last_clean

        if last is None:
            return False

        try:
            current, sequence = clipboard.get_text_and_sequence()
        except clipboard.ClipboardBusy:
            self.ui.show_toast("nudl — clipboard busy, could not undo", seconds=3)
            return False

        if current != last.cleaned:
            # Someone (probably the user) has copied something else. Refuse, and stop
            # offering: the moment has passed.
            logger.info("undo declined: the clipboard no longer holds nudl's cleaned link")
            with self._lock:
                self._last_clean = None
            self.ui.show_toast("nudl — nothing to undo; the clipboard changed", seconds=3)
            return False

        # Claim the undo, so two entry points firing at once cannot both restore it.
        with self._lock:
            if self._last_clean is not last:
                return False
            self._last_clean = None

        # `sequence` closes the gap between the check above and the write below: if the
        # user copies something in that window, the write is refused rather than
        # destroying what they just copied.
        if not self._write_clipboard(last.original, expect_sequence=sequence):
            # Put the undo back. A busy clipboard is transient, and discarding the
            # original here would mean telling the user "could not undo" and then never
            # letting them try again — losing the very thing undo exists to protect. If
            # instead the clipboard genuinely moved on, `can_undo()` sees that on the next
            # menu render and greys the entry out, so restoring it cannot mislead anyone.
            with self._lock:
                if self._last_clean is None:
                    self._last_clean = last
            self.ui.show_toast("nudl — could not undo; the clipboard was busy", seconds=3)
            return False

        logger.info("undo: restored the original link")
        self.ui.show_toast("nudl — undone", seconds=2)
        return True

    # -- tray actions --------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if mode == self.config["mode"]:
            return
        self.config["mode"] = mode
        config.save(self.config)
        self.ui.show_toast(f"nudl — {'automatic' if mode == 'auto' else 'hotkey'} mode", seconds=2)

    def _toggle_run_at_startup(self) -> None:
        wanted = not self.config["run_at_startup"]
        try:
            if wanted:
                autostart.enable()
            else:
                autostart.disable()
        except OSError:
            # Leave the setting where it was, so the tick in the menu keeps telling the
            # truth about what Windows will actually do at the next logon.
            logger.exception("could not %s autostart", "enable" if wanted else "disable")
            self.ui.show_toast("nudl — could not change Start with Windows. Check the log.")
            return
        self.config["run_at_startup"] = wanted
        config.save(self.config)

    def _open_settings(self) -> None:
        path = config.config_path()
        if not path.exists():
            config.save(self.config)
        self._open(path)

    def _open_log(self) -> None:
        path = config.log_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        self._open(path)

    def _show_about(self) -> None:
        message = "nudl — local only. Your links never leave this machine."
        # Rule freshness is the one thing about nudl a user cannot infer from watching it
        # work: a stale rule set strips fewer trackers and looks exactly like a current one.
        last_updated = clean.summarize(self.rules).last_updated
        if last_updated:
            message += f" Rules updated {last_updated}."
        self.ui.show_toast(message, seconds=6)

    @staticmethod
    def _open(path: Path) -> None:
        # `os.startfile` runs whatever the file's type is associated with, so nudl only
        # ever hands it its own config, rules and log. `.log` is on the list because
        # View log is the audit trail the README tells people to check nudl's work with;
        # refusing it made that menu item silently do nothing.
        if path.suffix.lower() not in (".json", ".log"):
            logger.warning("refusing to open %s — not a nudl config, rules or log file", path)
            return
        try:
            os.startfile(path)  # noqa: S606 — opening the user's own config/log
        except OSError:
            logger.exception("could not open %s", path)

    # -- the audit log -------------------------------------------------------------

    def _log(self, result: clean.CleanResult) -> None:
        """Append one entry to the local audit log. Never leaves this machine."""
        path = config.log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_suffix(".log.1"))

            fresh = not path.exists() or path.stat().st_size == 0
            with open(path, "a", encoding="utf-8") as fh:
                if fresh:
                    fh.write(LOG_HEADER)
                fh.write(format_log_entry(result, time.strftime("%Y-%m-%d %H:%M:%S")))
        except OSError:
            logger.exception("could not write the audit log")


def acquire_single_instance(name: str = SINGLE_INSTANCE_MUTEX) -> int | None:
    """Claim the single-instance mutex. Returns a handle, or None if nudl already runs.

    The handle must be held for the life of the process: the mutex exists exactly as
    long as someone holds a handle to it.

    `name` is a parameter so the tests can claim their own mutex. Sharing the real one
    would make the suite fail whenever nudl is actually running — a test that breaks
    because the product works is worse than no test.
    """
    try:
        sd = win32security.SECURITY_DESCRIPTOR()
        acl = win32security.ACL()
        # Grant the current user access so the same user can open the mutex
        # (for the "already exists" check) while denying everyone else.
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        acl.AddAccessAllowedAce(
            win32security.ACL_REVISION, win32con.GENERIC_ALL, user_sid
        )
        sd.SetSecurityDescriptorDacl(1, acl, 0)
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        handle = win32event.CreateMutex(sa, False, name)
    except pywintypes.error:
        # Under pythonw there is no console, so an uncaught exception here would kill
        # nudl before the tray ever appears, with nothing on screen and nothing in a log
        # to say why. If we cannot claim the mutex we cannot prove we are alone — so let
        # this instance run rather than vanish. The worst case is two nudls; the worst
        # case of the alternative is a program that silently refuses to start.
        logger.exception("could not create the single-instance mutex; starting anyway")
        return 0

    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        # CreateMutex hands back a valid handle to the EXISTING mutex even when it
        # already exists. Dropping it on the floor leaks a kernel handle every call.
        if handle:
            win32api.CloseHandle(handle)
        return None
    return handle


def _setup_logging() -> None:
    """Send nudl's own errors to a file.

    nudl runs windowed (pythonw), so there is no console and stderr goes nowhere. Any
    exception — a dead pump, a failed clipboard write — vanished silently, and the tool
    just quietly stopped working with no trace anywhere. Diagnosing that from the outside
    is guesswork, which is exactly what it was.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        path = config.config_dir() / "nudl-debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_suffix(".log.1"))
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        pass  # a tool that cannot write its debug log must still run


def main() -> None:
    _setup_logging()
    logging.getLogger(__name__).info("nudl starting")

    handle = acquire_single_instance()
    if handle is None:
        logger.info("nudl is already running; this instance is exiting")
        win32api.MessageBox(
            0,
            "nudl is already running.\n\nLook for the green n in your system tray.",
            "nudl",
            win32con.MB_OK | win32con.MB_ICONINFORMATION,
        )
        return

    try:
        NudlApp().run()
    finally:
        if handle:
            with contextlib.suppress(Exception):
                win32api.CloseHandle(handle)


if __name__ == "__main__":
    main()
