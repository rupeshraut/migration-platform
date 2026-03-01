"""
Spring Boot Legacy Application Scanner MCP Server
===================================================
Scans Spring Boot source code to extract bean metadata, dependency injection
graphs, DAO-to-Service relationships, configuration analysis, and generates
migration plans for modernizing to event-driven architecture.

Designed for: Spring Boot 1.x/2.x → Spring Boot 3.x + Event-Driven Migration

Approach:
  - AST-level Java source parsing via regex + structural analysis
  - Spring annotation scanning (@Service, @Repository, @Autowired, etc.)
  - application.yml / application.properties parsing
  - pom.xml / build.gradle dependency extraction
  - Constructor injection & field injection graph building

Requirements:
    pip install fastmcp pyyaml

Usage:
    python springboot_scanner_mcp_server.py
    fastmcp dev springboot_scanner_mcp_server.py
"""

import json
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml
from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp = FastMCP("Spring Boot Legacy Scanner")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class SpringBeanInfo:
    """Metadata for a Spring-managed bean discovered from source code."""

    fqcn: str  # Fully qualified class name
    simple_name: str = ""
    package: str = ""
    file_path: str = ""
    stereotype: str = ""  # @Service, @Repository, @Component, @Controller, etc.
    layer: str = "UNKNOWN"  # SERVICE, DAO, CONTROLLER, ENTITY, CONFIG, MESSAGING
    scope: str = "singleton"  # singleton, prototype, request, session

    # Class structure
    superclass: str = ""
    interfaces: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)

    # Dependency injection
    constructor_deps: list[dict] = field(default_factory=list)  # Constructor injection params
    field_deps: list[dict] = field(default_factory=list)  # @Autowired field injection
    setter_deps: list[dict] = field(default_factory=list)  # @Autowired setter injection
    all_dependencies: list[str] = field(default_factory=list)  # Unified dep list

    # Methods
    public_methods: list[dict] = field(default_factory=list)
    request_mappings: list[dict] = field(default_factory=list)  # REST endpoints
    scheduled_methods: list[str] = field(default_factory=list)  # @Scheduled
    event_listeners: list[str] = field(default_factory=list)  # @EventListener
    transactional_methods: list[str] = field(default_factory=list)  # @Transactional
    async_methods: list[str] = field(default_factory=list)  # @Async

    # Migration markers
    uses_deprecated_api: list[str] = field(default_factory=list)
    javax_imports: list[str] = field(default_factory=list)  # javax → jakarta migration
    migration_notes: list[str] = field(default_factory=list)


@dataclass
class ConfigProperty:
    """A configuration property from application.yml/properties."""

    key: str
    value: str = ""
    profile: str = "default"
    source_file: str = ""
    category: str = ""  # datasource, kafka, jms, server, custom


@dataclass
class MavenDependency:
    """A Maven/Gradle dependency."""

    group_id: str
    artifact_id: str
    version: str = ""
    scope: str = "compile"
    needs_upgrade: bool = False
    replacement: str = ""  # For deprecated/removed dependencies


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class SpringRegistry:
    """Session-scoped registry of all discovered Spring beans and config."""

    def __init__(self):
        self.project_root: str = ""
        self.beans: dict[str, SpringBeanInfo] = {}  # FQCN → BeanInfo
        self.configs: list[ConfigProperty] = []
        self.dependencies: list[MavenDependency] = []
        self.spring_boot_version: str = ""
        self.java_version: str = ""
        self.build_tool: str = ""  # maven or gradle

    def reset(self):
        self.__init__()


registry = SpringRegistry()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core: Java Source Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Spring stereotype annotations that mark a class as a managed bean
SPRING_STEREOTYPES = {
    "@Service": "SERVICE",
    "@Repository": "DAO",
    "@Component": "COMPONENT",
    "@Controller": "CONTROLLER",
    "@RestController": "CONTROLLER",
    "@Configuration": "CONFIG",
    "@ControllerAdvice": "CONTROLLER",
    "@RestControllerAdvice": "CONTROLLER",
}

# Annotations that indicate specific patterns
PATTERN_ANNOTATIONS = {
    "@KafkaListener": "MESSAGING",
    "@JmsListener": "MESSAGING",
    "@RabbitListener": "MESSAGING",
    "@StreamListener": "MESSAGING",
    "@EventListener": "MESSAGING",
    "@Scheduled": "SCHEDULED",
    "@Async": "ASYNC",
    "@Transactional": "TRANSACTIONAL",
    "@Cacheable": "CACHED",
    "@Entity": "ENTITY",
    "@Document": "ENTITY",
    "@Table": "ENTITY",
}

# Deprecated/removed APIs in Spring Boot 3.x
JAVAX_TO_JAKARTA = {
    "javax.persistence": "jakarta.persistence",
    "javax.validation": "jakarta.validation",
    "javax.servlet": "jakarta.servlet",
    "javax.annotation": "jakarta.annotation",
    "javax.transaction": "jakarta.transaction",
    "javax.mail": "jakarta.mail",
    "javax.jms": "jakarta.jms",
    "javax.websocket": "jakarta.websocket",
    "javax.xml.bind": "jakarta.xml.bind (or remove JAXB)",
    "javax.inject": "jakarta.inject",
    "javax.enterprise": "jakarta.enterprise",
}


def _parse_java_file(file_path: str) -> Optional[SpringBeanInfo]:
    """
    Parse a Java source file and extract Spring bean metadata.

    Handles:
      - Package declaration
      - Import statements (javax → jakarta detection)
      - Class-level annotations (stereotypes, scopes)
      - Constructor injection (primary constructor with params)
      - Field injection (@Autowired, @Inject, @Value)
      - Method annotations (@RequestMapping, @Transactional, @Scheduled, etc.)
      - Superclass and interface declarations
    """
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    # Skip non-class files (package-info, module-info)
    if "package-info" in file_path or "module-info" in file_path:
        return None

    info = SpringBeanInfo(fqcn="", file_path=file_path)

    # ── Package ──
    pkg_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
    info.package = pkg_match.group(1) if pkg_match else ""

    # ── Imports (javax detection) ──
    imports = re.findall(r"^import\s+([\w.*]+)\s*;", content, re.MULTILINE)
    for imp in imports:
        for javax_pkg, jakarta_pkg in JAVAX_TO_JAKARTA.items():
            if imp.startswith(javax_pkg):
                info.javax_imports.append(imp)
                info.migration_notes.append(
                    f"Migrate: {imp} → {jakarta_pkg}"
                )

    # ── Class-level annotations ──
    # Capture all annotations before class declaration
    # Pattern: everything from first annotation to class keyword
    class_region = re.search(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+)?"
        r"(?:abstract\s+|final\s+)?"
        r"(?:class|interface|enum)\s+(\w+)"
        r"(?:<[^>]+>)?"  # generics
        r"(?:\s+extends\s+([\w.<>,\s]+))?"
        r"(?:\s+implements\s+([\w.<>,\s]+))?",
        content,
    )

    if not class_region:
        return None

    annotation_block = class_region.group(1) or ""
    info.simple_name = class_region.group(2)
    info.fqcn = f"{info.package}.{info.simple_name}" if info.package else info.simple_name

    if class_region.group(3):
        info.superclass = class_region.group(3).strip().split("<")[0].strip()

    if class_region.group(4):
        raw_interfaces = class_region.group(4)
        info.interfaces = [
            iface.strip().split("<")[0].strip()
            for iface in raw_interfaces.split(",")
        ]

    # Parse class-level annotations
    class_annotations = re.findall(r"@(\w+)(?:\(([^)]*)\))?", annotation_block)
    for ann_name, ann_params in class_annotations:
        full_ann = f"@{ann_name}" + (f"({ann_params})" if ann_params else "")
        info.annotations.append(full_ann)

        # Classify stereotype
        ann_key = f"@{ann_name}"
        if ann_key in SPRING_STEREOTYPES:
            info.stereotype = ann_key
            info.layer = SPRING_STEREOTYPES[ann_key]

        # Scope detection
        if ann_name == "Scope":
            scope_match = re.search(r'"(\w+)"', ann_params or "")
            if scope_match:
                info.scope = scope_match.group(1)
        if ann_name == "RequestScope":
            info.scope = "request"
        if ann_name == "SessionScope":
            info.scope = "session"
        if ann_name == "Prototype":
            info.scope = "prototype"

    # ── Layer refinement from package naming ──
    if info.layer in ("UNKNOWN", "COMPONENT"):
        info.layer = _classify_layer_from_source(info)

    # ── Constructor Injection ──
    # Find constructors: public ClassName(Type1 param1, Type2 param2)
    # Also handle @RequiredArgsConstructor (Lombok) → all final fields are injected
    has_lombok_constructor = "@RequiredArgsConstructor" in content or "@AllArgsConstructor" in content

    constructor_pattern = re.findall(
        rf"(?:@Autowired\s+)?(?:public\s+)?{re.escape(info.simple_name)}\s*\(([^)]*)\)",
        content,
    )
    for params_str in constructor_pattern:
        if params_str.strip():
            for param in params_str.split(","):
                param = param.strip()
                if param:
                    parts = param.split()
                    if len(parts) >= 2:
                        param_type = parts[-2].split("<")[0]  # Remove generics
                        param_name = parts[-1]
                        info.constructor_deps.append({
                            "type": param_type,
                            "name": param_name,
                            "injection": "CONSTRUCTOR",
                        })
                        if param_type[0].isupper() and param_type not in _JAVA_BUILTINS:
                            info.all_dependencies.append(param_type)

    # ── Field Injection (@Autowired, @Inject, @Value, @Resource) ──
    field_injection_pattern = re.findall(
        r"(?:@Autowired|@Inject|@Resource)(?:\s*\([^)]*\))?\s*"
        r"(?:@Qualifier\s*\(\s*\"([^\"]*)\"\s*\)\s*)?"  # optional @Qualifier
        r"(?:@Lazy\s*)?"
        r"(?:private|protected|public)?\s+"
        r"(?:final\s+)?"
        r"([\w<>,.\[\]\s?]+?)\s+(\w+)\s*;",
        content,
    )
    for qualifier, field_type, field_name in field_injection_pattern:
        clean_type = field_type.strip().split("<")[0]
        info.field_deps.append({
            "type": clean_type,
            "name": field_name,
            "qualifier": qualifier or "",
            "injection": "FIELD",
        })
        if clean_type[0].isupper() and clean_type not in _JAVA_BUILTINS:
            info.all_dependencies.append(clean_type)

    # ── Lombok @RequiredArgsConstructor final fields ──
    if has_lombok_constructor:
        final_fields = re.findall(
            r"(?:private|protected)\s+final\s+([\w<>,.\[\]\s?]+?)\s+(\w+)\s*;",
            content,
        )
        for field_type, field_name in final_fields:
            clean_type = field_type.strip().split("<")[0]
            # Avoid duplicates if already captured by @Autowired
            existing_names = [d["name"] for d in info.constructor_deps + info.field_deps]
            if field_name not in existing_names:
                info.constructor_deps.append({
                    "type": clean_type,
                    "name": field_name,
                    "injection": "LOMBOK_CONSTRUCTOR",
                })
                if clean_type[0].isupper() and clean_type not in _JAVA_BUILTINS:
                    info.all_dependencies.append(clean_type)

    # ── @Value injection ──
    value_injections = re.findall(
        r'@Value\s*\(\s*["\']([^"\']+)["\']\s*\)\s*'
        r"(?:private|protected|public)?\s+"
        r"([\w<>,.\s]+?)\s+(\w+)\s*;",
        content,
    )
    for value_expr, field_type, field_name in value_injections:
        info.field_deps.append({
            "type": field_type.strip(),
            "name": field_name,
            "injection": "VALUE",
            "expression": value_expr,
        })

    # ── Public methods ──
    method_pattern = re.finditer(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"  # annotations
        r"public\s+"
        r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"([\w<>,.\[\]\s?]+?)\s+(\w+)\s*\(([^)]*)\)",
        content,
    )
    for m in method_pattern:
        method_annotations_block = m.group(1) or ""
        return_type = m.group(2).strip()
        method_name = m.group(3)
        params = m.group(4).strip()

        method_anns = re.findall(r"@(\w+)(?:\(([^)]*)\))?", method_annotations_block)
        ann_names = [f"@{a[0]}" for a in method_anns]

        info.public_methods.append({
            "name": method_name,
            "return_type": return_type,
            "parameters": params,
            "annotations": ann_names,
        })

        # Request mappings
        for ann_name, ann_params in method_anns:
            if ann_name in ("GetMapping", "PostMapping", "PutMapping",
                            "DeleteMapping", "PatchMapping", "RequestMapping"):
                path = ""
                path_match = re.search(r'["\']([^"\']*)["\']', ann_params or "")
                if path_match:
                    path = path_match.group(1)
                info.request_mappings.append({
                    "method": ann_name.replace("Mapping", "").upper()
                    if ann_name != "RequestMapping" else "ANY",
                    "path": path,
                    "handler": method_name,
                })

            if ann_name == "Scheduled":
                info.scheduled_methods.append(method_name)

            if ann_name == "EventListener":
                info.event_listeners.append(method_name)

            if ann_name == "Transactional":
                info.transactional_methods.append(method_name)

            if ann_name == "Async":
                info.async_methods.append(method_name)

    # ── Deprecated API usage ──
    deprecated_patterns = {
        "WebMvcConfigurerAdapter": "Extend WebMvcConfigurer interface instead",
        "SpringBootServletInitializer": "Check if still needed for Boot 3.x",
        "RestTemplate": "Consider migrating to WebClient (reactive) or RestClient (Boot 3.2+)",
        "StringUtils.isEmpty": "Use ObjectUtils.isEmpty or hasText()",
        "org.springframework.data.repository.CrudRepository": "Consider ReactiveCrudRepository for event-driven",
        "JdbcTemplate": "Consider Spring Data JPA/MongoDB Repository",
        "HibernateCallback": "Removed in Spring 6",
        "extends WebSecurityConfigurerAdapter": "Use SecurityFilterChain @Bean instead",
    }
    for pattern, note in deprecated_patterns.items():
        if pattern in content:
            info.uses_deprecated_api.append(f"{pattern}: {note}")

    # Deduplicate dependencies
    info.all_dependencies = list(set(info.all_dependencies))

    return info


_JAVA_BUILTINS = frozenset({
    "String", "Integer", "Long", "Boolean", "Double", "Float", "Short", "Byte",
    "Character", "Object", "List", "Map", "Set", "Collection", "Optional",
    "Stream", "Void", "void", "int", "long", "boolean", "double", "float",
    "BigDecimal", "BigInteger", "Date", "LocalDate", "LocalDateTime",
    "Instant", "Duration", "UUID", "URI", "URL", "Class", "Logger",
    "Pageable", "Page", "Sort", "ResponseEntity", "HttpServletRequest",
    "HttpServletResponse", "MultipartFile", "BindingResult", "Model",
    "HttpEntity", "RequestEntity", "CompletableFuture",
})


def _classify_layer_from_source(info: SpringBeanInfo) -> str:
    """Classify layer using package names and interface patterns."""
    name_lower = info.fqcn.lower()
    ifaces_lower = " ".join(info.interfaces).lower()

    if any(s in name_lower for s in [".dao.", ".repository.", "repository", "daoimpl"]):
        return "DAO"
    if any(s in name_lower for s in [".service.", "serviceimpl"]):
        return "SERVICE"
    if any(s in name_lower for s in [".controller.", ".rest.", ".api.", ".web.", ".resource."]):
        return "CONTROLLER"
    if any(s in name_lower for s in [".entity.", ".model.", ".domain.", ".dto.", ".vo."]):
        return "ENTITY"
    if any(s in name_lower for s in [".listener.", ".consumer.", ".handler.", ".messaging."]):
        return "MESSAGING"
    if any(s in name_lower for s in [".config.", ".configuration."]):
        return "CONFIG"
    if any(s in name_lower for s in [".util.", ".helper.", ".common.", ".support."]):
        return "INFRASTRUCTURE"
    if any(s in name_lower for s in [".mapper.", ".converter.", ".transformer."]):
        return "MAPPER"
    if any(s in name_lower for s in [".security.", ".auth."]):
        return "SECURITY"
    if any(s in name_lower for s in [".exception.", ".error."]):
        return "EXCEPTION"

    # Interface-based
    if any(s in ifaces_lower for s in ["jparepository", "crudrepository", "mongorepository",
                                        "pagingandsortingrepository", "reactivecrudrepository"]):
        return "DAO"

    return "UNKNOWN"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Config Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _parse_yaml_config(file_path: str) -> list[ConfigProperty]:
    """Parse application.yml into flat key-value ConfigProperty list."""
    props = []
    profile = "default"

    # Detect profile from filename: application-dev.yml → dev
    fname = Path(file_path).stem
    if "-" in fname:
        profile = fname.split("-", 1)[1]

    try:
        with open(file_path, "r") as f:
            # Handle multi-document YAML (--- separator)
            docs = list(yaml.safe_load_all(f))
            for doc in docs:
                if doc:
                    _flatten_yaml(doc, "", props, profile, file_path)
    except Exception as e:
        props.append(ConfigProperty(key="ERROR", value=str(e), source_file=file_path))

    return props


def _flatten_yaml(
    data: dict, prefix: str, props: list[ConfigProperty],
    profile: str, source_file: str
):
    """Recursively flatten YAML dict into dot-notation keys."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            _flatten_yaml(value, full_key, props, profile, source_file)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    _flatten_yaml(item, f"{full_key}[{i}]", props, profile, source_file)
                else:
                    props.append(ConfigProperty(
                        key=f"{full_key}[{i}]",
                        value=str(item),
                        profile=profile,
                        source_file=source_file,
                        category=_categorize_config(full_key),
                    ))
        else:
            props.append(ConfigProperty(
                key=full_key,
                value=str(value) if value is not None else "",
                profile=profile,
                source_file=source_file,
                category=_categorize_config(full_key),
            ))


def _parse_properties_config(file_path: str) -> list[ConfigProperty]:
    """Parse application.properties into ConfigProperty list."""
    props = []
    profile = "default"
    fname = Path(file_path).stem
    if "-" in fname:
        profile = fname.split("-", 1)[1]

    try:
        for line in Path(file_path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                props.append(ConfigProperty(
                    key=key.strip(),
                    value=value.strip(),
                    profile=profile,
                    source_file=file_path,
                    category=_categorize_config(key.strip()),
                ))
    except Exception:
        pass
    return props


def _categorize_config(key: str) -> str:
    """Categorize a config property key."""
    key_lower = key.lower()
    if "datasource" in key_lower or "jpa" in key_lower or "hibernate" in key_lower:
        return "DATABASE"
    if "kafka" in key_lower:
        return "KAFKA"
    if "jms" in key_lower or "activemq" in key_lower or "rabbitmq" in key_lower:
        return "MESSAGING"
    if "redis" in key_lower or "cache" in key_lower:
        return "CACHE"
    if "server." in key_lower or "management." in key_lower:
        return "SERVER"
    if "security" in key_lower or "oauth" in key_lower or "jwt" in key_lower:
        return "SECURITY"
    if "logging" in key_lower or "log." in key_lower:
        return "LOGGING"
    if "spring.mail" in key_lower:
        return "MAIL"
    if "actuator" in key_lower or "endpoints" in key_lower:
        return "ACTUATOR"
    return "APPLICATION"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Build File Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _parse_pom_xml(file_path: str) -> tuple[list[MavenDependency], str, str]:
    """Parse pom.xml for dependencies and Spring Boot version."""
    deps = []
    boot_version = ""
    java_version = ""

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        # Handle Maven namespaces
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}

        # Spring Boot version from parent
        parent = root.find("m:parent", ns) or root.find("parent")
        if parent is not None:
            art = parent.find("m:artifactId", ns) or parent.find("artifactId")
            ver = parent.find("m:version", ns) or parent.find("version")
            if art is not None and "spring-boot" in (art.text or ""):
                boot_version = ver.text if ver is not None else ""

        # Properties
        props_elem = root.find("m:properties", ns) or root.find("properties")
        if props_elem is not None:
            for prop in props_elem:
                tag = prop.tag.split("}")[-1] if "}" in prop.tag else prop.tag
                if tag == "java.version" or tag == "maven.compiler.source":
                    java_version = prop.text or ""

        # Dependencies
        deps_elem = root.find("m:dependencies", ns) or root.find("dependencies")
        if deps_elem is not None:
            for dep_elem in deps_elem:
                gid = dep_elem.find("m:groupId", ns) or dep_elem.find("groupId")
                aid = dep_elem.find("m:artifactId", ns) or dep_elem.find("artifactId")
                ver = dep_elem.find("m:version", ns) or dep_elem.find("version")
                scp = dep_elem.find("m:scope", ns) or dep_elem.find("scope")

                if gid is not None and aid is not None:
                    dep = MavenDependency(
                        group_id=gid.text or "",
                        artifact_id=aid.text or "",
                        version=ver.text if ver is not None else "",
                        scope=scp.text if scp is not None else "compile",
                    )
                    _check_dependency_migration(dep)
                    deps.append(dep)
    except Exception:
        pass

    return deps, boot_version, java_version


def _parse_build_gradle(file_path: str) -> tuple[list[MavenDependency], str, str]:
    """Parse build.gradle for dependencies and Spring Boot version."""
    deps = []
    boot_version = ""
    java_version = ""

    try:
        content = Path(file_path).read_text()

        # Spring Boot plugin version
        boot_match = re.search(
            r"org\.springframework\.boot['\"]?\s*version\s*['\"]?([\d.]+)",
            content,
        )
        if boot_match:
            boot_version = boot_match.group(1)

        # Java version
        java_match = re.search(r"sourceCompatibility\s*=\s*['\"]?(\d+)", content)
        if java_match:
            java_version = java_match.group(1)

        # Dependencies
        dep_pattern = re.findall(
            r"(implementation|compileOnly|runtimeOnly|testImplementation|api)\s+"
            r"['\"]([^:]+):([^:]+)(?::([^'\"]+))?['\"]",
            content,
        )
        for scope, gid, aid, ver in dep_pattern:
            dep = MavenDependency(
                group_id=gid, artifact_id=aid,
                version=ver or "", scope=scope,
            )
            _check_dependency_migration(dep)
            deps.append(dep)
    except Exception:
        pass

    return deps, boot_version, java_version


# Known dependency migrations for Spring Boot 3.x
_DEPENDENCY_MIGRATIONS = {
    "springfox-swagger2": ("springdoc-openapi-starter-webmvc-ui", "Springfox → SpringDoc"),
    "springfox-swagger-ui": ("springdoc-openapi-starter-webmvc-ui", "Springfox → SpringDoc"),
    "javax.validation:validation-api": ("jakarta.validation:jakarta.validation-api", "javax → jakarta"),
    "javax.persistence:javax.persistence-api": ("jakarta.persistence:jakarta.persistence-api", "javax → jakarta"),
    "spring-boot-starter-data-redis": ("", "Check Lettuce vs Jedis config changes"),
    "spring-cloud-starter-netflix-eureka-client": ("spring-cloud-starter-netflix-eureka-client", "Verify Spring Cloud version compatibility"),
    "spring-boot-starter-security": ("", "WebSecurityConfigurerAdapter removed in Boot 3.x"),
}


def _check_dependency_migration(dep: MavenDependency):
    """Flag dependencies that need migration for Spring Boot 3.x."""
    key = dep.artifact_id
    full_key = f"{dep.group_id}:{dep.artifact_id}"

    if key in _DEPENDENCY_MIGRATIONS:
        dep.needs_upgrade = True
        dep.replacement = _DEPENDENCY_MIGRATIONS[key][1]
    elif full_key in _DEPENDENCY_MIGRATIONS:
        dep.needs_upgrade = True
        dep.replacement = _DEPENDENCY_MIGRATIONS[full_key][1]

    if dep.group_id.startswith("javax."):
        dep.needs_upgrade = True
        dep.replacement = f"Migrate to jakarta.* equivalent"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def scan_spring_project(project_path: str) -> dict:
    """
    Full scan of a Spring Boot project: source code, configuration, and dependencies.

    Args:
        project_path: Root directory of the Spring Boot project (contains pom.xml or build.gradle).

    Returns:
        Comprehensive project summary with bean counts, layer breakdown,
        Spring Boot version, and migration readiness assessment.
    """
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        return {"error": f"Directory not found: {project_path}"}

    registry.reset()
    registry.project_root = project_path

    # ── Step 1: Parse build file ──
    pom = os.path.join(project_path, "pom.xml")
    gradle = os.path.join(project_path, "build.gradle")
    gradle_kts = os.path.join(project_path, "build.gradle.kts")

    if os.path.isfile(pom):
        registry.dependencies, registry.spring_boot_version, registry.java_version = _parse_pom_xml(pom)
        registry.build_tool = "maven"
    elif os.path.isfile(gradle):
        registry.dependencies, registry.spring_boot_version, registry.java_version = _parse_build_gradle(gradle)
        registry.build_tool = "gradle"
    elif os.path.isfile(gradle_kts):
        registry.dependencies, registry.spring_boot_version, registry.java_version = _parse_build_gradle(gradle_kts)
        registry.build_tool = "gradle-kts"

    # ── Step 2: Parse all Java source files ──
    java_files = list(Path(project_path).rglob("*.java"))
    # Exclude test files
    java_files = [f for f in java_files if "/test/" not in str(f) and "\\test\\" not in str(f)]

    layer_counts = defaultdict(int)
    for java_file in java_files:
        bean_info = _parse_java_file(str(java_file))
        if bean_info and bean_info.fqcn:
            registry.beans[bean_info.fqcn] = bean_info
            layer_counts[bean_info.layer] += 1

    # ── Step 3: Parse configuration files ──
    config_patterns = [
        "application.yml", "application.yaml",
        "application-*.yml", "application-*.yaml",
        "application.properties", "application-*.properties",
        "bootstrap.yml", "bootstrap.yaml",
    ]
    for pattern in config_patterns:
        for config_file in Path(project_path).rglob(pattern):
            if "/test/" in str(config_file) or "\\test\\" in str(config_file):
                continue
            if str(config_file).endswith((".yml", ".yaml")):
                registry.configs.extend(_parse_yaml_config(str(config_file)))
            else:
                registry.configs.extend(_parse_properties_config(str(config_file)))

    # ── Step 4: Migration readiness assessment ──
    javax_count = sum(len(b.javax_imports) for b in registry.beans.values())
    deprecated_count = sum(len(b.uses_deprecated_api) for b in registry.beans.values())
    deps_needing_upgrade = sum(1 for d in registry.dependencies if d.needs_upgrade)

    boot_major = int(registry.spring_boot_version.split(".")[0]) if registry.spring_boot_version else 0

    return {
        "project": project_path,
        "spring_boot_version": registry.spring_boot_version or "unknown",
        "java_version": registry.java_version or "unknown",
        "build_tool": registry.build_tool,
        "source_files_scanned": len(java_files),
        "beans_discovered": len(registry.beans),
        "layer_breakdown": dict(layer_counts),
        "config_properties": len(registry.configs),
        "maven_dependencies": len(registry.dependencies),
        "migration_readiness": {
            "javax_imports_to_migrate": javax_count,
            "deprecated_api_usage": deprecated_count,
            "dependencies_needing_upgrade": deps_needing_upgrade,
            "needs_boot_3_migration": boot_major < 3,
            "overall_effort": (
                "HIGH" if javax_count > 20 or deprecated_count > 10
                else "MEDIUM" if javax_count > 5 or deprecated_count > 3
                else "LOW"
            ),
        },
    }


@mcp.tool()
def find_dao_service_relationships() -> dict:
    """
    Discover all DAO-to-Service layer dependency injection relationships.

    Traces:
      - Constructor injection (including Lombok @RequiredArgsConstructor)
      - Field injection (@Autowired)
      - @Value property injection
      - Interface-based wiring (Service → Repository interface)

    Returns:
        Detailed dependency map showing how Services depend on DAOs.
    """
    relationships = []

    dao_beans = {
        fqcn: info for fqcn, info in registry.beans.items()
        if info.layer == "DAO"
    }
    dao_by_simple_name = {info.simple_name: fqcn for fqcn, info in dao_beans.items()}
    # Also map interface names (e.g., OrderRepository interface)
    dao_by_interface = {}
    for fqcn, info in dao_beans.items():
        for iface in info.interfaces:
            dao_by_interface[iface] = fqcn

    for fqcn, info in registry.beans.items():
        if info.layer != "SERVICE":
            continue

        # Check all injection points
        all_injection_points = (
            info.constructor_deps + info.field_deps + info.setter_deps
        )

        for dep in all_injection_points:
            dep_type = dep["type"]
            resolved_dao = None
            resolution_method = ""

            # 1. Match by simple name to known DAO beans
            if dep_type in dao_by_simple_name:
                resolved_dao = dao_by_simple_name[dep_type]
                resolution_method = "SIMPLE_NAME_MATCH"

            # 2. Match by FQCN
            elif dep_type in dao_beans:
                resolved_dao = dep_type
                resolution_method = "FQCN_MATCH"

            # 3. Match by interface name (e.g., JpaRepository subtype)
            elif dep_type in dao_by_interface:
                resolved_dao = dao_by_interface[dep_type]
                resolution_method = "INTERFACE_MATCH"

            # 4. Suffix matching (OrderRepository → likely a DAO)
            elif dep_type.endswith(("Repository", "Dao", "DAO")):
                # Search for it
                matches = [
                    dao_fqcn for dao_fqcn, dao_info in dao_beans.items()
                    if dao_info.simple_name == dep_type
                ]
                if matches:
                    resolved_dao = matches[0]
                    resolution_method = "SUFFIX_MATCH"

            if resolved_dao:
                dao_info = registry.beans[resolved_dao]
                relationships.append({
                    "service_class": fqcn,
                    "service_simple_name": info.simple_name,
                    "dao_class": resolved_dao,
                    "dao_simple_name": dao_info.simple_name,
                    "injection_type": dep.get("injection", "UNKNOWN"),
                    "field_name": dep.get("name", ""),
                    "qualifier": dep.get("qualifier", ""),
                    "resolution": resolution_method,
                    "service_file": info.file_path,
                    "dao_file": dao_info.file_path,
                })

    # Summary
    services_with_daos = set(r["service_class"] for r in relationships)
    daos_used = set(r["dao_class"] for r in relationships)
    orphan_daos = [
        fqcn for fqcn in dao_beans
        if fqcn not in daos_used
    ]

    return {
        "total_relationships": len(relationships),
        "services_using_daos": len(services_with_daos),
        "daos_referenced": len(daos_used),
        "orphan_daos": orphan_daos,
        "relationships": relationships,
        "injection_breakdown": {
            "constructor": len([r for r in relationships if r["injection_type"] in ("CONSTRUCTOR", "LOMBOK_CONSTRUCTOR")]),
            "field": len([r for r in relationships if r["injection_type"] == "FIELD"]),
            "value": len([r for r in relationships if r["injection_type"] == "VALUE"]),
        },
    }


@mcp.tool()
def find_layer_dependencies(
    source_layer: str = "SERVICE",
    target_layer: str = "DAO",
) -> dict:
    """
    Find all injection dependencies between any two architectural layers.

    Args:
        source_layer: CONTROLLER, SERVICE, MESSAGING, CONFIG, etc.
        target_layer: DAO, SERVICE, ENTITY, etc.
    """
    source_layer = source_layer.upper()
    target_layer = target_layer.upper()

    target_beans = {
        info.simple_name: fqcn
        for fqcn, info in registry.beans.items()
        if info.layer == target_layer
    }

    relationships = []
    for fqcn, info in registry.beans.items():
        if info.layer != source_layer:
            continue
        for dep in info.constructor_deps + info.field_deps:
            dep_type = dep["type"]
            if dep_type in target_beans:
                relationships.append({
                    "source": fqcn,
                    "target": target_beans[dep_type],
                    "injection": dep.get("injection", ""),
                    "field": dep.get("name", ""),
                })

    return {
        "source_layer": source_layer,
        "target_layer": target_layer,
        "count": len(relationships),
        "relationships": relationships,
    }


@mcp.tool()
def get_bean_info(class_name: str) -> dict:
    """
    Get full metadata for a Spring bean by class name.

    Args:
        class_name: FQCN or simple class name.
    """
    if class_name in registry.beans:
        return asdict(registry.beans[class_name])

    matches = [
        fqcn for fqcn in registry.beans
        if fqcn.endswith(f".{class_name}") or class_name in fqcn
    ]
    if len(matches) == 1:
        return asdict(registry.beans[matches[0]])
    elif matches:
        return {"message": "Multiple matches", "matches": matches}
    return {"error": f"Bean '{class_name}' not found. Run scan_spring_project first."}


@mcp.tool()
def find_beans_by_layer(layer: str) -> dict:
    """
    List all Spring beans in a specific layer.

    Args:
        layer: SERVICE, DAO, CONTROLLER, ENTITY, MESSAGING, CONFIG, SECURITY, etc.
    """
    layer = layer.upper()
    beans = [
        {
            "fqcn": fqcn,
            "simple_name": info.simple_name,
            "stereotype": info.stereotype,
            "scope": info.scope,
            "dependency_count": len(info.constructor_deps) + len(info.field_deps),
            "method_count": len(info.public_methods),
            "has_transactional": len(info.transactional_methods) > 0,
            "has_async": len(info.async_methods) > 0,
            "javax_imports": len(info.javax_imports),
        }
        for fqcn, info in registry.beans.items()
        if info.layer == layer
    ]
    return {"layer": layer, "count": len(beans), "beans": sorted(beans, key=lambda b: b["fqcn"])}


@mcp.tool()
def analyze_rest_endpoints() -> dict:
    """
    Extract all REST API endpoints from the scanned project.

    Returns:
        All endpoints grouped by controller with HTTP method, path, and handler.
    """
    controllers = {}
    for fqcn, info in registry.beans.items():
        if not info.request_mappings:
            continue

        # Get class-level @RequestMapping prefix
        class_prefix = ""
        for ann in info.annotations:
            if ann.startswith("@RequestMapping"):
                path_match = re.search(r'["\']([^"\']*)["\']', ann)
                if path_match:
                    class_prefix = path_match.group(1)

        controllers[info.simple_name] = {
            "class": fqcn,
            "base_path": class_prefix,
            "endpoints": [
                {
                    "method": ep["method"],
                    "path": f"{class_prefix}{ep['path']}",
                    "handler": ep["handler"],
                }
                for ep in info.request_mappings
            ],
        }

    total_endpoints = sum(len(c["endpoints"]) for c in controllers.values())
    return {
        "total_controllers": len(controllers),
        "total_endpoints": total_endpoints,
        "controllers": controllers,
    }


@mcp.tool()
def analyze_configuration() -> dict:
    """
    Analyze all application configuration (yml/properties) with migration notes.

    Returns:
        Config properties grouped by category with migration recommendations.
    """
    by_category = defaultdict(list)
    by_profile = defaultdict(list)

    for prop in registry.configs:
        by_category[prop.category].append(asdict(prop))
        by_profile[prop.profile].append(prop.key)

    # Migration-relevant configs
    migration_notes = []
    for prop in registry.configs:
        if "datasource" in prop.key.lower() and prop.profile == "default":
            migration_notes.append(f"Datasource config: {prop.key}={prop.value}")
        if "jms" in prop.key.lower() or "activemq" in prop.key.lower():
            migration_notes.append(f"JMS config (→ Kafka): {prop.key}={prop.value}")
        if "spring.jpa" in prop.key.lower():
            migration_notes.append(f"JPA config: {prop.key}={prop.value}")

    return {
        "total_properties": len(registry.configs),
        "profiles": {k: len(v) for k, v in by_profile.items()},
        "by_category": {k: v for k, v in by_category.items()},
        "migration_notes": migration_notes,
    }


@mcp.tool()
def analyze_dependencies() -> dict:
    """
    Analyze Maven/Gradle dependencies and flag those needing migration.

    Returns:
        All dependencies with upgrade flags, javax→jakarta migrations,
        and Spring Boot 3.x compatibility notes.
    """
    needs_upgrade = [asdict(d) for d in registry.dependencies if d.needs_upgrade]
    javax_deps = [
        asdict(d) for d in registry.dependencies
        if d.group_id.startswith("javax.")
    ]

    return {
        "spring_boot_version": registry.spring_boot_version,
        "java_version": registry.java_version,
        "total_dependencies": len(registry.dependencies),
        "needs_upgrade": {
            "count": len(needs_upgrade),
            "dependencies": needs_upgrade,
        },
        "javax_dependencies": {
            "count": len(javax_deps),
            "dependencies": javax_deps,
            "action": "Migrate all javax.* → jakarta.* for Spring Boot 3.x",
        },
        "all_dependencies": [asdict(d) for d in registry.dependencies],
    }


@mcp.tool()
def find_javax_imports() -> dict:
    """
    Find all classes using javax.* imports that need jakarta.* migration.

    Returns:
        Per-file list of javax imports with their jakarta replacements.
    """
    results = []
    for fqcn, info in registry.beans.items():
        if info.javax_imports:
            results.append({
                "class": fqcn,
                "file": info.file_path,
                "javax_imports": info.javax_imports,
                "migration_notes": info.migration_notes,
            })

    return {
        "total_classes_affected": len(results),
        "total_imports_to_migrate": sum(len(r["javax_imports"]) for r in results),
        "classes": results,
    }


@mcp.tool()
def find_deprecated_patterns() -> dict:
    """
    Find deprecated API usage patterns that won't work in Spring Boot 3.x.

    Detects: WebSecurityConfigurerAdapter, RestTemplate, WebMvcConfigurerAdapter,
    HibernateCallback, CrudRepository (when reactive needed), etc.
    """
    results = []
    for fqcn, info in registry.beans.items():
        if info.uses_deprecated_api:
            results.append({
                "class": fqcn,
                "file": info.file_path,
                "layer": info.layer,
                "deprecated_patterns": info.uses_deprecated_api,
            })

    return {
        "total_classes_affected": len(results),
        "classes": results,
    }


@mcp.tool()
def migration_impact_report(class_name: str) -> dict:
    """
    Generate migration impact report for a specific Spring bean.

    Shows upstream dependents (who injects this bean), downstream dependencies
    (what this bean injects), and migration recommendations.

    Args:
        class_name: FQCN or simple name.
    """
    # Resolve
    target = None
    if class_name in registry.beans:
        target = registry.beans[class_name]
    else:
        matches = [
            fqcn for fqcn in registry.beans
            if fqcn.endswith(f".{class_name}")
        ]
        if len(matches) == 1:
            target = registry.beans[matches[0]]
        elif matches:
            return {"error": f"Ambiguous: {matches}"}

    if not target:
        return {"error": f"Bean '{class_name}' not found."}

    # Upstream: who injects THIS bean?
    upstream = []
    for fqcn, info in registry.beans.items():
        if fqcn == target.fqcn:
            continue
        all_deps = info.constructor_deps + info.field_deps
        for dep in all_deps:
            if dep["type"] == target.simple_name or dep["type"] == target.fqcn:
                upstream.append({
                    "class": fqcn,
                    "layer": info.layer,
                    "injection": dep.get("injection", ""),
                    "field": dep.get("name", ""),
                })

    # Downstream: what does THIS bean inject?
    downstream = []
    for dep in target.constructor_deps + target.field_deps:
        dep_type = dep["type"]
        resolved = next(
            (fqcn for fqcn, info in registry.beans.items()
             if info.simple_name == dep_type),
            None,
        )
        if resolved:
            downstream.append({
                "class": resolved,
                "layer": registry.beans[resolved].layer,
                "injection": dep.get("injection", ""),
                "field": dep.get("name", ""),
            })

    # Migration recommendations
    recommendations = []
    if target.javax_imports:
        recommendations.append(f"Migrate {len(target.javax_imports)} javax → jakarta imports")
    if target.uses_deprecated_api:
        recommendations.append(f"Replace {len(target.uses_deprecated_api)} deprecated patterns")
    if target.layer == "DAO" and target.interfaces:
        recommendations.append("Consider migrating to Spring Data reactive repository if going event-driven")
    if target.layer == "SERVICE" and target.transactional_methods:
        recommendations.append(f"Review {len(target.transactional_methods)} @Transactional methods for Outbox pattern compatibility")
    if target.field_deps:
        recommendations.append(f"Refactor {len(target.field_deps)} field injections → constructor injection")
    if target.scheduled_methods:
        recommendations.append(f"Review {len(target.scheduled_methods)} @Scheduled methods for event-driven replacement")

    return {
        "class": target.fqcn,
        "layer": target.layer,
        "stereotype": target.stereotype,
        "file": target.file_path,
        "upstream_dependents": {
            "count": len(upstream),
            "classes": upstream,
            "impact": "These beans MUST be updated if this bean's interface changes.",
        },
        "downstream_dependencies": {
            "count": len(downstream),
            "classes": downstream,
            "impact": "Migrate these BEFORE this bean.",
        },
        "javax_imports": target.javax_imports,
        "deprecated_api": target.uses_deprecated_api,
        "recommendations": recommendations,
        "risk_level": (
            "HIGH" if len(upstream) > 5 or target.javax_imports
            else "MEDIUM" if len(upstream) > 2
            else "LOW"
        ),
    }


@mcp.tool()
def suggest_event_driven_migration() -> dict:
    """
    Analyze the current synchronous architecture and suggest event-driven
    migration targets.

    Identifies:
      - Service methods that should emit domain events
      - Synchronous service-to-service calls → async event choreography
      - DAO operations → event sourcing candidates
      - @Scheduled tasks → event-triggered replacements
      - REST endpoints → async command handlers

    Returns:
        Event-driven migration plan with specific recommendations.
    """
    suggestions = {
        "domain_events": [],
        "event_listeners_needed": [],
        "sync_to_async_candidates": [],
        "scheduled_to_event": [],
        "outbox_candidates": [],
    }

    for fqcn, info in registry.beans.items():
        if info.layer != "SERVICE":
            continue

        # Services that depend on other services (sync coupling)
        service_deps = [
            dep for dep in info.constructor_deps + info.field_deps
            if dep["type"] in {
                b.simple_name for b in registry.beans.values()
                if b.layer == "SERVICE" and b.fqcn != fqcn
            }
        ]
        if service_deps:
            suggestions["sync_to_async_candidates"].append({
                "service": fqcn,
                "coupled_services": [d["type"] for d in service_deps],
                "recommendation": (
                    f"{info.simple_name} directly calls "
                    f"{', '.join(d['type'] for d in service_deps)}. "
                    f"Decouple via domain events."
                ),
            })

        # Transactional methods → Outbox pattern candidates
        if info.transactional_methods:
            dao_deps = [
                dep["type"] for dep in info.constructor_deps + info.field_deps
                if dep["type"].endswith(("Repository", "Dao", "DAO"))
            ]
            if dao_deps:
                suggestions["outbox_candidates"].append({
                    "service": fqcn,
                    "transactional_methods": info.transactional_methods,
                    "daos_used": dao_deps,
                    "recommendation": (
                        f"Wrap @Transactional methods with Outbox pattern: "
                        f"write event to outbox table in same transaction, "
                        f"poller publishes to Kafka."
                    ),
                })

        # Suggest domain events from public methods
        for method in info.public_methods:
            method_name = method["name"]
            # Create/update/delete methods should emit events
            if any(method_name.lower().startswith(prefix)
                   for prefix in ["create", "save", "update", "delete",
                                  "process", "submit", "cancel", "approve",
                                  "reject", "complete", "assign"]):
                event_name = _method_to_event_name(method_name, info.simple_name)
                suggestions["domain_events"].append({
                    "service": info.simple_name,
                    "method": method_name,
                    "suggested_event": event_name,
                    "returns": method["return_type"],
                })

        # @Scheduled → event-driven
        for sched_method in info.scheduled_methods:
            suggestions["scheduled_to_event"].append({
                "service": info.simple_name,
                "method": sched_method,
                "recommendation": (
                    f"Consider replacing @Scheduled {sched_method}() "
                    f"with an event listener triggered by upstream events, "
                    f"or use Spring Batch with Kafka-triggered job."
                ),
            })

    return {
        "summary": {
            "domain_events_to_create": len(suggestions["domain_events"]),
            "sync_couplings_to_break": len(suggestions["sync_to_async_candidates"]),
            "outbox_pattern_candidates": len(suggestions["outbox_candidates"]),
            "scheduled_tasks_to_migrate": len(suggestions["scheduled_to_event"]),
        },
        "details": suggestions,
    }


def _method_to_event_name(method_name: str, class_name: str) -> str:
    """Convert a method name to a domain event name."""
    # Remove common prefixes
    clean_class = class_name.replace("Service", "").replace("Impl", "")

    # createOrder → OrderCreatedEvent
    verb_to_past = {
        "create": "Created", "save": "Saved", "update": "Updated",
        "delete": "Deleted", "process": "Processed", "submit": "Submitted",
        "cancel": "Cancelled", "approve": "Approved", "reject": "Rejected",
        "complete": "Completed", "assign": "Assigned",
    }

    for verb, past in verb_to_past.items():
        if method_name.lower().startswith(verb):
            entity = method_name[len(verb):] or clean_class
            return f"{entity}{past}Event"

    return f"{clean_class}{method_name.capitalize()}Event"


@mcp.tool()
def generate_dependency_graph() -> dict:
    """
    Generate a Mermaid dependency graph showing bean wiring across layers.
    Color-coded by architectural layer.
    """
    mermaid_lines = ["graph TD"]
    seen_edges = set()

    def short(fqcn: str) -> str:
        return registry.beans[fqcn].simple_name if fqcn in registry.beans else fqcn.rsplit(".", 1)[-1]

    for fqcn, info in registry.beans.items():
        if info.layer in ("UNKNOWN", "INFRASTRUCTURE", "EXCEPTION"):
            continue
        src = short(fqcn)
        for dep in info.constructor_deps + info.field_deps:
            dep_type = dep["type"]
            resolved = next(
                (f for f, i in registry.beans.items() if i.simple_name == dep_type),
                None,
            )
            if resolved:
                tgt = short(resolved)
                edge_key = f"{src}->{tgt}"
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    label = dep.get("injection", "")[0] if dep.get("injection") else ""
                    mermaid_lines.append(f"    {src} -->|{label}| {tgt}")

    # Styles
    mermaid_lines.extend([
        "", "    classDef dao fill:#e1f5fe,stroke:#0277bd",
        "    classDef service fill:#f3e5f5,stroke:#7b1fa2",
        "    classDef controller fill:#e8f5e9,stroke:#2e7d32",
        "    classDef entity fill:#fff3e0,stroke:#ef6c00",
        "    classDef messaging fill:#fce4ec,stroke:#c62828",
        "    classDef config fill:#f5f5f5,stroke:#616161",
        "    classDef security fill:#fce4ec,stroke:#ad1457",
    ])

    layer_to_style = {
        "DAO": "dao", "SERVICE": "service", "CONTROLLER": "controller",
        "ENTITY": "entity", "MESSAGING": "messaging", "CONFIG": "config",
        "SECURITY": "security",
    }
    for fqcn, info in registry.beans.items():
        sn = short(fqcn)
        style = layer_to_style.get(info.layer)
        if style:
            mermaid_lines.append(f"    class {sn} {style}")

    return {
        "mermaid_diagram": "\n".join(mermaid_lines),
        "total_nodes": len(registry.beans),
        "total_edges": len(seen_edges),
    }


@mcp.tool()
def suggest_migration_order() -> dict:
    """
    Suggest wave-based migration order for Spring Boot 3.x + event-driven.

    Wave order:
      0. Build & dependency upgrades (pom.xml / build.gradle)
      1. javax → jakarta import migration (mechanical)
      2. Entities & DTOs (no behavioral change)
      3. Repositories / DAOs (Spring Data upgrades)
      4. Services (business logic + Outbox pattern)
      5. Messaging (JMS → Kafka)
      6. Controllers (REST API updates)
      7. Security (WebSecurityConfigurerAdapter removal)
      8. Configuration & Infrastructure
    """
    waves = []

    # Wave 0: Build upgrades
    deps_to_upgrade = [d for d in registry.dependencies if d.needs_upgrade]
    if deps_to_upgrade or registry.spring_boot_version:
        waves.append({
            "wave": 0,
            "name": "Build & Dependency Upgrades",
            "tasks": [
                f"Upgrade Spring Boot {registry.spring_boot_version} → 3.2.x",
                f"Upgrade Java {registry.java_version} → 21",
                f"Update {len(deps_to_upgrade)} dependencies",
            ],
            "classes": [],
        })

    # Wave 1: javax → jakarta
    javax_classes = [fqcn for fqcn, i in registry.beans.items() if i.javax_imports]
    if javax_classes:
        waves.append({
            "wave": 1,
            "name": "javax → jakarta Import Migration",
            "tasks": ["Find-and-replace javax.* → jakarta.* across all files"],
            "classes": javax_classes,
        })

    # Waves 2-8: Layer by layer
    layer_waves = [
        (2, "ENTITY", "Entities & DTOs"),
        (3, "DAO", "Repositories / DAOs → Spring Data"),
        (4, "SERVICE", "Services → @Service + Outbox Pattern"),
        (5, "MESSAGING", "Messaging → @KafkaListener"),
        (6, "CONTROLLER", "Controllers → @RestController"),
        (7, "SECURITY", "Security → SecurityFilterChain"),
        (8, "CONFIG", "Configuration & Infrastructure"),
    ]

    for wave_num, layer, wave_name in layer_waves:
        layer_classes = sorted([
            fqcn for fqcn, i in registry.beans.items()
            if i.layer == layer
        ])
        if layer_classes:
            waves.append({
                "wave": wave_num,
                "name": wave_name,
                "classes": layer_classes,
                "count": len(layer_classes),
            })

    return {
        "total_beans": len(registry.beans),
        "total_waves": len(waves),
        "migration_waves": waves,
    }


@mcp.tool()
def reset_registry() -> dict:
    """Clear all scanned data."""
    count = len(registry.beans)
    registry.reset()
    return {"status": "cleared", "beans_removed": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("spring://status")
def project_status() -> str:
    """Current scan status."""
    layers = defaultdict(int)
    for info in registry.beans.values():
        layers[info.layer] += 1

    return (
        f"Spring Boot Scanner Status\n"
        f"==========================\n"
        f"Project: {registry.project_root or '(not scanned)'}\n"
        f"Spring Boot: {registry.spring_boot_version or 'unknown'}\n"
        f"Java: {registry.java_version or 'unknown'}\n"
        f"Beans: {len(registry.beans)}\n"
        f"Layers: {json.dumps(dict(layers), indent=2)}\n"
        f"Config properties: {len(registry.configs)}\n"
        f"Dependencies: {len(registry.dependencies)}\n"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entrypoint
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    mcp.run()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# VS Code (.vscode/mcp.json):
# {
#   "servers": {
#     "spring-scanner": {
#       "command": "python",
#       "args": ["/path/to/springboot_scanner_mcp_server.py"]
#     }
#   }
# }
#
# Claude Code:
#   claude mcp add spring-scanner python /path/to/springboot_scanner_mcp_server.py
#
# Combined with JAR scanner:
# {
#   "servers": {
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
