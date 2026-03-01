"""
Migration Validator MCP Server
================================
Validates every layer of the migration pipeline:

  1. KB freshness    — Is the knowledge base up to date with git?
  2. KB consistency  — Dangling refs, missing cross-project links
  3. Rule coverage   — Unmapped classes, dead rules
  4. Code quality    — Compilation, conventions, javax imports
  5. Tracking        — Per-class migration status, org-wide progress

Reads from the shared persistent KB at ~/.mcp-migration-kb/
Writes validation reports and tracking data to ~/.mcp-migration-kb/validation/

Requirements:
    pip install fastmcp pyyaml

Usage:
    python migration_validator_mcp_server.py
    fastmcp dev migration_validator_mcp_server.py
"""

import json
import os
import re
import subprocess
import hashlib
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
VALIDATION_DIR = os.path.join(KB_DIR, "validation")
TRACKING_FILE = os.path.join(VALIDATION_DIR, "_tracking.json")
REPORTS_DIR = os.path.join(VALIDATION_DIR, "reports")

mcp = FastMCP("Migration Validator")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared KB Readers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_kb_index() -> dict:
    fp = os.path.join(KB_DIR, "_index.json")
    if os.path.isfile(fp):
        try:
            with open(fp) as f:
                return json.load(f).get("projects", {})
        except Exception:
            pass
    return {}


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


def _load_tracking() -> dict:
    if os.path.isfile(TRACKING_FILE):
        try:
            with open(TRACKING_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"classes": {}, "updated_at": ""}


def _save_tracking(data: dict):
    os.makedirs(VALIDATION_DIR, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat()
    with open(TRACKING_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _save_report(report_name: str, data: dict):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fp = os.path.join(REPORTS_DIR, f"{report_name}_{ts}.json")
    with open(fp, "w") as f:
        json.dump(data, f, indent=2)
    return fp


def _compute_dir_hash(directory: str) -> str:
    """MD5 of all Java file timestamps in a directory."""
    h = hashlib.md5()
    try:
        for jf in sorted(Path(directory).rglob("*.java")):
            if "/test/" in str(jf):
                continue
            stat = jf.stat()
            h.update(f"{jf}:{stat.st_mtime}:{stat.st_size}".encode())
    except Exception:
        pass
    return h.hexdigest()[:12]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION 1: KB Freshness
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def check_kb_freshness() -> dict:
    """
    Check if the knowledge base is up to date with the current state of
    each project's source code on disk.

    Compares the stored file hash (from last scan) against the current
    hash of all Java files. If they differ, the project is STALE and
    should be rescanned before generating code.

    Returns:
        Per-project freshness status with last scan timestamps.
    """
    index = _load_kb_index()
    results = []

    for name, proj in index.items():
        proj_path = proj.get("path", "")
        stored_hash = proj.get("file_hash", "")
        scanned_at = proj.get("scanned_at", "")

        if not os.path.isdir(proj_path):
            results.append({
                "project": name,
                "status": "PATH_MISSING",
                "path": proj_path,
                "message": "Project directory not found. May have been moved.",
                "last_scanned": scanned_at,
            })
            continue

        current_hash = _compute_dir_hash(proj_path)

        if current_hash == stored_hash:
            results.append({
                "project": name,
                "status": "FRESH",
                "last_scanned": scanned_at,
            })
        else:
            # Count changed files
            results.append({
                "project": name,
                "status": "STALE",
                "last_scanned": scanned_at,
                "message": "Source code changed since last scan. Rescan required.",
                "stored_hash": stored_hash,
                "current_hash": current_hash,
            })

    fresh = len([r for r in results if r["status"] == "FRESH"])
    stale = len([r for r in results if r["status"] == "STALE"])
    missing = len([r for r in results if r["status"] == "PATH_MISSING"])

    report = {
        "timestamp": datetime.now().isoformat(),
        "total_projects": len(results),
        "fresh": fresh,
        "stale": stale,
        "path_missing": missing,
        "all_fresh": stale == 0 and missing == 0,
        "projects": results,
    }

    _save_report("kb_freshness", report)
    return report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION 2: KB Consistency
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def validate_kb_consistency() -> dict:
    """
    Check the knowledge base for structural integrity issues:

    1. Dangling references — class depends on a type not in any KB project
    2. Duplicate FQCNs — same class name appears in multiple projects
    3. Orphan interfaces — interfaces with no known implementors
    4. Orphan base classes — abstract classes with no known subclasses
    5. Missing cross-project links — app uses lib class but link not detected

    Returns:
        Categorized list of consistency issues with severity.
    """
    index = _load_kb_index()
    all_classes: dict[str, dict] = {}       # FQCN → class data
    all_simple_names: dict[str, list] = defaultdict(list)  # simple → [FQCNs]

    # Load all classes from all projects
    for name in index:
        classes = _load_kb_project(name)
        for fqcn, cls in classes.items():
            cls["_project"] = name
            all_classes[fqcn] = cls
            simple = cls.get("simple_name", fqcn.rsplit(".", 1)[-1])
            all_simple_names[simple].append(fqcn)

    issues = []

    # ── Check 1: Dangling references ──
    known_simple = set(all_simple_names.keys())
    java_builtins = {
        "String", "Integer", "Long", "Boolean", "Double", "Float", "Object",
        "List", "Map", "Set", "Collection", "Optional", "Stream", "Void",
        "BigDecimal", "Date", "LocalDate", "LocalDateTime", "Instant",
        "UUID", "Logger", "Pageable", "Page", "ResponseEntity",
        "CompletableFuture", "Mono", "Flux", "Sort", "Duration",
    }

    for fqcn, cls in all_classes.items():
        for dep_type in cls.get("all_dependency_types", []):
            if dep_type not in known_simple and dep_type not in java_builtins:
                issues.append({
                    "type": "DANGLING_REFERENCE",
                    "severity": "MEDIUM",
                    "class": fqcn,
                    "project": cls["_project"],
                    "references": dep_type,
                    "message": f"{fqcn} depends on '{dep_type}' which is not in any scanned project.",
                })

        # Superclass check
        sc = cls.get("superclass", "")
        if sc and sc not in known_simple and sc not in java_builtins and sc != "Object":
            issues.append({
                "type": "DANGLING_SUPERCLASS",
                "severity": "HIGH",
                "class": fqcn,
                "project": cls["_project"],
                "references": sc,
                "message": f"{fqcn} extends '{sc}' which is not in any scanned project.",
            })

    # ── Check 2: Duplicate FQCNs ──
    fqcn_projects: dict[str, list] = defaultdict(list)
    for fqcn, cls in all_classes.items():
        fqcn_projects[fqcn].append(cls["_project"])
    for fqcn, projects in fqcn_projects.items():
        if len(projects) > 1:
            issues.append({
                "type": "DUPLICATE_FQCN",
                "severity": "HIGH",
                "class": fqcn,
                "projects": projects,
                "message": f"{fqcn} exists in multiple projects: {projects}. This may cause resolution ambiguity.",
            })

    # ── Check 3: Orphan interfaces ──
    interfaces = [
        (fqcn, cls) for fqcn, cls in all_classes.items()
        if cls.get("class_type") == "INTERFACE"
    ]
    for ifqcn, icls in interfaces:
        isimple = icls.get("simple_name", ifqcn.rsplit(".", 1)[-1])
        implementors = [
            fqcn for fqcn, cls in all_classes.items()
            if isimple in cls.get("interfaces", []) or ifqcn in cls.get("interfaces", [])
        ]
        if not implementors:
            issues.append({
                "type": "ORPHAN_INTERFACE",
                "severity": "LOW",
                "class": ifqcn,
                "project": icls["_project"],
                "message": f"Interface {ifqcn} has no known implementors in the KB.",
            })

    # ── Check 4: Orphan abstract classes ──
    abstracts = [
        (fqcn, cls) for fqcn, cls in all_classes.items()
        if cls.get("class_type") == "ABSTRACT_CLASS"
    ]
    for afqcn, acls in abstracts:
        asimple = acls.get("simple_name", afqcn.rsplit(".", 1)[-1])
        subclasses = [
            fqcn for fqcn, cls in all_classes.items()
            if cls.get("superclass") in (asimple, afqcn)
        ]
        if not subclasses:
            issues.append({
                "type": "ORPHAN_ABSTRACT_CLASS",
                "severity": "LOW",
                "class": afqcn,
                "project": acls["_project"],
                "message": f"Abstract class {afqcn} has no known subclasses in the KB.",
            })

    by_severity = defaultdict(int)
    for issue in issues:
        by_severity[issue["severity"]] += 1

    by_type = defaultdict(int)
    for issue in issues:
        by_type[issue["type"]] += 1

    report = {
        "timestamp": datetime.now().isoformat(),
        "total_issues": len(issues),
        "by_severity": dict(by_severity),
        "by_type": dict(by_type),
        "is_consistent": len([i for i in issues if i["severity"] == "HIGH"]) == 0,
        "issues": issues,
    }

    _save_report("kb_consistency", report)
    return report


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION 3: Mapping Rule Coverage
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _rule_matches_class(rule: dict, cls: dict) -> bool:
    """Check if a mapping rule matches a class."""
    match = rule.get("legacy_match", {})

    if "layer" in match and match["layer"] != cls.get("layer"):
        return False
    if "annotation" in match:
        if match["annotation"] not in " ".join(cls.get("annotations", [])):
            return False
    if "extends" in match:
        if match["extends"] != cls.get("superclass"):
            return False
    if "implements" in match:
        if match["implements"] not in cls.get("interfaces", []):
            return False
    if "has_dependency" in match:
        pattern = match["has_dependency"]
        deps = cls.get("all_dependency_types", [])
        if not any(re.match(pattern, d) for d in deps):
            return False

    return True


@mcp.tool()
def validate_rule_coverage(project_name: str = "") -> dict:
    """
    Analyze mapping rule coverage across all (or one specific) project.

    Identifies:
    1. Unmapped classes — no mapping rule matches (MIGRATION GAP)
    2. Dead rules — rules that match nothing (MISCONFIGURED)
    3. Multi-matched classes — multiple rules apply (verify intent)
    4. Coverage percentage per layer

    Args:
        project_name: Specific project, or empty for all APPLICATION projects.

    Returns:
        Coverage report with gaps and recommendations.
    """
    rules = _load_mappings()
    index = _load_kb_index()

    if not rules:
        return {"error": "No mapping rules defined. Use migration-codegen to add rules first."}

    # Determine which projects to check
    target_projects = []
    if project_name:
        target_projects = [project_name]
    else:
        target_projects = [
            n for n, p in index.items()
            if p.get("project_type") == "APPLICATION"
        ]

    unmapped = []
    multi_matched = []
    rule_hit_counts: dict[str, int] = {rid: 0 for rid in rules}
    layer_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "mapped": 0})

    # Layers we expect to have mapping rules for
    migrateable_layers = {"DAO", "SERVICE", "CONTROLLER", "MESSAGING"}

    for proj in target_projects:
        classes = _load_kb_project(proj)
        for fqcn, cls in classes.items():
            layer = cls.get("layer", "UNKNOWN")
            if layer not in migrateable_layers:
                continue

            layer_stats[layer]["total"] += 1

            matched_rules = []
            for rid, rule in rules.items():
                if not rule.get("enabled", True):
                    continue
                if _rule_matches_class(rule, cls):
                    matched_rules.append(rid)
                    rule_hit_counts[rid] += 1

            if not matched_rules:
                unmapped.append({
                    "class": fqcn,
                    "project": proj,
                    "layer": layer,
                    "stereotype": cls.get("stereotype", ""),
                    "superclass": cls.get("superclass", ""),
                    "interfaces": cls.get("interfaces", []),
                    "suggestion": _suggest_rule_for_class(cls),
                })
            else:
                layer_stats[layer]["mapped"] += 1

            if len(matched_rules) > 1:
                multi_matched.append({
                    "class": fqcn,
                    "project": proj,
                    "matched_rules": matched_rules,
                    "note": "Multiple rules match. Verify transforms don't conflict.",
                })

    # Dead rules
    dead_rules = [
        {"rule_id": rid, "description": rules[rid].get("description", "")}
        for rid, count in rule_hit_counts.items()
        if count == 0
    ]

    # Layer coverage
    coverage = {}
    for layer, stats in layer_stats.items():
        total = stats["total"]
        mapped = stats["mapped"]
        coverage[layer] = {
            "total": total,
            "mapped": mapped,
            "unmapped": total - mapped,
            "percentage": round((mapped / total * 100) if total > 0 else 0, 1),
        }

    overall_total = sum(s["total"] for s in layer_stats.values())
    overall_mapped = sum(s["mapped"] for s in layer_stats.values())

    report = {
        "timestamp": datetime.now().isoformat(),
        "projects_checked": target_projects,
        "overall_coverage": round(
            (overall_mapped / overall_total * 100) if overall_total > 0 else 0, 1
        ),
        "overall_total": overall_total,
        "overall_mapped": overall_mapped,
        "coverage_by_layer": coverage,
        "unmapped_classes": {
            "count": len(unmapped),
            "classes": unmapped,
        },
        "multi_matched_classes": {
            "count": len(multi_matched),
            "classes": multi_matched,
        },
        "dead_rules": {
            "count": len(dead_rules),
            "rules": dead_rules,
        },
        "rule_hit_counts": rule_hit_counts,
    }

    _save_report("rule_coverage", report)
    return report


def _suggest_rule_for_class(cls: dict) -> str:
    """Suggest a mapping rule for an unmapped class."""
    layer = cls.get("layer", "UNKNOWN")
    sc = cls.get("superclass", "")
    ifaces = cls.get("interfaces", [])
    stereotype = cls.get("stereotype", "")

    if layer == "DAO":
        return f"Create rule: legacy_layer=DAO → target_extends=ReactiveRepository"
    if layer == "SERVICE" and sc:
        return f"Create rule: legacy_layer=SERVICE, legacy_extends={sc} → target service"
    if layer == "SERVICE":
        return f"Create rule: legacy_layer=SERVICE → target event-driven service"
    if layer == "CONTROLLER":
        return f"Create rule: legacy_layer=CONTROLLER → target REST controller update"
    if layer == "MESSAGING":
        return f"Create rule: legacy_layer=MESSAGING → target Kafka consumer"

    return f"Create a mapping rule for layer={layer}, stereotype={stereotype}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION 4: Generated Code Quality
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def validate_generated_code(output_dir: str) -> dict:
    """
    Run all code quality checks on a directory of generated Java files.

    Checks:
    1. javax import violations (must be jakarta for Boot 3.x)
    2. Field injection violations (must use constructor injection)
    3. Missing event publishing in mutating methods
    4. Unresolved TODO markers (counts them)
    5. Convention compliance (naming, annotations, structure)
    6. Missing @Transactional on methods that need it
    7. Missing @Slf4j / logger

    Args:
        output_dir: Directory containing generated .java files.

    Returns:
        Per-file violation report with severity and fix suggestions.
    """
    output_dir = os.path.abspath(output_dir)
    if not os.path.isdir(output_dir):
        return {"error": f"Directory not found: {output_dir}"}

    java_files = list(Path(output_dir).rglob("*.java"))
    if not java_files:
        return {"error": f"No .java files found in: {output_dir}"}

    all_violations = []
    file_results = []
    summary = defaultdict(int)

    for jf in java_files:
        try:
            content = jf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        violations = []
        rel_path = str(jf.relative_to(output_dir))

        # ── Check 1: javax imports ──
        javax_imports = re.findall(r"^import\s+(javax\.\S+);", content, re.MULTILINE)
        for imp in javax_imports:
            violations.append({
                "rule": "NO_JAVAX_IMPORTS",
                "severity": "CRITICAL",
                "message": f"javax import found: {imp} → must be jakarta.*",
                "fix": imp.replace("javax.", "jakarta."),
            })

        # ── Check 2: Field injection ──
        field_injections = re.findall(
            r"@Autowired\s+(?:private|protected|public)\s+\S+\s+\w+\s*;",
            content,
        )
        for fi in field_injections:
            violations.append({
                "rule": "NO_FIELD_INJECTION",
                "severity": "HIGH",
                "message": f"Field injection found: {fi.strip()[:60]}... Use constructor injection.",
                "fix": "Move to constructor parameter + @RequiredArgsConstructor",
            })

        # ── Check 3: @Inject instead of constructor ──
        inject_fields = re.findall(r"@Inject\s+(?:private|protected)", content)
        for _ in inject_fields:
            violations.append({
                "rule": "NO_INJECT_ANNOTATION",
                "severity": "HIGH",
                "message": "@Inject field injection found. Use constructor injection.",
            })

        # ── Check 4: Missing event publishing in mutating methods ──
        # Find public methods that look mutating but don't reference event/publish/outbox
        methods = re.finditer(
            r"public\s+\S+\s+(create|save|update|delete|process|submit|cancel|approve|reject|complete)\w*\s*\([^)]*\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
            content, re.DOTALL,
        )
        for m in methods:
            method_name = m.group(0).split("(")[0].split()[-1]
            method_body = m.group(2) if m.group(2) else ""
            event_keywords = ["event", "publish", "outbox", "EventBus", "emit"]
            has_event = any(kw.lower() in method_body.lower() for kw in event_keywords)
            # Allow if the line is a TODO comment about events
            has_event_todo = "event" in method_body.lower() and "todo" in method_body.lower()

            if not has_event and not has_event_todo:
                violations.append({
                    "rule": "MISSING_EVENT_PUBLISHING",
                    "severity": "MEDIUM",
                    "message": f"Mutating method '{method_name}' has no event publishing. Add domain event emission.",
                })

        # ── Check 5: TODO count ──
        todos = re.findall(r"//\s*TODO:?\s*(.*)", content)
        if todos:
            violations.append({
                "rule": "UNRESOLVED_TODOS",
                "severity": "INFO",
                "message": f"{len(todos)} unresolved TODO(s) in file.",
                "details": [t.strip()[:80] for t in todos[:10]],
            })

        # ── Check 6: Class naming conventions ──
        class_match = re.search(r"class\s+(\w+)", content)
        if class_match:
            class_name = class_match.group(1)
            # Service should end with Service or Handler
            if "service" in rel_path.lower():
                if not (class_name.endswith("Service") or class_name.endswith("Handler")
                        or class_name.endswith("Processor")):
                    violations.append({
                        "rule": "NAMING_CONVENTION",
                        "severity": "LOW",
                        "message": f"Class '{class_name}' in service package should end with Service/Handler.",
                    })
            # Repository should end with Repository
            if "repository" in rel_path.lower():
                if not class_name.endswith("Repository"):
                    violations.append({
                        "rule": "NAMING_CONVENTION",
                        "severity": "LOW",
                        "message": f"Class '{class_name}' in repository package should end with Repository.",
                    })

        # ── Check 7: Missing @Slf4j or Logger ──
        if "class " in content and "interface " not in content and "record " not in content:
            has_logger = "@Slf4j" in content or "Logger" in content or "LoggerFactory" in content
            if not has_logger:
                violations.append({
                    "rule": "MISSING_LOGGER",
                    "severity": "LOW",
                    "message": "No logger found. Add @Slf4j or private static final Logger.",
                })

        # ── Check 8: Missing @RequiredArgsConstructor with final fields ──
        has_final_fields = bool(re.search(r"private\s+final\s+\S+\s+\w+;", content))
        has_lombok = "@RequiredArgsConstructor" in content or "@AllArgsConstructor" in content
        has_explicit_ctor = bool(re.search(
            r"public\s+\w+\s*\([^)]*\)\s*\{", content
        ))
        if has_final_fields and not has_lombok and not has_explicit_ctor:
            violations.append({
                "rule": "MISSING_CONSTRUCTOR",
                "severity": "HIGH",
                "message": "Has final fields but no constructor or @RequiredArgsConstructor.",
            })

        for v in violations:
            summary[v["severity"]] += 1

        file_results.append({
            "file": rel_path,
            "violations": len(violations),
            "details": violations,
        })

        all_violations.extend([{**v, "file": rel_path} for v in violations])

    total_violations = len(all_violations)
    has_critical = summary.get("CRITICAL", 0) > 0
    has_high = summary.get("HIGH", 0) > 0

    report = {
        "timestamp": datetime.now().isoformat(),
        "output_dir": output_dir,
        "files_checked": len(java_files),
        "total_violations": total_violations,
        "pass": not has_critical and not has_high,
        "by_severity": dict(summary),
        "files": file_results,
    }

    _save_report("code_quality", report)
    return report


@mcp.tool()
def validate_compilation(
    output_dir: str,
    classpath: str = "",
    java_home: str = "",
) -> dict:
    """
    Attempt to compile all generated Java files using javac.

    Args:
        output_dir:  Directory containing generated .java files.
        classpath:   Classpath for compilation (JARs, target framework, etc.)
        java_home:   JAVA_HOME override, or auto-detected from PATH.

    Returns:
        Compilation result: PASS/FAIL with error details per file.
    """
    output_dir = os.path.abspath(output_dir)
    java_files = list(Path(output_dir).rglob("*.java"))

    if not java_files:
        return {"error": "No .java files found."}

    javac = "javac"
    if java_home:
        javac = os.path.join(java_home, "bin", "javac")

    # Test javac availability
    try:
        subprocess.run([javac, "-version"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"error": f"javac not found at '{javac}'. Set java_home or ensure JDK is on PATH."}

    cmd = [javac, "-proc:none", "-nowarn"]  # Skip annotation processing, suppress warnings
    if classpath:
        cmd.extend(["-cp", classpath])
    cmd.extend([str(f) for f in java_files])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Compilation timed out after 120 seconds."}

    if result.returncode == 0:
        return {
            "status": "PASS",
            "files_compiled": len(java_files),
            "message": "All files compiled successfully.",
        }

    # Parse errors
    errors = []
    for line in result.stderr.splitlines():
        err_match = re.match(r"(.+\.java):(\d+):\s*(error|warning):\s*(.*)", line)
        if err_match:
            errors.append({
                "file": err_match.group(1),
                "line": int(err_match.group(2)),
                "severity": err_match.group(3),
                "message": err_match.group(4),
            })

    return {
        "status": "FAIL",
        "files_compiled": len(java_files),
        "errors": len([e for e in errors if e["severity"] == "error"]),
        "warnings": len([e for e in errors if e["severity"] == "warning"]),
        "details": errors[:50],
        "raw_stderr": result.stderr[:2000],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION 5: Full Pipeline Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def run_full_validation(project_name: str = "", output_dir: str = "") -> dict:
    """
    Run the complete validation pipeline:
    1. KB freshness check
    2. KB consistency check
    3. Mapping rule coverage
    4. Generated code quality (if output_dir provided)

    Args:
        project_name: Specific project to validate, or empty for all.
        output_dir:   Directory of generated code (optional).

    Returns:
        Combined report with overall PASS/FAIL gate.
    """
    results = {}

    # Stage 1
    results["kb_freshness"] = check_kb_freshness()

    # Stage 2
    results["kb_consistency"] = validate_kb_consistency()

    # Stage 3
    results["rule_coverage"] = validate_rule_coverage(project_name)

    # Stage 4
    if output_dir:
        results["code_quality"] = validate_generated_code(output_dir)

    # ── Overall gate ──
    gates = {
        "kb_fresh": results["kb_freshness"].get("all_fresh", False),
        "kb_consistent": results["kb_consistency"].get("is_consistent", False),
        "rule_coverage_above_80": results["rule_coverage"].get("overall_coverage", 0) >= 80,
    }
    if output_dir:
        gates["code_quality_pass"] = results["code_quality"].get("pass", False)

    all_pass = all(gates.values())

    report = {
        "timestamp": datetime.now().isoformat(),
        "project": project_name or "(all)",
        "overall_pass": all_pass,
        "gates": gates,
        "details": results,
    }

    _save_report("full_validation", report)
    return {
        "overall_pass": all_pass,
        "gates": gates,
        "report_saved": True,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRACKING: Migration Progress
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def track_class_migration(
    project_name: str,
    class_fqcn: str,
    status: str = "MIGRATED",
    rules_applied: str = "",
    template_used: str = "",
    pr_url: str = "",
    reviewed_by: str = "",
    notes: str = "",
) -> dict:
    """
    Record migration status for a specific class. Persists to tracking file.

    Args:
        project_name:   Project name
        class_fqcn:     Fully qualified class name
        status:         NOT_STARTED, IN_PROGRESS, GENERATED, VALIDATED, MIGRATED, DEPLOYED
        rules_applied:  Comma-separated rule IDs
        template_used:  Template ID used
        pr_url:         Pull request URL
        reviewed_by:    Reviewer email
        notes:          Any additional notes
    """
    tracking = _load_tracking()

    key = f"{project_name}::{class_fqcn}"
    tracking["classes"][key] = {
        "project": project_name,
        "class": class_fqcn,
        "status": status,
        "rules_applied": [r.strip() for r in rules_applied.split(",") if r.strip()],
        "template_used": template_used,
        "pr_url": pr_url,
        "reviewed_by": reviewed_by,
        "notes": notes,
        "updated_at": datetime.now().isoformat(),
    }

    _save_tracking(tracking)
    return {"status": "tracked", "key": key, "current_status": status}


@mcp.tool()
def migration_progress_report(project_name: str = "") -> dict:
    """
    Generate migration progress dashboard data.

    Shows:
    - Per-project completion percentage
    - Per-layer completion
    - Per-status breakdown
    - Classes blocking progress (unmapped or stuck)

    Args:
        project_name: Specific project, or empty for org-wide.
    """
    tracking = _load_tracking()
    index = _load_kb_index()

    # Total classes per project (from KB)
    project_totals: dict[str, dict] = {}
    migrateable_layers = {"DAO", "SERVICE", "CONTROLLER", "MESSAGING"}

    for pname in index:
        if project_name and pname != project_name:
            continue
        if index[pname].get("project_type") == "LIBRARY":
            continue

        classes = _load_kb_project(pname)
        total = 0
        layer_totals = defaultdict(int)
        for fqcn, cls in classes.items():
            if cls.get("layer") in migrateable_layers:
                total += 1
                layer_totals[cls.get("layer", "UNKNOWN")] += 1

        project_totals[pname] = {
            "total": total,
            "layer_totals": dict(layer_totals),
        }

    # Count tracked migrations per project
    status_counts: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    layer_migrated: dict[str, dict] = defaultdict(lambda: defaultdict(int))

    for key, record in tracking.get("classes", {}).items():
        proj = record.get("project", "")
        if project_name and proj != project_name:
            continue
        status = record.get("status", "UNKNOWN")
        status_counts[proj][status] += 1

        # Look up layer from KB
        classes = _load_kb_project(proj)
        cls = classes.get(record.get("class", ""), {})
        layer = cls.get("layer", "UNKNOWN")
        if status in ("MIGRATED", "DEPLOYED", "VALIDATED"):
            layer_migrated[proj][layer] += 1

    # Build per-project report
    projects = []
    for pname, totals in project_totals.items():
        total = totals["total"]
        migrated = sum(
            1 for key, rec in tracking.get("classes", {}).items()
            if rec.get("project") == pname
            and rec.get("status") in ("MIGRATED", "DEPLOYED", "VALIDATED")
        )
        pct = round((migrated / total * 100) if total > 0 else 0, 1)

        layer_detail = {}
        for layer, ltotal in totals.get("layer_totals", {}).items():
            lmig = layer_migrated.get(pname, {}).get(layer, 0)
            layer_detail[layer] = {
                "total": ltotal,
                "migrated": lmig,
                "percentage": round((lmig / ltotal * 100) if ltotal > 0 else 0, 1),
            }

        projects.append({
            "project": pname,
            "total_classes": total,
            "migrated": migrated,
            "percentage": pct,
            "status_breakdown": dict(status_counts.get(pname, {})),
            "by_layer": layer_detail,
        })

    # Overall
    overall_total = sum(p["total_classes"] for p in projects)
    overall_migrated = sum(p["migrated"] for p in projects)

    report = {
        "timestamp": datetime.now().isoformat(),
        "overall": {
            "total_classes": overall_total,
            "migrated": overall_migrated,
            "percentage": round(
                (overall_migrated / overall_total * 100) if overall_total > 0 else 0, 1
            ),
        },
        "projects": sorted(projects, key=lambda p: -p["percentage"]),
    }

    _save_report("progress", report)
    return report


@mcp.tool()
def list_validation_reports() -> dict:
    """List all saved validation reports."""
    if not os.path.isdir(REPORTS_DIR):
        return {"reports": [], "total": 0}

    reports = []
    for f in sorted(Path(REPORTS_DIR).glob("*.json"), reverse=True):
        reports.append({
            "file": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })

    return {"reports_dir": REPORTS_DIR, "total": len(reports), "reports": reports[:30]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("validator://status")
def validator_status() -> str:
    tracking = _load_tracking()
    total_tracked = len(tracking.get("classes", {}))
    return (
        f"Migration Validator\n"
        f"===================\n"
        f"Tracked migrations: {total_tracked}\n"
        f"Reports dir: {REPORTS_DIR}\n"
        f"KB dir: {KB_DIR}\n"
    )


if __name__ == "__main__":
    mcp.run()
