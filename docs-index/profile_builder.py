"""Builds a Vulkan Profile (the KhronosGroup/Vulkan-Profiles JSON schema,
https://schema.khronos.org/vulkan/profiles-0.8.1-204.json) from real device
capability reports fetched from vulkan.gpuinfo.org, instead of from
authored opinion about what a profile should require.

Given a set of report IDs (real, specific devices the caller has already
chosen as their target baseline), this computes the *guaranteed
intersection* across them: extensions every device supports, boolean
features true on every device, numeric limits as the elementwise minimum
across devices. That's a mechanical computation over real data -- this
module never decides which devices or extensions matter, only what's
actually common to the ones it's given.

Not a substitute for the database's own getprofile endpoint (which was
returning HTTP 500 when this was written -- see gpuinfo_client.py) -- once
that's fixed, or once direct DB access exists (see the memory note on
gpuinfo.org access), a per-report profile should probably come from there
directly rather than this file's approximation.
"""

from __future__ import annotations

from typing import Any

# properties/features blocks nested under core11/core12/core13 map to
# these Vulkan-Profiles structure names -- see any published profile's
# "features"/"properties" blocks, e.g. VP_KHR_roadmap_2022.json.
_CORE_BLOCKS = {
    "core11": ("VkPhysicalDeviceVulkan11Features", "VkPhysicalDeviceVulkan11Properties"),
    "core12": ("VkPhysicalDeviceVulkan12Features", "VkPhysicalDeviceVulkan12Properties"),
    "core13": ("VkPhysicalDeviceVulkan13Features", "VkPhysicalDeviceVulkan13Properties"),
}


def _is_boolish(v: Any) -> bool:
    return isinstance(v, bool) or (isinstance(v, int) and v in (0, 1))


def _merge_bool_field(values: list[Any]) -> bool | None:
    if not all(_is_boolish(v) for v in values):
        return None
    return all(bool(v) for v in values)


def _merge_numeric_field(values: list[Any]) -> Any:
    """Returns the elementwise minimum for numbers/hex-strings/fixed-length
    number arrays, or None if the values aren't uniformly one of those
    shapes (mismatched types, non-numeric strings, differing array
    lengths) -- callers skip and report a field this returns None for
    rather than guessing.
    """
    if all(isinstance(v, (int, float)) and not _is_boolish(v) for v in values):
        return min(values)
    if all(isinstance(v, str) and v.startswith("0x") for v in values):
        try:
            return min(int(v, 16) for v in values)
        except ValueError:
            return None
    if all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
        merged = []
        for i in range(len(values[0])):
            column = [v[i] for v in values]
            if all(isinstance(x, (int, float)) for x in column):
                merged.append(min(column))
            else:
                return None
        return merged
    return None


def _merge_feature_block(blocks: list[dict]) -> tuple[dict, list[str]]:
    keys = set().union(*(b.keys() for b in blocks)) if blocks else set()
    merged = {}
    skipped = []
    for key in sorted(keys):
        values = [b[key] for b in blocks if key in b]
        if len(values) != len(blocks):
            skipped.append(key)  # not every device reported this field -- skip rather than guess
            continue
        result = _merge_bool_field(values)
        if result is None:
            skipped.append(key)
        else:
            merged[key] = result
    return merged, skipped


def _merge_property_block(blocks: list[dict]) -> tuple[dict, list[str]]:
    keys = set().union(*(b.keys() for b in blocks)) if blocks else set()
    merged = {}
    skipped = []
    for key in sorted(keys):
        values = [b[key] for b in blocks if key in b]
        if len(values) != len(blocks):
            skipped.append(key)
            continue
        result = _merge_numeric_field(values)
        if result is None:
            skipped.append(key)
        else:
            merged[key] = result
    return merged, skipped


def build_profile_from_reports(
    reports: list[dict], name: str, api_version: str, label: str | None = None, description: str | None = None
) -> dict:
    """`reports` is a list of gpuinfo_client.get_report() results (already
    fetched by the caller, so this stays a pure function over data rather
    than doing I/O). Returns a dict matching the Vulkan-Profiles JSON
    schema's shape, plus a top-level "_notes" key (not part of the real
    schema -- strip it before treating the output as schema-valid) listing
    every field this couldn't merge and why, so nothing is silently
    dropped.
    """
    if not reports:
        raise ValueError("need at least one report to build a profile from")

    notes: list[str] = []

    ext_sets = [{e["extensionName"]: e["specVersion"] for e in r["extensions"]} for r in reports]
    common_ext_names = set(ext_sets[0])
    for s in ext_sets[1:]:
        common_ext_names &= set(s)
    extensions = {name: min(s[name] for s in ext_sets) for name in sorted(common_ext_names)}
    dropped_exts = sorted(set().union(*(set(s) for s in ext_sets)) - common_ext_names)
    if dropped_exts:
        notes.append(
            f"{len(dropped_exts)} extension(s) supported by only some of the given reports were excluded "
            f"(not a guaranteed baseline across all of them): {', '.join(dropped_exts[:10])}"
            + (", ..." if len(dropped_exts) > 10 else "")
        )

    features: dict[str, dict] = {}
    base_features, base_skipped = _merge_feature_block([r["features"] for r in reports])
    features["VkPhysicalDeviceFeatures"] = base_features
    if base_skipped:
        notes.append(
            f"VkPhysicalDeviceFeatures: skipped {len(base_skipped)} non-uniform field(s): "
            + ", ".join(base_skipped[:15]) + (", ..." if len(base_skipped) > 15 else "")
        )
    properties: dict[str, dict] = {}
    limits_merged, limits_skipped = _merge_property_block([r["properties"]["limits"] for r in reports])
    if limits_merged:
        properties["VkPhysicalDeviceProperties"] = {"limits": limits_merged}
    if limits_skipped:
        notes.append(f"VkPhysicalDeviceProperties.limits: skipped {len(limits_skipped)} non-uniform field(s): "
                      + ", ".join(limits_skipped[:15]) + (", ..." if len(limits_skipped) > 15 else ""))

    for core_key, (feature_struct, property_struct) in _CORE_BLOCKS.items():
        blocks = [r.get(core_key) for r in reports]
        if not all(blocks):
            notes.append(f"{core_key} not present on all reports -- skipped {feature_struct}/{property_struct}")
            continue
        core_features, core_feat_skipped = _merge_feature_block([b["features"] for b in blocks])
        features[feature_struct] = core_features
        if core_feat_skipped:
            notes.append(
                f"{feature_struct}: skipped {len(core_feat_skipped)} non-uniform field(s): "
                + ", ".join(core_feat_skipped[:15]) + (", ..." if len(core_feat_skipped) > 15 else "")
            )
        props_merged, props_skipped = _merge_property_block([b["properties"] for b in blocks])
        if props_merged:
            properties[property_struct] = props_merged
        if props_skipped:
            notes.append(
                f"{property_struct}: skipped {len(props_skipped)} non-uniform field(s): "
                + ", ".join(props_skipped[:15]) + (", ..." if len(props_skipped) > 15 else "")
            )

    capability_name = f"{name}_baseline"
    profile: dict[str, Any] = {
        "$schema": "https://schema.khronos.org/vulkan/profiles-0.8.1-204.json#",
        "capabilities": {
            capability_name: {
                "extensions": extensions,
                "features": features,
                "properties": properties,
            }
        },
        "profiles": {
            name: {
                "version": 1,
                "api-version": api_version,
                "label": label or name,
                "description": description or f"Guaranteed baseline across {len(reports)} gpuinfo.org report(s).",
                "capabilities": [capability_name],
            }
        },
    }
    if notes:
        profile["_notes"] = notes
    return profile
