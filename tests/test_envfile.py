import os

from jobdigest.envfile import load_dotenv


def _load(tmp_path, monkeypatch, text, keys):
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    load_dotenv(p)
    return {k: os.environ.get(k) for k in keys}


def test_plain_export_comment_and_quotes(tmp_path, monkeypatch):
    text = "\n".join(
        [
            "# full line comment",
            "A=plain",
            "export B=exported",
            "export   C = spaced",
            "D=value # trailing comment",
            "E=a#b",
            'F="quoted # not comment"',
            "G='single # kept'",
            "H=\"it's\"",
            "I='mismatched\"",
            "J=",
            "exportK=nope",
        ]
    )
    got = _load(tmp_path, monkeypatch, text, list("ABCDEFGHIJ") + ["exportK"])
    assert got["A"] == "plain"
    assert got["B"] == "exported"
    assert got["C"] == "spaced"
    assert got["D"] == "value"
    assert got["E"] == "a#b"
    assert got["F"] == "quoted # not comment"
    assert got["G"] == "single # kept"
    assert got["H"] == "it's"
    assert got["I"] == "'mismatched\""
    assert got["J"] == ""
    assert got["exportK"] == "nope"


def test_existing_env_not_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZ_KEEP", "orig")
    p = tmp_path / ".env"
    p.write_text("ZZ_KEEP=new\n", encoding="utf-8")
    load_dotenv(p)
    assert os.environ["ZZ_KEEP"] == "orig"
