"""
Migration Code Generator MCP Server
=====================================
Scans your in-house target framework, builds intelligent mapping rules
between legacy patterns and target framework patterns, and generates
migration code using the knowledge base.

Architecture:
  ┌────────────────────────────────────────────────────────────────────┐
  │                                                                    │
  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐ │
  │  │ Knowledge    │    │ Mapper       │    │ Code Generator       │ │
  │  │ Base (KB)    │───►│ Engine       │───►│                      │ │
  │  │              │    │              │    │ • Service classes     │ │
  │  │ Legacy:      │    │ Maps legacy  │    │ • Event handlers     │ │
  │  │ • classes    │    │ patterns to  │    │ • Repository impls   │ │
  │  │ • deps      │    │ target fwk   │    │ • Config files       │ │
  │  │ • configs   │    │ patterns     │    │ • Test scaffolds     │ │
  │  └──────────────┘    └──────────────┘    └──────────────────────┘ │
  │         ▲                   ▲                                      │
  │         │                   │                                      │
  │  ┌──────────────┐    ┌──────────────┐                             │
  │  │ Legacy App   │    │ Target       │                             │
  │  │ Source       │    │ Framework    │                             │
  │  │ (scanned)    │    │ Source       │                             │
  │  └──────────────┘    │ (scanned)   │                             │
  │                      └──────────────┘                             │
  └────────────────────────────────────────────────────────────────────┘

Workflow:
  1. scan_target_framework("/path/to/target-framework")
     → Discovers base classes, interfaces, annotations, conventions

  2. create_mapping_rules()  or  auto_discover_mappings()
     → Legacy BaseDao → Target ReactiveRepository
     → Legacy @Service → Target @CommandHandler + EventBus
     → Legacy @Transactional → Target OutboxPublisher pattern

  3. generate_migration("order-service", "OrderService")
     → Reads legacy OrderService from KB
     → Applies mapping rules
     → Generates target framework code with all deps wired

Requirements:
    pip install fastmcp pyyaml

Usage:
    python migration_codegen_mcp_server.py
"""

import json
import os
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared KB path (reads from the same persistent KB)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KB_STORAGE_DIR = os.path.expanduser("~/.mcp-migration-kb")
MAPPINGS_FILE = os.path.join(KB_STORAGE_DIR, "_mappings.json")
TARGET_FWK_FILE = os.path.join(KB_STORAGE_DIR, "_target_framework.json")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp = FastMCP(
    "Migration Code Generator",
    description=(
        "Scans your in-house target framework, builds mapping rules between "
        "legacy and target patterns, and generates migration code."
    ),
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class FrameworkClass:
    """A class/interface from the target framework."""

    fqcn: str
    simple_name: str = ""
    package: str = ""
    class_type: str = ""       # INTERFACE, ABSTRACT_CLASS, CLASS, ANNOTATION
    layer: str = ""
    purpose: str = ""          # User-supplied or auto-detected description
    superclass: str = ""
    interfaces: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    generic_params: list[str] = field(default_factory=list)
    abstract_methods: list[dict] = field(default_factory=list)
    public_methods: list[dict] = field(default_factory=list)
    # For code gen: what imports are needed
    required_imports: list[str] = field(default_factory=list)


@dataclass
class MappingRule:
    """
    A mapping rule between a legacy pattern and a target framework pattern.
    This is the core of the migration intelligence.
    """

    rule_id: str
    description: str

    # What to match in legacy code
    legacy_match: dict = field(default_factory=dict)
    # legacy_match examples:
    #   {"layer": "DAO", "extends": "BaseDao"}
    #   {"layer": "SERVICE", "annotation": "@Transactional"}
    #   {"layer": "SERVICE", "has_dependency_type": ".*Repository"}
    #   {"annotation": "@Scheduled"}
    #   {"interface": "MessageListener"}

    # What to generate in target framework
    target_transform: dict = field(default_factory=dict)
    # target_transform examples:
    #   {"extends": "ReactiveRepository", "implements": ["EventSourcedAggregate"]}
    #   {"annotation": "@CommandHandler", "inject": ["EventBusConnector"]}
    #   {"pattern": "outbox", "template": "outbox_service"}
    #   {"replace_annotation": {"@Scheduled": "@EventTriggered"}}

    # Code template name (references templates dict)
    template_name: str = ""

    priority: int = 100      # Lower = applied first
    enabled: bool = True


@dataclass
class GeneratedFile:
    """A generated source file."""

    file_path: str
    content: str
    source_class: str = ""
    target_class: str = ""
    applied_rules: list[str] = field(default_factory=list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CodeGenRegistry:
    """Holds framework metadata, mapping rules, and generated code."""

    def __init__(self):
        self.framework_classes: dict[str, FrameworkClass] = {}
        self.framework_name: str = ""
        self.framework_path: str = ""
        self.mapping_rules: dict[str, MappingRule] = {}
        self.generated_files: list[GeneratedFile] = []
        self._load_persisted()

    def _load_persisted(self):
        """Load previously saved framework info and mappings from disk."""
        if os.path.isfile(TARGET_FWK_FILE):
            try:
                with open(TARGET_FWK_FILE, "r") as f:
                    data = json.load(f)
                self.framework_name = data.get("name", "")
                self.framework_path = data.get("path", "")
                for fqcn, cls_data in data.get("classes", {}).items():
                    self.framework_classes[fqcn] = FrameworkClass(**cls_data)
            except Exception:
                pass

        if os.path.isfile(MAPPINGS_FILE):
            try:
                with open(MAPPINGS_FILE, "r") as f:
                    data = json.load(f)
                for rule_id, rule_data in data.get("rules", {}).items():
                    self.mapping_rules[rule_id] = MappingRule(**rule_data)
            except Exception:
                pass

    def save_framework(self):
        """Persist framework metadata to disk."""
        os.makedirs(KB_STORAGE_DIR, exist_ok=True)
        data = {
            "name": self.framework_name,
            "path": self.framework_path,
            "classes": {k: asdict(v) for k, v in self.framework_classes.items()},
            "saved_at": datetime.now().isoformat(),
        }
        with open(TARGET_FWK_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def save_mappings(self):
        """Persist mapping rules to disk."""
        os.makedirs(KB_STORAGE_DIR, exist_ok=True)
        data = {
            "rules": {k: asdict(v) for k, v in self.mapping_rules.items()},
            "saved_at": datetime.now().isoformat(),
        }
        with open(MAPPINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)


registry = CodeGenRegistry()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KB Reader (reads from the shared persistent KB)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_kb_project(project_name: str) -> dict:
    """Load class records for a project from the shared KB."""
    file_path = os.path.join(KB_STORAGE_DIR, f"{project_name}.json")
    if not os.path.isfile(file_path):
        return {}
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
        return data.get("classes", {})
    except Exception:
        return {}


def _load_kb_index() -> dict:
    """Load KB project index."""
    idx_file = os.path.join(KB_STORAGE_DIR, "_index.json")
    if os.path.isfile(idx_file):
        try:
            with open(idx_file, "r") as f:
                return json.load(f).get("projects", {})
        except Exception:
            pass
    return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Java Source Parser (for target framework)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _parse_framework_class(file_path: str) -> Optional[FrameworkClass]:
    """Parse a Java source file from the target framework."""
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if "package-info" in file_path or "module-info" in file_path:
        return None

    fc = FrameworkClass(fqcn="", simple_name="")

    # Package
    pkg = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
    fc.package = pkg.group(1) if pkg else ""

    # Imports → needed for code gen
    fc.required_imports = re.findall(r"^import\s+([\w.]+)\s*;", content, re.MULTILINE)

    # Class declaration
    decl = re.search(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"
        r"(?:public\s+|protected\s+)?"
        r"(abstract\s+)?"
        r"(class|interface|enum|@interface)\s+(\w+)"
        r"(?:<([^>]+)>)?"
        r"(?:\s+extends\s+([\w.<>,\s]+))?"
        r"(?:\s+implements\s+([\w.<>,\s]+))?",
        content,
    )
    if not decl:
        return None

    ann_block = decl.group(1) or ""
    is_abstract = bool(decl.group(2))
    kind = decl.group(3)
    fc.simple_name = decl.group(4)
    generics = decl.group(5) or ""
    extends_clause = decl.group(6) or ""
    implements_clause = decl.group(7) or ""

    fc.fqcn = f"{fc.package}.{fc.simple_name}" if fc.package else fc.simple_name

    if kind == "interface":
        fc.class_type = "INTERFACE"
    elif kind == "@interface":
        fc.class_type = "ANNOTATION"
    elif is_abstract:
        fc.class_type = "ABSTRACT_CLASS"
    else:
        fc.class_type = "CLASS"

    if generics:
        fc.generic_params = [g.strip() for g in generics.split(",")]

    if extends_clause:
        fc.superclass = extends_clause.strip().split(",")[0].split("<")[0].strip()
    if implements_clause:
        fc.interfaces = [i.strip().split("<")[0].strip() for i in implements_clause.split(",")]
    if kind == "interface" and extends_clause:
        fc.interfaces = [i.strip().split("<")[0].strip() for i in extends_clause.split(",")]
        fc.superclass = ""

    # Annotations
    fc.annotations = [f"@{a[0]}" for a in re.findall(r"@(\w+)", ann_block)]

    # Classify purpose/layer
    name_lower = fc.fqcn.lower()
    if any(s in name_lower for s in ["event", "command", "message"]):
        fc.layer = "EVENTING"
    elif any(s in name_lower for s in ["repository", "dao", "store"]):
        fc.layer = "DATA_ACCESS"
    elif any(s in name_lower for s in ["handler", "processor", "consumer"]):
        fc.layer = "HANDLER"
    elif any(s in name_lower for s in ["service"]):
        fc.layer = "SERVICE"
    elif any(s in name_lower for s in ["controller", "resource", "api"]):
        fc.layer = "API"
    elif any(s in name_lower for s in ["config", "properties"]):
        fc.layer = "CONFIG"
    elif any(s in name_lower for s in ["entity", "aggregate", "domain", "model"]):
        fc.layer = "DOMAIN"
    elif fc.class_type == "ANNOTATION":
        fc.layer = "ANNOTATION"
    else:
        fc.layer = "FRAMEWORK"

    # Abstract methods
    if fc.class_type in ("INTERFACE", "ABSTRACT_CLASS"):
        for m in re.finditer(
            r"(?:public\s+)?(?:abstract\s+)?([\w<>,.?\[\]\s]+?)\s+(\w+)\s*\(([^)]*)\)\s*;",
            content,
        ):
            fc.abstract_methods.append({
                "name": m.group(2),
                "return_type": m.group(1).strip(),
                "parameters": m.group(3).strip(),
            })

    # Public methods
    for m in re.finditer(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"
        r"public\s+(?:static\s+)?(?:final\s+)?"
        r"([\w<>,.?\[\]\s]+?)\s+(\w+)\s*\(([^)]*)\)",
        content,
    ):
        anns = [f"@{a}" for a in re.findall(r"@(\w+)", m.group(1) or "")]
        fc.public_methods.append({
            "name": m.group(3),
            "return_type": m.group(2).strip(),
            "parameters": m.group(4).strip(),
            "annotations": anns,
        })

    # Auto-detect purpose from Javadoc
    doc_match = re.search(
        rf"/\*\*(.*?)\*/\s*(?:@\w+\s*)*\s*(?:public\s+)?(?:abstract\s+)?(?:class|interface)\s+{re.escape(fc.simple_name)}",
        content, re.DOTALL,
    )
    if doc_match:
        doc_text = re.sub(r"\s*\*\s*", " ", doc_match.group(1)).strip()
        fc.purpose = doc_text[:200]

    return fc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Code Templates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CODE_TEMPLATES = {
    # ── Service with Event Publishing ──
    "event_driven_service": textwrap.dedent("""\
        package {target_package};

        {imports}

        /**
         * Migrated from: {legacy_fqcn}
         * Migration date: {date}
         * Applied rules: {applied_rules}
         *
         * Original: {legacy_stereotype} in {legacy_project}
         */
        {class_annotations}
        public class {target_class_name} {{

            {dependency_fields}

            {constructor}

            {methods}
        }}
    """),

    # ── Event Handler (replaces @JmsListener / @Scheduled) ──
    "event_handler": textwrap.dedent("""\
        package {target_package};

        {imports}

        /**
         * Migrated from: {legacy_fqcn}
         * Replaces: {legacy_pattern}
         */
        {class_annotations}
        public class {target_class_name} {{

            {dependency_fields}

            {constructor}

            {handler_methods}
        }}
    """),

    # ── Repository (replaces legacy DAO) ──
    "repository": textwrap.dedent("""\
        package {target_package};

        {imports}

        /**
         * Migrated from: {legacy_fqcn}
         * Legacy: {legacy_stereotype} {legacy_superclass}
         */
        public interface {target_class_name} extends {target_base_repository}<{entity_type}, {id_type}> {{

            {custom_query_methods}
        }}
    """),

    # ── Domain Event Record ──
    "domain_event": textwrap.dedent("""\
        package {target_package}.events;

        {imports}

        /**
         * Domain event generated from {source_method} in {legacy_fqcn}.
         */
        public record {event_name}(
            String eventId,
            {event_fields}
            Instant timestamp,
            String correlationId
        ) {{
            public {event_name} {{
                eventId = eventId != null ? eventId : java.util.UUID.randomUUID().toString();
                timestamp = timestamp != null ? timestamp : Instant.now();
            }}
        }}
    """),

    # ── Configuration class ──
    "configuration": textwrap.dedent("""\
        package {target_package}.config;

        {imports}

        /**
         * Configuration for migrated {service_name}.
         */
        @Configuration
        public class {target_class_name}Config {{

            {bean_definitions}
        }}
    """),

    # ── Integration test scaffold ──
    "integration_test": textwrap.dedent("""\
        package {target_package};

        {imports}

        /**
         * Integration test for migrated {target_class_name}.
         * Validates behavior parity with legacy {legacy_fqcn}.
         */
        @SpringBootTest
        @ActiveProfiles("test")
        class {target_class_name}IntegrationTest {{

            {autowired_fields}

            {test_methods}
        }}
    """),
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def scan_target_framework(
    framework_path: str,
    framework_name: str = "target-framework",
) -> dict:
    """
    Scan your in-house target framework to discover its base classes,
    interfaces, annotations, and conventions.

    Args:
        framework_path: Path to the framework source code checkout.
        framework_name: Identifier for the framework.

    Returns:
        Framework inventory: base classes, interfaces, annotations,
        and their intended usage patterns.
    """
    framework_path = os.path.abspath(framework_path)
    if not os.path.isdir(framework_path):
        return {"error": f"Directory not found: {framework_path}"}

    registry.framework_name = framework_name
    registry.framework_path = framework_path
    registry.framework_classes.clear()

    java_files = [
        f for f in Path(framework_path).rglob("*.java")
        if "/test/" not in str(f) and "\\test\\" not in str(f)
    ]

    layer_counts = defaultdict(int)
    type_counts = defaultdict(int)

    for jf in java_files:
        fc = _parse_framework_class(str(jf))
        if fc and fc.fqcn:
            registry.framework_classes[fc.fqcn] = fc
            layer_counts[fc.layer] += 1
            type_counts[fc.class_type] += 1

    registry.save_framework()

    # Key extension points (what app developers extend/implement)
    extension_points = [
        {
            "fqcn": fc.fqcn,
            "simple_name": fc.simple_name,
            "type": fc.class_type,
            "layer": fc.layer,
            "purpose": fc.purpose,
            "generic_params": fc.generic_params,
            "abstract_methods": fc.abstract_methods[:5],
        }
        for fc in registry.framework_classes.values()
        if fc.class_type in ("INTERFACE", "ABSTRACT_CLASS")
    ]

    # Custom annotations
    custom_annotations = [
        {
            "fqcn": fc.fqcn,
            "simple_name": fc.simple_name,
            "purpose": fc.purpose,
        }
        for fc in registry.framework_classes.values()
        if fc.class_type == "ANNOTATION"
    ]

    return {
        "framework": framework_name,
        "path": framework_path,
        "total_classes": len(registry.framework_classes),
        "class_types": dict(type_counts),
        "layer_breakdown": dict(layer_counts),
        "extension_points": extension_points,
        "custom_annotations": custom_annotations,
        "persisted": True,
    }


@mcp.tool()
def add_mapping_rule(
    rule_id: str,
    description: str,
    legacy_layer: str = "",
    legacy_annotation: str = "",
    legacy_extends: str = "",
    legacy_implements: str = "",
    legacy_has_dependency: str = "",
    legacy_method_pattern: str = "",
    target_extends: str = "",
    target_implements: str = "",
    target_annotation: str = "",
    target_inject: str = "",
    target_template: str = "",
    target_additional_imports: str = "",
    priority: int = 100,
) -> dict:
    """
    Add a mapping rule between a legacy pattern and a target framework pattern.

    This is the CORE of the migration intelligence. Each rule says:
    "When you see THIS in legacy code, generate THAT in target framework."

    Args:
        rule_id:              Unique rule identifier (e.g., "dao-to-reactive-repo")
        description:          Human-readable description
        legacy_layer:         Match legacy layer (DAO, SERVICE, CONTROLLER, etc.)
        legacy_annotation:    Match legacy annotation (e.g., "@Transactional")
        legacy_extends:       Match legacy superclass (e.g., "BaseDao")
        legacy_implements:    Match legacy interface (e.g., "MessageListener")
        legacy_has_dependency: Match if class injects this type (regex, e.g., ".*Repository")
        legacy_method_pattern: Match method names (regex, e.g., "create.*|save.*")
        target_extends:       Target superclass to use
        target_implements:    Comma-separated target interfaces
        target_annotation:    Target class annotation
        target_inject:        Comma-separated additional deps to inject
        target_template:      Code template name
        target_additional_imports: Comma-separated extra imports
        priority:             Lower = applied first (default 100)

    Returns:
        Saved mapping rule.

    Examples:
        # DAO → Reactive Repository
        add_mapping_rule(
            rule_id="dao-to-repo",
            description="Convert legacy DAO to reactive repository",
            legacy_layer="DAO",
            legacy_extends="BaseDao",
            target_template="repository",
            target_extends="ReactiveMongoRepository",
        )

        # Service with @Transactional → Event-publishing service
        add_mapping_rule(
            rule_id="service-to-event-service",
            description="Service with transactions → event-driven service",
            legacy_layer="SERVICE",
            legacy_annotation="@Transactional",
            target_annotation="@Service",
            target_inject="EventBusConnector,OutboxPublisher",
            target_template="event_driven_service",
        )
    """
    rule = MappingRule(
        rule_id=rule_id,
        description=description,
        legacy_match={
            k: v for k, v in {
                "layer": legacy_layer,
                "annotation": legacy_annotation,
                "extends": legacy_extends,
                "implements": legacy_implements,
                "has_dependency": legacy_has_dependency,
                "method_pattern": legacy_method_pattern,
            }.items() if v
        },
        target_transform={
            k: v for k, v in {
                "extends": target_extends,
                "implements": [i.strip() for i in target_implements.split(",") if i.strip()] if target_implements else [],
                "annotation": target_annotation,
                "inject": [i.strip() for i in target_inject.split(",") if i.strip()] if target_inject else [],
                "additional_imports": [i.strip() for i in target_additional_imports.split(",") if i.strip()] if target_additional_imports else [],
            }.items() if v
        },
        template_name=target_template,
        priority=priority,
    )

    registry.mapping_rules[rule_id] = rule
    registry.save_mappings()

    return {
        "status": "saved",
        "rule": asdict(rule),
    }


@mcp.tool()
def auto_discover_mappings() -> dict:
    """
    Automatically discover possible mapping rules by comparing legacy KB
    classes with target framework extension points.

    Uses heuristics:
      - Name similarity (BaseDao → BaseRepository, BaseService → AbstractCommandHandler)
      - Layer matching (legacy DAO layer → framework DATA_ACCESS layer)
      - Interface contract matching (similar method signatures)

    Returns:
        Suggested mapping rules for review and confirmation.
    """
    kb_index = _load_kb_index()
    suggestions = []

    # Collect all unique legacy patterns
    legacy_base_classes = set()
    legacy_interfaces = set()
    legacy_layers = defaultdict(int)

    for proj_name, proj_data in kb_index.items():
        if proj_data.get("project_type") == "LIBRARY":
            continue
        classes = _load_kb_project(proj_name)
        for fqcn, cls in classes.items():
            if cls.get("superclass"):
                legacy_base_classes.add(cls["superclass"])
            for iface in cls.get("interfaces", []):
                legacy_interfaces.add(iface)
            legacy_layers[cls.get("layer", "UNKNOWN")] += 1

    # Framework extension points
    fwk_interfaces = {
        fc.simple_name: fc
        for fc in registry.framework_classes.values()
        if fc.class_type == "INTERFACE"
    }
    fwk_abstracts = {
        fc.simple_name: fc
        for fc in registry.framework_classes.values()
        if fc.class_type == "ABSTRACT_CLASS"
    }

    # ── Heuristic 1: Layer-based mapping ──
    layer_mapping = {
        "DAO": "DATA_ACCESS",
        "SERVICE": "SERVICE",
        "CONTROLLER": "API",
        "MESSAGING": "HANDLER",
        "ENTITY": "DOMAIN",
    }

    for legacy_layer, fwk_layer in layer_mapping.items():
        fwk_targets = [
            fc for fc in registry.framework_classes.values()
            if fc.layer == fwk_layer and fc.class_type in ("INTERFACE", "ABSTRACT_CLASS")
        ]
        if fwk_targets and legacy_layers.get(legacy_layer, 0) > 0:
            for target in fwk_targets:
                suggestions.append({
                    "suggested_rule_id": f"{legacy_layer.lower()}-to-{target.simple_name}",
                    "description": f"Legacy {legacy_layer} → {target.simple_name}",
                    "legacy_match": {"layer": legacy_layer},
                    "target_extends_or_implements": target.fqcn,
                    "target_type": target.class_type,
                    "target_layer": target.layer,
                    "confidence": "MEDIUM",
                    "reason": f"Layer mapping: legacy {legacy_layer} ↔ framework {fwk_layer}",
                })

    # ── Heuristic 2: Name similarity ──
    name_pairs = [
        ("Dao", "Repository"), ("Repository", "Repository"),
        ("Service", "Service"), ("Service", "Handler"),
        ("Service", "CommandHandler"), ("Listener", "EventHandler"),
        ("Controller", "Resource"), ("Controller", "Controller"),
    ]
    for legacy_suffix, fwk_suffix in name_pairs:
        for fwk_name, fc in {**fwk_interfaces, **fwk_abstracts}.items():
            if fwk_name.endswith(fwk_suffix) or fwk_suffix in fwk_name:
                for legacy_base in legacy_base_classes | legacy_interfaces:
                    if legacy_base.endswith(legacy_suffix) or legacy_suffix in legacy_base:
                        suggestions.append({
                            "suggested_rule_id": f"{legacy_base}-to-{fwk_name}",
                            "description": f"Legacy {legacy_base} → Framework {fwk_name}",
                            "legacy_match": {"extends_or_implements": legacy_base},
                            "target_extends_or_implements": fc.fqcn,
                            "confidence": "HIGH" if legacy_suffix == fwk_suffix else "MEDIUM",
                            "reason": f"Name pattern: *{legacy_suffix} → *{fwk_suffix}",
                        })

    # Deduplicate
    seen = set()
    unique = []
    for s in suggestions:
        key = s["suggested_rule_id"]
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return {
        "total_suggestions": len(unique),
        "suggestions": unique,
        "note": "Review these suggestions, then use add_mapping_rule() to confirm the ones you want.",
    }


@mcp.tool()
def list_mapping_rules() -> dict:
    """List all configured mapping rules."""
    rules = [
        asdict(rule)
        for rule in sorted(registry.mapping_rules.values(), key=lambda r: r.priority)
    ]
    return {
        "total_rules": len(rules),
        "rules": rules,
    }


@mcp.tool()
def remove_mapping_rule(rule_id: str) -> dict:
    """Remove a mapping rule by ID."""
    if rule_id in registry.mapping_rules:
        del registry.mapping_rules[rule_id]
        registry.save_mappings()
        return {"status": "removed", "rule_id": rule_id}
    return {"error": f"Rule '{rule_id}' not found."}


@mcp.tool()
def generate_migration(
    project_name: str,
    class_name: str,
    target_package: str = "",
    output_dir: str = "",
    dry_run: bool = True,
) -> dict:
    """
    Generate migrated code for a specific legacy class using mapping rules.

    Reads the legacy class from the KB, applies matching mapping rules,
    and generates target framework code.

    Args:
        project_name:   Legacy project name in KB (e.g., "order-service")
        class_name:     Class to migrate (FQCN or simple name)
        target_package: Target package for generated code (default: auto-derived)
        output_dir:     Directory to write generated files (default: dry_run only)
        dry_run:        If True, return code without writing files

    Returns:
        Generated source code with applied rules and migration notes.
    """
    # Load legacy class from KB
    classes = _load_kb_project(project_name)
    if not classes:
        return {"error": f"Project '{project_name}' not found in KB. Run scan_application first."}

    # Find the class
    legacy_class = None
    for fqcn, cls_data in classes.items():
        if fqcn == class_name or cls_data.get("simple_name") == class_name:
            legacy_class = cls_data
            legacy_class["fqcn"] = fqcn
            break

    if not legacy_class:
        return {"error": f"Class '{class_name}' not found in project '{project_name}'."}

    # Find matching mapping rules
    matched_rules = _match_rules(legacy_class)

    if not matched_rules:
        return {
            "warning": f"No mapping rules matched for {legacy_class['fqcn']}.",
            "legacy_class": {
                "fqcn": legacy_class["fqcn"],
                "layer": legacy_class.get("layer"),
                "stereotype": legacy_class.get("stereotype"),
                "superclass": legacy_class.get("superclass"),
                "interfaces": legacy_class.get("interfaces"),
            },
            "tip": "Use add_mapping_rule() to create rules, or auto_discover_mappings() for suggestions.",
        }

    # Determine target package
    if not target_package:
        target_package = legacy_class.get("package", "com.company.migrated")

    # Generate code
    generated = _generate_code(legacy_class, matched_rules, target_package, project_name)

    # Write files if not dry_run
    if not dry_run and output_dir:
        output_dir = os.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        for gf in generated:
            full_path = os.path.join(output_dir, gf.file_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            Path(full_path).write_text(gf.content)

    return {
        "legacy_class": legacy_class["fqcn"],
        "legacy_layer": legacy_class.get("layer"),
        "rules_applied": [r.rule_id for r in matched_rules],
        "files_generated": [
            {
                "path": gf.file_path,
                "target_class": gf.target_class,
                "content": gf.content,
                "applied_rules": gf.applied_rules,
            }
            for gf in generated
        ],
        "dry_run": dry_run,
        "output_dir": output_dir if not dry_run else "(dry run - no files written)",
    }


def _match_rules(legacy_class: dict) -> list[MappingRule]:
    """Find all mapping rules that match a legacy class."""
    matched = []

    for rule in sorted(registry.mapping_rules.values(), key=lambda r: r.priority):
        if not rule.enabled:
            continue

        match = rule.legacy_match
        is_match = True

        if "layer" in match and match["layer"] != legacy_class.get("layer"):
            is_match = False

        if "annotation" in match:
            annotations = " ".join(legacy_class.get("annotations", []))
            if match["annotation"] not in annotations:
                is_match = False

        if "extends" in match:
            if match["extends"] != legacy_class.get("superclass"):
                is_match = False

        if "implements" in match:
            if match["implements"] not in legacy_class.get("interfaces", []):
                is_match = False

        if "has_dependency" in match:
            pattern = match["has_dependency"]
            deps = legacy_class.get("all_dependency_types", [])
            if not any(re.match(pattern, d) for d in deps):
                is_match = False

        if is_match:
            matched.append(rule)

    return matched


def _generate_code(
    legacy: dict, rules: list[MappingRule],
    target_package: str, project_name: str,
) -> list[GeneratedFile]:
    """Generate migration code based on legacy class and matched rules."""

    generated = []
    simple_name = legacy.get("simple_name", "Unknown")
    fqcn = legacy.get("fqcn", "")
    applied_rule_ids = [r.rule_id for r in rules]

    # ── Merge all rule transforms ──
    merged_transform = {
        "extends": "",
        "implements": [],
        "annotations": [],
        "inject": [],
        "additional_imports": [],
    }

    template_name = "event_driven_service"  # default

    for rule in rules:
        t = rule.target_transform
        if t.get("extends"):
            merged_transform["extends"] = t["extends"]
        if t.get("implements"):
            merged_transform["implements"].extend(t["implements"])
        if t.get("annotation"):
            merged_transform["annotations"].append(t["annotation"])
        if t.get("inject"):
            merged_transform["inject"].extend(t["inject"])
        if t.get("additional_imports"):
            merged_transform["additional_imports"].extend(t["additional_imports"])
        if rule.template_name:
            template_name = rule.template_name

    # ── Build target class name ──
    target_class_name = simple_name
    # If it ends with "Bean" or "Impl", clean it up
    for suffix in ("Bean", "Impl", "EJB"):
        if target_class_name.endswith(suffix):
            target_class_name = target_class_name[: -len(suffix)]

    # ── Build imports ──
    imports_set = set()
    imports_set.add("import java.time.Instant;")
    imports_set.add("import java.util.UUID;")

    for imp in merged_transform["additional_imports"]:
        if "." in imp:
            imports_set.add(f"import {imp};")

    # Add framework imports
    if merged_transform["extends"]:
        fwk_cls = _find_framework_class(merged_transform["extends"])
        if fwk_cls:
            imports_set.add(f"import {fwk_cls.fqcn};")
            for ri in fwk_cls.required_imports:
                if not ri.startswith("java."):
                    imports_set.add(f"import {ri};")

    for iface in merged_transform["implements"]:
        fwk_cls = _find_framework_class(iface)
        if fwk_cls:
            imports_set.add(f"import {fwk_cls.fqcn};")

    for ann in merged_transform["annotations"]:
        clean_ann = ann.replace("@", "")
        fwk_cls = _find_framework_class(clean_ann)
        if fwk_cls:
            imports_set.add(f"import {fwk_cls.fqcn};")

    imports_block = "\n".join(sorted(imports_set))

    # ── Build dependency fields and constructor ──
    all_deps = []

    # Original legacy deps (translated)
    for dep in legacy.get("constructor_deps", []) + legacy.get("field_deps", []):
        dep_type = dep.get("type", "")
        dep_name = dep.get("name", "")
        # Check if this dep type needs translation via rules
        translated = _translate_dependency_type(dep_type)
        all_deps.append({"type": translated, "name": dep_name})

    # Additional injected deps from rules
    for inject_type in merged_transform["inject"]:
        var_name = inject_type[0].lower() + inject_type[1:]
        if var_name not in [d["name"] for d in all_deps]:
            all_deps.append({"type": inject_type, "name": var_name})
            fwk_cls = _find_framework_class(inject_type)
            if fwk_cls:
                imports_set.add(f"import {fwk_cls.fqcn};")

    # Regenerate imports
    imports_block = "\n".join(sorted(imports_set))

    fields_block = "\n    ".join(
        f"private final {d['type']} {d['name']};"
        for d in all_deps
    )

    # Constructor
    ctor_params = ", ".join(f"{d['type']} {d['name']}" for d in all_deps)
    ctor_body = "\n        ".join(f"this.{d['name']} = {d['name']};" for d in all_deps)
    constructor_block = (
        f"public {target_class_name}({ctor_params}) {{\n"
        f"        {ctor_body}\n"
        f"    }}"
    ) if all_deps else ""

    # ── Class annotations ──
    class_annotations = "\n".join(merged_transform["annotations"]) if merged_transform["annotations"] else "@Service"

    # ── Extends / Implements ──
    extends_clause = f" extends {merged_transform['extends']}" if merged_transform["extends"] else ""
    impl_list = merged_transform["implements"]
    implements_clause = f" implements {', '.join(impl_list)}" if impl_list else ""

    # ── Method bodies ──
    methods_block = _generate_methods(legacy, merged_transform, target_class_name)

    # ── Choose template ──
    if template_name == "repository":
        # Repository is simpler — just an interface
        entity_type = _guess_entity_type(legacy)
        content = CODE_TEMPLATES["repository"].format(
            target_package=target_package,
            imports=imports_block,
            legacy_fqcn=fqcn,
            legacy_stereotype=legacy.get("stereotype", ""),
            legacy_superclass=legacy.get("superclass", ""),
            target_class_name=target_class_name,
            target_base_repository=merged_transform["extends"] or "ReactiveMongoRepository",
            entity_type=entity_type,
            id_type="String",
            custom_query_methods=_generate_repo_methods(legacy),
        )
    else:
        # Service / Handler template
        content = CODE_TEMPLATES.get(template_name, CODE_TEMPLATES["event_driven_service"]).format(
            target_package=target_package,
            imports=imports_block,
            legacy_fqcn=fqcn,
            legacy_project=project_name,
            legacy_stereotype=legacy.get("stereotype", ""),
            date=datetime.now().strftime("%Y-%m-%d"),
            applied_rules=", ".join(applied_rule_ids),
            class_annotations=class_annotations,
            target_class_name=f"{target_class_name}{extends_clause}{implements_clause}",
            dependency_fields=fields_block,
            constructor=constructor_block,
            methods=methods_block,
            handler_methods=methods_block,
            legacy_pattern=legacy.get("stereotype", ""),
        )

    # Main file
    file_rel_path = os.path.join(
        target_package.replace(".", os.sep),
        f"{target_class_name}.java",
    )
    generated.append(GeneratedFile(
        file_path=file_rel_path,
        content=content,
        source_class=fqcn,
        target_class=f"{target_package}.{target_class_name}",
        applied_rules=applied_rule_ids,
    ))

    # ── Generate domain events for service methods ──
    if template_name in ("event_driven_service",):
        event_files = _generate_domain_events(legacy, target_package)
        generated.extend(event_files)

    # ── Generate integration test scaffold ──
    test_content = _generate_test_scaffold(
        legacy, target_class_name, target_package, all_deps
    )
    test_path = os.path.join(
        target_package.replace(".", os.sep),
        f"{target_class_name}IntegrationTest.java",
    )
    generated.append(GeneratedFile(
        file_path=test_path,
        content=test_content,
        source_class=fqcn,
        target_class=f"{target_package}.{target_class_name}IntegrationTest",
        applied_rules=["test-scaffold"],
    ))

    return generated


def _find_framework_class(name: str) -> Optional[FrameworkClass]:
    """Find a framework class by simple name."""
    for fc in registry.framework_classes.values():
        if fc.simple_name == name or fc.fqcn == name:
            return fc
    return None


def _translate_dependency_type(dep_type: str) -> str:
    """Translate a legacy dependency type using mapping rules."""
    for rule in registry.mapping_rules.values():
        match = rule.legacy_match
        target = rule.target_transform

        # If the dep type matches a legacy extends/implements pattern
        if match.get("extends") == dep_type and target.get("extends"):
            return target["extends"]
        if match.get("implements") == dep_type and target.get("implements"):
            return target["implements"][0] if target["implements"] else dep_type

    # Convention-based translation
    if dep_type.endswith("Dao"):
        return dep_type.replace("Dao", "Repository")
    if dep_type.endswith("DAO"):
        return dep_type.replace("DAO", "Repository")

    return dep_type


def _generate_methods(legacy: dict, transform: dict, class_name: str) -> str:
    """Generate method bodies for the migrated class."""
    lines = []

    for method in legacy.get("public_methods", []):
        name = method.get("name", "")
        ret = method.get("return_type", "void")
        params = method.get("parameters", "")
        annotations = method.get("annotations", [])

        # Skip getters/setters/toString/hashCode/equals
        if name.startswith(("get", "set", "is", "toString", "hashCode", "equals")):
            continue

        method_annotations = []

        # If method was @Transactional, add it and add event publishing
        if "@Transactional" in annotations:
            method_annotations.append("@Transactional")

        ann_block = "\n    ".join(method_annotations)
        if ann_block:
            ann_block = f"    {ann_block}\n"

        # Generate event publishing for mutating methods
        is_mutating = any(name.lower().startswith(p) for p in [
            "create", "save", "update", "delete", "process", "submit",
            "cancel", "approve", "reject", "complete",
        ])

        if is_mutating:
            event_name = _method_to_event_name(name, class_name)
            method_body = textwrap.dedent(f"""\
    {ann_block}    public {ret} {name}({params}) {{
            // TODO: Implement business logic (migrated from legacy)

            // Publish domain event
            // eventBusConnector.publish(new {event_name}(...));

            throw new UnsupportedOperationException("Migration TODO: {name}");
        }}""")
        else:
            method_body = textwrap.dedent(f"""\
    {ann_block}    public {ret} {name}({params}) {{
            // TODO: Implement (migrated from legacy)
            throw new UnsupportedOperationException("Migration TODO: {name}");
        }}""")

        lines.append(method_body)

    return "\n\n".join(lines) if lines else "    // No public methods found in legacy class"


def _generate_domain_events(legacy: dict, target_package: str) -> list[GeneratedFile]:
    """Generate domain event records for mutating service methods."""
    events = []

    for method in legacy.get("public_methods", []):
        name = method.get("name", "")
        params = method.get("parameters", "")

        is_mutating = any(name.lower().startswith(p) for p in [
            "create", "save", "update", "delete", "process", "submit",
            "cancel", "approve", "reject", "complete",
        ])

        if not is_mutating:
            continue

        event_name = _method_to_event_name(name, legacy.get("simple_name", ""))

        # Convert method params to event fields
        event_fields = []
        if params:
            for param in params.split(","):
                param = param.strip()
                param = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", param).strip()
                parts = param.split()
                if len(parts) >= 2:
                    event_fields.append(f"    {parts[-2]} {parts[-1]}")

        fields_str = ",\n".join(event_fields) + ("," if event_fields else "")

        content = CODE_TEMPLATES["domain_event"].format(
            target_package=target_package,
            imports="import java.time.Instant;",
            source_method=name,
            legacy_fqcn=legacy.get("fqcn", ""),
            event_name=event_name,
            event_fields=fields_str,
        )

        file_path = os.path.join(
            target_package.replace(".", os.sep),
            "events",
            f"{event_name}.java",
        )
        events.append(GeneratedFile(
            file_path=file_path,
            content=content,
            source_class=legacy.get("fqcn", ""),
            target_class=f"{target_package}.events.{event_name}",
            applied_rules=["auto-event-generation"],
        ))

    return events


def _generate_test_scaffold(
    legacy: dict, target_class: str,
    target_package: str, deps: list[dict],
) -> str:
    """Generate integration test scaffold."""
    autowired = "\n    ".join(
        f"@Autowired\n    private {d['type']} {d['name']};"
        for d in deps
    )

    test_methods = []
    for method in legacy.get("public_methods", []):
        name = method.get("name", "")
        if name.startswith(("get", "set", "is")):
            continue
        test_name = f"should{name[0].upper()}{name[1:]}Successfully"
        test_methods.append(textwrap.dedent(f"""\
    @Test
        @DisplayName("{name}() - migrated from legacy")
        void {test_name}() {{
            // Given
            // TODO: Set up test data

            // When
            // TODO: Call {name}()

            // Then
            // TODO: Assert behavior parity with legacy
            fail("Migration TODO: implement test for {name}");
        }}"""))

    tests_block = "\n\n    ".join(test_methods) if test_methods else "    // No test methods generated"

    return CODE_TEMPLATES["integration_test"].format(
        target_package=target_package,
        imports=(
            "import org.junit.jupiter.api.*;\n"
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "import org.springframework.boot.test.context.SpringBootTest;\n"
            "import org.springframework.test.context.ActiveProfiles;\n"
            "import static org.junit.jupiter.api.Assertions.*;"
        ),
        target_class_name=target_class,
        legacy_fqcn=legacy.get("fqcn", ""),
        autowired_fields=autowired,
        test_methods=tests_block,
    )


def _generate_repo_methods(legacy: dict) -> str:
    """Generate custom repository query methods from legacy DAO methods."""
    lines = []
    for method in legacy.get("public_methods", []):
        name = method.get("name", "")
        ret = method.get("return_type", "void")
        params = method.get("parameters", "")

        if name.startswith(("find", "get", "search", "count", "exists")):
            lines.append(f"    {ret} {name}({params});")
        elif name.startswith(("save", "update", "delete")):
            lines.append(f"    // Inherited from base: {name}")

    return "\n\n".join(lines) if lines else "    // Custom queries will be added during migration"


def _guess_entity_type(legacy: dict) -> str:
    """Guess the entity type from a DAO's dependencies or naming."""
    simple = legacy.get("simple_name", "")
    for suffix in ("Dao", "DAO", "Repository", "Repo"):
        if simple.endswith(suffix):
            return simple[: -len(suffix)]
    return "Object"


def _method_to_event_name(method_name: str, class_name: str) -> str:
    """Convert method name to domain event name."""
    clean = class_name.replace("Service", "").replace("Impl", "").replace("Bean", "")
    verbs = {
        "create": "Created", "save": "Saved", "update": "Updated",
        "delete": "Deleted", "process": "Processed", "submit": "Submitted",
        "cancel": "Cancelled", "approve": "Approved", "reject": "Rejected",
        "complete": "Completed", "assign": "Assigned",
    }
    for verb, past in verbs.items():
        if method_name.lower().startswith(verb):
            entity = method_name[len(verb):] or clean
            return f"{entity}{past}Event"
    return f"{clean}{method_name.capitalize()}Event"


@mcp.tool()
def generate_project_migration(
    project_name: str,
    target_package: str = "",
    output_dir: str = "",
    layers: str = "DAO,SERVICE",
    dry_run: bool = True,
) -> dict:
    """
    Generate migration code for ALL classes in specific layers of a project.

    Args:
        project_name:   Legacy project in KB
        target_package: Base target package
        output_dir:     Output directory for generated files
        layers:         Comma-separated layers to migrate (DAO,SERVICE,CONTROLLER,MESSAGING)
        dry_run:        If True, return summary without writing files

    Returns:
        Summary of all generated files.
    """
    classes = _load_kb_project(project_name)
    if not classes:
        return {"error": f"Project '{project_name}' not found in KB."}

    target_layers = {l.strip().upper() for l in layers.split(",")}
    results = []
    total_files = 0

    for fqcn, cls_data in classes.items():
        if cls_data.get("layer") not in target_layers:
            continue

        pkg = target_package or cls_data.get("package", "com.company.migrated")

        result = generate_migration(
            project_name=project_name,
            class_name=fqcn,
            target_package=pkg,
            output_dir=output_dir,
            dry_run=dry_run,
        )

        files_gen = result.get("files_generated", [])
        total_files += len(files_gen)
        results.append({
            "legacy_class": fqcn,
            "layer": cls_data.get("layer"),
            "rules_applied": result.get("rules_applied", []),
            "files_generated": len(files_gen),
        })

    return {
        "project": project_name,
        "layers_migrated": list(target_layers),
        "classes_processed": len(results),
        "total_files_generated": total_files,
        "dry_run": dry_run,
        "details": results,
    }


@mcp.tool()
def preview_mapping(project_name: str, class_name: str) -> dict:
    """
    Preview what mapping rules will apply to a legacy class WITHOUT generating code.
    Useful for validating rules before bulk generation.

    Args:
        project_name: Legacy project in KB
        class_name:   Class to preview
    """
    classes = _load_kb_project(project_name)
    legacy_class = None
    for fqcn, cls in classes.items():
        if fqcn == class_name or cls.get("simple_name") == class_name:
            legacy_class = cls
            legacy_class["fqcn"] = fqcn
            break

    if not legacy_class:
        return {"error": f"'{class_name}' not found in '{project_name}'."}

    matched = _match_rules(legacy_class)

    return {
        "legacy_class": {
            "fqcn": legacy_class["fqcn"],
            "layer": legacy_class.get("layer"),
            "stereotype": legacy_class.get("stereotype"),
            "superclass": legacy_class.get("superclass"),
            "interfaces": legacy_class.get("interfaces"),
            "annotations": legacy_class.get("annotations"),
            "dependencies": legacy_class.get("all_dependency_types"),
            "methods": [m["name"] for m in legacy_class.get("public_methods", [])],
        },
        "matched_rules": [
            {
                "rule_id": r.rule_id,
                "description": r.description,
                "legacy_match": r.legacy_match,
                "target_transform": r.target_transform,
                "template": r.template_name,
            }
            for r in matched
        ],
        "unmatched": len(matched) == 0,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("codegen://status")
def codegen_status() -> str:
    """Code generator status."""
    return (
        f"Migration Code Generator\n"
        f"========================\n"
        f"Target Framework: {registry.framework_name or '(not scanned)'}\n"
        f"Framework Classes: {len(registry.framework_classes)}\n"
        f"Mapping Rules: {len(registry.mapping_rules)}\n"
        f"\nRules:\n"
        + "\n".join(
            f"  [{r.priority:3d}] {r.rule_id}: {r.description}"
            for r in sorted(registry.mapping_rules.values(), key=lambda r: r.priority)
        )
    )


@mcp.resource("codegen://templates")
def list_templates() -> str:
    """List available code templates."""
    lines = ["Available Code Templates", "=" * 30]
    for name, template in CODE_TEMPLATES.items():
        first_lines = template.strip().splitlines()[:3]
        lines.append(f"\n{name}:")
        for fl in first_lines:
            lines.append(f"  {fl}")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    mcp.run()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Configuration — THE COMPLETE STACK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# .vscode/mcp.json:
# {
#   "servers": {
#     "migration-kb": {
#       "command": "python",
#       "args": ["/path/to/migration_kb_mcp_server.py"]
#     },
#     "migration-codegen": {
#       "command": "python",
#       "args": ["/path/to/migration_codegen_mcp_server.py"]
#     },
#     "spring-scanner": {
#       "command": "python",
#       "args": ["/path/to/springboot_scanner_mcp_server.py"]
#     },
#     "jar-scanner": {
#       "command": "python",
#       "args": ["/path/to/jar_scanner_mcp_server.py"]
#     }
#   }
# }
