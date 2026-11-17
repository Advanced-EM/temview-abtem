"""Remote (tunnel) mode: tokens, per-token presets and queue limits, job isolation. Needs no abTEM."""
import json
import threading
import urllib.request

import pytest

from temview_abtem import server

GOOD = {"format": "temview-abtem/1", "mode": "tem", "preset": "quick",
        "specimen": {"id": "gr", "fov_A": 20, "thickness_A": 3.4}, "beam": {"energy_eV": 80000}, "optics": {}}


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    f = tmp_path_factory.mktemp("t") / "tokens.txt"
    f.write_text("# name token max_preset max_queued\nclass401 class-token-0123456789 quick 1\ntoma toma-token-0123456789abc full 4\n")
    server.simulate = lambda req, progress: (threading.Event().wait(1.0), {"arrays": {}, "meta": {"abtem": {"seconds": 0}}})[1]
    server.to_npz_bytes = lambda res: b"npz"
    srv = server.make_server(0, str(f))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def call(url, path, token=None, body=None):
    h = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {token}"} if token else {})
    r = urllib.request.Request(url + path, data=json.dumps(body).encode() if body else None, headers=h)
    try:
        with urllib.request.urlopen(r) as f:
            return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_needs_token(url):
    assert call(url, "/jobs", body=GOOD)[0] == 401
    assert call(url, "/jobs", "wrong-token-0123456789", GOOD)[0] == 401


def test_preset_ceiling(url):
    assert call(url, "/jobs", "class-token-0123456789", GOOD | {"preset": "full"})[0] == 403
    assert call(url, "/jobs", "class-token-0123456789", GOOD | {"run": {"max_gpts": 6000}})[0] == 403


def test_queue_limit_and_isolation(url):
    code, j = call(url, "/jobs", "class-token-0123456789", GOOD)
    assert code == 202
    assert call(url, "/jobs", "class-token-0123456789", GOOD)[0] == 429  # max_queued 1
    assert call(url, f"/jobs/{j['id']}", "toma-token-0123456789abc")[0] == 404  # not toma's job
    assert call(url, f"/jobs/{j['id']}", "class-token-0123456789")[0] == 200


def test_bad_token_file(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("short abc\n")
    with pytest.raises(SystemExit):
        server.load_tokens(str(f))
