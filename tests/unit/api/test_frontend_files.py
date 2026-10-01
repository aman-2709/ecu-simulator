"""The M3a/M3b frontend files (decisions/0010 §6, §7, §9.3; gui-m3b-graphs-design.md §4.4,
§5.1, §10): each is served at a fixed route with its content type and body, from package
data, and nothing else under ``static/`` is reachable.
"""

import hashlib
from importlib import resources

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

from tests.unit.api.support import build, raw_request, url  # noqa: E402

# route -> (file under static/, content type). The whole list: nothing else is served.
FRONTEND = {
    "/": ("index.html", "text/html"),
    "/app.css": ("app.css", "text/css"),
    "/app.js": ("app.js", "text/javascript"),
    "/uPlot.iife.min.js": ("uPlot.iife.min.js", "text/javascript"),
    "/uPlot.min.css": ("uPlot.min.css", "text/css"),
    "/uPlot-LICENSE.txt": ("uPlot-LICENSE.txt", "text/plain"),
}
# gui-m3b-graphs-design.md §5.1: the vendored uPlot 1.6.32 files, pinned by hash.
UPLOT_SHA256 = {
    "uPlot.iife.min.js": "19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f",
    "uPlot.min.css": "df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04",
    "uPlot-LICENSE.txt": "8f989229699b4fe2f1a0432d0e9edc338a8a911e250e2d1b01ecd770a5f5b1bd",
}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}


def static_bytes(name: str) -> bytes:
    return resources.files("ecu_simulator.api").joinpath("static", name).read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(FRONTEND))
async def test_every_frontend_file_is_served_with_its_type_and_body(server, session, path):
    name, content_type = FRONTEND[path]
    async with session.get(url(server, path)) as r:
        assert r.status == 200, path
        assert r.content_type == content_type and r.charset == "utf-8", (path, r.headers["Content-Type"])
        assert await r.read() == static_bytes(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(FRONTEND))
async def test_frontend_responses_carry_the_security_headers(server, session, path):
    async with session.get(url(server, path)) as r:
        for header, value in SECURITY_HEADERS.items():
            assert r.headers.get(header) == value, (path, header)


@pytest.mark.asyncio
async def test_api_responses_do_not_carry_the_frontend_headers(server, session):
    # The CSP and nosniff belong to the page's own files; the JSON API is unchanged.
    for path in ("/api/v1/status", "/api/v1/exchanges"):
        async with session.get(url(server, path)) as r:
            assert "Content-Security-Policy" not in r.headers, path


def test_the_static_directory_holds_exactly_the_served_files():
    names = {p.name for p in resources.files("ecu_simulator.api").joinpath("static").iterdir()
             if p.is_file() and not p.name.startswith(".")}
    assert names == {name for name, _ in FRONTEND.values()}


@pytest.mark.parametrize("name", sorted(UPLOT_SHA256))
def test_the_vendored_uplot_files_match_their_pinned_sha256(name):
    assert hashlib.sha256(static_bytes(name)).hexdigest() == UPLOT_SHA256[name], name


@pytest.mark.asyncio
async def test_frontend_files_are_read_once_at_construction(session, monkeypatch):
    from ecu_simulator.api import server as server_module

    expected = {path: static_bytes(name) for path, (name, _) in FRONTEND.items()}
    s = build()

    def no_reads(*_: object) -> None:
        raise AssertionError("a frontend file was read after construction")

    monkeypatch.setattr(server_module.resources, "files", no_reads)
    await s.start()
    try:
        for path, body in expected.items():
            async with session.get(url(s, path)) as r:
                assert r.status == 200 and await r.read() == body, path
    finally:
        await s.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/index.html", "/static/index.html", "/static/app.js", "/static/", "/static", "/app.js/", "/app.css/x",
    "/App.js", "/__init__.py", "/server.py", "/options.py", "/favicon.ico", "/api/v1/", "/api/v1/app.js",
    "/%2e%2e/server.py", "/static/%2e%2e/server.py", "/app.js%00",
])
async def test_any_other_path_is_404(server, session, path):
    async with session.get(url(server, path)) as r:
        assert r.status == 404, path


@pytest.mark.asyncio
async def test_a_raw_dot_dot_path_is_404(server):
    reply = await raw_request(server.port, (
        f"GET /../server.py HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nConnection: close\r\n\r\n"
    ).encode())
    assert reply.startswith(b"HTTP/1.1 404"), reply[:40]


@pytest.mark.asyncio
async def test_a_real_file_under_static_that_is_not_listed_is_404(session, tmp_path, monkeypatch):
    # A package-data tree of our own, never the installed package: the listed files plus a
    # real file that is not on the list. Only the server's own lookup is redirected.
    from types import SimpleNamespace

    from ecu_simulator.api import server as server_module

    static = tmp_path / "static"
    static.mkdir()
    bodies = {name: f"tmp {name}".encode() for name, _ in FRONTEND.values()}
    for name, body in bodies.items():
        (static / name).write_bytes(body)
    (static / "not-listed.txt").write_bytes(b"must not be served")
    (tmp_path / "server.py").write_bytes(b"must not be served")
    looked_up: list[str] = []
    fake = SimpleNamespace(files=lambda package: looked_up.append(package) or tmp_path)
    monkeypatch.setattr(server_module, "resources", fake)
    s = build()
    assert looked_up == ["ecu_simulator.api"]
    await s.start()
    try:
        for path, (name, _) in FRONTEND.items():            # the tree really is the one served
            async with session.get(url(s, path)) as r:
                assert r.status == 200 and await r.read() == bodies[name], path
        for path in ("/not-listed.txt", "/static/not-listed.txt", "/static/../server.py", "/server.py"):
            async with session.get(url(s, path)) as r:
                assert r.status == 404, path
    finally:
        await s.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(FRONTEND))
async def test_frontend_routes_answer_other_methods_as_the_api_does(server, session, path):
    for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
        async with session.request(method, url(server, path)) as r:
            assert r.status == 405, (method, path)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(FRONTEND))
async def test_the_host_guard_applies_to_frontend_files(server, session, path):
    async with session.get(url(server, path), headers={"Host": f"evil.example:{server.port}"}) as r:
        assert r.status == 421, path


def test_the_page_names_only_its_own_files_and_relative_urls():
    page = static_bytes("index.html").decode()
    assert 'href="app.css"' in page and 'src="app.js"' in page
    for text in (page, static_bytes("app.js").decode(), static_bytes("app.css").decode()):
        assert "http://" not in text and "https://" not in text and "//cdn" not in text
        assert "sample" not in text.lower()
