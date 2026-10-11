"""bench/edge-days-faults-3p: every expected record re-derives from Brief A and replays on our bank (offline)."""
import importlib.util, pathlib

HERE = pathlib.Path(__file__).resolve().parent.parent / "bench" / "edge-days-faults-3p"


def _verify():
    spec = importlib.util.spec_from_file_location("edf_verify", HERE / "verify.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_brief_is_brief_a_unchanged():
    root = HERE.parent.parent
    assert (HERE / "brief.md").read_bytes() == (root / "briefs" / "brief-a.md").read_bytes()
    assert (HERE / "openapi.json").read_bytes() == (root / "briefs" / "openapi.json").read_bytes()


def test_expected_records_derive_format_and_replay():
    m = _verify()
    m.check_derived(); m.check_format(); m.replay()
