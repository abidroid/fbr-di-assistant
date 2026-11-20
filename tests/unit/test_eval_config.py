"""Tests for evals/config.py: the four configs load and share collections correctly."""

from pathlib import Path

import pytest

from evals.config import available_configs, load_config

EXPECTED = ["baseline", "final", "structure_dense", "structure_hybrid"]


def test_the_four_plan_configs_exist_and_load() -> None:
    assert available_configs() == EXPECTED
    for name in EXPECTED:
        config = load_config(name)
        assert config.name == name
        assert config.metrics.k == 5


def test_structure_configs_share_one_collection_and_baseline_has_its_own() -> None:
    structure = {load_config(name).collection_name() for name in EXPECTED if name != "baseline"}
    assert len(structure) == 1
    baseline = load_config("baseline").collection_name()
    assert baseline not in structure
    assert baseline.startswith("eval_fixed_512_")


def test_final_config_turns_every_step_on() -> None:
    retrieval = load_config("final").retrieval.to_config()
    assert retrieval.mode == "hybrid"
    assert retrieval.rerank and retrieval.exact_boost and retrieval.authority_boost


def test_boosts_without_rerank_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "name: bad\ndescription: x\nretrieval:\n  mode: hybrid\n  rerank: false\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rerank"):
        load_config("bad", tmp_path)


def test_unknown_keys_and_wrong_names_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "typo.yaml").write_text(
        "name: typo\ndescription: x\nretrieval:\n  rerannk: true\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_config("typo", tmp_path)
    (tmp_path / "other.yaml").write_text("name: different\ndescription: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        load_config("other", tmp_path)


def test_missing_config_lists_the_available_ones() -> None:
    with pytest.raises(FileNotFoundError, match="final"):
        load_config("nope")
