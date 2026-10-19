"""Versioned research candidates and an explicit, additive local configuration update."""
from importlib.resources import files
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values, set_key

from configuration.settings import LiveSettingsStore, TradingSettings, json_object


def candidate_catalog():
    catalog = json_object(files("configuration").joinpath("universe.json").read_text())
    combined = {}
    for group in catalog["categories"].values():
        if combined.keys() & group.keys():
            raise ValueError("Duplicate symbol across catalog categories")
        combined.update(group)
    TradingSettings(universe=combined)
    return catalog, combined


def merge_candidates(existing):
    catalog, candidates = candidate_catalog()
    normalized = dict(existing)
    for old, new in catalog.get("renamed_symbols", {}).items():
        if old in normalized:
            group = normalized.pop(old)
            normalized.setdefault(new, group)
    # Existing benchmark/group choices take precedence over the curated list.
    return TradingSettings(universe={**candidates, **normalized}).universe


def apply_candidates(env_path=Path('.env'), control_dir=None):
    values = dotenv_values(env_path, interpolate=False)
    original = json_object(values.get("TRADER_UNIVERSE") or "{}")
    merged = merge_candidates(original)
    store = LiveSettingsStore(control_dir) if control_dir is not None else None
    if store is not None and store.path.exists():
        current = store.read()
        updated = TradingSettings.model_validate({**current.settings.model_dump(),
                                                  "universe": merge_candidates(current.settings.universe)})
        store.save(updated, current.revision)
    # dotenv preserves unrelated keys without printing credentials.
    set_key(env_path, "TRADER_UNIVERSE", json.dumps(merged, separators=(",", ":")), quote_mode="always")
    return len(merged), len(set(merged) - set(original))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--show"]:
        print(json.dumps(candidate_catalog()[0], indent=2))
        return 0
    if args == ["--apply"]:
        state = Path(os.getenv("TRADER_STATE_DIR", "state"))
        control = Path(os.getenv("TRADER_CONTROL_DIR", str(state / "control")))
        total, added = apply_candidates(control_dir=control)
        print(f"Universe contains {total} candidates ({added} added); existing mappings and other settings preserved.")
        print("Recreate Compose services for root .env changes; saved runtime settings take precedence.")
        return 0
    print("Usage: python -m configuration.universe --show | --apply")
    return 0 if args == ["--help"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
