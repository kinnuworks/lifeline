"""Find and read satellite images for any area and date.

Sentinel-1 terrain-corrected radar (RTC), Sentinel-2 surface reflectance and
the Copernicus 30 m elevation model all come from the Microsoft Planetary
Computer STAC catalogue, which needs no account. Every read is warped straight
onto the run grid, so later steps never deal with projections.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
PIXEL_M = 20.0
REVISIT_DAYS = 12


@dataclass
class Grid:
    crs: str
    transform: tuple
    width: int
    height: int
    pixel_m: float = PIXEL_M

    @classmethod
    def for_bbox(cls, bbox: tuple[float, float, float, float], pixel_m: float = PIXEL_M) -> "Grid":
        """A north-up UTM grid covering a lon/lat box."""
        lon = (bbox[0] + bbox[2]) / 2
        lat = (bbox[1] + bbox[3]) / 2
        zone = int((lon + 180) // 6) + 1
        epsg = (32600 if lat >= 0 else 32700) + zone
        crs = f"EPSG:{epsg}"
        x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
        x0 = math.floor(x0 / pixel_m) * pixel_m
        y1 = math.ceil(y1 / pixel_m) * pixel_m
        width = int(math.ceil((x1 - x0) / pixel_m))
        height = int(math.ceil((y1 - y0) / pixel_m))
        t = from_origin(x0, y1, pixel_m, pixel_m)
        return cls(crs=crs, transform=tuple(t)[:6], width=width, height=height, pixel_m=pixel_m)

    @property
    def affine(self) -> rasterio.Affine:
        return rasterio.Affine(*self.transform)

    def bounds(self) -> tuple[float, float, float, float]:
        a = self.affine
        return (a.c, a.f + a.e * self.height, a.c + a.a * self.width, a.f)

    def to_json(self) -> dict:
        return {"crs": self.crs, "transform": list(self.transform), "width": self.width, "height": self.height, "pixel_m": self.pixel_m}

    @classmethod
    def from_json(cls, d: dict) -> "Grid":
        return cls(crs=d["crs"], transform=tuple(d["transform"]), width=d["width"], height=d["height"], pixel_m=d["pixel_m"])


def catalog() -> pystac_client.Client:
    return pystac_client.Client.open(STAC_URL, modifier=pc.sign_inplace)


def read_to_grid(href: str, grid: Grid, resampling: Resampling = Resampling.average, nodata: float | None = None) -> np.ndarray:
    """Read one band of a cloud-optimised GeoTIFF, warped onto the grid."""
    with rasterio.Env(GDAL_HTTP_MAX_RETRY=4, GDAL_HTTP_RETRY_DELAY=1, GDAL_HTTP_TIMEOUT=90, GDAL_HTTP_CONNECTTIMEOUT=20, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", VSI_CACHE=True, GDAL_HTTP_MULTIPLEX=True, GDAL_HTTP_VERSION=2):
        level = None
        with rasterio.open(href) as probe:
            native = abs(probe.transform.a)
            ovr = probe.overviews(1)
            # use the coarsest built-in overview that is still at least as fine as the grid
            for i, f in enumerate(ovr):
                if native * f <= grid.pixel_m * 1.01 and probe.crs and probe.crs.is_projected:
                    level = i
        kwargs = {"OVERVIEW_LEVEL": level} if level is not None else {}
        with rasterio.open(href, **kwargs) as src:
            src_nodata = src.nodata if nodata is None else nodata
            with WarpedVRT(
                src,
                crs=grid.crs,
                transform=grid.affine,
                width=grid.width,
                height=grid.height,
                resampling=resampling,
                src_nodata=src_nodata,
                nodata=np.nan,
                dtype="float32",
            ) as vrt:
                return vrt.read(1, out_dtype="float32")


# ---------------------------------------------------------------- Sentinel-1


@dataclass
class S1Scene:
    date: dt.date
    orbit: int
    direction: str
    platform: str
    items: list = field(default_factory=list)  # STAC items covering the area on that pass


@dataclass
class RadarPair:
    orbit: int
    direction: str
    before: S1Scene
    after: S1Scene
    history: list[S1Scene]  # earlier same-orbit passes used as the "normal" baseline

    def label(self) -> str:
        return f"track {self.orbit} {self.direction} {self.before.date} -> {self.after.date}"


def find_s1_scenes(bbox, start: dt.date, end: dt.date) -> list[S1Scene]:
    search = catalog().search(collections=["sentinel-1-rtc"], bbox=list(bbox), datetime=f"{start.isoformat()}/{end.isoformat()}")
    grouped: dict[tuple, S1Scene] = {}
    for it in search.items():
        p = it.properties
        d = dt.date.fromisoformat(p["datetime"][:10])
        key = (d, p["sat:relative_orbit"])
        sc = grouped.setdefault(key, S1Scene(date=d, orbit=p["sat:relative_orbit"], direction=p["sat:orbit_state"], platform=p.get("platform", "")))
        sc.items.append(it)
    return sorted(grouped.values(), key=lambda s: (s.orbit, s.date))


def choose_pairs(bbox, event: dt.date, baseline_passes: int = 4) -> list[RadarPair]:
    """For every orbit track over the area: the last pass before the event,
    the first pass after it, and a few earlier passes as a baseline."""
    scenes = find_s1_scenes(bbox, event - dt.timedelta(days=REVISIT_DAYS * (baseline_passes + 2)), event + dt.timedelta(days=REVISIT_DAYS + 2))
    by_orbit = defaultdict(list)
    for s in scenes:
        by_orbit[s.orbit].append(s)
    pairs = []
    for orbit, ss in by_orbit.items():
        before = [s for s in ss if s.date < event]
        # a pass on the event day itself may be before or after the flood: use neither side
        after = [s for s in ss if s.date > event]
        if not before or not after:
            continue
        b, a = before[-1], after[0]
        if (a.date - b.date).days > 3 * REVISIT_DAYS:
            continue
        pairs.append(RadarPair(orbit=orbit, direction=b.direction, before=b, after=a, history=before[-(baseline_passes + 1):-1]))
    # closest post-event pass first: it shows the flood before water recedes
    return sorted(pairs, key=lambda p: (p.after.date - event).days)


def read_s1_scene(scene: S1Scene, grid: Grid) -> dict[str, np.ndarray]:
    """VV and VH gamma-nought in dB, mosaicked over the slices of one pass."""
    out = {}
    for pol in ("vv", "vh"):
        acc = np.full((grid.height, grid.width), np.nan, dtype="float32")
        for it in scene.items:
            arr = read_to_grid(it.assets[pol].href, grid, nodata=0)
            fill = np.isnan(acc) & np.isfinite(arr) & (arr > 0)
            acc[fill] = arr[fill]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[pol] = (10.0 * np.log10(np.where(acc > 0, acc, np.nan))).astype("float32")
    return out


# ---------------------------------------------------------------- Sentinel-2


def find_s2(bbox, start: dt.date, end: dt.date, max_cloud: float = 60.0) -> list:
    search = catalog().search(
        collections=["sentinel-2-l2a"],
        bbox=list(bbox),
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    return sorted(search.items(), key=lambda it: it.properties["eo:cloud_cover"])


def read_s2_composite(items: list, grid: Grid, bands=("B03", "B04", "B08", "B11")) -> dict[str, np.ndarray] | None:
    """Cloud-masked mosaic of the clearest scenes (scene classification layer)."""
    if not items:
        return None
    from concurrent.futures import ThreadPoolExecutor

    out = {b: np.full((grid.height, grid.width), np.nan, dtype="float32") for b in bands}
    use = items[:6]
    jobs = [(i, "SCL") for i in range(len(use))] + [(i, b) for i in range(len(use)) for b in bands]

    def read(job):
        i, b = job
        if b == "SCL":
            return job, read_to_grid(use[i].assets["SCL"].href, grid, resampling=Resampling.nearest)
        return job, read_to_grid(use[i].assets[b].href, grid) / 10000.0

    with ThreadPoolExecutor(max_workers=16) as ex:   # all reads at once; the mosaic is built in cloud order below
        got = dict(ex.map(read, jobs))
    for i in range(len(use)):
        clear = np.isin(got[(i, "SCL")], [2, 4, 5, 6, 7, 11])  # dark, vegetation, bare, water, unclassified, snow
        for b in bands:
            arr = got[(i, b)]
            fill = np.isnan(out[b]) & clear & np.isfinite(arr)
            out[b][fill] = arr[fill]
    return out


# ---------------------------------------------------------------- DEM


def read_dem(bbox, grid: Grid) -> np.ndarray:
    items = list(catalog().search(collections=["cop-dem-glo-30"], bbox=list(bbox)).items())
    dem = np.full((grid.height, grid.width), np.nan, dtype="float32")
    for it in items:
        arr = read_to_grid(it.assets["data"].href, grid, resampling=Resampling.bilinear)
        fill = np.isnan(dem) & np.isfinite(arr)
        dem[fill] = arr[fill]
    return dem


def write_raster(path: Path, arr: np.ndarray, grid: Grid, nodata=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": str(arr.dtype),
        "crs": grid.crs,
        "transform": grid.affine,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)


def read_raster(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)


def save_grid(run_dir: Path, grid: Grid) -> None:
    (run_dir / "grid.json").write_text(json.dumps(grid.to_json(), indent=1))
