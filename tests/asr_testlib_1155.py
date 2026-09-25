"""Shared test plumbing for the meeting-analysis ASR adapters (#1155).

The ONLY thing faked is the HTTP boundary: `urllib.request.urlopen`, which
every adapter calls. A `FakeHttp` routes each request by (method, URL
substring) to a canned response built from the fixture JSON files under
`tests/fixtures/asr_1155/`. Those files follow the response shapes in the
providers' current official docs (URLs on the ticket); nothing here talks to a
network, and no real credential exists anywhere in the suite.
"""
from __future__ import annotations

import contextlib
import email.message
import io
import json
import shutil
import sys
import tempfile
import urllib.error
import wave
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "skills" / "meeting-analysis" / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "asr_1155"

# An obviously fake value. The contract tests assert it never reaches stdout,
# stderr or any output file.
FAKE_CRED = "FAKE-ASR-CRED-VALUE-1155-NEVER-REAL"

CONTRACT_FILES = {"transcript.txt", "transcript.json", "speaker_turns.json",
                  "summary.json", "done"}


_MODULE_TMP: list[Path] = []


def new_tmp(testcase=None) -> Path:
    """A fresh temp dir that is always removed: by `testcase.addCleanup`, or,
    without a testcase, by `cleanup_module_tmp()` from the module's
    `tearDownModule` (pytest's conftest tidies too, `unittest discover` not)."""
    d = Path(tempfile.mkdtemp(prefix="asr1155-"))
    if testcase is not None:
        testcase.addCleanup(shutil.rmtree, d, True)
    else:
        _MODULE_TMP.append(d)
    return d


def cleanup_module_tmp() -> None:
    while _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP.pop(), True)


def import_script(name: str):
    """Import a meeting-analysis script as a module (the scripts dir is not a
    package; the scripts import each other by bare name, as they do when run
    with `python3 scripts/<x>.py`)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    __import__(name)
    return sys.modules[name]


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    """A silent 16 kHz mono PCM wav, the shape extract.sh produces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class FakeResponse:
    def __init__(self, body, headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self._body = body or b""
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    """Route (method, url-substring) -> response. A route's value is a
    FakeResponse, a list of them (served in order, the last one repeats), or
    an int HTTP status to raise as HTTPError."""

    def __init__(self, routes):
        self.routes = [(m, frag, v if not isinstance(v, list) else list(v))
                       for (m, frag), v in routes]
        self.calls = []

    def __call__(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "data": req.data or b""})
        for m, frag, value in self.routes:
            if m == method and frag in url:
                if isinstance(value, list):
                    value = value.pop(0) if len(value) > 1 else value[0]
                if isinstance(value, int):
                    raise urllib.error.HTTPError(url, value, "fake", {},
                                                 io.BytesIO(b'{"error":"fake"}'))
                return value
        raise AssertionError(f"unrouted request {method} {url}")

    def find(self, method, frag):
        return [c for c in self.calls if c["method"] == method and frag in c["url"]]


@contextlib.contextmanager
def fake_http(routes):
    fake = FakeHttp(routes)
    with mock.patch("urllib.request.urlopen", fake), \
            mock.patch("time.sleep", lambda s: None):
        yield fake
