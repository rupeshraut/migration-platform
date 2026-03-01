"""
Golden Sample Runner MCP Server
==================================
The most critical accuracy mechanism in the migration platform.

For each mapping rule, maintains a hand-verified, architect-approved
reference migration (the "golden sample"). Every time a rule or template
changes, the runner regenerates code for each golden sample and compares
against the approved expected output.

If the diff is non-empty, the change is flagged as DRIFT and should
block CI until reviewed and either:
  a) The rule/template change is reverted, or
  b) The golden sample expected output is updated and re-approved

Storage:
  ~/.mcp-migration-kb/
  └── golden-samples/
      ├── _golden_index.json           ← Registry of all golden samples
      ├── dao-to-reactive-repo/
      │   ├── legacy/
      │   │   └── OrderDao.java        ← Real legacy class
      │   ├── expected/
      │   │   └── OrderRepository.java ← Architect-approved correct output
      │   └── metadata.json            ← Rule ID, approver, date, notes
      │
      ├── service-to-event-service/
      │   ├── legacy/
      │   │   └── OrderService.java
      │   ├── expected/
      │   │   └── OrderService.java
      │   └── metadata.json
      └── ...

Requirements:
    pip install fastmcp pyyaml jinja2 deepdiff

Usage:
    python golden_sample_runner.py
    fastmcp dev golden_sample_runner.py
"""

import difflib
import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KB_DIR = os.path.expanduser("~/.mcp-migration-kb")
GOLDEN_DIR = os.path.join(KB_DIR, "golden-samples")
GOLDEN_INDEX = os.path.join(GOLDEN_DIR, "_golden_index.json")
TEMPLATES_DIR = os.path.join(KB_DIR, "templates")

mcp = FastMCP("Golden Sample Runner")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class GoldenSample:
    """A golden sample: legacy input + approved expected output."""

    sample_id: str
    description: str = ""
    mapping_rule_id: str = ""        # Which mapping rule this tests
    template_id: str = ""            # Which template this tests
    legacy_project: str = ""         # KB project the legacy class belongs to
    legacy_class_fqcn: str = ""      # FQCN of the legacy class
    target_package: str = ""         # Target package for generation
    approved_by: str = ""            # Who approved the expected output
    approved_date: str = ""
    last_tested: str = ""
    last_result: str = ""            # PASS, FAIL, DRIFT
    notes: str = ""
    # Tolerance settings
    ignore_timestamps: bool = True   # Ignore generation dates in comparison
    ignore_todos: bool = False       # Ignore TODO markers
    ignore_whitespace: bool = True   # Ignore blank line differences


@dataclass
class TestResult:
    """Result of running a golden sample test."""

    sample_id: str
    status: str                      # PASS, FAIL, DRIFT, ERROR
    message: str = ""
    diff_lines: int = 0
    diff_summary: str = ""
    diff_detail: str = ""
    expected_file: str = ""
    actual_content: str = ""
    tested_at: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class GoldenRegistry:
    def __init__(self):
        self.samples: dict[str, GoldenSample] = {}
        self._ensure_dirs()
        self._load_index()

    def _ensure_dirs(self):
        os.makedirs(GOLDEN_DIR, exist_ok=True)

    def _load_index(self):
        if os.path.isfile(GOLDEN_INDEX):
            try:
                with open(GOLDEN_INDEX) as f:
                    data = json.load(f)
                for sid, sdata in data.get("samples", {}).items():
                    self.samples[sid] = GoldenSample(**sdata)
            except Exception:
                pass

    def _save_index(self):
        data = {
            "samples": {k: asdict(v) for k, v in self.samples.items()},
            "saved_at": datetime.now().isoformat(),
        }
        with open(GOLDEN_INDEX, "w") as f:
            json.dump(data, f, indent=2)

    def get_sample_dir(self, sample_id: str) -> str:
        return os.path.join(GOLDEN_DIR, sample_id)


golden = GoldenRegistry()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KB + Template readers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_kb_project(name: str) -> dict:
    fp = os.path.join(KB_DIR, f"{name}.json")
    if os.path.isfile(fp):
        try:
            with open(fp) as f:
                return json.load(f).get("classes", {})
        except Exception:
            pass
    return {}


def _load_mappings() -> dict:
    fp = os.path.join(KB_DIR, "_mappings.json")
    if os.path.isfile(fp):
        try:
            with open(fp) as f:
                return json.load(f).get("rules", {})
        except Exception:
            pass
    return {}


def _load_target_framework() -> dict:
    fp = os.path.join(KB_DIR, "_target_framework.json")
    if os.path.isfile(fp):
        try:
            with open(fp) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _render_with_template(template_id: str, context: dict) -> Optional[str]:
    """Render a template using Jinja2 (imports template engine logic)."""
    try:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        )
        # Register same custom filters as template engine
        env.filters["camel_case"] = lambda s: s[0].lower() + s[1:] if s else ""
        env.filters["pascal_case"] = lambda s: "".join(w.capitalize() for w in re.split(r"[_\-\s]+", s))
        env.filters["snake_case"] = lambda s: re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)).lower()
        env.filters["upper_snake"] = lambda s: env.filters["snake_case"](s).upper()
        env.filters["first_lower"] = lambda s: s[0].lower() + s[1:] if s else ""
        env.filters["first_upper"] = lambda s: s[0].upper() + s[1:] if s else ""
        env.filters["strip_suffix"] = lambda s, *sx: next((s[:-len(x)] for x in sx if s.endswith(x)), s)
        env.filters["to_event_name"] = _to_event_name
        env.filters["to_topic_name"] = lambda s: re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s.replace("Service", "").replace("Impl", ""))).lower().replace("_", "-") + ".events"
        env.filters["java_type"] = lambda s: s.rsplit(".", 1)[-1] if "." in s else s
        env.filters["is_mutating"] = lambda m: any(m.get("name", "").lower().startswith(p) for p in ["create", "save", "update", "delete", "process", "submit", "cancel", "approve", "reject", "complete", "assign", "remove", "add", "register"])
        env.filters["is_query"] = lambda m: any(m.get("name", "").lower().startswith(p) for p in ["find", "get", "search", "list", "count", "exists", "fetch", "load", "read", "query"])
        env.globals["now"] = datetime.now

        # Find template file
        tpl_index_file = os.path.join(TEMPLATES_DIR, "_template_index.json")
        tpl_file_name = None
        if os.path.isfile(tpl_index_file):
            with open(tpl_index_file) as f:
                tpl_index = json.load(f).get("templates", {})
            tpl_record = tpl_index.get(template_id, {})
            tpl_file_name = tpl_record.get("file_name")

        if not tpl_file_name:
            return None

        template = env.get_template(tpl_file_name)
        return template.render(**context)
    except Exception as e:
        return f"// TEMPLATE RENDER ERROR: {e}"


def _to_event_name(method_name: str, class_context: str = "") -> str:
    verbs = {
        "create": "Created", "save": "Saved", "update": "Updated",
        "delete": "Deleted", "process": "Processed", "submit": "Submitted",
        "cancel": "Cancelled", "approve": "Approved", "reject": "Rejected",
        "complete": "Completed", "assign": "Assigned", "remove": "Removed",
        "add": "Added", "register": "Registered",
    }
    for verb, past in verbs.items():
        if method_name.lower().startswith(verb):
            entity = method_name[len(verb):] or class_context
            return f"{entity}{past}Event"
    return f"{method_name}CompletedEvent"


def _build_render_context(legacy: dict, target_package: str, project_name: str) -> dict:
    """Build template context from legacy class data + mapping rules."""
    simple = legacy.get("simple_name", "Unknown")
    target_class = simple
    for suffix in ("Bean", "Impl", "EJB"):
        if target_class.endswith(suffix):
            target_class = target_class[:-len(suffix)]

    entity_name = target_class
    for suffix in ("Service", "Dao", "DAO", "Repository", "Handler", "Controller", "Listener"):
        if entity_name.endswith(suffix):
            entity_name = entity_name[:-len(suffix)]
            break

    mappings = _load_mappings()
    matched_rules = []
    for rid, rule in sorted(mappings.items(), key=lambda x: x[1].get("priority", 100)):
        if not rule.get("enabled", True):
            continue
        match = rule.get("legacy_match", {})
        ok = True
        if "layer" in match and match["layer"] != legacy.get("layer"):
            ok = False
        if "annotation" in match and match["annotation"] not in " ".join(legacy.get("annotations", [])):
            ok = False
        if "extends" in match and match["extends"] != legacy.get("superclass"):
            ok = False
        if "implements" in match and match["implements"] not in legacy.get("interfaces", []):
            ok = False
        if ok:
            matched_rules.append(rule)

    merged = {"extends": "", "implements": [], "annotations": ["@Service"],
              "inject": [], "imports": []}
    for rule in matched_rules:
        t = rule.get("target_transform", {})
        if t.get("extends"):
            merged["extends"] = t["extends"]
        if t.get("implements"):
            merged["implements"].extend(t["implements"])
        if t.get("annotation"):
            merged["annotations"] = [t["annotation"]]
        if t.get("inject"):
            merged["inject"].extend(t["inject"])
        if t.get("additional_imports"):
            merged["imports"].extend(t["additional_imports"])

    all_deps = []
    for dep in legacy.get("constructor_deps", []) + legacy.get("field_deps", []):
        dt = dep.get("type", "")
        if dt.endswith("Dao"):
            dt = dt.replace("Dao", "Repository")
        elif dt.endswith("DAO"):
            dt = dt.replace("DAO", "Repository")
        all_deps.append({"type": dt, "name": dep.get("name", "")})
    for inj in merged["inject"]:
        name = inj[0].lower() + inj[1:]
        if name not in [d["name"] for d in all_deps]:
            all_deps.append({"type": inj, "name": name})

    extends_clause = f" extends {merged['extends']}" if merged["extends"] else ""
    implements_clause = f" implements {', '.join(merged['implements'])}" if merged["implements"] else ""

    enriched_methods = []
    for m in legacy.get("public_methods", []):
        em = dict(m)
        n = m.get("name", "").lower()
        em["is_mutating"] = any(n.startswith(p) for p in ["create", "save", "update", "delete", "process", "submit", "cancel", "approve", "reject", "complete", "assign", "remove", "add", "register"])
        em["is_query"] = any(n.startswith(p) for p in ["find", "get", "search", "list", "count", "exists", "fetch"])
        enriched_methods.append(em)

    fwk = _load_target_framework()

    return {
        "legacy": {
            **legacy,
            "project": project_name,
            "entity_name": entity_name,
            "public_methods": enriched_methods,
            "migration_notes": legacy.get("migration_notes", []) + legacy.get("javax_imports", []),
        },
        "target": {
            "package": target_package,
            "class_name": target_class,
            "extends": merged["extends"],
            "extends_clause": extends_clause,
            "implements": merged["implements"],
            "implements_clause": implements_clause,
            "annotations": merged["annotations"],
            "all_deps": all_deps,
            "imports": sorted(set(merged["imports"])),
            "base_repository": merged["extends"] or "ReactiveMongoRepository",
            "framework_name": fwk.get("name", ""),
            "service_name": target_class,
            "consumed_events": [],
            "uses_mongodb": True,
            "uses_jpa": False,
            "kafka_topics": [{"name": entity_name.lower(), "value": f"{entity_name.lower()}.events"}],
        },
        "meta": {
            "date": "YYYY-MM-DD",  # Placeholder for deterministic comparison
            "rules_applied": [r.get("rule_id", rid) for r in matched_rules],
            "generator_version": "2.0.0",
        },
        "event": {
            "name": f"{entity_name}CreatedEvent",
            "source_method": "create",
            "fields": [{"type": "String", "name": f"{entity_name[0].lower()}{entity_name[1:]}Id"}],
        },
        "saga": {"steps": [], "compensations": []},
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Diff Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _normalize_for_comparison(
    content: str,
    ignore_timestamps: bool = True,
    ignore_todos: bool = False,
    ignore_whitespace: bool = True,
) -> list[str]:
    """Normalize generated code for deterministic comparison."""
    lines = content.splitlines()
    normalized = []

    for line in lines:
        # Normalize timestamps (generation dates)
        if ignore_timestamps:
            line = re.sub(r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD", line)
            line = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "YYYY-MM-DDTHH:MM:SS", line)
            line = re.sub(r"Generated:\s*\S+", "Generated: YYYY-MM-DD", line)

        # Skip TODO lines if configured
        if ignore_todos and re.match(r"\s*//\s*TODO", line):
            continue

        # Normalize whitespace
        if ignore_whitespace:
            # Collapse multiple blank lines into one
            stripped = line.rstrip()
            normalized.append(stripped)
        else:
            normalized.append(line)

    # Remove consecutive blank lines
    if ignore_whitespace:
        deduped = []
        prev_blank = False
        for line in normalized:
            is_blank = line.strip() == ""
            if is_blank and prev_blank:
                continue
            deduped.append(line)
            prev_blank = is_blank
        normalized = deduped

    return normalized


def _compute_diff(expected: str, actual: str, sample: GoldenSample) -> tuple[str, int, str]:
    """Compute a unified diff between expected and actual output."""
    expected_lines = _normalize_for_comparison(
        expected,
        ignore_timestamps=sample.ignore_timestamps,
        ignore_todos=sample.ignore_todos,
        ignore_whitespace=sample.ignore_whitespace,
    )
    actual_lines = _normalize_for_comparison(
        actual,
        ignore_timestamps=sample.ignore_timestamps,
        ignore_todos=sample.ignore_todos,
        ignore_whitespace=sample.ignore_whitespace,
    )

    diff = list(difflib.unified_diff(
        expected_lines, actual_lines,
        fromfile="expected", tofile="actual",
        lineterm="",
    ))

    diff_count = len([l for l in diff if l.startswith("+") or l.startswith("-")])
    # Subtract the file headers
    diff_count = max(0, diff_count - 2)

    diff_text = "\n".join(diff[:100])  # Limit diff output
    summary = f"{diff_count} lines differ" if diff_count > 0 else "No differences"

    return summary, diff_count, diff_text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def create_golden_sample(
    sample_id: str,
    mapping_rule_id: str,
    template_id: str,
    legacy_project: str,
    legacy_class_fqcn: str,
    expected_code: str,
    target_package: str = "",
    approved_by: str = "",
    description: str = "",
    notes: str = "",
) -> dict:
    """
    Create a new golden sample with architect-approved expected output.

    Args:
        sample_id:          Unique ID (e.g., "dao-to-reactive-repo-order")
        mapping_rule_id:    The mapping rule this sample validates
        template_id:        The template this sample validates
        legacy_project:     KB project name containing the legacy class
        legacy_class_fqcn:  FQCN of the legacy class to use as input
        expected_code:      The APPROVED correct migration output
        target_package:     Target package for generation
        approved_by:        Email of the architect who approved
        description:        What this sample tests
        notes:              Additional context

    Returns:
        Created sample metadata.
    """
    # Verify legacy class exists in KB
    classes = _load_kb_project(legacy_project)
    if legacy_class_fqcn not in classes:
        # Try simple name match
        match = [fqcn for fqcn in classes if classes[fqcn].get("simple_name") == legacy_class_fqcn]
        if match:
            legacy_class_fqcn = match[0]
        else:
            return {"error": f"Class '{legacy_class_fqcn}' not found in KB project '{legacy_project}'."}

    # Create sample directory
    sample_dir = golden.get_sample_dir(sample_id)
    os.makedirs(os.path.join(sample_dir, "legacy"), exist_ok=True)
    os.makedirs(os.path.join(sample_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(sample_dir, "actual"), exist_ok=True)

    # Write legacy class snapshot (from KB)
    legacy_data = classes[legacy_class_fqcn]
    with open(os.path.join(sample_dir, "legacy", "class_data.json"), "w") as f:
        json.dump(legacy_data, f, indent=2)

    # Write original source if available
    legacy_file = legacy_data.get("file_path", "")
    if legacy_file and os.path.isfile(legacy_file):
        simple = legacy_data.get("simple_name", "Unknown")
        shutil.copy2(legacy_file, os.path.join(sample_dir, "legacy", f"{simple}.java"))

    # Write expected output
    target_class = legacy_data.get("simple_name", "Unknown")
    for suffix in ("Bean", "Impl", "EJB"):
        if target_class.endswith(suffix):
            target_class = target_class[:-len(suffix)]

    expected_file = os.path.join(sample_dir, "expected", f"{target_class}.java")
    Path(expected_file).write_text(expected_code)

    # Write metadata
    sample = GoldenSample(
        sample_id=sample_id,
        description=description or f"Golden sample for rule '{mapping_rule_id}' + template '{template_id}'",
        mapping_rule_id=mapping_rule_id,
        template_id=template_id,
        legacy_project=legacy_project,
        legacy_class_fqcn=legacy_class_fqcn,
        target_package=target_package or legacy_data.get("package", "com.company"),
        approved_by=approved_by,
        approved_date=datetime.now().isoformat(),
        notes=notes,
    )

    metadata_file = os.path.join(sample_dir, "metadata.json")
    with open(metadata_file, "w") as f:
        json.dump(asdict(sample), f, indent=2)

    golden.samples[sample_id] = sample
    golden._save_index()

    return {
        "status": "created",
        "sample_id": sample_id,
        "sample_dir": sample_dir,
        "legacy_class": legacy_class_fqcn,
        "expected_file": expected_file,
        "approved_by": approved_by,
    }


@mcp.tool()
def create_golden_from_generation(
    sample_id: str,
    mapping_rule_id: str,
    template_id: str,
    legacy_project: str,
    legacy_class_fqcn: str,
    target_package: str = "",
    approved_by: str = "",
    description: str = "",
) -> dict:
    """
    Create a golden sample by generating code first, then saving it as the
    expected output. Use this when you want to review and approve the current
    generation output as the golden baseline.

    After creation, review the expected/ file and have the architect approve it.

    Args:
        sample_id:          Unique ID
        mapping_rule_id:    Mapping rule being tested
        template_id:        Template being tested
        legacy_project:     KB project
        legacy_class_fqcn:  Legacy class
        target_package:     Target package
        approved_by:        Approver email (can be set later)
        description:        Description
    """
    # Load legacy class
    classes = _load_kb_project(legacy_project)
    legacy = None
    for fqcn, cls in classes.items():
        if fqcn == legacy_class_fqcn or cls.get("simple_name") == legacy_class_fqcn:
            legacy = cls
            legacy["fqcn"] = fqcn
            legacy_class_fqcn = fqcn
            break

    if not legacy:
        return {"error": f"Class '{legacy_class_fqcn}' not found in '{legacy_project}'."}

    pkg = target_package or legacy.get("package", "com.company")
    ctx = _build_render_context(legacy, pkg, legacy_project)
    rendered = _render_with_template(template_id, ctx)

    if not rendered or rendered.startswith("// TEMPLATE RENDER ERROR"):
        return {"error": f"Template rendering failed: {rendered}"}

    # Create the golden sample with generated output as expected
    return create_golden_sample(
        sample_id=sample_id,
        mapping_rule_id=mapping_rule_id,
        template_id=template_id,
        legacy_project=legacy_project,
        legacy_class_fqcn=legacy_class_fqcn,
        expected_code=rendered,
        target_package=pkg,
        approved_by=approved_by,
        description=description,
        notes="Auto-generated baseline. REVIEW AND APPROVE before using as golden sample.",
    )


@mcp.tool()
def run_golden_sample(sample_id: str) -> dict:
    """
    Run a single golden sample test.

    1. Load the legacy class data from the golden sample
    2. Regenerate code using current rules + templates
    3. Compare against the approved expected output
    4. Return PASS, DRIFT, or ERROR

    Args:
        sample_id: Golden sample to test.

    Returns:
        Test result with diff if DRIFT detected.
    """
    if sample_id not in golden.samples:
        return {"error": f"Golden sample '{sample_id}' not found."}

    sample = golden.samples[sample_id]
    sample_dir = golden.get_sample_dir(sample_id)

    # Load legacy class data
    legacy_data_file = os.path.join(sample_dir, "legacy", "class_data.json")
    if not os.path.isfile(legacy_data_file):
        return {"error": f"Legacy data not found: {legacy_data_file}"}

    with open(legacy_data_file) as f:
        legacy = json.load(f)
    legacy["fqcn"] = sample.legacy_class_fqcn

    # Load expected output
    expected_dir = os.path.join(sample_dir, "expected")
    expected_files = list(Path(expected_dir).glob("*.java"))
    if not expected_files:
        return {"error": "No expected .java files found in golden sample."}

    expected_content = expected_files[0].read_text()

    # Generate code using current rules + template
    ctx = _build_render_context(legacy, sample.target_package, sample.legacy_project)
    actual_content = _render_with_template(sample.template_id, ctx)

    if not actual_content or actual_content.startswith("// TEMPLATE RENDER ERROR"):
        result = TestResult(
            sample_id=sample_id,
            status="ERROR",
            message=f"Template rendering failed: {actual_content}",
            tested_at=datetime.now().isoformat(),
        )
        sample.last_tested = result.tested_at
        sample.last_result = "ERROR"
        golden._save_index()
        return asdict(result)

    # Save actual output for inspection
    actual_dir = os.path.join(sample_dir, "actual")
    os.makedirs(actual_dir, exist_ok=True)
    actual_file = os.path.join(actual_dir, expected_files[0].name)
    Path(actual_file).write_text(actual_content)

    # Compare
    diff_summary, diff_count, diff_detail = _compute_diff(
        expected_content, actual_content, sample
    )

    if diff_count == 0:
        status = "PASS"
        message = "Generated output matches approved golden sample."
    else:
        status = "DRIFT"
        message = f"Generated output differs from golden sample: {diff_summary}"

    result = TestResult(
        sample_id=sample_id,
        status=status,
        message=message,
        diff_lines=diff_count,
        diff_summary=diff_summary,
        diff_detail=diff_detail,
        expected_file=str(expected_files[0]),
        actual_content=actual_content[:500] + "..." if len(actual_content) > 500 else actual_content,
        tested_at=datetime.now().isoformat(),
    )

    # Update sample record
    sample.last_tested = result.tested_at
    sample.last_result = status
    golden._save_index()

    return asdict(result)


@mcp.tool()
def run_all_golden_samples() -> dict:
    """
    Run ALL golden sample tests. This is the primary CI gate.

    Returns:
        Overall PASS/FAIL with per-sample results.
    """
    if not golden.samples:
        return {"error": "No golden samples defined. Use create_golden_sample first."}

    results = []
    pass_count = 0
    fail_count = 0
    drift_count = 0
    error_count = 0

    for sid in sorted(golden.samples.keys()):
        result = run_golden_sample(sid)
        status = result.get("status", "ERROR")

        if status == "PASS":
            pass_count += 1
        elif status == "DRIFT":
            drift_count += 1
        elif status == "ERROR":
            error_count += 1
        else:
            fail_count += 1

        results.append({
            "sample_id": sid,
            "rule": golden.samples[sid].mapping_rule_id,
            "template": golden.samples[sid].template_id,
            "status": status,
            "message": result.get("message", ""),
            "diff_lines": result.get("diff_lines", 0),
        })

    all_pass = drift_count == 0 and error_count == 0 and fail_count == 0

    report = {
        "timestamp": datetime.now().isoformat(),
        "overall_pass": all_pass,
        "total_samples": len(results),
        "passed": pass_count,
        "drift": drift_count,
        "errors": error_count,
        "results": results,
    }

    # Save report
    report_dir = os.path.join(KB_DIR, "validation", "reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(report_dir, f"golden_samples_{ts}.json"), "w") as f:
        json.dump(report, f, indent=2)

    return report


@mcp.tool()
def approve_drift(sample_id: str, approved_by: str) -> dict:
    """
    When a rule or template change causes expected drift, update the golden
    sample's expected output to match the new generation and re-approve.

    This replaces the expected/ file with the latest actual/ output.

    Args:
        sample_id:   Golden sample to update
        approved_by: Email of the architect approving the new baseline

    Returns:
        Updated sample with new approval date.
    """
    if sample_id not in golden.samples:
        return {"error": f"Golden sample '{sample_id}' not found."}

    sample = golden.samples[sample_id]
    sample_dir = golden.get_sample_dir(sample_id)

    actual_dir = os.path.join(sample_dir, "actual")
    expected_dir = os.path.join(sample_dir, "expected")

    actual_files = list(Path(actual_dir).glob("*.java"))
    if not actual_files:
        return {"error": "No actual output found. Run run_golden_sample first."}

    # Archive the old expected
    archive_dir = os.path.join(sample_dir, "archive", datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(archive_dir, exist_ok=True)
    for ef in Path(expected_dir).glob("*.java"):
        shutil.copy2(ef, archive_dir)

    # Copy actual → expected
    for af in actual_files:
        shutil.copy2(af, os.path.join(expected_dir, af.name))

    # Update metadata
    sample.approved_by = approved_by
    sample.approved_date = datetime.now().isoformat()
    sample.last_result = "APPROVED"
    sample.notes = f"Re-approved after drift. Previous expected archived at {archive_dir}"

    golden._save_index()

    # Update metadata.json in sample dir
    metadata_file = os.path.join(sample_dir, "metadata.json")
    with open(metadata_file, "w") as f:
        json.dump(asdict(sample), f, indent=2)

    return {
        "status": "approved",
        "sample_id": sample_id,
        "approved_by": approved_by,
        "previous_expected_archived": archive_dir,
    }


@mcp.tool()
def list_golden_samples() -> dict:
    """List all golden samples with their last test status."""
    samples = []
    for sid, s in sorted(golden.samples.items()):
        samples.append({
            "sample_id": sid,
            "description": s.description,
            "rule": s.mapping_rule_id,
            "template": s.template_id,
            "legacy_class": s.legacy_class_fqcn,
            "approved_by": s.approved_by,
            "approved_date": s.approved_date,
            "last_tested": s.last_tested,
            "last_result": s.last_result or "NOT_TESTED",
        })

    return {
        "total": len(samples),
        "golden_dir": GOLDEN_DIR,
        "samples": samples,
    }


@mcp.tool()
def get_golden_sample_detail(sample_id: str) -> dict:
    """
    Get full detail for a golden sample including expected code content.

    Args:
        sample_id: Golden sample to inspect.
    """
    if sample_id not in golden.samples:
        return {"error": f"Golden sample '{sample_id}' not found."}

    sample = golden.samples[sample_id]
    sample_dir = golden.get_sample_dir(sample_id)

    # Load expected
    expected_content = ""
    expected_dir = os.path.join(sample_dir, "expected")
    for ef in Path(expected_dir).glob("*.java"):
        expected_content = ef.read_text()
        break

    # Load actual if exists
    actual_content = ""
    actual_dir = os.path.join(sample_dir, "actual")
    for af in Path(actual_dir).glob("*.java"):
        actual_content = af.read_text()
        break

    return {
        "metadata": asdict(sample),
        "expected_code": expected_content,
        "actual_code": actual_content,
        "has_actual": bool(actual_content),
    }


@mcp.tool()
def delete_golden_sample(sample_id: str) -> dict:
    """Remove a golden sample."""
    if sample_id not in golden.samples:
        return {"error": f"Golden sample '{sample_id}' not found."}

    sample_dir = golden.get_sample_dir(sample_id)
    if os.path.isdir(sample_dir):
        shutil.rmtree(sample_dir)

    del golden.samples[sample_id]
    golden._save_index()

    return {"status": "deleted", "sample_id": sample_id}


@mcp.tool()
def golden_sample_coverage() -> dict:
    """
    Check which mapping rules and templates have golden samples,
    and which are uncovered (no golden sample exists).
    """
    rules = _load_mappings()

    covered_rules = set()
    covered_templates = set()
    for s in golden.samples.values():
        if s.mapping_rule_id:
            covered_rules.add(s.mapping_rule_id)
        if s.template_id:
            covered_templates.add(s.template_id)

    uncovered_rules = [
        {"rule_id": rid, "description": r.get("description", "")}
        for rid, r in rules.items()
        if rid not in covered_rules
    ]

    # Check template coverage
    tpl_index_file = os.path.join(TEMPLATES_DIR, "_template_index.json")
    all_templates = {}
    if os.path.isfile(tpl_index_file):
        with open(tpl_index_file) as f:
            all_templates = json.load(f).get("templates", {})

    uncovered_templates = [
        {"template_id": tid, "description": t.get("description", "")}
        for tid, t in all_templates.items()
        if tid not in covered_templates
    ]

    return {
        "total_rules": len(rules),
        "rules_with_golden_samples": len(covered_rules),
        "uncovered_rules": uncovered_rules,
        "total_templates": len(all_templates),
        "templates_with_golden_samples": len(covered_templates),
        "uncovered_templates": uncovered_templates,
        "rule_coverage_pct": round(
            (len(covered_rules) / len(rules) * 100) if rules else 0, 1
        ),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("golden://status")
def golden_status() -> str:
    total = len(golden.samples)
    passed = len([s for s in golden.samples.values() if s.last_result == "PASS"])
    drift = len([s for s in golden.samples.values() if s.last_result == "DRIFT"])
    untested = len([s for s in golden.samples.values() if not s.last_result])
    return (
        f"Golden Sample Runner\n"
        f"====================\n"
        f"Total samples: {total}\n"
        f"Last run: {passed} PASS, {drift} DRIFT, {untested} untested\n"
        f"Storage: {GOLDEN_DIR}\n"
    )


if __name__ == "__main__":
    mcp.run()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# THE COMPLETE 7-SERVER STACK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# .vscode/mcp.json:
# {
#   "servers": {
#     "migration-kb":        {"command": "python", "args": ["/path/to/migration_kb_mcp_server.py"]},
#     "migration-codegen":   {"command": "python", "args": ["/path/to/migration_codegen_mcp_server.py"]},
#     "migration-templates": {"command": "python", "args": ["/path/to/migration_template_engine.py"]},
#     "spring-scanner":      {"command": "python", "args": ["/path/to/springboot_scanner_mcp_server.py"]},
#     "jar-scanner":         {"command": "python", "args": ["/path/to/jar_scanner_mcp_server.py"]},
#     "migration-validator": {"command": "python", "args": ["/path/to/migration_validator_mcp_server.py"]},
#     "golden-samples":      {"command": "python", "args": ["/path/to/golden_sample_runner.py"]}
#   }
# }
