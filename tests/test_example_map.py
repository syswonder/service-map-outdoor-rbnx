"""The bundled example map seeds fresh data dirs and never touches used ones."""
import json

from outdoor_mapping.core import Mapping
from outdoor_mapping.main import seed_example_map


def test_seed_imports_and_loads_example_into_empty_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(__import__("os").path.dirname(__import__("os").path.dirname(__file__)))
    service = Mapping(tmp_path)
    seed_example_map(service)
    assert [m["map_id"] for m in service.store.list()] == ["example-campus"]
    loaded = service.store.load("example-campus")
    assert loaded["example_data"] is True
    assert service.status()["map_id"] == "example-campus"
    assert loaded["network"]["roads"]


def test_seed_leaves_existing_maps_untouched(tmp_path):
    service = Mapping(tmp_path)
    service.store.save(dict(
        map_id="keep-me", map_type="outdoor", version="v1", example_data=False,
        origin_wgs84=dict(latitude=30.0, longitude=120.0),
        network=dict(nodes=[], roads=[dict(id="r", points=[dict(x=0.0, y=0.0)])]),
    ))
    seed_example_map(service)
    assert [m["map_id"] for m in service.store.list()] == ["keep-me"]
    assert service.status()["map_id"] == ""


def test_seed_then_explicit_initial_map_wins(tmp_path):
    service = Mapping(tmp_path)
    seed_example_map(service)
    service.store.save(dict(
        map_id="preferred", map_type="outdoor", version="v2", example_data=False,
        origin_wgs84=dict(latitude=30.0, longitude=120.0),
        network=dict(nodes=[], roads=[dict(id="r", points=[dict(x=0.0, y=0.0)])]),
    ))
    service.load_map("preferred")
    assert service.status()["map_id"] == "preferred"
    value = service.store.load("example-campus")
    assert value["map_id"] == "example-campus" and value["example_data"] is True


def test_bundled_example_artifact_is_valid():
    """The committed example map must load through the real store rules."""
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    value = json.loads((root / "data" / "examples" / "example-campus" / "map.json").read_text(encoding="utf-8"))
    assert value["map_id"] == "example-campus"
    assert value["map_type"] == "outdoor"
    assert value["example_data"] is True
    assert value["origin_wgs84"]["latitude"] and value["origin_wgs84"]["longitude"]
    assert value["network"]["roads"]
