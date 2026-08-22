from app.config import _parse_cors


def test_confirmed_vercel_origin_is_explicitly_allowed(monkeypatch):
    monkeypatch.setenv("MEV_CORS_ORIGINS", "https://revacc.vercel.app")

    origins = _parse_cors()

    assert "https://revacc.vercel.app" in origins
    assert "*" not in origins


def test_wildcard_remains_an_explicit_local_override(monkeypatch):
    monkeypatch.setenv("MEV_CORS_ORIGINS", "*")

    assert _parse_cors() == ("*",)
