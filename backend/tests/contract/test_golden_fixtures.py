import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "golden"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
EXPECTED_CASES = {
    "cold_start_founder",
    "returning_founder",
    "cross_signal_candidate",
    "duplicate_identity",
    "seeded_contradiction",
}


def _object(value: JsonValue, context: str) -> JsonObject:
    assert isinstance(value, dict), f"{context} must be an object"
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    assert isinstance(value, list), f"{context} must be an array"
    return value


def _string(value: JsonValue, context: str) -> str:
    assert isinstance(value, str) and value, f"{context} must be a non-empty string"
    return value


def _strings(value: JsonValue, context: str) -> list[str]:
    items = _array(value, context)
    assert all(isinstance(item, str) and item for item in items), (
        f"{context} must contain non-empty strings"
    )
    return cast(list[str], items)


def _read_object(path: Path) -> JsonObject:
    loaded = cast(JsonValue, json.loads(path.read_text(encoding="utf-8")))
    return _object(loaded, str(path))


def _walk_objects(value: JsonValue) -> Iterator[JsonObject]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_objects(nested)


def test_golden_manifest_covers_required_cases_and_categories() -> None:
    manifest = _read_object(MANIFEST_PATH)
    manifest_entries = [
        _object(value, "manifest.fixtures[]")
        for value in _array(manifest["fixtures"], "manifest.fixtures")
    ]
    fixture_files = {_string(entry["file"], "fixture file") for entry in manifest_entries}
    disk_files = {path.name for path in FIXTURE_DIR.glob("*.json")} - {MANIFEST_PATH.name}

    assert manifest["fixture_schema_version"] == "golden_candidate_manifest.v0.1.0"
    assert manifest["fictional"] is True
    assert manifest["candidate_count"] == len(manifest_entries) == 5
    assert fixture_files == disk_files
    assert len(_strings(manifest["early_signal_categories"], "early signal categories")) >= 3

    coverage = _object(manifest["required_case_coverage"], "required case coverage")
    assert coverage.keys() >= EXPECTED_CASES
    for case_name in EXPECTED_CASES:
        covered_files = _strings(coverage[case_name], f"coverage for {case_name}")
        assert covered_files
        assert set(covered_files) <= fixture_files


def test_each_golden_fixture_is_fictional_consistent_and_reference_complete() -> None:
    manifest = _read_object(MANIFEST_PATH)
    expected_as_of = _string(manifest["evaluation_as_of"], "manifest evaluation_as_of")
    manifest_entries = [
        _object(value, "manifest.fixtures[]")
        for value in _array(manifest["fixtures"], "manifest.fixtures")
    ]
    seen_fixture_ids: set[str] = set()
    all_cases: set[str] = set()
    all_categories: set[str] = set()

    for entry in manifest_entries:
        filename = _string(entry["file"], "fixture file")
        fixture = _read_object(FIXTURE_DIR / filename)
        fixture_id = _string(fixture["fixture_id"], f"{filename}.fixture_id")
        labels = _object(fixture["labels"], f"{filename}.labels")
        expected = _object(fixture["expected"], f"{filename}.expected")

        assert fixture["fixture_schema_version"] == "golden_candidate.v0.1.0"
        assert fixture["fictional"] is True
        assert fixture["evaluation_as_of"] == expected_as_of
        assert fixture_id == entry["fixture_id"]
        assert fixture_id not in seen_fixture_ids
        seen_fixture_ids.add(fixture_id)

        cases = set(_strings(labels["required_cases"], f"{filename} required cases"))
        categories = set(_strings(labels["early_signal_categories"], f"{filename} categories"))
        assert categories == set(
            _strings(entry["early_signal_categories"], f"{filename} manifest categories")
        )
        all_cases.update(cases)
        all_categories.update(categories)

        artifacts = [
            _object(value, f"{filename}.source_artifacts[]")
            for value in _array(fixture["source_artifacts"], f"{filename}.source_artifacts")
        ]
        observations = [
            _object(value, f"{filename}.observations[]")
            for value in _array(fixture["observations"], f"{filename}.observations")
        ]
        evidence = [
            _object(value, f"{filename}.evidence[]")
            for value in _array(fixture["evidence"], f"{filename}.evidence")
        ]
        artifact_ids = {
            _string(item["source_artifact_id"], f"{filename} artifact id") for item in artifacts
        }
        observation_ids = {
            _string(item["observation_id"], f"{filename} observation id") for item in observations
        }
        evidence_ids = {
            _string(item["evidence_id"], f"{filename} evidence id") for item in evidence
        }

        assert len(artifact_ids) == len(artifacts)
        assert len(observation_ids) == len(observations)
        assert len(evidence_ids) == len(evidence)
        for artifact in artifacts:
            locator = _object(artifact["locator"], f"{filename} artifact locator")
            assert ".example.invalid" in _string(locator["uri"], f"{filename} artifact URI")
        for observation in observations:
            assert observation["source_artifact_id"] in artifact_ids
        for evidence_item in evidence:
            assert evidence_item["source_artifact_id"] in artifact_ids
            assert evidence_item["observation_id"] in observation_ids

        for nested in _walk_objects(fixture):
            if "evidence_ids" in nested:
                referenced_ids = set(
                    _strings(nested["evidence_ids"], f"{filename} evidence references")
                )
                assert referenced_ids <= evidence_ids
            if "state" in nested:
                state = _string(nested["state"], f"{filename} knowledge state")
                assert state in {
                    "known",
                    "unknown",
                    "not_disclosed",
                    "not_applicable",
                    "conflicted",
                }
                if state == "known":
                    assert "value" in nested
                else:
                    _string(nested["reason"], f"{filename} {state} reason")
                if state == "conflicted":
                    assert len(_array(nested["alternatives"], "conflicted alternatives")) >= 2

        coverage = _object(expected["coverage"], f"{filename}.expected.coverage")
        preliminary = _object(
            expected["preliminary_assessment"], f"{filename}.expected.preliminary_assessment"
        )
        founder_score = _object(preliminary["founder_score"], f"{filename}.expected founder score")
        assert coverage["missing_history_quality_penalty"] == 0
        assert founder_score["missing_history_quality_penalty"] == 0
        assert founder_score["exact_numeric_score"] == "not_asserted"
        assert {
            "pre_fundraising_trigger",
            "reason_to_contact",
            "knowledge_values",
            "thesis",
            "coverage",
            "identity",
            "preliminary_assessment",
        } <= expected.keys()

    assert all_cases >= EXPECTED_CASES
    assert len(all_categories) >= 3
