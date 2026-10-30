"""Release-integrity report for the published API contract (read-only; writes only the report).

Usage: python scripts/release_integrity.py [--baseline REV] [--out reports/pre-deployment/release-integrity.json]

Compares frozen v1 schemas/fixtures with a Git baseline when one exists (--baseline, default: the first
commit that contains them). With no baseline it says so and does NOT claim byte-identity; it still checks
that the files on disk equal the models' export, that fixtures validate, that the water additions are
isolated, and records SHA-256 hashes plus a per-route contract snapshot so the next review has a baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

import jsonschema
from fastapi.testclient import TestClient
from floodlens_api import main as api
from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S
from floodlens_api import water_schemas as WS

SCHEMAS = ROOT / "schemas" / "v1"
GROUPS = {"v1.0.0": S.SCHEMAS, "rainfall_1.1.0": RS.RAINFALL_SCHEMAS, "water_1.1.0": WS.WATER_SCHEMAS}
GROUP_OF = {n: g for g, d in GROUPS.items() for n in d}
RAINFALL_PATH_PREFIXES = ("/v1/regions/{region}/rainfall", "/v1/regions/{region}/scenario-catalog")
WATER_PATH_PREFIX = "/v1/regions/{region}/water"
BASE = "/v1/regions/chennai"
# parameterless / fixed requests whose full response bytes are hashed (a snapshot, not a baseline comparison)
SNAPSHOT_NOTE = "Hashes of the CURRENT behaviour (data_freshness.as_of_utc excluded: it is a per-request clock value; age_days still depends on the date); they prove nothing about earlier versions until compared with a committed earlier snapshot."

SNAPSHOT_REQUESTS = [
    "/v1/health", "/v1/regions", f"{BASE}/scenarios", f"{BASE}/scenario-catalog", f"{BASE}/provenance",
    f"{BASE}/rainfall", f"{BASE}/rainfall/coverage", f"{BASE}/water/provenance",
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def lf(b: bytes) -> bytes:
    """Canonical form: LF endings. Windows checkouts (core.autocrlf=true) hold CRLF; Git stores LF."""
    return b.replace(b"\r\n", b"\n")


def stable_sha(content: bytes) -> str:
    """Hash a response with the per-request clock field removed (data_freshness.as_of_utc changes every call)."""
    try:
        doc = json.loads(content)
    except ValueError:
        return sha(content)

    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "as_of_utc"}
        return [strip(i) for i in o] if isinstance(o, list) else o

    return sha(json.dumps(strip(doc), sort_keys=True).encode())


def git(*args: str) -> tuple[int, bytes]:
    r = subprocess.run(["git", *args], check=False, cwd=ROOT, capture_output=True)
    return r.returncode, r.stdout


def baseline_rev(requested: str | None) -> str | None:
    if requested:
        return requested
    rc, out = git("rev-list", "--max-parents=0", "HEAD")
    return out.decode().split()[0] if rc == 0 and out.strip() else None


def main(out_path: Path, requested: str | None) -> int:
    findings: list[dict] = []

    def finding(sev: str, msg: str) -> None:
        findings.append({"severity": sev, "message": msg})

    # 1. files on disk vs the models' own export
    files: dict[str, dict] = {}
    for name, model in {**S.SCHEMAS, **RS.RAINFALL_SCHEMAS, **WS.WATER_SCHEMAS}.items():
        p = SCHEMAS / f"{name}.schema.json"
        fx = SCHEMAS / "fixtures" / f"{name}.example.json"
        want = (json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n").encode()
        raw = p.read_bytes() if p.exists() else b""
        have = lf(raw)
        try:
            jsonschema.validate(json.loads(fx.read_text(encoding="utf-8")), json.loads(have))
            fixture_valid = True
        except Exception as e:  # noqa: BLE001
            fixture_valid = False
            finding("HIGH", f"fixture {fx.name} does not validate: {str(e)[:120]}")
        files[name] = {
            "group": GROUP_OF[name], "schema_sha256_lf": sha(have), "schema_sha256_raw": sha(raw),
            "schema_has_crlf": raw != have,
            "fixture_sha256_lf": sha(lf(fx.read_bytes())) if fx.exists() else None,
            "schema_equals_model_export": have == want, "fixture_valid_against_schema": fixture_valid}
        if have != want:
            finding("HIGH", f"{p.name} differs from the model export")

    # 2. Git baseline for the frozen v1 group
    rev = baseline_rev(requested)
    git_cmp: dict = {"baseline": rev, "status": "baseline_unavailable", "v1_files": {}}
    _rc, tracked = git("ls-files", "schemas")
    if rev is None:
        finding("HIGH", "No Git commit exists (git log is empty), so frozen v1 byte-identity CANNOT be "
                        "verified against history. Commit the current tree, then re-run with --baseline "
                        "pointing at a commit that predates Phase 3 if one exists.")
    elif not tracked.strip():
        git_cmp["status"] = "schemas_untracked_at_head"
        finding("HIGH", "schemas/ is not tracked; no historical copy to compare against.")
    else:
        git_cmp["status"] = "compared"
        for name in S.SCHEMAS:
            for rel in (f"schemas/v1/{name}.schema.json", f"schemas/v1/fixtures/{name}.example.json"):
                rcg, old = git("show", f"{rev}:{rel}")
                cur = lf((ROOT / rel).read_bytes())
                same = rcg == 0 and lf(old) == cur
                git_cmp["v1_files"][rel] = "identical" if same else ("missing_in_baseline" if rcg else "DIFFERENT")
                if not same:
                    finding("BLOCKER", f"{rel}: {git_cmp['v1_files'][rel]}")

    # 3. additivity: the frozen v1 schemas must not mention rainfall/water models
    leaked = []
    for name in S.SCHEMAS:
        text = (SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")
        if any(w in text for w in ("WaterFeature", "RainfallCell", "ObservedRainfall")):
            leaked.append(name)
    if leaked:
        finding("BLOCKER", f"v1 schemas reference 1.1.0 models: {leaked}")
    versions = {"v1": S.SCHEMA_VERSION, "rainfall": RS.RAINFALL_SCHEMA_VERSION, "water": WS.WATER_SCHEMA_VERSION}

    # 4. API routes
    spec = api.app.openapi()
    routes = {}
    for path, item in spec["paths"].items():
        grp = ("water_1.1.0" if path.startswith(WATER_PATH_PREFIX) else
               "rainfall_1.1.0" if path.startswith(RAINFALL_PATH_PREFIXES) else "v1.0.0")
        routes[path] = {"group": grp, "operation_sha256": sha(json.dumps(item, sort_keys=True).encode())}
    client = TestClient(api.app, raise_server_exceptions=False)
    snapshot = {}
    for req in SNAPSHOT_REQUESTS:
        r = client.get(req)
        snapshot[req] = {"status": r.status_code, "response_sha256": stable_sha(r.content)}
    ids = [i for i in client.get(f"{BASE}/rainfall").json()["datasets"]]
    ids = [d["scenario_id"] for d in ids]
    for sid in ids:
        for suffix in ("", "/summary", "/provenance", "/cells"):
            req = f"{BASE}/rainfall/{sid}{suffix}"
            r = client.get(req)
            snapshot[req] = {"status": r.status_code, "response_sha256": stable_sha(r.content)}
    if any(v["status"] != 200 for v in snapshot.values()):
        finding("HIGH", "a snapshot request did not return 200: "
                        + ", ".join(k for k, v in snapshot.items() if v["status"] != 200))
    if any(f["schema_has_crlf"] for f in files.values()):
        gattr = (ROOT / ".gitattributes").exists()
        finding("LOW" if gattr else "MEDIUM",
                "schemas/ on disk use CRLF (Windows autocrlf); hashes are compared after LF normalisation. "
                + ("" if gattr else "Add .gitattributes (schemas/** text eol=lf) so checkouts are byte-stable."))

    report = {
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_comparison": git_cmp,
        "schema_versions": versions,
        "schema_files": files,
        "routes": routes,
        "response_snapshot": snapshot,
        "additive_water": {
            "water_schema_files": sorted(n for n in files if GROUP_OF[n] == "water_1.1.0"),
            "water_routes": sorted(p for p in routes if routes[p]["group"] == "water_1.1.0"),
            "v1_schemas_referencing_1_1_0_models": leaked},
        "findings": findings,
        "notes": [SNAPSHOT_NOTE],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out_path}; git comparison: {git_cmp['status']}; "
          f"findings: {[f['severity'] for f in findings]}")
    return 1 if any(f["severity"] == "BLOCKER" for f in findings) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline")
    ap.add_argument("--out", default=str(ROOT / "reports" / "pre-deployment" / "release-integrity.json"))
    a = ap.parse_args()
    sys.exit(main(Path(a.out), a.baseline))
