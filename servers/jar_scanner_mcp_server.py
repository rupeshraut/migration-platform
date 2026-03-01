"""
Legacy Java JAR Scanner MCP Server
===================================
Scans compiled Java JAR files to extract class metadata, dependency graphs,
and DAO-to-Service layer relationships for legacy application migration.

Approach: Uses `javap` (JDK bytecode disassembler) + ZIP inspection for
reliable .class file analysis without needing source code.

Requirements:
    pip install fastmcp
    Java JDK 17+ installed (for javap)

Usage:
    # Direct run
    python jar_scanner_mcp_server.py

    # FastMCP dev mode (interactive testing)
    fastmcp dev jar_scanner_mcp_server.py

    # Install in Claude Desktop / VS Code
    Add to mcp.json (see bottom of file)
"""

import json
import os
import re
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Server Definition
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp = FastMCP("Legacy JAR Scanner")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class ClassInfo:
    """Metadata extracted from a compiled .class file."""
    fqcn: str                                  # Fully qualified class name
    package: str = ""
    simple_name: str = ""
    jar_source: str = ""                       # Which JAR this class came from
    superclass: str = ""
    interfaces: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # Classes this references
    layer: str = "UNKNOWN"                     # DAO, SERVICE, CONTROLLER, ENTITY, etc.
    is_ejb: bool = False
    is_spring: bool = False


@dataclass
class DependencyEdge:
    """A dependency relationship between two classes."""
    source_class: str        # The class that depends on another
    target_class: str        # The class being depended upon
    source_jar: str = ""
    target_jar: str = ""
    relationship: str = ""   # FIELD_INJECTION, CONSTRUCTOR_PARAM, METHOD_CALL, EXTENDS, IMPLEMENTS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# In-Memory Registry (persists across tool calls within session)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class JarRegistry:
    """Session-scoped registry of scanned JARs and discovered classes."""

    def __init__(self):
        self.scanned_jars: dict[str, list[str]] = {}      # jar_path → [class FQCNs]
        self.class_index: dict[str, ClassInfo] = {}        # FQCN → ClassInfo
        self.dependency_edges: list[DependencyEdge] = []

    def reset(self):
        self.scanned_jars.clear()
        self.class_index.clear()
        self.dependency_edges.clear()


registry = JarRegistry()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core: Bytecode Analysis via javap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _check_javap() -> bool:
    """Verify javap is available on PATH."""
    try:
        subprocess.run(["javap", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_javap(class_name: str, classpath: str, verbose: bool = False) -> str:
    """
    Run javap to disassemble a .class file.

    Args:
        class_name: Fully qualified class name (e.g., com.company.dao.OrderDao)
        classpath:  Path to JAR or directory containing .class files
        verbose:    If True, use -verbose flag (shows constant pool, dependencies)

    Returns:
        javap output as string
    """
    cmd = ["javap", "-p"]  # -p shows all members including private
    if verbose:
        cmd.append("-verbose")
    cmd.extend(["-cp", classpath, class_name])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        return result.stdout if result.returncode == 0 else result.stderr
    except subprocess.TimeoutExpired:
        return f"ERROR: javap timed out for {class_name}"


def _list_classes_in_jar(jar_path: str) -> list[str]:
    """List all .class FQCNs in a JAR file (excludes inner classes by default)."""
    classes = []
    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            for entry in zf.namelist():
                if entry.endswith(".class") and not entry.startswith("META-INF"):
                    # Convert path to FQCN: com/company/Foo.class → com.company.Foo
                    fqcn = entry.replace("/", ".").removesuffix(".class")
                    classes.append(fqcn)
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        return [f"ERROR: {e}"]
    return classes


def _parse_javap_output(javap_output: str, jar_path: str) -> Optional[ClassInfo]:
    """
    Parse javap output into structured ClassInfo.

    Handles:
      - Class declaration (extends, implements)
      - Annotations (@Stateless, @Service, @Entity, etc.)
      - Field declarations (identifies injected dependencies)
      - Method signatures
    """
    if not javap_output or javap_output.startswith("ERROR"):
        return None

    info = ClassInfo(fqcn="", jar_source=jar_path)
    lines = javap_output.strip().splitlines()

    # ── Parse class declaration ──
    for line in lines:
        line = line.strip()

        # Class declaration: public class com.company.OrderDao extends BaseDao implements Serializable
        class_match = re.match(
            r"(?:public\s+|protected\s+|private\s+)?"
            r"(?:abstract\s+|final\s+)?"
            r"(?:class|interface|enum)\s+"
            r"([\w.]+)"
            r"(?:\s+extends\s+([\w.]+))?"
            r"(?:\s+implements\s+(.+))?\s*\{?",
            line,
        )
        if class_match:
            info.fqcn = class_match.group(1)
            info.simple_name = info.fqcn.rsplit(".", 1)[-1]
            info.package = info.fqcn.rsplit(".", 1)[0] if "." in info.fqcn else ""
            if class_match.group(2):
                info.superclass = class_match.group(2).strip()
                info.dependencies.append(info.superclass)
            if class_match.group(3):
                info.interfaces = [
                    iface.strip() for iface in class_match.group(3).split(",")
                ]
                info.dependencies.extend(info.interfaces)
            continue

        # Annotations
        ann_match = re.match(r".*(@\w+(?:\([^)]*\))?)", line)
        if ann_match and not line.strip().startswith("//"):
            info.annotations.append(ann_match.group(1))

        # Fields: private OrderDao orderDao;
        field_match = re.match(
            r"\s*(?:public|protected|private)\s+"
            r"(?:static\s+)?(?:final\s+)?"
            r"([\w.<>,\[\]\s]+?)\s+(\w+)\s*;",
            line,
        )
        if field_match:
            field_type = field_match.group(1).strip()
            field_name = field_match.group(2).strip()
            info.fields.append({"type": field_type, "name": field_name})
            # Track as dependency if it looks like a class reference
            if field_type[0].isupper() and field_type not in (
                "String", "Integer", "Long", "Boolean", "Double", "Float",
                "Object", "List", "Map", "Set", "Collection", "Optional",
            ):
                info.dependencies.append(field_type)

        # Methods
        method_match = re.match(
            r"\s*(?:public|protected|private)\s+"
            r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
            r"([\w.<>,\[\]\s]+?)\s+(\w+)\s*\(([^)]*)\)",
            line,
        )
        if method_match:
            return_type = method_match.group(1).strip()
            method_name = method_match.group(2).strip()
            params = method_match.group(3).strip()
            info.methods.append({
                "name": method_name,
                "return_type": return_type,
                "parameters": params,
            })

    # ── Classify layer ──
    info.layer = _classify_layer(info)
    info.is_ejb = any(
        a.startswith(("@Stateless", "@Stateful", "@Singleton", "@MessageDriven"))
        for a in info.annotations
    )
    info.is_spring = any(
        a.startswith(("@Service", "@Component", "@Repository", "@Controller", "@RestController"))
        for a in info.annotations
    )

    # Deduplicate dependencies
    info.dependencies = list(set(info.dependencies))

    return info


def _classify_layer(info: ClassInfo) -> str:
    """
    Classify a class into an architectural layer based on naming conventions,
    annotations, interfaces, and superclass patterns.
    """
    name_lower = info.fqcn.lower()
    all_annotations = " ".join(info.annotations).lower()

    # ── Annotation-based (most reliable) ──
    if any(a in all_annotations for a in ["@entity", "@table", "@document"]):
        return "ENTITY"
    if any(a in all_annotations for a in ["@repository", "@dao"]):
        return "DAO"
    if any(a in all_annotations for a in ["@service", "@stateless", "@stateful"]):
        return "SERVICE"
    if any(a in all_annotations for a in [
        "@controller", "@restcontroller", "@webservlet", "@path"
    ]):
        return "CONTROLLER"
    if any(a in all_annotations for a in ["@messagedriven", "@jmslistener", "@kafkalistener"]):
        return "MESSAGING"
    if any(a in all_annotations for a in ["@configuration", "@component"]):
        return "CONFIG"

    # ── Interface / superclass-based ──
    all_ifaces = " ".join(info.interfaces).lower()
    if "messagelistener" in all_ifaces or "messagedriven" in all_ifaces:
        return "MESSAGING"
    if "httpservlet" in info.superclass.lower():
        return "CONTROLLER"
    if "crudrepository" in all_ifaces or "jparepository" in all_ifaces:
        return "DAO"

    # ── Package / naming convention-based (fallback) ──
    if any(seg in name_lower for seg in [".dao.", ".repository.", "daoimpl", "daobean"]):
        return "DAO"
    if any(seg in name_lower for seg in [".service.", "serviceimpl", "servicebean"]):
        return "SERVICE"
    if any(seg in name_lower for seg in [
        ".controller.", ".action.", ".servlet.", ".resource.", ".rest.", ".web.", ".api."
    ]):
        return "CONTROLLER"
    if any(seg in name_lower for seg in [".entity.", ".model.", ".domain.", ".dto."]):
        return "ENTITY"
    if any(seg in name_lower for seg in [".listener.", ".consumer.", ".handler.", ".mdb."]):
        return "MESSAGING"
    if any(seg in name_lower for seg in [".config.", ".util.", ".helper.", ".common."]):
        return "INFRASTRUCTURE"

    return "UNKNOWN"


def _extract_verbose_dependencies(javap_verbose_output: str) -> list[str]:
    """
    Extract class references from javap -verbose output (constant pool).
    This catches ALL dependencies including method calls, not just field types.
    """
    deps = set()
    for line in javap_verbose_output.splitlines():
        # Constant pool class references: #23 = Class  #145  // com/company/dao/OrderDao
        class_ref = re.search(r"//\s+([\w/]+)\s*$", line)
        if class_ref:
            ref = class_ref.group(1).replace("/", ".")
            # Filter out JDK internals and primitives
            if not ref.startswith(("java.", "javax.", "sun.", "jdk.", "[", "org.w3c")):
                deps.add(ref)
    return sorted(deps)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def scan_jar(jar_path: str, deep_scan: bool = False) -> dict:
    """
    Scan a Java JAR file and extract all class metadata.

    Args:
        jar_path:   Absolute path to the JAR file.
        deep_scan:  If True, uses javap -verbose for full dependency extraction
                    from the constant pool (slower but catches method-level deps).

    Returns:
        Summary with class count, layer breakdown, and scan status.
    """
    jar_path = os.path.abspath(jar_path)
    if not os.path.isfile(jar_path):
        return {"error": f"JAR not found: {jar_path}"}

    if not _check_javap():
        return {"error": "javap not found. Install JDK 17+ and ensure it's on PATH."}

    class_names = _list_classes_in_jar(jar_path)
    if not class_names or (class_names and class_names[0].startswith("ERROR")):
        return {"error": f"Failed to read JAR: {class_names}"}

    scanned = []
    layer_counts = defaultdict(int)

    for fqcn in class_names:
        # Skip inner classes for initial scan (reduce noise)
        if "$" in fqcn:
            continue

        javap_output = _run_javap(fqcn, jar_path)
        class_info = _parse_javap_output(javap_output, jar_path)

        if class_info and class_info.fqcn:
            # Deep scan: get verbose output for full dependency extraction
            if deep_scan:
                verbose_output = _run_javap(fqcn, jar_path, verbose=True)
                extra_deps = _extract_verbose_dependencies(verbose_output)
                class_info.dependencies = list(
                    set(class_info.dependencies + extra_deps)
                )

            registry.class_index[class_info.fqcn] = class_info
            scanned.append(class_info.fqcn)
            layer_counts[class_info.layer] += 1

    registry.scanned_jars[jar_path] = scanned

    return {
        "jar": jar_path,
        "total_classes": len(class_names),
        "scanned_classes": len(scanned),
        "inner_classes_skipped": len(class_names) - len(scanned),
        "layer_breakdown": dict(layer_counts),
        "deep_scan": deep_scan,
        "status": "SUCCESS",
    }


@mcp.tool()
def scan_jar_directory(directory: str, deep_scan: bool = False) -> dict:
    """
    Scan all JAR files in a directory (e.g., WEB-INF/lib).

    Args:
        directory:  Path to directory containing JAR files.
        deep_scan:  If True, uses javap -verbose for each class.

    Returns:
        Summary of all scanned JARs with totals.
    """
    directory = os.path.abspath(directory)
    if not os.path.isdir(directory):
        return {"error": f"Directory not found: {directory}"}

    jar_files = sorted(Path(directory).rglob("*.jar"))
    if not jar_files:
        return {"error": f"No JAR files found in: {directory}"}

    results = []
    total_classes = 0

    for jar in jar_files:
        result = scan_jar(str(jar), deep_scan=deep_scan)
        results.append({
            "jar": str(jar.name),
            "classes": result.get("scanned_classes", 0),
            "layers": result.get("layer_breakdown", {}),
        })
        total_classes += result.get("scanned_classes", 0)

    return {
        "directory": directory,
        "jars_scanned": len(results),
        "total_classes": total_classes,
        "jars": results,
    }


@mcp.tool()
def get_class_info(class_name: str) -> dict:
    """
    Get detailed metadata for a specific scanned class.

    Args:
        class_name: Fully qualified class name or simple name (partial match).

    Returns:
        Full ClassInfo including fields, methods, annotations, dependencies.
    """
    # Exact match first
    if class_name in registry.class_index:
        return asdict(registry.class_index[class_name])

    # Partial / simple name match
    matches = [
        fqcn for fqcn in registry.class_index
        if class_name in fqcn or fqcn.endswith(f".{class_name}")
    ]

    if len(matches) == 1:
        return asdict(registry.class_index[matches[0]])
    elif len(matches) > 1:
        return {
            "message": f"Multiple matches for '{class_name}'. Be more specific.",
            "matches": matches,
        }
    else:
        return {"error": f"Class '{class_name}' not found. Run scan_jar first."}


@mcp.tool()
def find_dao_service_relationships() -> dict:
    """
    Discover all DAO-to-Service layer relationships across scanned JARs.

    Analyzes field injection, constructor parameters, and interface
    references to build a dependency map showing which Services
    depend on which DAOs.

    Returns:
        List of relationships with source service, target DAO,
        injection type, and cross-JAR flag.
    """
    relationships = []
    dao_classes = {
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "DAO"
    }
    dao_simple_names = {
        info.simple_name: fqcn
        for fqcn, info in registry.class_index.items()
        if info.layer == "DAO"
    }

    for fqcn, info in registry.class_index.items():
        if info.layer != "SERVICE":
            continue

        # Check fields for DAO references
        for fld in info.fields:
            field_type = fld["type"]
            resolved_dao = None

            # Match by FQCN
            if field_type in dao_classes:
                resolved_dao = field_type
            # Match by simple name
            elif field_type in dao_simple_names:
                resolved_dao = dao_simple_names[field_type]

            if resolved_dao:
                dao_info = registry.class_index[resolved_dao]
                edge = DependencyEdge(
                    source_class=fqcn,
                    target_class=resolved_dao,
                    source_jar=info.jar_source,
                    target_jar=dao_info.jar_source,
                    relationship="FIELD_INJECTION",
                )
                relationships.append(asdict(edge))
                registry.dependency_edges.append(edge)

        # Check dependencies list (from javap verbose)
        for dep in info.dependencies:
            if dep in dao_classes and dep not in [
                r["target_class"] for r in relationships if r["source_class"] == fqcn
            ]:
                dao_info = registry.class_index[dep]
                edge = DependencyEdge(
                    source_class=fqcn,
                    target_class=dep,
                    source_jar=info.jar_source,
                    target_jar=dao_info.jar_source,
                    relationship="DEPENDENCY_REFERENCE",
                )
                relationships.append(asdict(edge))
                registry.dependency_edges.append(edge)

    # Identify cross-JAR relationships
    cross_jar = [r for r in relationships if r["source_jar"] != r["target_jar"]]

    return {
        "total_relationships": len(relationships),
        "cross_jar_relationships": len(cross_jar),
        "relationships": relationships,
        "summary": {
            "services_using_daos": len(set(r["source_class"] for r in relationships)),
            "daos_used_by_services": len(set(r["target_class"] for r in relationships)),
        },
    }


@mcp.tool()
def find_layer_dependencies(
    source_layer: str = "SERVICE", target_layer: str = "DAO"
) -> dict:
    """
    Find all dependencies between any two architectural layers.

    Args:
        source_layer: Layer that depends (SERVICE, CONTROLLER, MESSAGING, etc.)
        target_layer: Layer being depended on (DAO, SERVICE, ENTITY, etc.)

    Returns:
        Dependency map between the two layers.
    """
    source_layer = source_layer.upper()
    target_layer = target_layer.upper()

    target_classes = {
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == target_layer
    }
    target_simple_names = {
        info.simple_name: fqcn
        for fqcn, info in registry.class_index.items()
        if info.layer == target_layer
    }

    relationships = []

    for fqcn, info in registry.class_index.items():
        if info.layer != source_layer:
            continue

        for dep in info.dependencies:
            resolved = dep if dep in target_classes else target_simple_names.get(dep)
            if resolved:
                relationships.append({
                    "source": fqcn,
                    "target": resolved,
                    "source_jar": info.jar_source,
                    "target_jar": registry.class_index[resolved].jar_source,
                })

        for fld in info.fields:
            ft = fld["type"]
            resolved = ft if ft in target_classes else target_simple_names.get(ft)
            if resolved and resolved not in [r["target"] for r in relationships if r["source"] == fqcn]:
                relationships.append({
                    "source": fqcn,
                    "target": resolved,
                    "source_jar": info.jar_source,
                    "target_jar": registry.class_index[resolved].jar_source,
                })

    return {
        "source_layer": source_layer,
        "target_layer": target_layer,
        "total": len(relationships),
        "relationships": relationships,
    }


@mcp.tool()
def find_classes_by_layer(layer: str) -> dict:
    """
    List all classes in a specific architectural layer.

    Args:
        layer: One of DAO, SERVICE, CONTROLLER, ENTITY, MESSAGING,
               CONFIG, INFRASTRUCTURE, UNKNOWN.

    Returns:
        All classes in that layer with their JAR source.
    """
    layer = layer.upper()
    matches = [
        {
            "fqcn": fqcn,
            "simple_name": info.simple_name,
            "jar": os.path.basename(info.jar_source),
            "annotations": info.annotations,
            "is_ejb": info.is_ejb,
            "is_spring": info.is_spring,
        }
        for fqcn, info in registry.class_index.items()
        if info.layer == layer
    ]

    return {
        "layer": layer,
        "count": len(matches),
        "classes": sorted(matches, key=lambda x: x["fqcn"]),
    }


@mcp.tool()
def find_ejb_components() -> dict:
    """
    Find all EJB components across scanned JARs, categorized by type.

    Returns:
        EJBs grouped by type (Stateless, Stateful, Singleton, MessageDriven)
        with their dependencies and migration recommendations.
    """
    ejb_types = {
        "@Stateless": [],
        "@Stateful": [],
        "@Singleton": [],
        "@MessageDriven": [],
    }

    for fqcn, info in registry.class_index.items():
        for ann in info.annotations:
            for ejb_type in ejb_types:
                if ann.startswith(ejb_type):
                    dao_deps = [
                        d for d in info.dependencies
                        if d in registry.class_index
                        and registry.class_index[d].layer == "DAO"
                    ]
                    ejb_types[ejb_type].append({
                        "fqcn": fqcn,
                        "jar": os.path.basename(info.jar_source),
                        "dao_dependencies": dao_deps,
                        "all_dependencies": info.dependencies[:10],  # Limit output
                        "migration_target": _suggest_migration_target(ejb_type, info),
                    })
                    break

    return {
        "total_ejbs": sum(len(v) for v in ejb_types.values()),
        "by_type": {k: {"count": len(v), "beans": v} for k, v in ejb_types.items()},
    }


def _suggest_migration_target(ejb_type: str, info: ClassInfo) -> str:
    """Suggest Spring Boot migration target for an EJB type."""
    suggestions = {
        "@Stateless": "@Service",
        "@Stateful": "@Service + @SessionScope (externalize state to Redis)",
        "@Singleton": "@Component (Spring default is singleton)",
        "@MessageDriven": "@KafkaListener (or @JmsListener for phased migration)",
    }
    target = suggestions.get(ejb_type, "@Component")

    if info.layer == "DAO":
        target = "Spring Data Repository (JpaRepository / MongoRepository)"

    return target


@mcp.tool()
def generate_dependency_graph() -> dict:
    """
    Generate a full dependency graph across all scanned classes.
    Output is formatted for Mermaid diagram rendering.

    Returns:
        Mermaid diagram string + raw adjacency list.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    mermaid_lines = ["graph TD"]

    # Shorten names for readability
    def short_name(fqcn: str) -> str:
        return fqcn.rsplit(".", 1)[-1]

    seen_edges = set()

    for fqcn, info in registry.class_index.items():
        src = short_name(fqcn)
        node_style = {
            "DAO": f'{src}["{src}<br/>DAO"]:::dao',
            "SERVICE": f'{src}["{src}<br/>SERVICE"]:::service',
            "CONTROLLER": f'{src}["{src}<br/>CONTROLLER"]:::controller',
            "ENTITY": f'{src}["{src}<br/>ENTITY"]:::entity',
            "MESSAGING": f'{src}["{src}<br/>MESSAGING"]:::messaging',
        }

        for dep in info.dependencies:
            if dep in registry.class_index:
                tgt = short_name(dep)
                edge_key = f"{src}->{tgt}"
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    adjacency[src].append(tgt)
                    mermaid_lines.append(f"    {src} --> {tgt}")

    # Add styles
    mermaid_lines.append("")
    mermaid_lines.append("    classDef dao fill:#e1f5fe,stroke:#0277bd")
    mermaid_lines.append("    classDef service fill:#f3e5f5,stroke:#7b1fa2")
    mermaid_lines.append("    classDef controller fill:#e8f5e9,stroke:#2e7d32")
    mermaid_lines.append("    classDef entity fill:#fff3e0,stroke:#ef6c00")
    mermaid_lines.append("    classDef messaging fill:#fce4ec,stroke:#c62828")

    # Apply styles
    for fqcn, info in registry.class_index.items():
        sn = short_name(fqcn)
        if info.layer in ("DAO", "SERVICE", "CONTROLLER", "ENTITY", "MESSAGING"):
            mermaid_lines.append(f"    class {sn} {info.layer.lower()}")

    return {
        "mermaid_diagram": "\n".join(mermaid_lines),
        "adjacency_list": dict(adjacency),
        "total_nodes": len(registry.class_index),
        "total_edges": len(seen_edges),
    }


@mcp.tool()
def migration_impact_report(class_name: str) -> dict:
    """
    Generate a migration impact report for a specific class.
    Shows what will be affected if this class is migrated.

    Args:
        class_name: FQCN or simple name of the class to analyze.

    Returns:
        Upstream dependents, downstream dependencies, and migration notes.
    """
    # Resolve class name
    target_fqcn = None
    if class_name in registry.class_index:
        target_fqcn = class_name
    else:
        matches = [
            fqcn for fqcn in registry.class_index
            if fqcn.endswith(f".{class_name}")
        ]
        if len(matches) == 1:
            target_fqcn = matches[0]
        elif matches:
            return {"error": f"Ambiguous: {matches}. Use FQCN."}

    if not target_fqcn:
        return {"error": f"Class '{class_name}' not found in scanned JARs."}

    target_info = registry.class_index[target_fqcn]

    # Find upstream: who depends on THIS class?
    upstream = []
    for fqcn, info in registry.class_index.items():
        if fqcn == target_fqcn:
            continue
        if target_fqcn in info.dependencies or target_info.simple_name in [
            f["type"] for f in info.fields
        ]:
            upstream.append({
                "class": fqcn,
                "layer": info.layer,
                "jar": os.path.basename(info.jar_source),
            })

    # Downstream: what does THIS class depend on?
    downstream = []
    for dep in target_info.dependencies:
        if dep in registry.class_index:
            dep_info = registry.class_index[dep]
            downstream.append({
                "class": dep,
                "layer": dep_info.layer,
                "jar": os.path.basename(dep_info.jar_source),
            })

    return {
        "class": target_fqcn,
        "layer": target_info.layer,
        "jar": os.path.basename(target_info.jar_source),
        "is_ejb": target_info.is_ejb,
        "annotations": target_info.annotations,
        "migration_target": _suggest_migration_target(
            next((a for a in target_info.annotations if a.startswith("@")), ""),
            target_info,
        ),
        "upstream_dependents": {
            "count": len(upstream),
            "classes": upstream,
            "note": "These classes MUST be updated when migrating this class.",
        },
        "downstream_dependencies": {
            "count": len(downstream),
            "classes": downstream,
            "note": "These classes should be migrated BEFORE this class.",
        },
        "risk_level": (
            "HIGH" if len(upstream) > 5
            else "MEDIUM" if len(upstream) > 2
            else "LOW"
        ),
    }


@mcp.tool()
def suggest_migration_order() -> dict:
    """
    Suggest optimal migration order based on dependency analysis.
    Uses reverse topological sort: migrate leaves (fewest dependents) first.

    Returns:
        Ordered list of classes to migrate, grouped into waves.
    """
    # Calculate dependency counts
    dependent_count: dict[str, int] = defaultdict(int)  # How many things depend on this
    dependency_count: dict[str, int] = defaultdict(int)  # How many things this depends on

    for fqcn, info in registry.class_index.items():
        for dep in info.dependencies:
            if dep in registry.class_index:
                dependent_count[dep] += 1
                dependency_count[fqcn] += 1

    # Build migration waves
    waves = []
    migrated = set()

    # Wave 1: Entities (no dependencies typically)
    wave1 = sorted([
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "ENTITY"
    ])
    if wave1:
        waves.append({"wave": 1, "name": "Entities & Value Objects", "classes": wave1})
        migrated.update(wave1)

    # Wave 2: DAOs (depend on entities)
    wave2 = sorted([
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "DAO" and fqcn not in migrated
    ])
    if wave2:
        waves.append({"wave": 2, "name": "Data Access Layer (DAOs → Repositories)", "classes": wave2})
        migrated.update(wave2)

    # Wave 3: Services (depend on DAOs)
    wave3 = sorted([
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "SERVICE" and fqcn not in migrated
    ])
    if wave3:
        waves.append({"wave": 3, "name": "Service Layer (EJBs → @Service)", "classes": wave3})
        migrated.update(wave3)

    # Wave 4: Messaging
    wave4 = sorted([
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "MESSAGING" and fqcn not in migrated
    ])
    if wave4:
        waves.append({"wave": 4, "name": "Messaging (MDB/JMS → @KafkaListener)", "classes": wave4})
        migrated.update(wave4)

    # Wave 5: Controllers
    wave5 = sorted([
        fqcn for fqcn, info in registry.class_index.items()
        if info.layer == "CONTROLLER" and fqcn not in migrated
    ])
    if wave5:
        waves.append({"wave": 5, "name": "Controllers (Servlets/Struts → @RestController)", "classes": wave5})
        migrated.update(wave5)

    # Wave 6: Everything else
    wave6 = sorted([
        fqcn for fqcn in registry.class_index if fqcn not in migrated
    ])
    if wave6:
        waves.append({"wave": 6, "name": "Infrastructure & Config", "classes": wave6})

    return {
        "total_classes": len(registry.class_index),
        "total_waves": len(waves),
        "migration_waves": waves,
    }


@mcp.tool()
def reset_registry() -> dict:
    """Clear all scanned data. Use before re-scanning."""
    count = len(registry.class_index)
    registry.reset()
    return {"status": "Registry cleared", "classes_removed": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Resources (read-only context for LLM)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.resource("scanner://status")
def scanner_status() -> str:
    """Current scanner state: scanned JARs and class counts."""
    jars = [
        f"  - {os.path.basename(j)}: {len(classes)} classes"
        for j, classes in registry.scanned_jars.items()
    ]
    layers = defaultdict(int)
    for info in registry.class_index.values():
        layers[info.layer] += 1

    return (
        f"JAR Scanner Status\n"
        f"==================\n"
        f"Scanned JARs: {len(registry.scanned_jars)}\n"
        + ("\n".join(jars) if jars else "  (none)") + "\n"
        f"\nTotal classes indexed: {len(registry.class_index)}\n"
        f"Layer breakdown: {json.dumps(dict(layers), indent=2)}\n"
        f"Dependency edges tracked: {len(registry.dependency_edges)}"
    )


@mcp.resource("scanner://layers")
def layer_summary() -> str:
    """Summary of all architectural layers discovered."""
    layers = defaultdict(list)
    for fqcn, info in registry.class_index.items():
        layers[info.layer].append(info.simple_name)

    lines = ["Architectural Layer Summary", "=" * 30]
    for layer in ["ENTITY", "DAO", "SERVICE", "MESSAGING", "CONTROLLER", "CONFIG", "INFRASTRUCTURE", "UNKNOWN"]:
        if layer in layers:
            lines.append(f"\n{layer} ({len(layers[layer])} classes):")
            for name in sorted(layers[layer])[:20]:
                lines.append(f"  - {name}")
            if len(layers[layer]) > 20:
                lines.append(f"  ... and {len(layers[layer]) - 20} more")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entrypoint
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    mcp.run()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Configuration Examples
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# VS Code (.vscode/mcp.json):
# {
#   "servers": {
#     "jar-scanner": {
#       "command": "python",
#       "args": ["/path/to/jar_scanner_mcp_server.py"]
#     }
#   }
# }
#
# Claude Code:
#   claude mcp add jar-scanner python /path/to/jar_scanner_mcp_server.py
#
# Cursor (.cursor/mcp.json):
# {
#   "mcpServers": {
#     "jar-scanner": {
#       "command": "python",
#       "args": ["/path/to/jar_scanner_mcp_server.py"]
#     }
#   }
# }
#
# Claude Desktop (claude_desktop_config.json):
# {
#   "mcpServers": {
#     "jar-scanner": {
#       "command": "python",
#       "args": ["/path/to/jar_scanner_mcp_server.py"]
#     }
#   }
# }
