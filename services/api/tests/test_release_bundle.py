"""scripts/create_release_bundle.py: inventory, safety refusals, archive determinism and tamper detection (on temporary trees)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "create_release_bundle.py"
spec = importlib.util.spec_from_file_location("create_release_bundle", SCRIPT)
rb = importlib.util.module_from_spec(spec)
sys.modules["create_release_bundle"] = rb
spec.loader.exec_module(rb)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "ROOT", tmp_path)
    monkeypatch.setattr(rb, "RELEASES", tmp_path / "reports" / "releases")
    api = tmp_path / "data" / "api" / "chennai"
    (api / "rainfall").mkdir(parents=True)
    (api / "index.json").write_text('{"a":1}', encoding="utf-8")
    (api / "rainfall" / "catalog.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_release_id_rules():
    for ok in ("2026-10-30-rc1", "rc.1", "FloodLens_1"):
        assert rb.ID_RE.match(ok)
    for bad in ("", "a", "../x", "x/y", "-rc1", "has space", "x" * 80):
        assert not rb.ID_RE.match(bad)


def test_inventory_has_relative_posix_paths_sizes_and_sha256(tree):
    base = tree / "data" / "api"
    ents = rb.inventory(rb.walk_files(base), base)
    assert [e["path"] for e in ents] == ["chennai/index.json", "chennai/rainfall/catalog.json"]
    assert ents[0]["bytes"] == 7 and len(ents[0]["sha256"]) == 64
    assert not any(os.path.isabs(e["path"]) or "\\" in e["path"] for e in ents)


def test_text_hash_ignores_line_endings_but_binary_does_not(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"x\ny\n")
    b.write_bytes(b"x\r\ny\r\n")
    assert rb.sha_file(a, True) == rb.sha_file(b, True) and rb.sha_file(a) != rb.sha_file(b)
    c, d = tmp_path / "c.bin", tmp_path / "d.bin"
    c.write_bytes(b"\0x\n")
    d.write_bytes(b"\0x\r\n")
    assert rb.sha_file(c, True) != rb.sha_file(d, True)


def test_symbolic_links_are_refused(tree):
    target = tree / "outside.txt"
    target.write_text("secret", encoding="utf-8")
    link = tree / "data" / "api" / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not permitted on this system")
    with pytest.raises(rb.BundleError, match="symbolic link"):
        rb.walk_files(tree / "data" / "api")


def test_duplicate_paths_that_differ_only_by_case_are_refused(tree):
    base = tree / "data" / "api"
    ents = [base / "chennai" / "index.json", base / "chennai" / "INDEX.json"]
    if ents[1].exists():  # case-insensitive filesystem: the two names are the same file
        pytest.skip("case-insensitive filesystem")
    ents[1].write_text("{}", encoding="utf-8")
    with pytest.raises(rb.BundleError, match="duplicate path"):
        rb.inventory(ents, base)


def test_secret_scan_flags_keys_tokens_and_credential_files_but_not_env_example(tree):
    f = tree / "pkg"
    f.mkdir()
    (f / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (f / ".env.example").write_text("VITE_X=\n", encoding="utf-8")
    assert rb.scan_secrets(sorted(f.iterdir()), f) == []
    (f / "a.py").write_text("k = 'AKIA" + "ABCDEFGHIJKLMNOP'\n", encoding="utf-8")
    (f / "b.txt").write_text("EARTHDATA_TOKEN=" + "abcdefghijklmnop123\n", encoding="utf-8")
    (f / "c.json").write_text('{"k":"v1.public.' + "A" * 30 + '"}', encoding="utf-8")
    (f / "d.txt").write_text("-----BEGIN " + "PRIVATE KEY-----", encoding="utf-8")
    (f / ".env").write_text("A=1", encoding="utf-8")
    (f / "server.pem").write_text("x", encoding="utf-8")
    hits = " ".join(rb.scan_secrets(sorted(f.iterdir()), f))
    for token in ("aws_access_key_id: a.py", "earthdata_token_assignment: b.txt", "location_key_literal: c.json",
                  "private_key_block: d.txt", "forbidden file name: .env", "forbidden file name: server.pem"):
        assert token in hits, token


def test_elf_machine_distinguishes_arm64_and_x86(tmp_path):
    def elf(machine: int) -> bytes:
        return b"\x7fELF" + bytes([2, 1, 1]) + bytes(9) + (3).to_bytes(2, "little") + machine.to_bytes(2, "little") + bytes(40)
    a, x, t = tmp_path / "a.so", tmp_path / "x.so", tmp_path / "t.txt"
    a.write_bytes(elf(183))
    x.write_bytes(elf(62))
    t.write_text("hi", encoding="utf-8")
    assert rb.elf_machine(a) == 183 and rb.elf_machine(x) == 62 and rb.elf_machine(t) is None


def test_archive_is_deterministic_and_matches_inventory_and_detects_tampering(tree, tmp_path):
    api = tree / "data" / "api"
    ents = rb.inventory(rb.walk_files(api), api)
    one, two = tmp_path / "one.tar.gz", tmp_path / "two.tar.gz"
    rb.write_archive(one, api)
    rb.write_archive(two, api)
    assert rb.sha_file(one) == rb.sha_file(two)
    assert rb.archive_matches(one, ents) == []
    (api / "chennai" / "index.json").write_text('{"a":2}', encoding="utf-8")
    assert rb.archive_matches(one, ents) == []  # the archive still matches the recorded inventory...
    now = rb.inventory(rb.walk_files(api), api)
    assert rb.archive_matches(one, now) == ["data/api/chennai/index.json"]  # ...but not the changed disk


def test_create_never_overwrites_and_requires_inputs(tree, monkeypatch):
    class Args:
        release_id = "rc-test"
        api_dir, lambda_dir, web_dist = "data/api", "infrastructure/build/lambda", "apps/web/dist"
        allow_dirty = True
        no_archive = True
        quick = True
        test_results = None
        keep_failed = False

    with pytest.raises(rb.BundleError, match="missing required input"):
        rb.create(Args)
    (rb.RELEASES / "rc-test").mkdir(parents=True)
    with pytest.raises(rb.BundleError, match="already exists"):
        rb.create(Args)


def test_verify_reports_a_missing_bundle(tree):
    class Args:
        release_id = "nope-1"
        quick = True

    with pytest.raises(rb.BundleError, match="no bundle"):
        rb.verify(Args)


def test_listing_hash_is_order_independent_and_content_sensitive():
    a = [{"path": "a", "sha256": "1"}, {"path": "b", "sha256": "2"}]
    assert rb.listing_hash(a) == rb.listing_hash(list(reversed(a)))
    assert rb.listing_hash(a) != rb.listing_hash([{"path": "a", "sha256": "1"}, {"path": "b", "sha256": "3"}])
    json.dumps(a)
