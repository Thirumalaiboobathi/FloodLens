"""Assemble the API Lambda package without Docker: infrastructure/build/lambda/.

Contents: floodlens_api (pure Python), Linux/arm64 wheels for fastapi/pydantic/mangum (cross-installed
with pip, binary wheels only) and the prebuilt artifact tree data/api/<region>. No rasterio, GeoPandas
or other geospatial library is packaged: the API only reads artifacts built offline.

Usage: python scripts/package_lambda.py [--arch arm64|x86_64] [--python 3.13]
Nothing is uploaded and no AWS call is made; CDK uploads the folder at deploy time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "infrastructure" / "build" / "lambda"
REQUIREMENTS = ROOT / "services" / "api" / "requirements-lambda.txt"  # exact, tested versions
PLATFORMS = {"arm64": ["manylinux2014_aarch64", "manylinux_2_17_aarch64"],
             "x86_64": ["manylinux2014_x86_64", "manylinux_2_17_x86_64"]}
MAX_UNZIPPED_MB = 240  # Lambda limit is 250 MB unzipped including layers


def main(arch: str, py: str) -> int:
    api = ROOT / "data" / "api"
    regions = [p for p in api.iterdir() if (p / "index.json").is_file()] if api.is_dir() else []
    if not regions:
        print("no built artifacts in data/api: run scripts/build_api_artifacts.py first", file=sys.stderr)
        return 2
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--target", str(OUT), "--python-version", py,
           "--implementation", "cp", "--only-binary=:all:", "--no-compile"]
    for plat in PLATFORMS[arch]:
        cmd += ["--platform", plat]
    subprocess.run(cmd + ["-r", str(REQUIREMENTS)], check=True)
    shutil.copytree(ROOT / "services" / "api" / "floodlens_api", OUT / "floodlens_api",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for r in regions:
        shutil.copytree(r, OUT / "data" / "api" / r.name)
    shutil.rmtree(OUT / "bin", ignore_errors=True)  # console scripts pip made for the build host (.exe on Windows)
    for junk in list(OUT.rglob("__pycache__")) + list(OUT.glob("*.dist-info/RECORD")):
        shutil.rmtree(junk, ignore_errors=True) if junk.is_dir() else junk.unlink(missing_ok=True)
    banned = [p.name for p in OUT.iterdir() if p.name.lower().startswith(("rasterio", "geopandas", "gdal", "numpy"))]
    if banned:
        print(f"geospatial/native packages must not be packaged: {banned}", file=sys.stderr)
        return 3
    core = list(OUT.glob("pydantic_core/_pydantic_core.cpython-313-*"))
    want = "aarch64" if arch == "arm64" else "x86_64"
    if py == "3.13" and not any(want in c.name for c in core):
        print(f"pydantic_core native module is not {want}/cp313: {[c.name for c in core]}", file=sys.stderr)
        return 5
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 2**20
    print(f"lambda package: {OUT}  {size:.1f} MiB unzipped  regions={[r.name for r in regions]}")
    if size > MAX_UNZIPPED_MB:
        print("package exceeds the Lambda unzipped size limit; move artifacts to S3", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", choices=PLATFORMS, default="arm64")
    ap.add_argument("--python", default="3.13")
    a = ap.parse_args()
    raise SystemExit(main(a.arch, a.python))
