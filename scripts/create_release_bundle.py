"""Create and verify a local release bundle: an inventory that ties a Git commit, the Git-ignored deployment inputs and
the built Lambda package / web build together with SHA-256 checksums. Nothing is uploaded and no AWS call is made.

  python scripts/create_release_bundle.py create --release-id 2026-10-30-rc1 [--allow-dirty] [--test-results tests.json]
  python scripts/create_release_bundle.py verify --release-id 2026-10-30-rc1 [--quick]

create writes reports/releases/<id>/ (release-manifest.json, checksums.sha256, validation.json, data-api.tar.gz) and refuses to
overwrite an existing bundle. verify recomputes everything from disk and exits 1 on any difference.
Git alone cannot reproduce a deployment: data/api, the Lambda package and the web build are ignored, so these hashes are the record.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = ROOT / "reports" / "releases"
BASELINE = ROOT / "reports" / "pre-deployment" / "phase6-git-baseline.json"
SCHEMA = "floodlens-release-manifest/1"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
LAMBDA_UNZIPPED_LIMIT = 250 * 1024 * 1024  # Lambda: 250 MB unzipped, including layers
FORBIDDEN_DISTS = {"geopandas", "rasterio", "fiona", "shapely", "pyproj", "gdal", "osgeo"}
FORBIDDEN_NAME = re.compile(r"(^\.env(\..*)?$|\.pem$|\.key$|\.p12$|^id_(rsa|ed25519)|^\.npmrc$|^\.pypirc$|^credentials$)", re.IGNORECASE)
ALLOWED_NAME = {".env.example"}
SECRET_PATTERNS = {
    "aws_access_key_id": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "earthdata_token_assignment": re.compile(rb"EARTHDATA_TOKEN\s*[=:]\s*['\"]?[A-Za-z0-9._-]{12,}"),
    "aws_secret_assignment": re.compile(rb"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{20,}", re.IGNORECASE),
    "location_key_literal": re.compile(rb"v1\.public\.[A-Za-z0-9_-]{20,}"),
}
TEXT_EXT = {".py", ".json", ".js", ".mjs", ".ts", ".tsx", ".html", ".css", ".txt", ".md", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".sha256"}
SCAN_MAX = 5 * 1024 * 1024
# Documentation and generated reports are not deployable inputs: excluding them lets the write-up cite the bundle's hashes
# without invalidating them. Everything else that Git tracks or would track (code, schemas, infrastructure, scripts) is hashed.
SKIP_SOURCE_PREFIX = ("reports/", "docs/")


class BundleError(Exception):
    pass


# ---- hashing and listing --------------------------------------------------------------------------------

def sha_file(p: Path, normalize_text: bool = False) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        if normalize_text:
            head = f.read(8192)
            f.seek(0)
            if b"\0" not in head:  # text: hash with LF endings so a CRLF checkout (core.autocrlf) hashes like Linux
                h.update(f.read().replace(b"\r\n", b"\n"))
                return h.hexdigest()
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def listing_hash(entries: list[dict]) -> str:
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda x: x["path"]):
        h.update(f"{e['path']}\0{e['sha256']}\n".encode())
    return h.hexdigest()


def walk_files(base: Path, skip_dirs: tuple[str, ...] = ()) -> list[Path]:
    """Every regular file under base; refuses symbolic links and anything resolving outside the project."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for d in list(dirnames):
            if (Path(dirpath) / d).is_symlink():
                raise BundleError(f"symbolic link directory refused: {Path(dirpath) / d}")
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink():
                raise BundleError(f"symbolic link refused: {p}")
            if ROOT not in p.resolve().parents:
                raise BundleError(f"file resolves outside the project: {p}")
            out.append(p)
    return out


def inventory(files: list[Path], base: Path, normalize_text: bool = False) -> list[dict]:
    seen: dict[str, str] = {}
    entries = []
    for p in files:
        rel = p.relative_to(base).as_posix()
        if rel.lower() in seen:
            raise BundleError(f"duplicate path (case-insensitive): {rel} vs {seen[rel.lower()]}")
        seen[rel.lower()] = rel
        entries.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha_file(p, normalize_text)})
    return sorted(entries, key=lambda e: e["path"])


def group(name: str, base: Path, files: list[Path], normalize_text: bool = False) -> dict:
    ents = inventory(files, base, normalize_text)
    return {"root": base.relative_to(ROOT).as_posix(), "files": len(ents), "bytes": sum(e["bytes"] for e in ents),
            "sha256_of_listing": listing_hash(ents), "entries": ents}


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise BundleError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def source_files() -> list[Path]:
    names = git("ls-files", "-co", "--exclude-standard", "-z").split("\0")
    return sorted(ROOT / n for n in names if n and not n.startswith(SKIP_SOURCE_PREFIX) and not ("/" not in n and n.endswith(".md"))
                  and (ROOT / n).is_file())


# ---- checks ---------------------------------------------------------------------------------------------

def check(name: str, ok: bool, detail: str, severity: str = "BLOCKER") -> dict:
    return {"name": name, "status": "PASS" if ok else "FAIL", "severity_if_failed": severity, "detail": detail}


def elf_machine(p: Path) -> int | None:
    with p.open("rb") as f:
        h = f.read(20)
    if len(h) < 20 or h[:4] != b"\x7fELF":
        return None
    return int.from_bytes(h[18:20], "little" if h[5] == 1 else "big")


def scan_secrets(files: list[Path], base: Path) -> list[str]:
    hits = []
    for p in files:
        rel = p.relative_to(base).as_posix()
        if FORBIDDEN_NAME.search(p.name) and p.name not in ALLOWED_NAME:
            hits.append(f"forbidden file name: {rel}")
            continue
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size <= SCAN_MAX:
            data = p.read_bytes()
            for label, rx in SECRET_PATTERNS.items():
                if rx.search(data):
                    hits.append(f"{label}: {rel}")
    return hits


def lambda_checks(pkg: Path, api_src: Path, ents: list[dict]) -> list[dict]:
    res = []
    files = [pkg / e["path"] for e in ents]
    # architecture and interpreter of every native module
    elfs = [(p, elf_machine(p)) for p in files if p.suffix == ".so" or ".so." in p.name]
    wrong = [p.relative_to(pkg).as_posix() for p, m in elfs if m != 183]
    res.append(check("lambda_native_modules_are_aarch64", bool(elfs) and not wrong,
                     f"{len(elfs)} ELF modules, all EM_AARCH64" if not wrong else f"not AArch64: {wrong[:5]}"))
    py = [p.name for p, _ in elfs if "cpython-" in p.name]
    bad_py = [n for n in py if "cpython-313-" not in n]
    tags = sorted({n.split(".")[1] for n in py if n.count(".") >= 2})
    res.append(check("lambda_native_modules_are_cpython_313_linux", bool(py) and not bad_py and all("linux-gnu" in n for n in py),
                     f"{len(py)} CPython extension modules, tags: {tags}"))
    win = [e["path"] for e in ents if Path(e["path"]).suffix.lower() in {".exe", ".dll", ".pyd", ".bat", ".cmd"}]
    pe = [p.relative_to(pkg).as_posix() for p in files if p.stat().st_size > 2 and p.open("rb").read(2) == b"MZ"]
    res.append(check("lambda_has_no_windows_binaries", not win and not pe, f"windows files: {(win or pe)[:5]}" if (win or pe) else "no .exe/.dll/.pyd, no PE headers"))
    tops = {Path(e["path"]).parts[0] for e in ents}
    dists = sorted(t[: -len(".dist-info")].rsplit("-", 1)[0].lower().replace("_", "-") for t in tops if t.endswith(".dist-info"))
    forb = sorted(FORBIDDEN_DISTS & (set(dists) | {t.lower() for t in tops}))
    res.append(check("lambda_has_no_geospatial_libraries", not forb, f"forbidden: {forb}" if forb else f"distributions: {dists}"))
    # handler
    stack = (ROOT / "infrastructure" / "lib" / "floodlens-stack.ts").read_text(encoding="utf-8")
    m = re.search(r'handler:\s*"([\w.]+)"', stack)
    mod, _, attr = (m.group(1).rpartition(".") if m else ("", "", ""))
    hp = pkg / (mod.replace(".", "/") + ".py")
    ok = False
    if hp.is_file():
        ok = any(isinstance(n, ast.Assign) and any(getattr(t, "id", "") == attr for t in n.targets) for n in ast.parse(hp.read_text(encoding="utf-8")).body)
    res.append(check("lambda_handler_resolves", ok, f"CDK handler {m.group(1) if m else '?'} -> {hp.relative_to(pkg).as_posix() if hp.is_file() else 'MISSING'} defines {attr}"))
    # code and artifacts equal the source
    src_py = {p.name: sha_file(p) for p in (ROOT / "services/api/floodlens_api").glob("*.py")}
    pkg_py = {p.name: sha_file(p) for p in (pkg / "floodlens_api").glob("*.py")}
    res.append(check("lambda_code_equals_source", src_py == pkg_py, f"{len(pkg_py)} modules compared" if src_py == pkg_py else f"differs: {sorted(set(src_py) ^ set(pkg_py) | {k for k in src_py if src_py.get(k) != pkg_py.get(k)})}"))
    src_art = {p.relative_to(api_src).as_posix(): sha_file(p) for p in walk_files(api_src)}
    pkg_art = {e["path"][len("data/api/"):]: e["sha256"] for e in ents if e["path"].startswith("data/api/")}
    res.append(check("lambda_artifacts_equal_data_api", src_art == pkg_art, f"{len(pkg_art)} artifact files compared"))
    need = ["data/api/chennai/index.json", "data/api/chennai/scenarios.json", "data/api/chennai/rainfall/catalog.json"]
    missing = [n for n in need if not (pkg / n).is_file()]
    res.append(check("lambda_required_artifacts_present", not missing, f"missing: {missing}" if missing else f"{len(need)} required files present"))
    # pinned versions
    pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s#]+)", (ROOT / "services/api/requirements-lambda.txt").read_text(encoding="utf-8"), re.MULTILINE))
    have = {}
    for e in ents:
        mm = re.match(r"^([A-Za-z0-9_.]+)-([^/]+)\.dist-info/METADATA$", e["path"])
        if mm:
            have[mm.group(1).lower().replace("_", "-")] = mm.group(2)
    off = {k: (v, have.get(k.lower().replace("_", "-"))) for k, v in pins.items() if have.get(k.lower().replace("_", "-")) != v}
    res.append(check("lambda_versions_match_pins", not off, f"{len(pins)} pins matched" if not off else f"mismatch (pinned, found): {off}", "HIGH"))
    # sizes
    total = sum(e["bytes"] for e in ents)
    with tempfile.TemporaryDirectory() as td:
        z = Path(td) / "pkg.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for e in ents:
                zf.write(pkg / e["path"], e["path"])
        zipped = z.stat().st_size
    res.append(check("lambda_unzipped_size_within_250mb", total < LAMBDA_UNZIPPED_LIMIT, f"{total/2**20:.1f} MiB unzipped of 250 MiB; {zipped/2**20:.1f} MiB as a deflate zip (CDK uploads via S3, so the 50 MB direct-upload limit does not apply)"))
    return res


def build_checks(man: dict) -> list[dict]:
    res: list[dict] = []
    ents = man["inputs"]["lambda_package"]["entries"]
    res += lambda_checks(ROOT / man["inputs"]["lambda_package"]["root"], ROOT / man["inputs"]["api_artifacts"]["root"], ents)
    web = ROOT / man["inputs"]["web_build"]["root"]
    res.append(check("web_build_has_index_and_no_runtime_config", (web / "index.html").is_file() and not (web / "config.json").exists(),
                     "index.html present; config.json is created at deploy time and is not in the build"))
    hits = scan_secrets([web / e["path"] for e in man["inputs"]["web_build"]["entries"]], web)
    hits += scan_secrets([ROOT / e["path"] for e in man["inputs"]["source_tree"]["entries"]], ROOT)
    pkg = ROOT / man["inputs"]["lambda_package"]["root"]
    hits += scan_secrets([pkg / e["path"] for e in ents], pkg)
    res.append(check("no_secrets_or_credential_files_in_bundle_inputs", not hits,
                     "none found (access keys, private keys, Earthdata token, Location key literal, .env/.pem/.key files)" if not hits else str(hits[:10])))
    # the IMERG random-error field must not reach the application (API code, frontend source, published artifacts)
    rx = re.compile(rb"random_?error", re.IGNORECASE)
    offenders = []
    for base in (ROOT / "services/api/floodlens_api", ROOT / "apps/web/src", ROOT / "data/api"):
        for p in walk_files(base):
            if rx.search(p.read_bytes()):
                offenders.append(p.relative_to(ROOT).as_posix())
    res.append(check("imerg_random_error_absent_from_api_frontend_and_artifacts", not offenders, "no match in services/api/floodlens_api, apps/web/src, data/api" if not offenders else str(offenders[:5])))
    # protected tracked paths unchanged against HEAD (git normalizes line endings)
    prot = ["schemas", "data/fixtures", "data/manifests", "geospatial"]
    diff = git("diff", "--stat", "HEAD", "--", *prot).strip()
    res.append(check("protected_tracked_paths_match_head", diff == "", "schemas/, data/fixtures/, data/manifests/, geospatial/ identical to HEAD" if not diff else diff))
    return res


def data_inventory_check(quick: bool) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    import data_baseline as db

    rec = json.loads(BASELINE.read_text(encoding="utf-8"))["ignored_data_inventory"]
    if quick:  # only the 91 MB data/api aggregate; the 800 MB of raw data is rechecked in full mode
        h, n = hashlib.sha256(), 0
        for p in sorted((ROOT / "data/api").rglob("*")):
            if p.is_file():
                h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + sha_file(p).encode() + b"\n")
                n += 1
        ok = rec["aggregate"]["data/api"] == {"files": n, "sha256_of_listing": h.hexdigest()}
        return check("data_inventory_unchanged_since_recording", ok, f"quick mode: only data/api rechecked ({n} files)" if ok else "data/api differs from the recorded aggregate")
    now = db.collect()
    bad = [k for k in rec["per_file"].keys() | now["per_file"].keys() if rec["per_file"].get(k) != now["per_file"].get(k)]
    bad += [k for k in rec["aggregate"] if rec["aggregate"][k] != now["aggregate"].get(k)]
    return check("data_inventory_unchanged_since_recording", not bad,
                 f"{len(now['per_file'])} raw/processed files and {now['aggregate']['data/api']['files']} data/api files match {BASELINE.name} (recorded {rec['recorded_utc']})"
                 if not bad else f"differs: {sorted(bad)[:10]}")


# ---- archive --------------------------------------------------------------------------------------------

def write_archive(dest: Path, api_dir: Path) -> None:
    """Deterministic tar.gz of data/api (sorted, fixed mtime/owner) so the same files always give the same hash."""
    with dest.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in walk_files(api_dir):
            ti = tarfile.TarInfo("data/api/" + p.relative_to(api_dir).as_posix())
            ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = p.stat().st_size, 0, 0o644, 0, 0, "", ""
            with p.open("rb") as f:
                tar.addfile(ti, f)


def archive_matches(archive: Path, entries: list[dict]) -> list[str]:
    want = {"data/api/" + e["path"]: e["sha256"] for e in entries}
    got: dict[str, str] = {}
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            if m.isfile():
                f = tar.extractfile(m)
                assert f is not None
                got[m.name] = hashlib.file_digest(f, "sha256").hexdigest()
    return sorted(set(want) ^ set(got)) + sorted(k for k in want if k in got and want[k] != got[k])


# ---- commands -------------------------------------------------------------------------------------------

def tool_versions() -> dict:
    def run(*a: str) -> str:
        try:
            return subprocess.run(a, capture_output=True, text=True, check=False, shell=(os.name == "nt" and a[0] == "npm")).stdout.strip()
        except OSError:
            return "unavailable"
    cdk_pkg = json.loads((ROOT / "infrastructure/node_modules/aws-cdk-lib/package.json").read_text(encoding="utf-8"))["version"]
    return {"python": platform.python_version(), "node": run("node", "--version"), "npm": run("npm", "--version"),
            "aws_cdk_lib": cdk_pkg, "platform": platform.platform(), "machine": platform.machine()}


def create(a: argparse.Namespace) -> int:
    if not ID_RE.match(a.release_id):
        raise BundleError(f"release id must match {ID_RE.pattern}")
    out = RELEASES / a.release_id
    if out.exists():
        raise BundleError(f"{out} already exists; bundles are never overwritten. Choose a new --release-id.")
    api, pkg, web = ROOT / a.api_dir, ROOT / a.lambda_dir, ROOT / a.web_dist
    for label, d in (("API artifacts", api), ("Lambda package", pkg), ("web build", web), ("baseline report", BASELINE)):
        if not d.exists():
            raise BundleError(f"missing required input: {label} ({d})")
    if not (api / "chennai" / "index.json").is_file():
        raise BundleError("data/api has no chennai/index.json; run scripts/build_api_artifacts.py")
    status = git("status", "--porcelain").splitlines()
    status = [s for s in status if not s[3:].startswith("reports/releases/")]
    if status and not a.allow_dirty:
        raise BundleError("working tree has uncommitted changes (pass --allow-dirty to record them in the manifest):\n" + "\n".join(status[:20]))
    head = git("rev-parse", "HEAD").strip()
    src = source_files()
    manifests = sorted((ROOT / "data" / "manifests").rglob("*.json"))
    cdk_t = ROOT / "infrastructure" / "cdk.out" / "FloodLensAmplify.template.json"
    man = {
        "schema": SCHEMA, "release_id": a.release_id, "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed": False, "uploaded": False,
        "note": "Local inventory only. Nothing here has been uploaded to AWS. Hashes are of file bytes (source_tree and dataset_manifests: text normalized to LF).",
        "git": {"commit": head, "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(), "origin_main": git("rev-parse", "origin/main").strip(),
                "working_tree_clean": not status, "uncommitted": status,
                "note": "With a dirty tree the commit alone does not identify the source; source_tree does."},
        "tools": tool_versions(),
        "inputs": {
            "source_tree": group("source_tree", ROOT, src, normalize_text=True),
            "dataset_manifests": group("dataset_manifests", ROOT / "data" / "manifests", manifests, normalize_text=True),
            "api_artifacts": group("api_artifacts", api, walk_files(api)),
            "lambda_package": group("lambda_package", pkg, walk_files(pkg, skip_dirs=("__pycache__",))),
            "web_build": group("web_build", web, walk_files(web)),
        },
        "package_metadata": {
            "lambda_requirements_sha256": sha_file(ROOT / "services/api/requirements-lambda.txt", True),
            "web_package_lock_sha256": sha_file(ROOT / "apps/web/package-lock.json", True),
            "infrastructure_package_lock_sha256": sha_file(ROOT / "infrastructure/package-lock.json", True),
            "api_version": re.search(r'API_VERSION\s*=\s*"([^"]+)"', (ROOT / "services/api/floodlens_api/__init__.py").read_text(encoding="utf-8")).group(1),
            "cdk_template_sha256": sha_file(cdk_t) if cdk_t.exists() else None,
            "cdk_template_note": "Depends on the -c alarmEmail / locationKeyExpireTime / frontendOrigins used for the synth; recorded for reference only.",
        },
        "data_baseline": {"report": BASELINE.relative_to(ROOT).as_posix(), "sha256": sha_file(BASELINE, True),
                          "note": "Per-file hashes of data/raw and data/processed live in this report; verify with scripts/data_baseline.py --verify."},
    }
    checks = build_checks(man) + [data_inventory_check(a.quick)]
    if a.test_results:
        tests = json.loads(Path(a.test_results).read_text(encoding="utf-8"))
    else:
        tests = {"note": "no test results supplied (--test-results); run the suites and pass their summary"}
    failed = [c for c in checks if c["status"] == "FAIL"]
    validation = {"release_id": a.release_id, "created_utc": man["created_utc"], "checks": checks, "tests": tests,
                  "verdict": "PASS" if not failed else "FAIL", "failed": [c["name"] for c in failed],
                  "not_verified_here": ["Lambda ARM64 runtime execution (host is not ARM64)", "API Gateway CORS, throttling and route behaviour in AWS",
                                        "Amplify build, rewrites and response headers", "production CSP in a browser", "Amazon Location tile rendering", "cost and alarm behaviour"]}
    if failed and not a.keep_failed:
        raise BundleError("validation failed, bundle not written: " + json.dumps([(c["name"], c["detail"]) for c in failed], indent=1))
    out.mkdir(parents=True)
    if not a.no_archive:
        write_archive(out / "data-api.tar.gz", api)
        man["archive"] = {"path": f"reports/releases/{a.release_id}/data-api.tar.gz", "bytes": (out / "data-api.tar.gz").stat().st_size,
                          "sha256": sha_file(out / "data-api.tar.gz"), "contents": "data/api only (deterministic tar.gz); git-ignored by .gitignore"}
        bad = archive_matches(out / "data-api.tar.gz", man["inputs"]["api_artifacts"]["entries"])
        if bad:
            raise BundleError(f"archive does not match data/api: {bad[:5]}")
    (out / "release-manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "validation.json").write_text(json.dumps(validation, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    sums: dict[str, str] = {}  # a file can belong to two groups (data/manifests is also tracked source): list it once, never with two hashes
    for g in ("api_artifacts", "lambda_package", "web_build", "dataset_manifests", "source_tree"):
        base = man["inputs"][g]["root"]
        for e in man["inputs"][g]["entries"]:
            rel = (base + "/" if base != "." else "") + e["path"]
            if sums.setdefault(rel, e["sha256"]) != e["sha256"]:
                raise BundleError(f"{rel} has two different hashes across groups")
    lines = [f"{h}  {rel}" for rel, h in sums.items()]
    if not a.no_archive:
        lines.append(f"{man['archive']['sha256']}  {man['archive']['path']}")
    lines.append(f"{sha_file(out / 'release-manifest.json')}  reports/releases/{a.release_id}/release-manifest.json")
    (out / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"bundle written: {out}\n  verdict {validation['verdict']}; {len(checks)} checks; {sum(g['files'] for g in man['inputs'].values())} files inventoried; "
          f"commit {head[:12]} ({'dirty' if status else 'clean'})")
    return 0 if not failed else 1


def verify(a: argparse.Namespace) -> int:
    out = RELEASES / a.release_id
    if not (out / "release-manifest.json").is_file():
        raise BundleError(f"no bundle at {out}")
    man = json.loads((out / "release-manifest.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    # 1. manifest and checksum file are consistent and untampered
    lines = (out / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    sums = {}
    for ln in lines:
        h, _, rel = ln.partition("  ")
        if rel in sums:
            problems.append(f"duplicate path in checksums.sha256: {rel}")
        sums[rel] = h
    mrel = f"reports/releases/{a.release_id}/release-manifest.json"
    if sums.get(mrel) != sha_file(out / "release-manifest.json"):
        problems.append("release-manifest.json does not match checksums.sha256")
    # 2. each group on disk equals the recorded inventory exactly (no missing, extra or changed files)
    for g, rec in man["inputs"].items():
        base = ROOT / rec["root"]
        skip = ("__pycache__",) if g == "lambda_package" else ()
        if g == "source_tree":
            disk = inventory(source_files(), ROOT, True)
        elif g == "dataset_manifests":
            disk = inventory(sorted(base.rglob("*.json")), base, True)
        else:
            disk = inventory(walk_files(base, skip), base)
        want = {e["path"]: e["sha256"] for e in rec["entries"]}
        have = {e["path"]: e["sha256"] for e in disk}
        d = sorted(set(want) ^ set(have)) + sorted(k for k in want if k in have and want[k] != have[k])
        if d:
            problems.append(f"{g}: {len(d)} difference(s), e.g. {d[:5]}")
        pref = (rec["root"] + "/") if rec["root"] != "." else ""
        for e in rec["entries"]:
            if sums.get(pref + e["path"]) != e["sha256"]:
                problems.append(f"{g}: checksums.sha256 disagrees for {e['path']}")
                break
    # 3. archive
    if "archive" in man:
        ap = ROOT / man["archive"]["path"]
        if not ap.is_file() or sha_file(ap) != man["archive"]["sha256"]:
            problems.append("data-api.tar.gz missing or its hash changed")
        else:
            bad = archive_matches(ap, man["inputs"]["api_artifacts"]["entries"])
            if bad:
                problems.append(f"archive contents differ from the inventory: {bad[:5]}")
    # 4. git
    head = git("rev-parse", "HEAD").strip()
    if head != man["git"]["commit"]:
        problems.append(f"HEAD is {head[:12]} but the bundle was made at {man['git']['commit'][:12]}")
    # 5. protected data
    c = data_inventory_check(a.quick)
    if c["status"] == "FAIL":
        problems.append(c["detail"])
    print(f"verify {a.release_id}: {'OK' if not problems else 'FAILED'}")
    print(f"  commit {man['git']['commit'][:12]} ({'clean' if man['git']['working_tree_clean'] else 'dirty at creation'}), "
          f"{sum(g['files'] for g in man['inputs'].values())} files, data inventory: {c['detail']}")
    for p in problems:
        print("  -", p)
    return 0 if not problems else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--release-id", required=True)
    c.add_argument("--api-dir", default="data/api")
    c.add_argument("--lambda-dir", default="infrastructure/build/lambda")
    c.add_argument("--web-dist", default="apps/web/dist")
    c.add_argument("--allow-dirty", action="store_true", help="record uncommitted changes instead of refusing")
    c.add_argument("--no-archive", action="store_true")
    c.add_argument("--quick", action="store_true", help="recheck only data/api, not the 800 MB of raw data")
    c.add_argument("--test-results", help="JSON file with the test summary to embed in validation.json")
    c.add_argument("--keep-failed", action="store_true", help="write the bundle even if a check fails (for investigation)")
    v = sub.add_parser("verify")
    v.add_argument("--release-id", required=True)
    v.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    try:
        return create(a) if a.cmd == "create" else verify(a)
    except BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
