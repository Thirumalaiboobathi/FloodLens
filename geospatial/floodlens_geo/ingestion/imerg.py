"""NASA GPM IMERG V07 daily rainfall ingestion (Final and Late Run).

Products (GES DISC, collection version "07"): GPM_3IMERGDF (Final), GPM_3IMERGDL (Late) and, for
identification only, GPM_3IMERGDE (Early). 0.1 deg grid, daily mean precipitation rate in mm/day.
The granule version (V07B, V07C, ...) exists only in the file name and is read per granule; no
version is hard-coded. Runs are different processing streams and are never mixed implicitly.
Granules are discovered through the public NASA CMR API (no credentials) and downloaded from
data.gesdisc.earthdata.nasa.gov with an Earthdata Login user token sent as
``Authorization: Bearer <token>`` (https://urs.earthdata.nasa.gov/documentation/for_users/user_token).

The token is read only from ``os.environ["EARTHDATA_TOKEN"]``. It is never logged, stored,
or included in exception text, and it is only attached to https requests to *.earthdata.nasa.gov.
"""

from __future__ import annotations

import itertools
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import urljoin, urlparse

import numpy as np
import rasterio
import requests
import xarray as xr
from rasterio.transform import from_origin

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"
VERSION = "07"
VARIABLE = "precipitation"
EXPECTED_UNITS = "mm/day"
RESOLUTION_DEG = 0.1
QUALITY_VARIABLES = (
    "precipitation",
    "precipitation_cnt",
    "precipitation_cnt_cond",
    "randomError",
    "randomError_cnt",
    "probabilityLiquidPrecipitation",
)
# randomError is recorded as delivered but is NOT used as an uncertainty: its stored magnitude and
# its linear growth with randomError_cnt contradict the documented daily RMS (mm/day) definition.
RANDOM_ERROR_STATUS = "as_delivered_unverified"
RANDOM_ERROR_NOTE = (
    "Stored as delivered by NASA, unchanged. Its magnitude (hundreds to thousands of mm/day where "
    "the mean rainfall is a few mm/day) and its linear growth with randomError_cnt are inconsistent "
    "with the documented daily root-mean-square error (mm/day). It is not a validated uncertainty "
    "measure: do not display it, accumulate it across days, or use it in scores or confidence."
)
RANDOM_ERROR_HYPOTHESIS = {
    "status": "unverified_research_hypothesis",
    "formula": "sqrt(24 * V / N), V = stored randomError, N = randomError_cnt",
    "assumption": "stored value is the sum over valid half-hours of squared half-hourly error "
                  "(mm^2/hr^2); not confirmed by NASA or by half-hourly reconstruction",
    "units_if_true": "mm/day",
}
OUT_NODATA = -9999.0
TOKEN_ENV = "EARTHDATA_TOKEN"
ALLOWED_HOST_SUFFIX = ".earthdata.nasa.gov"
MAX_REDIRECTS = 8

BBox = tuple[float, float, float, float]  # west, south, east, north


class Run(StrEnum):
    """IMERG processing stream. Runs differ in latency and in gauge adjustment."""

    EARLY = "early"  # ~4 h latency; identification only, not ingested
    LATE = "late"  # ~14 h latency
    FINAL = "final"  # gauge-adjusted, months of latency


SHORT_NAMES = {Run.EARLY: "GPM_3IMERGDE", Run.LATE: "GPM_3IMERGDL", Run.FINAL: "GPM_3IMERGDF"}
SHORT_NAME = SHORT_NAMES[Run.FINAL]  # kept for backward compatibility
_NAME_CODES = {"": Run.FINAL, "E": Run.EARLY, "L": Run.LATE}
FILENAME_RE = re.compile(
    r"^3B-DAY(?:-(?P<code>[EL]))?\.MS\.MRG\.3IMERG\.(?P<ymd>\d{8})-S000000-E235959"
    r"\.V(?P<ver>\d{2}[A-Z])\.nc4$"
)
PAGE_SIZE = 200
MAX_PAGES = 50


class ImergError(Exception):
    """Base class; messages never contain credentials."""


class AuthenticationError(ImergError):
    """401: token missing, invalid or expired."""


class AuthorizationError(ImergError):
    """403: token valid but not authorised (e.g. GES DISC app not approved)."""


class RateLimitError(ImergError):
    """429 persisted after retries."""


class DownloadError(ImergError):
    """Other HTTP or network failure."""


class ImergDataError(ImergError):
    """File content is not what the product specification says it should be."""


@dataclass(frozen=True)
class ParsedName:
    run: Run
    day: date
    version: str  # e.g. "V07B", "V07C"


def parse_granule_name(name: str) -> ParsedName:
    """Run, observation day and product version from a daily IMERG file name (any V07x)."""
    m = FILENAME_RE.match(name.rsplit("/", 1)[-1])
    if not m:
        raise ImergDataError(f"not a daily IMERG granule name: {name!r}")
    d = m["ymd"]
    try:
        day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
    except ValueError:
        raise ImergDataError(f"invalid date in granule name {name!r}") from None
    return ParsedName(_NAME_CODES[m["code"] or ""], day, "V" + m["ver"])


@dataclass(frozen=True)
class Granule:
    day: date
    title: str
    url: str
    run: Run = Run.FINAL
    version: str | None = None  # per-granule product version from the file name
    time_start: str | None = None  # CMR temporal extent, ISO 8601 UTC
    time_end: str | None = None
    updated: str | None = None  # CMR record update time (publication proxy)
    size_mb: float | None = None

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    @property
    def granule_id(self) -> str:
        return self.filename.removesuffix(".nc4")


def get_token(environ=os.environ) -> str:
    token = environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise AuthenticationError(f"environment variable {TOKEN_ENV} is not set or empty")
    return token


def _is_trusted(url: str) -> bool:
    u = urlparse(url)
    host = u.hostname or ""
    return u.scheme == "https" and (host.endswith(ALLOWED_HOST_SUFFIX) or host == "earthdata.nasa.gov")


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def is_complete_day(g: Granule) -> bool:
    """True if the CMR extent starts at 00:00:00 UTC of the granule's day and ends at its last second."""
    if not g.time_start or not g.time_end:
        return False
    t0, t1 = _parse_utc(g.time_start), _parse_utc(g.time_end)
    midnight = datetime(g.day.year, g.day.month, g.day.day, tzinfo=UTC)
    return t0 == midnight and midnight + timedelta(hours=23, minutes=59, seconds=59) <= t1 < (
        midnight + timedelta(days=1, seconds=1)
    )


def _cmr_entries(s, params: dict, page_size: int) -> list[dict]:
    """All CMR entries for a query, following ``page_num`` until a short page."""
    entries: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        try:
            r = s.get(
                CMR_GRANULES, params={**params, "page_size": page_size, "page_num": page}, timeout=30
            )
        except requests.RequestException as e:
            raise DownloadError(f"CMR request failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise DownloadError(f"CMR returned HTTP {r.status_code}")
        batch = r.json()["feed"]["entry"]
        entries.extend(batch)
        if len(batch) < page_size:
            return entries
    raise DownloadError(f"CMR result exceeds {MAX_PAGES} pages")


def _to_granule(entry: dict, run: Run) -> Granule | None:
    url = next(
        (
            link["href"]
            for link in entry["links"]
            if link.get("rel", "").endswith("/data#") and link["href"].endswith(".nc4")
            and link["href"].startswith("https://")
        ),
        None,
    )
    if url is None:
        return None
    p = parse_granule_name(url)
    if p.run is not run:
        raise ImergDataError(
            f"granule {url.rsplit('/', 1)[-1]} is a {p.run.value} run file in a {run.value} query; "
            "refusing to mix processing streams"
        )
    start = entry["time_start"]
    if date.fromisoformat(start[:10]) != p.day:
        raise ImergDataError(f"CMR time_start {start} does not match file name date {p.day}")
    size = entry.get("granule_size")
    return Granule(
        p.day, entry["title"], url, run=run, version=p.version, time_start=start,
        time_end=entry.get("time_end"), updated=entry.get("updated"),
        size_mb=float(size) if size is not None else None,
    )


def find_granules(
    start: date,
    end: date,
    session: requests.Session | None = None,
    *,
    run: Run = Run.FINAL,
    page_size: int = PAGE_SIZE,
) -> list[Granule]:
    """Daily granules of one run with data in [start, end] inclusive, via CMR (public, paged)."""
    if end < start:
        raise ValueError("end before start")
    params = {
        "short_name": SHORT_NAMES[run],
        "version": VERSION,
        "temporal": f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z",
        "sort_key": "start_date",
    }
    entries = _cmr_entries(session or requests.Session(), params, page_size)
    return sorted((g for e in entries if (g := _to_granule(e, run))), key=lambda g: g.day)


def find_latest_complete(
    days: int,
    session: requests.Session | None = None,
    *,
    run: Run = Run.LATE,
    now: datetime | None = None,
    lookback_days: int = 21,
    page_size: int = PAGE_SIZE,
) -> list[Granule]:
    """The latest ``days`` consecutive complete UTC days of one run, oldest first.

    The newest granule is discovered dynamically. A granule counts only if its CMR extent covers
    its whole UTC day and that day has ended. An incomplete newest granule is skipped; a duplicate
    day or a gap inside the window raises instead of silently choosing.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    found = find_granules(
        today - timedelta(days=days + lookback_days), today, session, run=run, page_size=page_size
    )
    done = [g for g in found if g.day < today and is_complete_day(g)]
    if not done:
        raise ImergDataError(f"no complete {run.value} run day in the last {lookback_days} days")
    by_day: dict[date, Granule] = {}
    for g in done:
        if g.day in by_day:
            raise ImergDataError(f"more than one {run.value} granule for {g.day}")
        by_day[g.day] = g
    latest = max(by_day)
    window = [latest - timedelta(days=k) for k in range(days - 1, -1, -1)]
    missing = [d.isoformat() for d in window if d not in by_day]
    if missing:
        raise ImergDataError(f"{run.value} run has no complete granule for: {', '.join(missing)}")
    return [by_day[d] for d in window]


def _raise_for_status(code: int, retry_after: str | None = None) -> None:
    if code == 401:
        raise AuthenticationError(
            "HTTP 401: Earthdata token rejected (invalid, revoked or expired; tokens last 60 days)"
        )
    if code == 403:
        raise AuthorizationError(
            "HTTP 403: token not authorised for this data; approve 'NASA GESDISC DATA ARCHIVE' "
            "under Earthdata Login > Applications > Authorized Apps"
        )
    if code == 429:
        raise RateLimitError(f"HTTP 429: rate limited (Retry-After={retry_after})")
    if code >= 400:
        raise DownloadError(f"HTTP {code}")


def _get_following_redirects(session, url: str, token: str, timeout: int):
    """GET with manual redirects so the bearer token only goes to trusted NASA hosts."""
    for _ in range(MAX_REDIRECTS):
        headers = {"Authorization": f"Bearer {token}"} if _is_trusted(url) else {}
        r = session.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers["Location"])
            r.close()
            if not url.startswith("https://"):
                raise DownloadError("refusing non-https redirect")
            continue
        return r
    raise DownloadError("too many redirects")


def download_granule(
    granule: Granule,
    dest: Path,
    token: str,
    session: requests.Session | None = None,
    *,
    retries: int = 3,
    backoff_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    timeout: int = 60,
) -> Path:
    """Download one granule. 401/403 fail immediately; 429/5xx/network errors are retried."""
    s = session or requests.Session()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / granule.url.rsplit("/", 1)[-1]
    if out.exists() and out.stat().st_size > 0:
        return out
    last: ImergError | None = None
    for attempt in range(retries + 1):
        if attempt:
            sleep(backoff_s * 2 ** (attempt - 1))
        try:
            r = _get_following_redirects(s, granule.url, token, timeout)
            try:
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = r.headers.get("Retry-After")
                    last = (
                        RateLimitError(f"HTTP 429 (Retry-After={wait})")
                        if r.status_code == 429
                        else DownloadError(f"HTTP {r.status_code}")
                    )
                    if wait and wait.isdigit() and attempt < retries:
                        sleep(min(int(wait), 60))
                    continue
                _raise_for_status(r.status_code)
                ctype = r.headers.get("Content-Type", "")
                if "text/html" in ctype:
                    # Earthdata login page instead of data == not authenticated
                    raise AuthenticationError("received a login page instead of data (token not accepted)")
                tmp = out.with_suffix(out.suffix + ".part")
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(out)
                return out
            finally:
                r.close()
        except requests.RequestException as e:
            last = DownloadError(f"network failure: {type(e).__name__}")
    assert last is not None
    raise last


@dataclass(frozen=True)
class DailyField:
    day: date
    data: np.ndarray  # mm/day, north-up, NaN where missing
    west: float
    north: float
    res: float
    source_file: str


def check_axis(values, name: str, limit: float) -> tuple[float, bool]:
    """Validate one coordinate axis against the nominal 0.1 degree grid.

    NASA stores IMERG ``lon`` as float32, whose quantisation step near |180| is 1.5e-5 degrees, so
    adjacent differences legitimately range 0.099991..0.100006. The tolerance is therefore derived
    from the coordinate dtype (two roundings = 2 ulp at the largest |value|) instead of a fixed
    constant; a wrong spacing (e.g. 0.09), irregular steps or a drifting axis still fail. The data
    are never resampled: the axis is only checked, and the returned origin is the least-squares
    regular-grid position (residuals are verified to be within the same tolerance).

    Returns ``(first_centre, increasing)`` for the axis in file order.
    """
    a = np.asarray(values)
    if a.ndim != 1 or a.size < 2 or not np.issubdtype(a.dtype, np.floating):
        raise ImergDataError(f"invalid {name} coordinates (need a 1-D float array, >= 2 values)")
    if not np.isfinite(a).all():
        raise ImergDataError(f"{name} contains non-finite values")
    tol = 2 * float(np.spacing(np.abs(a).max().astype(a.dtype))) + 1e-9
    x = a.astype("float64")
    d = np.diff(x)
    if not (np.all(d > 0) or np.all(d < 0)):
        raise ImergDataError(f"{name} is not strictly monotonic")
    if np.abs(np.abs(d) - RESOLUTION_DEG).max() > tol:
        raise ImergDataError(
            f"unexpected {name} spacing: steps {np.abs(d).min():.9f}..{np.abs(d).max():.9f}, "
            f"expected {RESOLUTION_DEG} +/- {tol:.2g} (coordinate dtype {a.dtype})"
        )
    increasing = bool(d[0] > 0)
    sign = 1.0 if increasing else -1.0
    idx = np.arange(x.size)
    first = float(np.mean(x - sign * idx * RESOLUTION_DEG))
    if np.abs(x - (first + sign * idx * RESOLUTION_DEG)).max() > tol:
        raise ImergDataError(f"{name} is not a regular grid (drift beyond dtype precision)")
    half = RESOLUTION_DEG / 2
    if min(x[0], x[-1]) - half < -limit - tol or max(x[0], x[-1]) + half > limit + tol:
        raise ImergDataError(f"{name} outside +/-{limit} degrees")
    return first, increasing


def read_daily(path: Path) -> DailyField:
    """Read the daily precipitation grid with checked units, fill values and orientation."""
    try:
        ds = xr.open_dataset(path, engine="h5netcdf", mask_and_scale=False)
    except Exception as e:  # noqa: BLE001 - unreadable file of any kind
        raise ImergDataError(f"cannot open {path.name}: {type(e).__name__}") from None
    with ds:
        if VARIABLE not in ds:
            raise ImergDataError(f"variable '{VARIABLE}' not found in {path.name}")
        v = ds[VARIABLE]
        units = str(v.attrs.get("units", ""))
        if units != EXPECTED_UNITS:
            raise ImergDataError(f"unexpected units {units!r}, expected {EXPECTED_UNITS!r}")
        if "lat" not in v.dims or "lon" not in v.dims:
            raise ImergDataError(f"unexpected dims {v.dims}")
        if "time" in v.dims:
            v = v.isel(time=0)
        v = v.transpose("lat", "lon")
        arr = v.values.astype("float64")
        fill = v.attrs.get("_FillValue", v.attrs.get("missing_value"))
        if fill is not None:
            arr[arr == float(np.asarray(fill).ravel()[0])] = np.nan
        arr[~np.isfinite(arr)] = np.nan
        if (arr[np.isfinite(arr)] < 0).any():
            raise ImergDataError("negative precipitation after fill-value masking")
        if "lat" not in ds.variables or "lon" not in ds.variables:
            raise ImergDataError("missing lat/lon coordinate variables")
        lat_first, lat_inc = check_axis(ds["lat"].values, "lat", 90.0)
        lon_first, lon_inc = check_axis(ds["lon"].values, "lon", 180.0)
        if not lon_inc:
            raise ImergDataError("longitude is not west-to-east increasing")
        if arr.shape != (ds.sizes["lat"], ds.sizes["lon"]):
            raise ImergDataError(f"data shape {arr.shape} does not match coordinates")
        if lat_inc:  # south-up -> flip to north-up
            arr = arr[::-1]
            lat_first = lat_first + (arr.shape[0] - 1) * RESOLUTION_DEG
        res = RESOLUTION_DEG
        lat = np.array([lat_first])
        lon = np.array([lon_first])
        m = re.search(r"(\d{8})-S000000", path.name)
        if not m:
            raise ImergDataError(f"cannot derive date from file name {path.name}")
        d = m.group(1)
        return DailyField(
            day=date(int(d[:4]), int(d[4:6]), int(d[6:])),
            data=arr,
            west=float(lon[0]) - res / 2,
            north=float(lat[0]) + res / 2,
            res=res,
            source_file=path.name,
        )


_HEADER_RE = {
    k: re.compile(rf"{k}GranuleDateTime=([^;\s]+)") for k in ("Start", "Stop")
}


@dataclass(frozen=True)
class FileCoverage:
    start: datetime
    stop: datetime


def verify_internal_date(path: Path, expected_day: date) -> FileCoverage:
    """Check that the file's own FileHeader and time axis cover exactly ``expected_day`` (UTC).

    Guards against a file whose name says one day but whose content is another, or a partial day.
    """
    try:
        ds = xr.open_dataset(path, engine="h5netcdf", mask_and_scale=False, decode_times=True)
    except Exception as e:  # noqa: BLE001 - unreadable file of any kind
        raise ImergDataError(f"cannot open {path.name}: {type(e).__name__}") from None
    with ds:
        header = str(ds.attrs.get("FileHeader", ""))
        found = {k: rx.search(header) for k, rx in _HEADER_RE.items()}
        if not all(found.values()):
            raise ImergDataError(f"{path.name}: FileHeader has no Start/StopGranuleDateTime")
        start = _parse_utc(found["Start"][1].replace("Z", "+00:00"))
        stop = _parse_utc(found["Stop"][1].replace("Z", "+00:00"))
        midnight = datetime(expected_day.year, expected_day.month, expected_day.day, tzinfo=UTC)
        if start != midnight:
            raise ImergDataError(
                f"{path.name}: internal start {start.isoformat()} is not {midnight.isoformat()}"
            )
        if not midnight + timedelta(hours=23, minutes=59, seconds=59) <= stop < (
            midnight + timedelta(days=1, seconds=1)
        ):
            raise ImergDataError(f"{path.name}: internal stop {stop.isoformat()} is not end of day")
        if "time" in ds.variables:
            t = np.asarray(ds["time"].values).ravel()
            if t.size != 1 or str(t[0])[:10] != expected_day.isoformat():
                raise ImergDataError(f"{path.name}: time axis does not match {expected_day}")
        return FileCoverage(start, stop)


def _north_up(ds, name: str) -> np.ndarray:
    """A (time, lon, lat) variable as a north-up (lat, lon) float64 array; fill value -> NaN."""
    if name not in ds:
        raise ImergDataError(f"variable '{name}' not found")
    v = ds[name]
    if "time" in v.dims:
        v = v.isel(time=0)
    arr = v.transpose("lat", "lon").values.astype("float64")
    fill = v.attrs.get("_FillValue", v.attrs.get("missing_value"))
    if fill is not None:
        arr[arr == float(np.asarray(fill).ravel()[0])] = np.nan
    lat = ds["lat"].values
    return arr[::-1] if lat[0] < lat[-1] else arr


def _summary(a: np.ndarray, units: str) -> dict:
    ok = np.isfinite(a)
    v = a[ok]
    out: dict = {"units": units, "aoi_cells": int(a.size), "valid_cells": int(ok.sum()),
                 "missing_cells": int((~ok).sum())}
    out.update({k: round(float(fn(v)), 3) for k, fn in
                (("min", np.min), ("mean", np.mean), ("max", np.max))} if v.size else {})
    return out


def quality_stats(path: Path, ref: DailyField, bbox: BBox) -> dict:
    """AOI statistics of precipitation validity and the IMERG quality/uncertainty fields.

    Zero rainfall is a valid observation. A cell is *unavailable* only when ``precipitation`` is
    the fill value; ``zero_rain_with_no_retrievals`` flags valid zeros that have no supporting
    half-hourly retrieval count so they can be reviewed rather than trusted blindly.
    """
    try:
        ds = xr.open_dataset(path, engine="h5netcdf", mask_and_scale=False)
    except Exception as e:  # noqa: BLE001
        raise ImergDataError(f"cannot open {path.name}: {type(e).__name__}") from None
    with ds:
        grids = {n: clip_to_bbox(_north_up(ds, n), ref, bbox)[0] for n in QUALITY_VARIABLES}
        units = {n: str(ds[n].attrs.get("units", "")) for n in QUALITY_VARIABLES}
    rain, cnt = grids["precipitation"], grids["precipitation_cnt"]
    ok = np.isfinite(rain)
    # randomError is 0 (not fill) where precipitation is fill, and meaningless where no valid
    # half-hourly error exists: those cells must not enter its statistics.
    err_cnt = grids["randomError_cnt"]
    err_ok = ok & np.isfinite(err_cnt) & (err_cnt > 0)
    err = np.where(err_ok, grids["randomError"], np.nan)
    out = {n: _summary(grids[n], units[n]) for n in QUALITY_VARIABLES if n != "randomError"}
    out["randomError"] = {
        **_summary(err, units["randomError"]),
        "status": RANDOM_ERROR_STATUS,
        "note": RANDOM_ERROR_NOTE,
        "excluded_cells": int((~err_ok).sum()),
        "hypothesis": {**RANDOM_ERROR_HYPOTHESIS, **_summary(
            np.sqrt(24 * err / np.where(err_ok, err_cnt, np.nan)), "mm/day (if hypothesis holds)")},
    }
    out["precipitation"].update({
        "zero_rain_cells": int((ok & (rain == 0)).sum()),
        "wet_cells": int((ok & (rain > 0)).sum()),
        "zero_rain_with_no_retrievals": int((ok & (rain == 0) & (cnt == 0)).sum()),
    })
    return out


def accumulate(fields: list[DailyField]) -> tuple[np.ndarray, DailyField]:
    """Total rainfall (mm) over consecutive days; a cell missing on any day stays missing."""
    if not fields:
        raise ValueError("no fields")
    ref = fields[0]
    days = sorted(f.day for f in fields)
    if any((b - a) != timedelta(days=1) for a, b in itertools.pairwise(days)):
        raise ImergDataError("days are not consecutive; refusing to accumulate")
    for f in fields:
        if (f.west, f.north, f.res, f.data.shape) != (ref.west, ref.north, ref.res, ref.data.shape):
            raise ImergDataError("grids differ between days")
    return np.sum([f.data for f in fields], axis=0), ref  # NaN propagates


def clip_to_bbox(total: np.ndarray, ref: DailyField, bbox: BBox):
    """Pixel-aligned window of whole IMERG cells intersecting bbox. Returns (array, transform)."""
    w, s, e, n = bbox
    r = ref.res
    c0 = int(np.floor((w - ref.west) / r + 1e-9))
    c1 = int(np.ceil((e - ref.west) / r - 1e-9))
    r0 = int(np.floor((ref.north - n) / r + 1e-9))
    r1 = int(np.ceil((ref.north - s) / r - 1e-9))
    h, wd = total.shape
    if c0 < 0 or r0 < 0 or c1 > wd or r1 > h or c1 <= c0 or r1 <= r0:
        raise ImergDataError("bbox outside the granule grid")
    return total[r0:r1, c0:c1], from_origin(ref.west + c0 * r, ref.north - r0 * r, r, r)


def write_geotiff(path: Path, arr: np.ndarray, transform, tags: dict[str, str]) -> None:
    out = np.where(np.isfinite(arr), arr, OUT_NODATA).astype("float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=out.shape[0], width=out.shape[1], count=1,
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=OUT_NODATA, compress="deflate",
    ) as dst:
        dst.write(out, 1)
        dst.update_tags(**tags)
        dst.set_band_description(1, "accumulated precipitation (mm)")
