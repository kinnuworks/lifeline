import numpy as np
import pytest

from lifeline.model.infer import WEIGHTS

pytestmark = pytest.mark.skipif(not WEIGHTS.exists(), reason="model weights not exported yet")


def _scene(h, w, seed=0):
    rng = np.random.default_rng(seed)
    vv = rng.normal(-9, 3, (h, w)).astype(np.float32)
    vh = vv - 7 + rng.normal(0, 1, (h, w)).astype(np.float32)
    # A dark band (open water) across the scene.
    vv[h // 3: h // 3 + max(h // 6, 1)] = -22 + rng.normal(0, 1, (max(h // 6, 1), w))
    vh[h // 3: h // 3 + max(h // 6, 1)] = -29 + rng.normal(0, 1, (max(h // 6, 1), w))
    return vv, vh


def test_shape_and_range():
    from lifeline.model.infer import predict_water
    vv, vh = _scene(512, 512)
    p = predict_water(vv, vh)
    assert p.shape == (512, 512) and p.dtype == np.float32
    assert np.nanmin(p) >= 0 and np.nanmax(p) <= 1


@pytest.mark.parametrize("shape", [(700, 913), (100, 37), (1031, 515)])
def test_non_multiple_sizes(shape):
    from lifeline.model.infer import predict_water
    vv, vh = _scene(*shape, seed=1)
    p = predict_water(vv, vh, tile=512, overlap=64)
    assert p.shape == shape
    assert np.isfinite(p).all()
    assert (p >= 0).all() and (p <= 1).all()


def test_deterministic():
    from lifeline.model.infer import predict_water
    vv, vh = _scene(600, 600, seed=2)
    a = predict_water(vv, vh)
    b = predict_water(vv, vh)
    np.testing.assert_allclose(a, b, atol=1e-5)


def test_nan_propagates_and_water_detected():
    from lifeline.model.infer import predict_water
    vv, vh = _scene(512, 512, seed=3)
    vv[:10, :10] = np.nan
    p = predict_water(vv, vh)
    assert np.isnan(p[:10, :10]).all()
    assert np.isfinite(p[10:, 10:]).all()
    band = p[512 // 3 + 20: 512 // 3 + 60, 50:-50]
    land = p[400:, 50:-50]
    assert np.nanmean(band) > np.nanmean(land)


def test_rejects_bad_input():
    from lifeline.model.infer import predict_water
    with pytest.raises(ValueError):
        predict_water(np.zeros((10, 10)), np.zeros((10, 11)))


def _write_run(tmp_path, h=260, w=333):
    import rasterio
    from rasterio.transform import from_origin
    r = tmp_path / "rasters"
    r.mkdir(parents=True)
    vv, vh = _scene(h, w, seed=4)
    prof = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32", crs="EPSG:32645",
                transform=from_origin(300000, 3100000, 20, 20), nodata=np.nan)
    for name, a in (("s1_post_vv_db", vv), ("s1_post_vh_db", vh)):
        with rasterio.open(r / f"{name}.tif", "w", **prof) as s:
            s.write(a, 1)
    return r


def test_run_on_run_dir(tmp_path):
    import rasterio
    from lifeline.model.infer import run_on_run_dir
    r = _write_run(tmp_path)
    out = run_on_run_dir(tmp_path)
    assert out["water_km2"] >= 0
    with rasterio.open(r / "model_prob.tif") as s:
        assert s.dtypes[0] == "float32" and s.shape == (260, 333) and s.crs.to_epsg() == 32645


def test_run_on_run_dir_missing_weights(tmp_path):
    from lifeline.model.infer import run_on_run_dir
    _write_run(tmp_path)
    with pytest.raises(RuntimeError):
        run_on_run_dir(tmp_path, weights=tmp_path / "missing.pt")
