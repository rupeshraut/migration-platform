"""
Migration Knowledge Base MCP Server — v2 (Pluggable Storage)
================================================================
Persistent cross-project knowledge base for migration analysis.

Storage backends (selected via environment variable MIGRATION_KB_STORAGE):

  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                  │
  │  "mongodb"  (recommended for platform engineering)               │
  │  ─────────                                                       │
  │  • MongoDB for persistent structured data                        │
  │  • Redis for caching, locks, and session-scoped scan state       │
  │  • Survives container restarts, shared across teams              │
  │  • Env: MONGODB_URI, REDIS_URL                                   │
  │                                                                  │
  │  "mongodb_only"                                                  │
  │  ──────────────                                                  │
  │  • MongoDB only, no Redis                                        │
  │  • Env: MONGODB_URI                                              │
  │                                                                  │
  │  "local" (default, backward-compatible)                          │
  │  ───────                                                         │
  │  • JSON files at ~/.mcp-migration-kb/                            │
  │  • Single developer / local use only                             │
  │                                                                  │
  └──────────────────────────────────────────────────────────────────┘

Environment Variables:
    MIGRATION_KB_STORAGE=mongodb|mongodb_only|local   (default: local)
    MONGODB_URI=mongodb://host:27017/migration_kb     (required for mongodb*)
    MONGODB_DB=migration_kb                           (default: migration_kb)
    REDIS_URL=redis://host:6379/0                     (optional, for caching)
    REDIS_CACHE_TTL=3600                              (cache TTL in seconds)

MongoDB Collections:
    projects        — Project registry (one doc per project)
    classes          — Class records (one doc per class, indexed by fqcn + project)
    mappings         — Mapping rules
    target_framework — Scanned framework metadata
    golden_samples   — Golden sample metadata
    tracking         — Migration progress tracking
    validation_reports — Validation run history

Requirements:
    pip install fastmcp pyyaml
    pip install pymongo        # if using mongodb backend
    pip install redis          # if using redis caching

Usage:
    # Local (backward compatible)
    python migration_kb_mcp_server.py

    # Container with MongoDB + Redis
    MIGRATION_KB_STORAGE=mongodb \
    MONGODB_URI=mongodb://mongo:27017/migration_kb \
    REDIS_URL=redis://redis:6379/0 \
    python migration_kb_mcp_server.py

    # Docker Compose (see bottom of file for example)
"""

import json
import os
import re
import abc
import hashlib
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import yaml
from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration from environment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STORAGE_BACKEND = os.environ.get("MIGRATION_KB_STORAGE", "local")
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/migration_kb")
MONGODB_DB = os.environ.get("MONGODB_DB", "migration_kb")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_CACHE_TTL = int(os.environ.get("REDIS_CACHE_TTL", "3600"))
LOCAL_KB_DIR = os.path.expanduser("~/.mcp-migration-kb")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp = FastMCP(
    "Migration Knowledge Base",
    description=(
        "Persistent knowledge base for cross-project Java migration analysis. "
        f"Storage backend: {STORAGE_BACKEND}."
    ),
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models (unchanged from v1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class ClassRecord:
    fqcn: str
    simple_name: str = ""
    package: str = ""
    project: str = ""
    file_path: str = ""
    class_type: str = ""
    layer: str = "UNKNOWN"
    stereotype: str = ""
    superclass: str = ""
    interfaces: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    generic_params: list[str] = field(default_factory=list)
    constructor_deps: list[dict] = field(default_factory=list)
    field_deps: list[dict] = field(default_factory=list)
    all_dependency_types: list[str] = field(default_factory=list)
    public_methods: list[dict] = field(default_factory=list)
    abstract_methods: list[dict] = field(default_factory=list)
    known_implementors: list[str] = field(default_factory=list)
    known_subclasses: list[str] = field(default_factory=list)
    javax_imports: list[str] = field(default_factory=list)
    deprecated_patterns: list[str] = field(default_factory=list)
    migration_notes: list[str] = field(default_factory=list)


@dataclass
class ProjectRecord:
    name: str
    path: str
    project_type: str = ""
    spring_boot_version: str = ""
    java_version: str = ""
    scanned_at: str = ""
    class_count: int = 0
    file_hash: str = ""
    depends_on: list[str] = field(default_factory=list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Storage Backend Interface (Strategy Pattern)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StorageBackend(abc.ABC):
    """Abstract interface for KB storage."""

    # ── Project operations ──
    @abc.abstractmethod
    def list_projects(self) -> dict[str, dict]:
        """Return {name: project_dict} for all projects."""

    @abc.abstractmethod
    def get_project(self, name: str) -> Optional[dict]:
        """Get a single project record."""

    @abc.abstractmethod
    def save_project(self, name: str, project: dict):
        """Upsert a project record."""

    @abc.abstractmethod
    def delete_project(self, name: str):
        """Delete a project and all its classes."""

    # ── Class operations ──
    @abc.abstractmethod
    def get_classes_by_project(self, project_name: str) -> dict[str, dict]:
        """Return {fqcn: class_dict} for all classes in a project."""

    @abc.abstractmethod
    def save_classes(self, project_name: str, classes: dict[str, dict]):
        """Replace all classes for a project (bulk upsert)."""

    @abc.abstractmethod
    def get_class(self, fqcn: str) -> Optional[dict]:
        """Get a single class record by FQCN."""

    @abc.abstractmethod
    def search_classes(
        self, query: str, project_filter: str = "", layer_filter: str = ""
    ) -> list[dict]:
        """Search classes by text match."""

    @abc.abstractmethod
    def get_all_classes(self) -> dict[str, dict]:
        """Return ALL classes across ALL projects."""

    # ── Metadata ──
    @abc.abstractmethod
    def get_storage_info(self) -> dict:
        """Return storage backend info for diagnostics."""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backend 1: Local JSON (backward compatible)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class LocalJsonStorage(StorageBackend):
    """Original JSON-on-disk storage. Single developer / local use."""

    def __init__(self):
        self.kb_dir = LOCAL_KB_DIR
        self.index_file = os.path.join(self.kb_dir, "_index.json")
        os.makedirs(self.kb_dir, exist_ok=True)

    def _load_index(self) -> dict:
        if os.path.isfile(self.index_file):
            try:
                with open(self.index_file) as f:
                    return json.load(f).get("projects", {})
            except Exception:
                pass
        return {}

    def _save_index(self, projects: dict):
        with open(self.index_file, "w") as f:
            json.dump({"projects": projects}, f, indent=2)

    def list_projects(self) -> dict[str, dict]:
        return self._load_index()

    def get_project(self, name: str) -> Optional[dict]:
        return self._load_index().get(name)

    def save_project(self, name: str, project: dict):
        idx = self._load_index()
        idx[name] = project
        self._save_index(idx)

    def delete_project(self, name: str):
        idx = self._load_index()
        idx.pop(name, None)
        self._save_index(idx)
        fp = os.path.join(self.kb_dir, f"{name}.json")
        if os.path.isfile(fp):
            os.remove(fp)

    def get_classes_by_project(self, project_name: str) -> dict[str, dict]:
        fp = os.path.join(self.kb_dir, f"{project_name}.json")
        if os.path.isfile(fp):
            try:
                with open(fp) as f:
                    return json.load(f).get("classes", {})
            except Exception:
                pass
        return {}

    def save_classes(self, project_name: str, classes: dict[str, dict]):
        fp = os.path.join(self.kb_dir, f"{project_name}.json")
        with open(fp, "w") as f:
            json.dump({
                "classes": classes,
                "saved_at": datetime.now().isoformat(),
            }, f, indent=2, default=str)

    def get_class(self, fqcn: str) -> Optional[dict]:
        for proj_name in self._load_index():
            classes = self.get_classes_by_project(proj_name)
            if fqcn in classes:
                cls = classes[fqcn]
                cls["project"] = proj_name
                return cls
        return None

    def search_classes(self, query: str, project_filter="", layer_filter="") -> list[dict]:
        results = []
        q = query.lower()
        for proj_name in self._load_index():
            if project_filter and proj_name != project_filter:
                continue
            for fqcn, cls in self.get_classes_by_project(proj_name).items():
                if layer_filter and cls.get("layer") != layer_filter.upper():
                    continue
                searchable = f"{fqcn} {' '.join(cls.get('annotations', []))} {cls.get('superclass', '')}".lower()
                if q in searchable:
                    results.append({**cls, "fqcn": fqcn, "project": proj_name})
        return results[:50]

    def get_all_classes(self) -> dict[str, dict]:
        all_cls = {}
        for proj_name in self._load_index():
            for fqcn, cls in self.get_classes_by_project(proj_name).items():
                cls["project"] = proj_name
                all_cls[fqcn] = cls
        return all_cls

    def get_storage_info(self) -> dict:
        return {
            "backend": "local_json",
            "path": self.kb_dir,
            "writable": os.access(self.kb_dir, os.W_OK),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backend 2: MongoDB (+ optional Redis cache)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class MongoDBStorage(StorageBackend):
    """
    MongoDB for persistent storage, optional Redis for caching.

    Collections:
      projects  — {_id: name, path, project_type, ...}
      classes   — {_id: auto, fqcn, project, layer, ...}  indexed by (fqcn, project)
    """

    def __init__(self, use_redis: bool = True):
        try:
            import pymongo
        except ImportError:
            raise ImportError(
                "pymongo is required for MongoDB backend. "
                "Install: pip install pymongo"
            )

        self.client = pymongo.MongoClient(MONGODB_URI)
        self.db = self.client[MONGODB_DB]

        # Collections
        self.projects_col = self.db["projects"]
        self.classes_col = self.db["classes"]

        # Indexes
        self.classes_col.create_index([("fqcn", 1), ("project", 1)], unique=True)
        self.classes_col.create_index([("project", 1)])
        self.classes_col.create_index([("layer", 1)])
        self.classes_col.create_index([("simple_name", 1)])
        self.classes_col.create_index([
            ("fqcn", "text"),
            ("simple_name", "text"),
            ("annotations", "text"),
        ], name="text_search")

        # Optional Redis cache
        self.redis = None
        if use_redis:
            try:
                import redis as redis_lib
                self.redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
                self.redis.ping()
            except Exception:
                self.redis = None  # Degrade gracefully

    def _cache_key(self, *parts) -> str:
        return f"mig_kb:{'::'.join(parts)}"

    def _cache_get(self, key: str) -> Optional[dict]:
        if not self.redis:
            return None
        try:
            data = self.redis.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    def _cache_set(self, key: str, data: dict, ttl: int = REDIS_CACHE_TTL):
        if not self.redis:
            return
        try:
            self.redis.setex(key, ttl, json.dumps(data, default=str))
        except Exception:
            pass

    def _cache_delete_pattern(self, pattern: str):
        if not self.redis:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
                if keys:
                    self.redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception:
            pass

    # ── Project operations ──

    def list_projects(self) -> dict[str, dict]:
        cached = self._cache_get(self._cache_key("projects", "all"))
        if cached:
            return cached

        projects = {}
        for doc in self.projects_col.find():
            name = doc.pop("_id")
            projects[name] = doc

        self._cache_set(self._cache_key("projects", "all"), projects, ttl=300)
        return projects

    def get_project(self, name: str) -> Optional[dict]:
        cached = self._cache_get(self._cache_key("project", name))
        if cached:
            return cached

        doc = self.projects_col.find_one({"_id": name})
        if doc:
            doc.pop("_id", None)
            self._cache_set(self._cache_key("project", name), doc)
            return doc
        return None

    def save_project(self, name: str, project: dict):
        self.projects_col.update_one(
            {"_id": name},
            {"$set": project},
            upsert=True,
        )
        # Invalidate cache
        self._cache_delete_pattern("mig_kb::project*")
        self._cache_delete_pattern("mig_kb::classes*")

    def delete_project(self, name: str):
        self.projects_col.delete_one({"_id": name})
        self.classes_col.delete_many({"project": name})
        self._cache_delete_pattern("mig_kb::*")

    # ── Class operations ──

    def get_classes_by_project(self, project_name: str) -> dict[str, dict]:
        cached = self._cache_get(self._cache_key("classes", project_name))
        if cached:
            return cached

        classes = {}
        for doc in self.classes_col.find({"project": project_name}):
            doc.pop("_id", None)
            fqcn = doc.get("fqcn", "")
            classes[fqcn] = doc

        self._cache_set(self._cache_key("classes", project_name), classes)
        return classes

    def save_classes(self, project_name: str, classes: dict[str, dict]):
        # Bulk replace: delete old, insert new
        self.classes_col.delete_many({"project": project_name})

        if classes:
            docs = []
            for fqcn, cls in classes.items():
                doc = {**cls, "fqcn": fqcn, "project": project_name}
                doc.pop("_id", None)
                docs.append(doc)

            # Batch insert in chunks of 500
            for i in range(0, len(docs), 500):
                self.classes_col.insert_many(docs[i:i + 500])

        self._cache_delete_pattern(f"mig_kb::classes::{project_name}*")
        self._cache_delete_pattern("mig_kb::class::*")

    def get_class(self, fqcn: str) -> Optional[dict]:
        cached = self._cache_get(self._cache_key("class", fqcn))
        if cached:
            return cached

        doc = self.classes_col.find_one({"fqcn": fqcn})
        if doc:
            doc.pop("_id", None)
            self._cache_set(self._cache_key("class", fqcn), doc)
            return doc
        return None

    def search_classes(self, query: str, project_filter="", layer_filter="") -> list[dict]:
        mongo_filter: dict[str, Any] = {}

        if project_filter:
            mongo_filter["project"] = project_filter
        if layer_filter:
            mongo_filter["layer"] = layer_filter.upper()

        # Use text search if available, fall back to regex
        try:
            mongo_filter["$text"] = {"$search": query}
            cursor = self.classes_col.find(mongo_filter).limit(50)
            results = []
            for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
            if results:
                return results
        except Exception:
            pass

        # Fallback: regex search
        del mongo_filter["$text"]
        mongo_filter["$or"] = [
            {"fqcn": {"$regex": query, "$options": "i"}},
            {"simple_name": {"$regex": query, "$options": "i"}},
            {"annotations": {"$regex": query, "$options": "i"}},
        ]
        results = []
        for doc in self.classes_col.find(mongo_filter).limit(50):
            doc.pop("_id", None)
            results.append(doc)
        return results

    def get_all_classes(self) -> dict[str, dict]:
        # This can be expensive — use with caution on large KBs
        all_cls = {}
        for doc in self.classes_col.find():
            doc.pop("_id", None)
            fqcn = doc.get("fqcn", "")
            all_cls[fqcn] = doc
        return all_cls

    def get_storage_info(self) -> dict:
        try:
            server_info = self.client.server_info()
            mongo_version = server_info.get("version", "unknown")
        except Exception:
            mongo_version = "connection error"

        info = {
            "backend": "mongodb",
            "uri": MONGODB_URI.split("@")[-1] if "@" in MONGODB_URI else MONGODB_URI,
            "database": MONGODB_DB,
            "mongo_version": mongo_version,
            "collections": {
                "projects": self.projects_col.count_documents({}),
                "classes": self.classes_col.count_documents({}),
            },
            "redis_enabled": self.redis is not None,
        }
        if self.redis:
            try:
                redis_info = self.redis.info("server")
                info["redis_version"] = redis_info.get("redis_version", "unknown")
            except Exception:
                info["redis_version"] = "connection error"
        return info


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backend Factory
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def create_storage_backend() -> StorageBackend:
    """Create storage backend based on MIGRATION_KB_STORAGE env var."""
    if STORAGE_BACKEND == "mongodb":
        return MongoDBStorage(use_redis=True)
    elif STORAGE_BACKEND == "mongodb_only":
        return MongoDBStorage(use_redis=False)
    else:
        return LocalJsonStorage()


# Initialize storage
storage = create_storage_backend()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Java Source Parser (unchanged from v1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SPRING_STEREOTYPES = {
    "@Service": "SERVICE", "@Repository": "DAO", "@Component": "COMPONENT",
    "@Controller": "CONTROLLER", "@RestController": "CONTROLLER",
    "@Configuration": "CONFIG", "@ControllerAdvice": "CONTROLLER",
}

JAVAX_TO_JAKARTA = {
    "javax.persistence": "jakarta.persistence",
    "javax.validation": "jakarta.validation",
    "javax.servlet": "jakarta.servlet",
    "javax.annotation": "jakarta.annotation",
    "javax.transaction": "jakarta.transaction",
    "javax.jms": "jakarta.jms",
}

_JAVA_BUILTINS = frozenset({
    "String", "Integer", "Long", "Boolean", "Double", "Float", "Short",
    "Byte", "Object", "List", "Map", "Set", "Collection", "Optional",
    "Stream", "Void", "void", "int", "long", "boolean", "double", "float",
    "BigDecimal", "BigInteger", "Date", "LocalDate", "LocalDateTime",
    "Instant", "Duration", "UUID", "Logger", "Pageable", "Page", "Sort",
    "ResponseEntity", "CompletableFuture", "Mono", "Flux",
})


def _parse_java_source(file_path: str, project_name: str) -> Optional[ClassRecord]:
    """Parse a Java source file into a ClassRecord."""
    try:
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if "package-info" in file_path or "module-info" in file_path:
        return None

    record = ClassRecord(fqcn="", project=project_name, file_path=file_path)

    pkg_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
    record.package = pkg_match.group(1) if pkg_match else ""

    for imp in re.findall(r"^import\s+([\w.*]+)\s*;", content, re.MULTILINE):
        for javax_pkg, jakarta_pkg in JAVAX_TO_JAKARTA.items():
            if imp.startswith(javax_pkg):
                record.javax_imports.append(imp)
                record.migration_notes.append(f"{imp} → {jakarta_pkg}")

    class_decl = re.search(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"
        r"(?:public\s+|protected\s+|private\s+)?"
        r"(abstract\s+)?"
        r"(class|interface|enum|@interface)\s+(\w+)"
        r"(?:<([^>]+)>)?"
        r"(?:\s+extends\s+([\w.<>,\s]+))?"
        r"(?:\s+implements\s+([\w.<>,\s]+))?",
        content,
    )

    if not class_decl:
        return None

    annotation_block = class_decl.group(1) or ""
    is_abstract = bool(class_decl.group(2))
    kind = class_decl.group(3)
    record.simple_name = class_decl.group(4)
    generic_params = class_decl.group(5) or ""
    extends_clause = class_decl.group(6) or ""
    implements_clause = class_decl.group(7) or ""

    record.fqcn = f"{record.package}.{record.simple_name}" if record.package else record.simple_name

    if kind == "interface":
        record.class_type = "INTERFACE"
    elif kind == "enum":
        record.class_type = "ENUM"
    elif kind == "@interface":
        record.class_type = "ANNOTATION"
    elif is_abstract:
        record.class_type = "ABSTRACT_CLASS"
    else:
        record.class_type = "CLASS"

    if generic_params:
        record.generic_params = [g.strip() for g in generic_params.split(",")]
    if extends_clause:
        record.superclass = extends_clause.strip().split(",")[0].split("<")[0].strip()
    if implements_clause:
        record.interfaces = [i.strip().split("<")[0].strip() for i in implements_clause.split(",")]
    if kind == "interface" and extends_clause:
        record.interfaces = [i.strip().split("<")[0].strip() for i in extends_clause.split(",")]
        record.superclass = ""

    class_annotations = re.findall(r"@(\w+)(?:\(([^)]*)\))?", annotation_block)
    for ann_name, ann_params in class_annotations:
        full_ann = f"@{ann_name}" + (f"({ann_params})" if ann_params else "")
        record.annotations.append(full_ann)
        ann_key = f"@{ann_name}"
        if ann_key in SPRING_STEREOTYPES:
            record.stereotype = ann_key
            record.layer = SPRING_STEREOTYPES[ann_key]

    if record.layer in ("UNKNOWN", "COMPONENT"):
        record.layer = _classify_layer(record)

    # Constructor injection
    has_lombok = "@RequiredArgsConstructor" in content or "@AllArgsConstructor" in content
    ctor_pattern = re.findall(
        rf"(?:@Autowired\s+)?(?:public\s+|protected\s+)?{re.escape(record.simple_name)}\s*\(([^)]*)\)",
        content,
    )
    for params_str in ctor_pattern:
        for param in params_str.split(","):
            param = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", param).strip()
            parts = param.split()
            if len(parts) >= 2:
                ptype = parts[-2].split("<")[0]
                pname = parts[-1]
                record.constructor_deps.append({"type": ptype, "name": pname, "injection": "CONSTRUCTOR"})
                if ptype[0].isupper() and ptype not in _JAVA_BUILTINS:
                    record.all_dependency_types.append(ptype)

    if has_lombok:
        for field_type, field_name in re.findall(
            r"(?:private|protected)\s+final\s+([\w<>,.?\[\]\s]+?)\s+(\w+)\s*;", content
        ):
            clean = field_type.strip().split("<")[0]
            existing = [d["name"] for d in record.constructor_deps]
            if field_name not in existing:
                record.constructor_deps.append({"type": clean, "name": field_name, "injection": "LOMBOK_CONSTRUCTOR"})
                if clean[0].isupper() and clean not in _JAVA_BUILTINS:
                    record.all_dependency_types.append(clean)

    # Field injection
    for qualifier, ftype, fname in re.findall(
        r"(?:@Autowired|@Inject|@Resource)(?:\s*\([^)]*\))?\s*"
        r"(?:@Qualifier\s*\(\s*\"([^\"]*)\"\s*\)\s*)?"
        r"(?:@Lazy\s*)?"
        r"(?:private|protected|public)?\s+(?:final\s+)?"
        r"([\w<>,.?\[\]\s]+?)\s+(\w+)\s*;",
        content,
    ):
        clean = ftype.strip().split("<")[0]
        record.field_deps.append({
            "type": clean, "name": fname,
            "qualifier": qualifier or "", "injection": "FIELD",
        })
        if clean[0].isupper() and clean not in _JAVA_BUILTINS:
            record.all_dependency_types.append(clean)

    # Public methods
    for m in re.finditer(
        r"((?:@\w+(?:\([^)]*\))?\s*\n?\s*)*)"
        r"(?:public\s+)"
        r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"([\w<>,.?\[\]\s]+?)\s+(\w+)\s*\(([^)]*)\)",
        content,
    ):
        ann_block = m.group(1) or ""
        ret = m.group(2).strip()
        name = m.group(3)
        params = m.group(4).strip()
        anns = [f"@{a[0]}" for a in re.findall(r"@(\w+)", ann_block)]
        record.public_methods.append({
            "name": name, "return_type": ret,
            "parameters": params, "annotations": anns,
        })

    if record.class_type in ("INTERFACE", "ABSTRACT_CLASS"):
        for m in re.finditer(
            r"(?:public\s+)?(?:abstract\s+)?([\w<>,.?\[\]\s]+?)\s+(\w+)\s*\(([^)]*)\)\s*;",
            content,
        ):
            record.abstract_methods.append({
                "name": m.group(2), "return_type": m.group(1).strip(), "parameters": m.group(3).strip(),
            })

    deprecated = {
        "WebSecurityConfigurerAdapter": "Use SecurityFilterChain @Bean",
        "RestTemplate": "Consider RestClient (Boot 3.2+)",
        "extends WebMvcConfigurerAdapter": "Use WebMvcConfigurer interface",
    }
    for pattern, note in deprecated.items():
        if pattern in content:
            record.deprecated_patterns.append(f"{pattern}: {note}")

    record.all_dependency_types = list(set(record.all_dependency_types))
    return record


def _classify_layer(record: ClassRecord) -> str:
    name = record.fqcn.lower()
    ifaces = " ".join(record.interfaces).lower()

    if any(s in name for s in [".dao.", ".repository."]) or "repository" in ifaces:
        return "DAO"
    if any(s in name for s in [".service."]):
        return "SERVICE"
    if any(s in name for s in [".controller.", ".rest.", ".api.", ".resource."]):
        return "CONTROLLER"
    if any(s in name for s in [".entity.", ".model.", ".domain.", ".dto."]):
        return "ENTITY"
    if any(s in name for s in [".listener.", ".consumer.", ".handler.", ".messaging."]):
        return "MESSAGING"
    if any(s in name for s in [".config.", ".configuration."]):
        return "CONFIG"
    if any(s in name for s in [".exception.", ".error."]):
        return "EXCEPTION"
    if any(s in name for s in [".util.", ".helper.", ".common.", ".support.", ".base."]):
        return "INFRASTRUCTURE"
    if record.class_type in ("INTERFACE", "ABSTRACT_CLASS"):
        return "CONTRACT"
    return "UNKNOWN"


def _compute_project_hash(project_path: str) -> str:
    h = hashlib.md5()
    for java_file in sorted(Path(project_path).rglob("*.java")):
        if "/test/" in str(java_file):
            continue
        stat = java_file.stat()
        h.update(f"{java_file}:{stat.st_mtime}:{stat.st_size}".encode())
    return h.hexdigest()[:12]


def _parse_pom_for_deps(project_path: str) -> tuple[str, str, list[str]]:
    pom = os.path.join(project_path, "pom.xml")
    boot_ver, java_ver, dep_artifacts = "", "", []
    if not os.path.isfile(pom):
        return boot_ver, java_ver, dep_artifacts
    try:
        tree = ET.parse(pom)
        root = tree.getroot()
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        parent = root.find("m:parent", ns) or root.find("parent")
        if parent is not None:
            art = parent.find("m:artifactId", ns) or parent.find("artifactId")
            ver = parent.find("m:version", ns) or parent.find("version")
            if art is not None and "spring-boot" in (art.text or ""):
                boot_ver = ver.text if ver is not None else ""
        props = root.find("m:properties", ns) or root.find("properties")
        if props is not None:
            for p in props:
                tag = p.tag.split("}")[-1]
                if tag in ("java.version", "maven.compiler.source"):
                    java_ver = p.text or ""
        deps_elem = root.find("m:dependencies", ns) or root.find("dependencies")
        if deps_elem is not None:
            for dep in deps_elem:
                aid = dep.find("m:artifactId", ns) or dep.find("artifactId")
                if aid is not None and aid.text:
                    dep_artifacts.append(aid.text)
    except Exception:
        pass
    return boot_ver, java_ver, dep_artifacts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools (use storage backend instead of direct file access)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def scan_library(project_name: str, project_path: str) -> dict:
    """
    Scan a shared Java library and persist to the knowledge base.
    Works identically regardless of storage backend (local/MongoDB/Redis).
    """
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        return {"error": f"Directory not found: {project_path}"}

    current_hash = _compute_project_hash(project_path)
    existing = storage.get_project(project_name)
    if existing and existing.get("file_hash") == current_hash:
        classes = storage.get_classes_by_project(project_name)
        return {
            "status": "UNCHANGED",
            "message": f"'{project_name}' has not changed since last scan.",
            "class_count": len(classes),
        }

    java_files = [
        f for f in Path(project_path).rglob("*.java")
        if "/test/" not in str(f) and "\\test\\" not in str(f)
    ]

    classes: dict[str, dict] = {}
    layer_counts = defaultdict(int)
    type_counts = defaultdict(int)

    for jf in java_files:
        record = _parse_java_source(str(jf), project_name)
        if record and record.fqcn:
            classes[record.fqcn] = asdict(record)
            layer_counts[record.layer] += 1
            type_counts[record.class_type] += 1

    boot_ver, java_ver, _ = _parse_pom_for_deps(project_path)

    project_data = asdict(ProjectRecord(
        name=project_name, path=project_path, project_type="LIBRARY",
        spring_boot_version=boot_ver, java_version=java_ver,
        scanned_at=datetime.now().isoformat(),
        class_count=len(classes), file_hash=current_hash,
    ))

    storage.save_project(project_name, project_data)
    storage.save_classes(project_name, classes)

    contracts = [
        {"fqcn": fqcn, "type": c.get("class_type"), "layer": c.get("layer")}
        for fqcn, c in classes.items()
        if c.get("class_type") in ("INTERFACE", "ABSTRACT_CLASS")
    ]

    return {
        "status": "SCANNED",
        "project": project_name,
        "storage_backend": storage.get_storage_info()["backend"],
        "total_classes": len(classes),
        "class_types": dict(type_counts),
        "layer_breakdown": dict(layer_counts),
        "key_contracts": contracts[:30],
    }


@mcp.tool()
def scan_application(project_name: str, project_path: str) -> dict:
    """Scan a Spring Boot application and persist to the knowledge base."""
    project_path = os.path.abspath(project_path)
    if not os.path.isdir(project_path):
        return {"error": f"Directory not found: {project_path}"}

    current_hash = _compute_project_hash(project_path)
    existing = storage.get_project(project_name)
    if existing and existing.get("file_hash") == current_hash:
        return {"status": "UNCHANGED", "class_count": existing.get("class_count", 0)}

    java_files = [
        f for f in Path(project_path).rglob("*.java")
        if "/test/" not in str(f) and "\\test\\" not in str(f)
    ]

    classes: dict[str, dict] = {}
    layer_counts = defaultdict(int)

    for jf in java_files:
        record = _parse_java_source(str(jf), project_name)
        if record and record.fqcn:
            classes[record.fqcn] = asdict(record)
            layer_counts[record.layer] += 1

    boot_ver, java_ver, _ = _parse_pom_for_deps(project_path)

    # Detect library dependencies
    library_deps = []
    all_projects = storage.list_projects()
    for lib_name, lib_proj in all_projects.items():
        if lib_proj.get("project_type") != "LIBRARY":
            continue
        lib_classes = storage.get_classes_by_project(lib_name)
        lib_simple_names = {c.get("simple_name") for c in lib_classes.values()}
        for app_cls in classes.values():
            if set(app_cls.get("all_dependency_types", [])) & lib_simple_names:
                if lib_name not in library_deps:
                    library_deps.append(lib_name)
                break

    project_data = asdict(ProjectRecord(
        name=project_name, path=project_path, project_type="APPLICATION",
        spring_boot_version=boot_ver, java_version=java_ver,
        scanned_at=datetime.now().isoformat(),
        class_count=len(classes), file_hash=current_hash,
        depends_on=library_deps,
    ))

    storage.save_project(project_name, project_data)
    storage.save_classes(project_name, classes)

    return {
        "status": "SCANNED",
        "project": project_name,
        "storage_backend": storage.get_storage_info()["backend"],
        "total_classes": len(classes),
        "layer_breakdown": dict(layer_counts),
        "detected_library_dependencies": library_deps,
    }


@mcp.tool()
def find_cross_project_dependencies(project_name: str) -> dict:
    """Show all deps from an application to shared libraries."""
    if not storage.get_project(project_name):
        return {"error": f"Project '{project_name}' not found."}

    app_classes = storage.get_classes_by_project(project_name)
    all_classes = storage.get_all_classes()
    refs = []

    for fqcn, record in app_classes.items():
        for dep_type in record.get("all_dependency_types", []):
            for other_fqcn, other in all_classes.items():
                if other.get("project") == project_name:
                    continue
                if other.get("simple_name") == dep_type:
                    refs.append({
                        "app_class": fqcn, "reference_type": "INJECTS",
                        "target_class": other_fqcn, "target_project": other.get("project"),
                    })

        sc = record.get("superclass", "")
        if sc:
            for other_fqcn, other in all_classes.items():
                if other.get("project") != project_name and other.get("simple_name") == sc:
                    refs.append({
                        "app_class": fqcn, "reference_type": "EXTENDS",
                        "target_class": other_fqcn, "target_project": other.get("project"),
                    })

    by_project = defaultdict(list)
    for ref in refs:
        by_project[ref["target_project"]].append(ref)

    return {
        "project": project_name,
        "total_cross_refs": len(refs),
        "by_library": {k: {"count": len(v), "refs": v} for k, v in by_project.items()},
    }


@mcp.tool()
def find_library_impact(library_name: str, class_name: str = "") -> dict:
    """Analyze impact of changing a library class across ALL apps."""
    if not storage.get_project(library_name):
        return {"error": f"Library '{library_name}' not found."}

    lib_classes = storage.get_classes_by_project(library_name)
    if class_name:
        lib_classes = {k: v for k, v in lib_classes.items()
                       if v.get("simple_name") == class_name or k == class_name}
        if not lib_classes:
            return {"error": f"Class '{class_name}' not found in '{library_name}'."}

    lib_simple_names = {c.get("simple_name"): fqcn for fqcn, c in lib_classes.items()}
    all_classes = storage.get_all_classes()
    impact = []

    for fqcn, record in all_classes.items():
        if record.get("project") == library_name:
            continue
        if record.get("superclass") in lib_simple_names:
            impact.append({
                "affected_class": fqcn, "affected_project": record.get("project"),
                "dependency_type": "EXTENDS",
                "library_class": lib_simple_names[record["superclass"]], "risk": "HIGH",
            })
        for iface in record.get("interfaces", []):
            if iface in lib_simple_names:
                impact.append({
                    "affected_class": fqcn, "affected_project": record.get("project"),
                    "dependency_type": "IMPLEMENTS",
                    "library_class": lib_simple_names[iface], "risk": "HIGH",
                })
        for dep_type in record.get("all_dependency_types", []):
            if dep_type in lib_simple_names:
                impact.append({
                    "affected_class": fqcn, "affected_project": record.get("project"),
                    "dependency_type": "INJECTS",
                    "library_class": lib_simple_names[dep_type], "risk": "MEDIUM",
                })

    by_project = defaultdict(list)
    for item in impact:
        by_project[item["affected_project"]].append(item)

    return {
        "library": library_name, "class_filter": class_name or "(all)",
        "total_impact": len(impact), "affected_projects": len(by_project),
        "by_project": {k: {"count": len(v), "impacts": v} for k, v in by_project.items()},
    }


@mcp.tool()
def find_dao_service_relationships(project_name: str = "") -> dict:
    """Find DAO-Service relationships, resolving across libraries."""
    all_classes = storage.get_all_classes()
    all_dao_by_name = {}
    for fqcn, record in all_classes.items():
        if record.get("layer") == "DAO" or any(
            "Repository" in i or "Dao" in i
            for i in record.get("interfaces", []) + [record.get("simple_name", "")]
        ):
            all_dao_by_name[record.get("simple_name", "")] = record

    relationships = []
    for fqcn, record in all_classes.items():
        if record.get("layer") != "SERVICE":
            continue
        if project_name and record.get("project") != project_name:
            continue
        for dep in record.get("constructor_deps", []) + record.get("field_deps", []):
            dep_type = dep.get("type", "")
            if dep_type in all_dao_by_name:
                dao = all_dao_by_name[dep_type]
                relationships.append({
                    "service": fqcn, "service_project": record.get("project"),
                    "dao": dao.get("fqcn", ""), "dao_project": dao.get("project"),
                    "cross_project": record.get("project") != dao.get("project"),
                })

    return {"total": len(relationships), "relationships": relationships}


@mcp.tool()
def search_knowledge_base(query: str, project_filter: str = "", layer_filter: str = "") -> dict:
    """Search the KB by class name, annotation, or pattern."""
    results = storage.search_classes(query, project_filter, layer_filter)
    return {"query": query, "matches": len(results), "results": results}


@mcp.tool()
def list_projects() -> dict:
    """List all projects with summary stats."""
    projects = storage.list_projects()
    result = []
    for name, proj in projects.items():
        result.append({
            "name": name, "type": proj.get("project_type"),
            "path": proj.get("path"),
            "spring_boot_version": proj.get("spring_boot_version"),
            "java_version": proj.get("java_version"),
            "class_count": proj.get("class_count"),
            "scanned_at": proj.get("scanned_at"),
            "depends_on": proj.get("depends_on", []),
        })
    return {
        "total_projects": len(result),
        "storage": storage.get_storage_info(),
        "projects": result,
    }


@mcp.tool()
def get_class_detail(class_name: str) -> dict:
    """Get full detail for a class from the knowledge base."""
    record = storage.get_class(class_name)
    if not record:
        results = storage.search_classes(class_name)
        exact = [r for r in results if r.get("simple_name") == class_name]
        if len(exact) == 1:
            record = exact[0]
        elif exact:
            return {"error": "Ambiguous", "matches": [r.get("fqcn") for r in exact]}
        else:
            return {"error": f"'{class_name}' not found."}
    return record


@mcp.tool()
def rescan_project(project_name: str) -> dict:
    """Force a full rescan of a project."""
    proj = storage.get_project(project_name)
    if not proj:
        return {"error": f"'{project_name}' not in KB."}

    # Reset hash to force rescan
    proj["file_hash"] = ""
    storage.save_project(project_name, proj)

    if proj.get("project_type") == "LIBRARY":
        return scan_library(project_name, proj["path"])
    else:
        return scan_application(project_name, proj["path"])


@mcp.tool()
def remove_project(project_name: str) -> dict:
    """Remove a project from the knowledge base."""
    proj = storage.get_project(project_name)
    if not proj:
        return {"error": f"'{project_name}' not in KB."}
    class_count = len(storage.get_classes_by_project(project_name))
    storage.delete_project(project_name)
    return {"status": "removed", "project": project_name, "classes_removed": class_count}


@mcp.tool()
def migration_landscape_report() -> dict:
    """Generate a comprehensive migration landscape report."""
    all_projects = storage.list_projects()
    all_classes = storage.get_all_classes()

    javax_by_project = defaultdict(int)
    for fqcn, cls in all_classes.items():
        count = len(cls.get("javax_imports", []))
        if count:
            javax_by_project[cls.get("project", "")] += count

    project_graph = {
        name: {
            "type": p.get("project_type"), "depends_on": p.get("depends_on", []),
            "spring_boot": p.get("spring_boot_version"),
            "java": p.get("java_version"), "classes": p.get("class_count"),
        }
        for name, p in all_projects.items()
    }

    libraries = [n for n, p in all_projects.items() if p.get("project_type") == "LIBRARY"]
    apps = sorted(
        [n for n, p in all_projects.items() if p.get("project_type") == "APPLICATION"],
        key=lambda n: len(all_projects[n].get("depends_on", [])),
    )

    migration_order = []
    wave = 1
    if libraries:
        migration_order.append({"wave": wave, "name": "Shared Libraries", "projects": libraries})
        wave += 1
    for app in apps:
        migration_order.append({"wave": wave, "name": f"App: {app}", "projects": [app]})
        wave += 1

    return {
        "total_projects": len(all_projects),
        "total_classes": len(all_classes),
        "storage_backend": storage.get_storage_info()["backend"],
        "project_dependency_graph": project_graph,
        "javax_migration_scope": dict(javax_by_project),
        "suggested_project_migration_order": migration_order,
    }


@mcp.tool()
def storage_info() -> dict:
    """Show storage backend configuration and health."""
    return storage.get_storage_info()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("kb://status")
def kb_status() -> str:
    info = storage.get_storage_info()
    projects = storage.list_projects()
    lines = [
        "Migration Knowledge Base",
        "=" * 40,
        f"Backend: {info.get('backend')}",
    ]
    if info.get("backend") == "mongodb":
        lines.append(f"MongoDB: {info.get('uri')}")
        lines.append(f"Redis: {'enabled' if info.get('redis_enabled') else 'disabled'}")
    else:
        lines.append(f"Storage: {info.get('path')}")

    lines.append(f"Projects: {len(projects)}")
    lines.append("")

    for name, proj in projects.items():
        lines.append(
            f"  [{proj.get('project_type', '?'):11}] {name:25} "
            f"{proj.get('class_count', 0):4} classes"
        )
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    mcp.run()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Docker Compose Example
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# docker-compose.yml:
#
# version: '3.8'
# services:
#   mongodb:
#     image: mongo:7.0
#     ports: ["27017:27017"]
#     volumes:
#       - mongo-data:/data/db
#
#   redis:
#     image: redis:7-alpine
#     ports: ["6379:6379"]
#
#   migration-kb:
#     build: .
#     environment:
#       MIGRATION_KB_STORAGE: mongodb
#       MONGODB_URI: mongodb://mongodb:27017/migration_kb
#       REDIS_URL: redis://redis:6379/0
#       REDIS_CACHE_TTL: 3600
#     volumes:
#       - ./repos:/repos:ro          # Mount source repos read-only
#     depends_on:
#       - mongodb
#       - redis
#
# volumes:
#   mongo-data:
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Dockerfile Example
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# FROM python:3.12-slim
# RUN apt-get update && apt-get install -y openjdk-21-jdk-headless && rm -rf /var/lib/apt/lists/*
# WORKDIR /app
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt
# COPY *.py .
# ENV MIGRATION_KB_STORAGE=mongodb
# CMD ["python", "migration_kb_mcp_server.py"]
#
# requirements.txt:
#   fastmcp
#   pyyaml
#   jinja2
#   pymongo
#   redis
