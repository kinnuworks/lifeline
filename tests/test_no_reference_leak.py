"""Reference maps (Copernicus EMS, UNOSAT) may only be used to check results.

1. No pipeline code may mention them: everything under src/lifeline except src/lifeline/validate,
   and everything under scripts except scripts/validate*.
2. The comparison may write only under runs/<id>/validation/.
"""

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("emergency.copernicus", "emsr", "unosat", "data/reference")
# Exact lines allowed to name the activation without touching its data. Each entry must
# be justified. The copilot guard lets the activation code pass as a harmless token in
# generated text (it is a name, not a number from the reference maps).
ALLOWED_LINES = {
    "src/lifeline/copilot/guard.py": (
        '"Sentinel-1", "EMSR927", the year 2026, a fact ID like [F012] or a list marker.',
        're.compile(r"\\bEMSR\\s?\\d+\\b", re.I),',
    ),
}
TEXT_EXT = {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".js", ".ts", ".html", ".cfg", ".ini", ".sh"}


def _scan(base: Path, skip):
    hits = []
    if not base.exists():
        return hits
    for p in base.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix.lower() not in TEXT_EXT:
            continue
        if skip(p):
            continue
        rel = p.relative_to(ROOT).as_posix()
        allowed = ALLOWED_LINES.get(rel, ())
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            low = line.lower()
            for word in FORBIDDEN:
                if word in low and line.strip() not in allowed:
                    hits.append(f"{rel}:{n}: {word}")
    return hits


def test_pipeline_code_never_mentions_reference_maps():
    validate_dir = ROOT / "src" / "lifeline" / "validate"
    hits = _scan(ROOT / "src" / "lifeline", lambda p: validate_dir in p.parents)
    hits += _scan(ROOT / "scripts", lambda p: p.relative_to(ROOT / "scripts").parts[0].startswith("validate"))
    assert not hits, "reference data mentioned outside the validation module:\n" + "\n".join(hits)


def _snapshot(root: Path):
    out = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            st = p.stat()
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


def test_compare_writes_only_to_validation(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "validate_demo_fixture", ROOT / "tests" / "fixtures" / "validate_demo" / "build_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run, ref = mod.build(tmp_path)
    before = _snapshot(tmp_path)

    from lifeline.validate.compare import compare_run
    compare_run(run, ref_dir=ref, n_boot=20)

    after = _snapshot(tmp_path)
    changed = [p for p in after if before.get(p) != after[p]]
    removed = [p for p in before if p not in after]
    assert changed, "compare_run wrote nothing"
    vdir = run / "validation"
    bad = [p for p in changed + removed if vdir not in p.parents]
    assert not bad, f"compare_run touched files outside {vdir}: {bad}"
