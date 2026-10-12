"""The Chrome extension's socket is accepted only in the extension's own shape.

A loopback peer used to be enough, but Next's ``/api`` rewrite and a tunnel
both reach uvicorn from 127.0.0.1, so a LAN device, a web page in another
browser on the Mac or anyone behind the tunnel could become Edward's hands.
Now the socket needs no proxy header at all, a loopback peer and
``Origin: chrome-extension://<Edward's extension>`` (``routers/browser_ext.py::refusal``):
the manifest's fixed key gives every copy one ID, and any other extension is
refused unless ``EDWARD_CHROME_EXTENSION_ID`` names it (2026-10-12 review).
"""

import json
import logging
from pathlib import Path

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

pytestmark = pytest.mark.unit

EDWARD_ID = "appfgnpjahkedldopocalbkoommcalae"
EXT_ORIGIN = f"chrome-extension://{EDWARD_ID}"
OTHER_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def _ws(host="127.0.0.1", port=52311, **headers):
    raw = [(k.replace("_", "-").lower().encode(), str(v).encode()) for k, v in headers.items()]
    return SimpleNamespace(client=SimpleNamespace(host=host, port=port), headers=Headers(raw=raw))


@pytest.fixture(autouse=True)
def _no_pinned_id(monkeypatch):
    monkeypatch.delenv("EDWARD_CHROME_EXTENSION_ID", raising=False)


# ----------------------------------------------------------- the decision --

def test_the_genuine_shape_is_accepted():
    from routers.browser_ext import refusal

    assert refusal(_ws(origin=EXT_ORIGIN)) is None
    assert refusal(_ws(host="::1", origin=EXT_ORIGIN)) is None


@pytest.mark.parametrize("header,value", [
    ("x-forwarded-for", "127.0.0.1"),          # loopback-looking, still a proxy
    ("x-forwarded-for", "203.0.113.9"),
    ("x-forwarded-host", "localhost:3000"),    # Next's rewrite always adds this
    ("x-forwarded-host", ""),
    ("x-real-ip", "127.0.0.1"),
    ("forwarded", "for=127.0.0.1"),
    ("cf-connecting-ip", "198.51.100.4"),      # the tunnel
])
def test_a_proxied_request_is_refused_even_from_loopback_with_the_right_origin(header, value):
    from routers.browser_ext import refusal

    why = refusal(_ws(origin=EXT_ORIGIN, **{header: value}))
    assert why and "proxied" in why and header in why


@pytest.mark.parametrize("origin", [None, "", "https://evil.example.com", "http://localhost:3000",
                                    "chrome-extension://", "moz-extension://abc"])
def test_a_missing_or_wrong_origin_is_refused(origin):
    from routers.browser_ext import refusal

    headers = {} if origin is None else {"origin": origin}
    why = refusal(_ws(**headers))
    assert why and "Origin" in why


def test_a_lan_peer_is_refused():
    from routers.browser_ext import refusal

    why = refusal(_ws(host="192.168.1.40", origin=EXT_ORIGIN))
    assert why and "local" in why


def test_a_pinned_extension_id_admits_only_that_extension(monkeypatch):
    from routers.browser_ext import refusal

    monkeypatch.setenv("EDWARD_CHROME_EXTENSION_ID", "someotherextensionid, ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP")
    assert refusal(_ws(origin=OTHER_ORIGIN)) is None
    assert "EDWARD_CHROME_EXTENSION_ID" in refusal(_ws(origin="chrome-extension://zzzzzzzzzzzzzzzz"))
    # The variable replaces the default: Edward's own ID is no longer enough.
    assert refusal(_ws(origin=EXT_ORIGIN))


def test_unpinned_only_edwards_own_extension_is_accepted():
    """2026-10-12 review: with nothing pinned, any extension in any Chromium
    on this Mac could take the hands socket over."""
    from routers.browser_ext import refusal

    assert refusal(_ws(origin=EXT_ORIGIN)) is None
    assert refusal(_ws(origin=EXT_ORIGIN.upper().replace("CHROME-EXTENSION", "chrome-extension"))) is None
    why = refusal(_ws(origin=OTHER_ORIGIN))
    assert why and "abcdefghijklmnopabcdefghijklmnop" in why and "EDWARD_CHROME_EXTENSION_ID" in why
    assert len(why) <= 120, "the whole reason fits a WebSocket close frame"


def test_the_default_id_is_the_one_the_manifest_key_gives():
    from routers.browser_ext import EDWARD_EXTENSION_ID, extension_id_from_key

    manifest = json.loads((Path(__file__).resolve().parents[2] / "chrome-extension" / "manifest.json").read_text())
    assert EDWARD_EXTENSION_ID == EDWARD_ID
    assert extension_id_from_key(manifest["key"]) == EDWARD_EXTENSION_ID


def test_only_the_public_key_is_in_the_repo():
    import base64

    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads((repo / "chrome-extension" / "manifest.json").read_text())
    der = base64.b64decode(manifest["key"])
    # A DER SubjectPublicKeyInfo for RSA-2048 is 294 bytes; a private key is far longer.
    assert len(der) == 294 and der[:4] == bytes.fromhex("30820122")
    for path in (repo / "chrome-extension").rglob("*"):
        if path.is_file():
            text = path.read_bytes()
            assert b"PRIVATE KEY" not in text, path
            assert path.suffix not in (".pem", ".key"), path


# ----------------------------------------------------------- the route --

def _app_from(peer_host, monkeypatch, *, hands="on"):
    from routers import browser_ext
    from services import browser_ext_service

    served = []

    async def serve(ws):
        served.append(ws)
        await ws.send_text("hello extension")

    monkeypatch.setenv("EDWARD_CHROME_HANDS", hands)
    monkeypatch.setattr(browser_ext_service, "serve", serve)
    app = FastAPI()
    app.include_router(browser_ext.router, prefix="/api")

    async def with_peer(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (peer_host, 52311)}
        await app(scope, receive, send)

    return TestClient(with_peer), served


def test_the_route_accepts_the_extension_when_hands_are_on(monkeypatch):
    client, served = _app_from("127.0.0.1", monkeypatch)
    with client.websocket_connect("/api/browser/ext", headers={"origin": EXT_ORIGIN}) as ws:
        assert ws.receive_text() == "hello extension"
    assert len(served) == 1


def test_the_route_refuses_a_proxied_upgrade(monkeypatch):
    client, served = _app_from("127.0.0.1", monkeypatch)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/browser/ext",
                                      headers={"origin": EXT_ORIGIN, "x-forwarded-for": "127.0.0.1"}) as ws:
            ws.receive_text()
    assert exc.value.code == 4003
    assert served == []


def test_the_route_refuses_a_web_page(monkeypatch):
    client, served = _app_from("127.0.0.1", monkeypatch)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/browser/ext", headers={"origin": "https://evil.example.com"}) as ws:
            ws.receive_text()
    assert exc.value.code == 4003
    assert served == []


def test_the_route_still_says_hands_are_off(monkeypatch):
    client, served = _app_from("127.0.0.1", monkeypatch, hands="off")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/browser/ext", headers={"origin": EXT_ORIGIN}) as ws:
            ws.receive_text()
    assert exc.value.code == 4004
    assert served == []


def test_the_route_refuses_another_extension_and_logs_its_id(monkeypatch, caplog):
    client, served = _app_from("127.0.0.1", monkeypatch)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/browser/ext", headers={"origin": OTHER_ORIGIN}) as ws:
                ws.receive_text()
    assert exc.value.code == 4003
    assert served == []
    assert "abcdefghijklmnopabcdefghijklmnop" in caplog.text
