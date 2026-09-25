#!/usr/bin/env python3
"""MCP server over the Vulkan docs index (docs.vulkan.org, all components,
including the Vulkan-Docs man pages via the refpages component).

A thin wrapper opening the (read-only) index built by build_index.py and
calling into store.py. Two ways to run it:

- Local/stdio (default) -- for a local MCP client config:

    {
      "mcpServers": {
        "vulkan-docs": {
          "command": "/path/to/venv/bin/python3",
          "args": ["/path/to/mcp-Vulkan/docs-index/mcp_server.py"]
        }
      }
    }

- Remote/HTTP -- set MCP_TRANSPORT=streamable-http (MCP_HOST/MCP_PORT
  override the 127.0.0.1:8000 default). Binds to localhost only and does
  no authentication of its own -- meant to sit behind a reverse proxy.
  Never bind this directly to a public interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

import gpuinfo_client
import store
from config import DB_PATH
from profile_builder import build_profile_from_reports as _build_profile_from_reports

# Candidate patterns for explain()'s auto-detection over arbitrary text
# (validation-layer stderr, a pasted struct dump, whatever). Each is
# deliberately broad -- false positives are cheap (an extra DB lookup that
# comes back empty/not-found and gets dropped), false negatives aren't.
_VUID_RE = re.compile(r"VUID-[A-Za-z0-9]+-[A-Za-z0-9]+-\d+")
_EXTENSION_RE = re.compile(r"\bVK_[A-Z][A-Z0-9]*(?:_[A-Za-z0-9]+)+\b")
_TYPE_RE = re.compile(r"\bVk[A-Z][A-Za-z0-9]*\b")
_OPCODE_RE = re.compile(r"\bOp[A-Z][A-Za-z0-9]*\b")

server = MCPServer(
    name="vulkan-docs",
    instructions=(
        "Query the full Vulkan documentation corpus built from "
        "docs.vulkan.org: guide, samples, GLSL, tutorial, and the "
        "Vulkan-Docs man pages (component='refpages'). Prefer search_docs "
        "for open-ended topic questions, then get_page for the full text "
        "of a specific result's rel_path. Cite each result's URL, where "
        "known, rather than the raw Markdown when pointing a user at "
        "documentation. For a validation-layer error, prefer lookup_vuid "
        "over search_docs -- it's an exact match, not a text search. For "
        "extension support questions use extension_info/list_extensions. "
        "For SPIR-V opcodes/capabilities use spirv_opcode/spirv_capability. "
        "Got a raw blob of Vulkan debugging output (validation-layer "
        "stderr, a captured struct dump, a shader log) rather than a "
        "specific known token? Call explain(text) first -- it auto-detects "
        "and resolves every VUID/extension/type/opcode in it in one call."
    ),
)


def _db_path() -> str:
    if not os.path.isfile(DB_PATH):
        raise RuntimeError(
            f"No index found at {DB_PATH}. Run build_index.py first, or fetch a "
            "published one (see docs-index/README.md)."
        )
    return DB_PATH


@server.tool()
def search_docs(query: str, limit: int = 20, components: list[str] | None = None) -> list[dict]:
    """Full-text search across every indexed page. `query` uses SQLite
    FTS5 syntax (plain words are ANDed; use OR/quotes/column filters like
    `title:atomics` as needed). Returns rel_path + source + url + a
    matching snippet for each hit -- call get_page for full content.

    For "how do I do X in modern Vulkan" / code-generation questions,
    pass components=["guide", "tutorial"] to scope to current usage
    guidance and walkthroughs rather than the normative but
    version-agnostic refpages or raw spec chapters -- e.g. searching
    "synchronization" unscoped surfaces the VkSubpassDependency refpage
    right alongside the Synchronization2 guide; scoping to guide/tutorial
    keeps the latter. Pair with modernize_check on any extension names the
    results mention, since older guide/tutorial pages can still reference
    an extension that's since been promoted into core.
    """
    with store.connect(_db_path()) as conn:
        return store.search_docs(conn, query, limit=limit, components=components)


@server.tool()
def get_page(rel_path: str) -> dict | None:
    """Full content of one page by its rel_path (as returned by
    search_docs or list_pages), e.g. "man/vkCreateInstance.md" or
    "site-docs/guide/latest/atomics.md".
    """
    with store.connect(_db_path()) as conn:
        return store.get_page(conn, rel_path)


@server.tool()
def list_pages(component: str | None = None, limit: int = 100) -> list[dict]:
    """Browse indexed pages by rel_path, optionally filtered to an Antora
    component (e.g. component='refpages' for man pages, or 'guide',
    'samples', 'glsl', 'tutorial'). Use search_docs instead for anything
    topic-shaped -- this is for browsing/discovery.
    """
    with store.connect(_db_path()) as conn:
        return store.list_pages(conn, component=component, limit=limit)


@server.tool()
def lookup_vuid(vuid: str) -> list[dict]:
    """Resolve a Valid Usage ID (VUID) from a validation-layer error
    message, e.g. "VUID-vkCmdDraw-magFilter-04553", straight to the spec
    text explaining it -- an exact-match lookup, not a text search.
    Returns one entry per refpage the VUID appears on (usually just one),
    each with the refpage's rel_path/url and the explanation text. Empty
    list means the VUID wasn't found in the indexed refpages -- double
    check it was copied in full including the trailing digits.
    """
    with store.connect(_db_path()) as conn:
        return store.lookup_vuid(conn, vuid)


@server.tool()
def extension_info(name: str) -> dict | None:
    """Metadata for one Vulkan extension by its full name, e.g.
    "VK_KHR_ray_tracing_pipeline": type (device/instance), dependencies,
    ratification status, and -- when present -- what it was promoted to or
    deprecated/obsoleted by, its interactions with other extensions, and
    its SPIR-V dependencies. Returns None if not found -- call
    list_extensions to browse/search by name instead.
    """
    with store.connect(_db_path()) as conn:
        return store.get_extension(conn, name)


@server.tool()
def list_extensions(vendor: str | None = None, promoted_only: bool = False, limit: int = 500) -> list[dict]:
    """Browse indexed Vulkan extensions. `vendor` filters by the vendor tag
    in the name (e.g. vendor='KHR', 'EXT', 'ARM', 'NV'). `promoted_only`
    filters to extensions that have a "Deprecation State" (promoted to
    another extension or a core version, or deprecated/obsoleted) --
    useful for checking whether an extension you're targeting has since
    been superseded. Call extension_info for full details on any result.
    """
    with store.connect(_db_path()) as conn:
        return store.list_extensions(conn, vendor=vendor, promoted_only=promoted_only, limit=limit)


@server.tool()
def modernize_check(names: list[str]) -> list[dict]:
    """Checks a list of Vulkan extension names against the current spec's
    own promotion/deprecation records -- useful before generating or
    reviewing Vulkan code, to catch extensions from a Vulkan 1.0-era
    tutorial that have since been promoted into a core version or
    superseded by a newer extension. For each name: status is "current"
    (still the right thing to use), "promoted"/"deprecated"/"obsoleted"
    (with superseded_by naming what to use instead, and
    promoted_to_core_version when the replacement chain resolves to a
    core version), "deprecated_no_replacement", or "unknown" (not found in
    the indexed extension list -- check the name, or it may not be a
    Vulkan extension). Not an opinion layer -- every result is read
    straight off the extension's own "Deprecation State" field.
    """
    with store.connect(_db_path()) as conn:
        return store.modernize_check(conn, names)


@server.tool()
def resolve_dependencies(name: str) -> dict:
    """Recursively resolves one Vulkan extension's full dependency tree --
    every extension and core version it transitively requires -- instead
    of an agent parsing each hop's raw dependency text by hand. Each node
    keeps its own raw "Extension and Version Dependencies" text
    (`raw_dependencies_text`) alongside the parsed links, since that text
    can express AND/OR relationships (e.g. "spirv_1_4 OR Vulkan 1.2, AND
    acceleration_structure") this doesn't resolve into boolean logic --
    read the raw text at each node to see which applies. A dependency of
    type "other" is a link that didn't parse as either an extension or a
    core version -- rare, but pass it through rather than silently drop
    it.
    """
    with store.connect(_db_path()) as conn:
        return store.resolve_dependencies(conn, name)


@server.tool()
def annotate_struct(type_name: str, values: dict) -> list[dict]:
    """Pairs a captured/decoded struct's field->value map with each
    field's spec description, e.g. type_name="VkBufferCreateInfo",
    values={"sharingMode": "VK_SHARING_MODE_CONCURRENT", "size": 65536} --
    useful for explaining a debugger's captured struct state without the
    caller re-calling get_field per key. A key with no indexed field
    description still comes back with description=None rather than being
    silently dropped.
    """
    with store.connect(_db_path()) as conn:
        return store.annotate_struct(conn, type_name, values)


@server.tool()
def get_field(type_name: str, field_name: str) -> dict | None:
    """Description of one struct member or enum value, e.g.
    type_name="VkBufferCreateInfo", field_name="sharingMode" -- or, for an
    enum, type_name="VkSharingMode", field_name="VK_SHARING_MODE_CONCURRENT".
    An exact-match lookup, not a text search -- useful when a debugger has
    a captured struct/enum value and needs to explain what a specific
    field or value means without pulling the whole refpage. Returns None
    if not found; call list_fields to browse a type's fields/values by
    name instead.
    """
    with store.connect(_db_path()) as conn:
        return store.get_field(conn, type_name, field_name)


@server.tool()
def list_fields(type_name: str) -> list[dict]:
    """All struct members or enum values indexed for one type (e.g.
    "VkImageCreateInfo" or "VkFormat"), in declaration order, each with its
    description. Empty list means the type wasn't found or has no
    struct/enum fields indexed (e.g. it's a command, handle, or function
    pointer type instead).
    """
    with store.connect(_db_path()) as conn:
        return store.list_fields(conn, type_name)


@server.tool()
def format_compatibility(format_name: str) -> dict | None:
    """One VkFormat's compatibility class, texel block size/extent, and
    every other format that shares its class (i.e. can alias it via
    VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT or size-compatibility) -- common
    questions when debugging a failed image copy/blit or a format-view
    creation. Parsed from the spec's own "Format Compatibility Classes"
    table. Does not cover which *usages* (sampled, color attachment, blit
    src/dst, etc.) a format supports -- that's implementation-dependent
    and only knowable via vkGetPhysicalDeviceFormatProperties at runtime,
    not a static spec fact this index can answer. Returns None if the
    format isn't found (VK_FORMAT_UNDEFINED has no class).
    """
    with store.connect(_db_path()) as conn:
        return store.format_compatibility(conn, format_name)


@server.tool()
def gpuinfo_report(report_id: int) -> dict:
    """Full capability report for one real, submitted device from
    vulkan.gpuinfo.org (https://vulkan.gpuinfo.org/displayreport.php?id=<report_id>
    to view it on the site): extensions, features, properties/limits,
    formats, and which published Vulkan Profiles it already satisfies.
    Proof-of-concept use of the public API -- see gpuinfo_client.py's
    module docstring for the usage constraints that come with that.
    """
    try:
        return gpuinfo_client.get_report(report_id)
    except gpuinfo_client.GpuinfoError as e:
        return {"error": str(e)}


@server.tool()
def gpuinfo_profile(report_id: int) -> dict:
    """Ready-made Vulkan Profile JSON for one gpuinfo.org report, as
    generated by the database itself. As of this tool being written, this
    upstream endpoint was returning HTTP 500 for every report_id tried --
    looks broken server-side. Prefer build_profile_from_reports, which
    computes an equivalent profile from gpuinfo_report() data instead,
    which does work; this tool is kept as a thin passthrough in case
    upstream gets fixed.
    """
    try:
        return gpuinfo_client.get_profile(report_id)
    except gpuinfo_client.GpuinfoError as e:
        return {"error": str(e)}


@server.tool()
def gpuinfo_driver_coverage(
    kind: str, value: str, extension: str | None = None, platform: str | None = None
) -> dict:
    """Real devices + first known driver version supporting a given
    Vulkan extension, feature, extension-feature pair, present mode, or
    subgroup stage/operation -- from vulkan.gpuinfo.org's submitted
    reports. `kind` is one of "extension", "feature", "feature2",
    "presentmode", "subgroup_stage", "subgroup_operation". `value` is the
    name to check (e.g. "VK_KHR_ray_tracing_pipeline" for kind="extension");
    for kind="feature2" pass the feature name as `value` and the owning
    extension's name as `extension`. `platform` optionally limits to
    "windows"/"linux"/"android". Useful for "is this extension actually
    supported in the field, and since when" questions that the spec itself
    can't answer -- pair with extension_info for what the extension does.
    """
    try:
        return gpuinfo_client.driver_coverage(kind, value, extension=extension, platform=platform)
    except gpuinfo_client.GpuinfoError as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": str(e)}


@server.tool()
def build_profile_from_reports(
    report_ids: list[int],
    name: str,
    api_version: str,
    label: str | None = None,
    description: str | None = None,
) -> dict:
    """Compiles a Vulkan Profile (Vulkan-Profiles JSON schema) from a set
    of real device reports on gpuinfo.org, rather than from authored
    opinion about what a profile should require: the result is the
    guaranteed intersection across the given devices -- extensions every
    one supports, boolean features true on every one, numeric limits as
    the elementwise minimum across all of them. `report_ids` are gpuinfo.org
    report IDs (view one at https://vulkan.gpuinfo.org/displayreport.php?id=<id>);
    picking which real devices define the target baseline is the caller's
    call, not this tool's -- it only computes what's actually common to
    the ones given. `name` becomes the profile's identifier (by
    convention `VP_<VENDOR>_<description>`, e.g. "VP_MYENGINE_2026_min");
    `api_version` is the Vulkan API version string the profile targets
    (e.g. "1.3.0").

    Every extension name in the result is cross-checked against this
    index's own extension data and annotated in "_extension_notes" with
    anything worth knowing before shipping the profile: an extension not
    found in the indexed refpages (possible typo or too new for this
    index's build), or one that's since been promoted/deprecated per
    modernize_check.

    The result's top-level "_notes" key lists every field that couldn't be
    merged (present on some but not all reports, or of inconsistent
    shape) and isn't part of the real profile schema -- strip both "_notes"
    and "_extension_notes" before treating the output as schema-valid.
    """
    reports = []
    for report_id in report_ids:
        try:
            reports.append(gpuinfo_client.get_report(report_id))
        except gpuinfo_client.GpuinfoError as e:
            return {"error": f"could not fetch report {report_id}: {e}"}

    profile = _build_profile_from_reports(reports, name=name, api_version=api_version, label=label, description=description)

    capability = next(iter(profile["capabilities"].values()))
    extension_notes = []
    with store.connect(_db_path()) as conn:
        for ext_name in capability["extensions"]:
            ext = store.get_extension(conn, ext_name)
            if ext is None:
                extension_notes.append(f"{ext_name}: not found in this index's indexed extensions")
            elif ext.get("superseded_status"):
                extension_notes.append(
                    f"{ext_name}: {ext['superseded_status']}"
                    + (f" (superseded by {ext['superseded_by']})" if ext.get("superseded_by") else "")
                )
    if extension_notes:
        profile["_extension_notes"] = extension_notes

    return profile


@server.tool()
def spirv_opcode(name_or_number: str) -> list[dict]:
    """Look up a SPIR-V instruction by opname (e.g.
    "OpImageSampleImplicitLod") or numeric opcode. Searches the core
    SPIR-V grammar plus the extended instruction sets most relevant to
    graphics/shader tooling: GLSL.std.450, OpenCL.std,
    NonSemantic.Shader.DebugInfo.100, and NonSemantic.DebugPrintf --
    check the `source` field of each result to see which. Returns each
    match's opcode, operand list (kind/name/quantifier), required
    capabilities/extensions, and minimum core version. Opcode numbers are
    only unique within a source, so a bare number can return several
    matches across different instruction sets.
    """
    with store.connect(_db_path()) as conn:
        results = store.spirv_opcode(conn, name_or_number)
    for r in results:
        r["operands"] = json.loads(r["operands"])
    return results


@server.tool()
def spirv_capability(name: str) -> dict | None:
    """Metadata for one SPIR-V capability by name (e.g. "RayQueryKHR"):
    its numeric value, minimum core version (if a core capability), the
    capabilities it implicitly depends on (`implies`), and the SPV_*
    extensions that enable it when it isn't core. Returns None if not
    found.
    """
    with store.connect(_db_path()) as conn:
        return store.spirv_capability(conn, name)


@server.tool()
def explain(text: str) -> dict:
    """Auto-detects and resolves every VUID, extension name, struct/enum
    type name, and SPIR-V opcode mentioned in an arbitrary blob of text --
    e.g. raw validation-layer stderr, a captured struct dump, a shader
    compile log -- without the caller needing to know which specific tool
    (lookup_vuid, extension_info, list_fields, spirv_opcode) applies to
    which token. Meant as the one entry point for "here's some Vulkan
    debugging output, explain it" rather than making the caller pre-parse
    the text themselves. Detection is pattern-based and over-eager by
    design (e.g. every VK_FOO_BAR-shaped token is checked against the
    extension list, not just ones that are actually extensions) -- a
    token that doesn't resolve to anything indexed is silently dropped
    rather than included as a false hit, so an empty section just means
    nothing of that kind was found, not that detection failed.

    Returns {"vuids": [...], "extensions": [...], "types": [{"type_name",
    "fields"}, ...], "spirv_opcodes": [...]}.
    """
    vuids = sorted(set(_VUID_RE.findall(text)))
    extension_candidates = sorted(set(_EXTENSION_RE.findall(text)))
    type_candidates = sorted(set(_TYPE_RE.findall(text)))
    opcode_candidates = sorted(set(_OPCODE_RE.findall(text)))

    with store.connect(_db_path()) as conn:
        vuid_results = [row for v in vuids for row in store.lookup_vuid(conn, v)]

        extension_results = []
        for name in extension_candidates:
            ext = store.get_extension(conn, name)
            if ext is not None:
                extension_results.append(ext)

        type_results = []
        for name in type_candidates:
            fields = store.list_fields(conn, name)
            if fields:
                type_results.append({"type_name": name, "fields": fields})

        opcode_results = [row for name in opcode_candidates for row in store.spirv_opcode(conn, name)]

    for row in opcode_results:
        row["operands"] = json.loads(row["operands"])

    return {
        "vuids": vuid_results,
        "extensions": extension_results,
        "types": type_results,
        "spirv_opcodes": opcode_results,
    }


@server.tool()
def index_status() -> dict:
    """When the index was last built and against which Vulkan-Site commit
    -- check this before trusting a result as current."""
    with store.connect(_db_path()) as conn:
        return {
            "built_at": store.get_meta(conn, "built_at"),
            "source_ref": store.get_meta(conn, "source_ref"),
            "spirv_grammar_version": store.get_meta(conn, "spirv_grammar_version"),
            "db_path": DB_PATH,
        }


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run()
    else:
        asyncio.run(
            server.run_streamable_http_async(
                host=os.environ.get("MCP_HOST", "127.0.0.1"),
                port=int(os.environ.get("MCP_PORT", "8000")),
            )
        )
