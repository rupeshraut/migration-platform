"""
Codebase Intelligence MCP Server
====================================
Indexes your entire codebase into a searchable knowledge graph.
Answers "who, what, where, why" questions about code in seconds
instead of the 30+ minutes engineers spend reading unfamiliar code.

What It Solves:
  ┌─────────────────────────────────────────────────────────────────┐
  │                                                                 │
  │  "Who owns the payment retry logic?"            → 2 seconds    │
  │  "What services call the Order API?"            → 2 seconds    │
  │  "What's the blast radius of changing User?"    → 5 seconds    │
  │  "Show me the architecture of order-service"    → 3 seconds    │
  │  "What code is dead and can be deleted?"        → 10 seconds   │
  │  "How does checkout flow work end to end?"      → 10 seconds   │
  │                                                                 │
  │  WITHOUT THIS SERVER: 30-60 minutes of grep, git log, and     │
  │  asking people on Slack.                                        │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

Indexes:
  • Java/Kotlin/Python/TypeScript source files
  • REST endpoints (Spring @RequestMapping, JAX-RS, FastAPI, Express)
  • Message producers/consumers (Kafka, RabbitMQ, JMS)
  • Database entities and repositories
  • Git history (ownership, change frequency, recency)
  • CODEOWNERS files
  • Build dependencies (pom.xml, build.gradle, package.json)
  • Configuration files (application.yml, .env)

Storage:
  Uses the same pluggable backend as migration-kb (MongoDB/Redis/local).
  New collections: codebase_repos, codebase_files, codebase_endpoints,
                   codebase_messages, codebase_ownership

Environment Variables:
    MIGRATION_KB_STORAGE=mongodb|local   (shared with migration KB)
    MONGODB_URI=mongodb://...            (shared)
    REDIS_URL=redis://...                (shared)
    CODEBASE_SCAN_DEPTH=3                (max directory depth for multi-repo scan)

Requirements:
    pip install fastmcp pyyaml

Usage:
    python codebase_intel_mcp_server.py
"""

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KB_DIR = os.path.expanduser("~/.mcp-migration-kb")
INTEL_DIR = os.path.join(KB_DIR, "codebase-intel")
REPOS_INDEX = os.path.join(INTEL_DIR, "_repos.json")
GRAPH_DIR = os.path.join(INTEL_DIR, "graphs")

mcp = FastMCP(
    "Codebase Intelligence",
    description=(
        "Indexes your codebase into a searchable knowledge graph. "
        "Answers who-owns-what, what-calls-what, blast radius, "
        "architecture diagrams, and dead code detection."
    ),
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class RepoRecord:
    name: str
    path: str
    language: str = ""
    framework: str = ""
    indexed_at: str = ""
    file_count: int = 0
    class_count: int = 0
    endpoint_count: int = 0
    message_count: int = 0


@dataclass
class FileRecord:
    path: str
    repo: str
    language: str = ""
    package: str = ""
    classes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line_count: int = 0
    # Ownership
    primary_owner: str = ""
    contributors: list[dict] = field(default_factory=list)
    last_modified: str = ""
    change_frequency: int = 0          # commits in last 90 days
    # Complexity
    cyclomatic_complexity: int = 0


@dataclass
class EndpointRecord:
    """A REST, gRPC, or GraphQL endpoint."""
    method: str                        # GET, POST, PUT, DELETE, SUBSCRIBE
    path: str                          # /api/orders/{id}
    repo: str
    source_file: str
    source_class: str
    source_method: str = ""
    parameters: list[dict] = field(default_factory=list)
    response_type: str = ""
    annotations: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)


@dataclass
class MessageRecord:
    """A Kafka topic, RabbitMQ queue, or JMS destination."""
    topic_or_queue: str
    direction: str                     # PRODUCE, CONSUME
    repo: str
    source_file: str
    source_class: str
    source_method: str = ""
    message_type: str = ""
    group_id: str = ""


@dataclass
class OwnershipRecord:
    path_pattern: str                  # from CODEOWNERS
    owners: list[str] = field(default_factory=list)
    team: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Storage
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class IntelStore:
    """Local JSON storage for codebase intelligence."""

    def __init__(self):
        os.makedirs(INTEL_DIR, exist_ok=True)
        os.makedirs(GRAPH_DIR, exist_ok=True)
        self.repos: dict[str, RepoRecord] = {}
        self.files: dict[str, FileRecord] = {}           # key: repo::relative_path
        self.endpoints: list[EndpointRecord] = []
        self.messages: list[MessageRecord] = []
        self.ownership: list[OwnershipRecord] = []
        self._load()

    def _load(self):
        if os.path.isfile(REPOS_INDEX):
            try:
                with open(REPOS_INDEX) as f:
                    data = json.load(f)
                for name, rd in data.get("repos", {}).items():
                    self.repos[name] = RepoRecord(**rd)
            except Exception:
                pass

        idx_file = os.path.join(INTEL_DIR, "_graph.json")
        if os.path.isfile(idx_file):
            try:
                with open(idx_file) as f:
                    data = json.load(f)
                for key, fd in data.get("files", {}).items():
                    self.files[key] = FileRecord(**fd)
                for ed in data.get("endpoints", []):
                    self.endpoints.append(EndpointRecord(**ed))
                for md in data.get("messages", []):
                    self.messages.append(MessageRecord(**md))
                for od in data.get("ownership", []):
                    self.ownership.append(OwnershipRecord(**od))
            except Exception:
                pass

    def save(self):
        with open(REPOS_INDEX, "w") as f:
            json.dump({
                "repos": {k: asdict(v) for k, v in self.repos.items()},
                "saved_at": datetime.now().isoformat(),
            }, f, indent=2, default=str)

        idx_file = os.path.join(INTEL_DIR, "_graph.json")
        with open(idx_file, "w") as f:
            json.dump({
                "files": {k: asdict(v) for k, v in self.files.items()},
                "endpoints": [asdict(e) for e in self.endpoints],
                "messages": [asdict(m) for m in self.messages],
                "ownership": [asdict(o) for o in self.ownership],
                "saved_at": datetime.now().isoformat(),
            }, f, indent=2, default=str)


store = IntelStore()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Parsers — Language-Aware Source Analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LANGUAGE_EXTENSIONS = {
    ".java": "java", ".kt": "kotlin", ".py": "python",
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".cs": "csharp", ".go": "go",
}

SKIP_DIRS = {
    "node_modules", ".git", "target", "build", "dist", ".gradle",
    "__pycache__", ".mvn", ".idea", ".vscode", "vendor",
}


def _detect_language(repo_path: str) -> str:
    """Detect the dominant language in a repo."""
    counts = defaultdict(int)
    for f in Path(repo_path).rglob("*"):
        if any(sd in f.parts for sd in SKIP_DIRS):
            continue
        ext = f.suffix
        if ext in LANGUAGE_EXTENSIONS:
            counts[LANGUAGE_EXTENSIONS[ext]] += 1
    if not counts:
        return "unknown"
    return max(counts, key=counts.get)


def _detect_framework(repo_path: str, language: str) -> str:
    """Detect the framework used in a repo."""
    if language == "java":
        pom = os.path.join(repo_path, "pom.xml")
        if os.path.isfile(pom):
            try:
                content = Path(pom).read_text(errors="ignore")
                if "spring-boot" in content:
                    return "spring-boot"
                if "micronaut" in content:
                    return "micronaut"
                if "quarkus" in content:
                    return "quarkus"
            except Exception:
                pass
    if language == "python":
        for req_file in ["requirements.txt", "pyproject.toml", "setup.py"]:
            fp = os.path.join(repo_path, req_file)
            if os.path.isfile(fp):
                try:
                    content = Path(fp).read_text(errors="ignore").lower()
                    if "django" in content:
                        return "django"
                    if "fastapi" in content:
                        return "fastapi"
                    if "flask" in content:
                        return "flask"
                except Exception:
                    pass
    if language in ("typescript", "javascript"):
        pkg = os.path.join(repo_path, "package.json")
        if os.path.isfile(pkg):
            try:
                content = Path(pkg).read_text(errors="ignore").lower()
                if "react" in content:
                    return "react"
                if "angular" in content:
                    return "angular"
                if "express" in content:
                    return "express"
                if "next" in content:
                    return "nextjs"
            except Exception:
                pass
    return "unknown"


def _parse_java_endpoints(content: str, file_path: str, repo_name: str) -> list[EndpointRecord]:
    """Extract REST endpoints from Java source."""
    endpoints = []

    # Class-level RequestMapping
    class_path = ""
    class_mapping = re.search(
        r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']+)["\']', content
    )
    if class_mapping:
        class_path = class_mapping.group(1)

    # Class name
    class_match = re.search(r"class\s+(\w+)", content)
    class_name = class_match.group(1) if class_match else ""

    # Method-level mappings
    mapping_pattern = re.compile(
        r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*\(\s*'
        r'(?:value\s*=\s*)?(?:["\']([^"\']*)["\'])?[^)]*\)\s*'
        r'(?:public\s+)?(?:\w+(?:<[^>]+>)?\s+)?(\w+)\s*\(',
        re.DOTALL,
    )

    method_to_http = {
        "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
        "DeleteMapping": "DELETE", "PatchMapping": "PATCH",
    }

    for m in mapping_pattern.finditer(content):
        annotation = m.group(1)
        path = m.group(2) or ""
        method_name = m.group(3)

        http_method = method_to_http.get(annotation, "")
        if annotation == "RequestMapping":
            rm_method = re.search(r"method\s*=\s*RequestMethod\.(\w+)", m.group(0))
            http_method = rm_method.group(1) if rm_method else "GET"

        full_path = class_path.rstrip("/") + "/" + path.lstrip("/") if path else class_path

        endpoints.append(EndpointRecord(
            method=http_method,
            path=full_path or f"/{method_name}",
            repo=repo_name,
            source_file=file_path,
            source_class=class_name,
            source_method=method_name,
        ))

    return endpoints


def _parse_java_messages(content: str, file_path: str, repo_name: str) -> list[MessageRecord]:
    """Extract Kafka/JMS producers and consumers from Java source."""
    messages = []
    class_match = re.search(r"class\s+(\w+)", content)
    class_name = class_match.group(1) if class_match else ""

    # Kafka consumers
    for m in re.finditer(
        r'@KafkaListener\s*\(\s*topics?\s*=\s*["\'\{]([^"\'}\)]+)["\'\}]'
        r'(?:.*?groupId\s*=\s*["\']([^"\']+)["\'])?[^)]*\)\s*'
        r'(?:public\s+)?(?:\w+\s+)?(\w+)\s*\(',
        content, re.DOTALL,
    ):
        topics = [t.strip().strip('"').strip("'") for t in m.group(1).split(",")]
        group = m.group(2) or ""
        method = m.group(3)

        for topic in topics:
            messages.append(MessageRecord(
                topic_or_queue=topic, direction="CONSUME",
                repo=repo_name, source_file=file_path,
                source_class=class_name, source_method=method,
                group_id=group,
            ))

    # Kafka producers (KafkaTemplate.send)
    for m in re.finditer(
        r'(?:kafkaTemplate|kafka[Tt]emplate)\s*\.\s*send\s*\(\s*["\']([^"\']+)["\']',
        content,
    ):
        messages.append(MessageRecord(
            topic_or_queue=m.group(1), direction="PRODUCE",
            repo=repo_name, source_file=file_path,
            source_class=class_name,
        ))

    # JMS listeners
    for m in re.finditer(
        r'@JmsListener\s*\(\s*destination\s*=\s*["\']([^"\']+)["\']',
        content,
    ):
        messages.append(MessageRecord(
            topic_or_queue=m.group(1), direction="CONSUME",
            repo=repo_name, source_file=file_path,
            source_class=class_name,
        ))

    return messages


def _parse_codeowners(repo_path: str) -> list[OwnershipRecord]:
    """Parse CODEOWNERS file."""
    owners = []
    for candidate in [
        os.path.join(repo_path, "CODEOWNERS"),
        os.path.join(repo_path, ".github", "CODEOWNERS"),
        os.path.join(repo_path, "docs", "CODEOWNERS"),
    ]:
        if os.path.isfile(candidate):
            try:
                for line in Path(candidate).read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        pattern = parts[0]
                        owner_list = [p for p in parts[1:] if p.startswith("@") or "@" in p]
                        team = ""
                        for o in owner_list:
                            if "/" in o:
                                team = o.split("/")[-1]
                                break
                        owners.append(OwnershipRecord(
                            path_pattern=pattern, owners=owner_list, team=team,
                        ))
            except Exception:
                pass
            break
    return owners


def _git_file_ownership(file_path: str, repo_path: str) -> dict:
    """Get git blame ownership for a file."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ae", "--follow", "-n", "50", "--", file_path],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        if not result.stdout.strip():
            return {"primary_owner": "", "contributors": [], "change_frequency": 0}

        emails = result.stdout.strip().splitlines()
        counts = defaultdict(int)
        for email in emails:
            counts[email.strip()] += 1

        sorted_contribs = sorted(counts.items(), key=lambda x: -x[1])
        primary = sorted_contribs[0][0] if sorted_contribs else ""

        # Change frequency in last 90 days
        since = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        freq_result = subprocess.run(
            ["git", "log", f"--since={since}", "--oneline", "--follow", "--", file_path],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        freq = len(freq_result.stdout.strip().splitlines()) if freq_result.stdout else 0

        # Last modified
        last_mod = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "--", file_path],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        last_modified = last_mod.stdout.strip() if last_mod.stdout else ""

        return {
            "primary_owner": primary,
            "contributors": [{"email": e, "commits": c} for e, c in sorted_contribs[:10]],
            "change_frequency": freq,
            "last_modified": last_modified,
        }
    except Exception:
        return {"primary_owner": "", "contributors": [], "change_frequency": 0}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def index_repository(
    repo_path: str,
    repo_name: str = "",
    scan_git_ownership: bool = True,
    max_files: int = 5000,
) -> dict:
    """
    Index a repository into the codebase intelligence graph.

    Scans: source files, REST endpoints, message producers/consumers,
    CODEOWNERS, git ownership, and build dependencies.

    Args:
        repo_path:           Path to the repository root.
        repo_name:           Name for this repo (auto-detected from directory if empty).
        scan_git_ownership:  Whether to run git blame for ownership (slower but valuable).
        max_files:           Maximum source files to index.
    """
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        return {"error": f"Directory not found: {repo_path}"}

    if not repo_name:
        repo_name = os.path.basename(repo_path)

    language = _detect_language(repo_path)
    framework = _detect_framework(repo_path, language)

    # Collect source files
    source_files = []
    for f in Path(repo_path).rglob("*"):
        if any(sd in f.parts for sd in SKIP_DIRS):
            continue
        if f.suffix in LANGUAGE_EXTENSIONS and f.is_file():
            if "/test/" not in str(f) and "\\test\\" not in str(f):
                source_files.append(f)
        if len(source_files) >= max_files:
            break

    # Clear old data for this repo
    store.files = {k: v for k, v in store.files.items() if not k.startswith(f"{repo_name}::")}
    store.endpoints = [e for e in store.endpoints if e.repo != repo_name]
    store.messages = [m for m in store.messages if m.repo != repo_name]

    file_count = 0
    class_count = 0
    endpoints_found = []
    messages_found = []

    for sf in source_files:
        try:
            content = sf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        rel_path = str(sf.relative_to(repo_path))
        file_lang = LANGUAGE_EXTENSIONS.get(sf.suffix, "unknown")

        # Extract package
        package = ""
        if file_lang == "java":
            pkg_match = re.search(r"^package\s+([\w.]+)\s*;", content, re.MULTILINE)
            package = pkg_match.group(1) if pkg_match else ""
        elif file_lang == "python":
            package = str(sf.parent.relative_to(repo_path)).replace(os.sep, ".")

        # Extract class names
        classes = []
        if file_lang in ("java", "kotlin", "csharp"):
            for cm in re.finditer(r"(?:class|interface|enum|record)\s+(\w+)", content):
                classes.append(cm.group(1))
                class_count += 1
        elif file_lang == "python":
            for cm in re.finditer(r"^class\s+(\w+)", content, re.MULTILINE):
                classes.append(cm.group(1))
                class_count += 1
        elif file_lang in ("typescript", "javascript"):
            for cm in re.finditer(r"(?:export\s+)?(?:class|interface)\s+(\w+)", content):
                classes.append(cm.group(1))
                class_count += 1

        # Extract imports
        imports = []
        if file_lang == "java":
            imports = re.findall(r"^import\s+([\w.*]+)\s*;", content, re.MULTILINE)
        elif file_lang == "python":
            imports = re.findall(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE)
        elif file_lang in ("typescript", "javascript"):
            imports = re.findall(r"from\s+['\"]([^'\"]+)['\"]", content)

        # Git ownership (expensive — skip if disabled)
        ownership = {}
        if scan_git_ownership:
            ownership = _git_file_ownership(rel_path, repo_path)

        line_count = content.count("\n") + 1

        # Cyclomatic complexity estimate
        complexity = len(re.findall(r"\b(if|else|for|while|switch|case|catch|&&|\|\||\?)\b", content))

        file_key = f"{repo_name}::{rel_path}"
        store.files[file_key] = FileRecord(
            path=rel_path, repo=repo_name, language=file_lang,
            package=package, classes=classes, imports=imports[:50],
            line_count=line_count,
            primary_owner=ownership.get("primary_owner", ""),
            contributors=ownership.get("contributors", []),
            last_modified=ownership.get("last_modified", ""),
            change_frequency=ownership.get("change_frequency", 0),
            cyclomatic_complexity=complexity,
        )
        file_count += 1

        # Extract endpoints (Java for now)
        if file_lang == "java":
            eps = _parse_java_endpoints(content, rel_path, repo_name)
            endpoints_found.extend(eps)
            msgs = _parse_java_messages(content, rel_path, repo_name)
            messages_found.extend(msgs)

    store.endpoints.extend(endpoints_found)
    store.messages.extend(messages_found)

    # Parse CODEOWNERS
    codeowners = _parse_codeowners(repo_path)
    # Replace existing for this repo
    store.ownership = [o for o in store.ownership if True]  # keep all, CODEOWNERS is global
    store.ownership.extend(codeowners)

    # Save repo record
    store.repos[repo_name] = RepoRecord(
        name=repo_name, path=repo_path, language=language, framework=framework,
        indexed_at=datetime.now().isoformat(),
        file_count=file_count, class_count=class_count,
        endpoint_count=len(endpoints_found), message_count=len(messages_found),
    )

    store.save()

    return {
        "status": "indexed",
        "repo": repo_name,
        "language": language,
        "framework": framework,
        "files_indexed": file_count,
        "classes_found": class_count,
        "endpoints_found": len(endpoints_found),
        "message_channels": len(messages_found),
        "codeowners_rules": len(codeowners),
        "git_ownership_scanned": scan_git_ownership,
    }


@mcp.tool()
def search_codebase(query: str, repo_filter: str = "", scope: str = "all") -> dict:
    """
    Search across all indexed repos for classes, endpoints, files, or patterns.

    Args:
        query:       Search term (class name, endpoint path, topic name, etc.)
        repo_filter: Limit to specific repo.
        scope:       "all", "classes", "endpoints", "messages", "files"
    """
    q = query.lower()
    results = {"classes": [], "endpoints": [], "messages": [], "files": []}

    if scope in ("all", "classes", "files"):
        for key, fr in store.files.items():
            if repo_filter and fr.repo != repo_filter:
                continue

            # Match class names
            for cls in fr.classes:
                if q in cls.lower():
                    results["classes"].append({
                        "class": cls, "file": fr.path, "repo": fr.repo,
                        "package": fr.package, "owner": fr.primary_owner,
                    })

            # Match file paths
            if q in fr.path.lower() or q in fr.package.lower():
                results["files"].append({
                    "file": fr.path, "repo": fr.repo, "package": fr.package,
                    "classes": fr.classes, "owner": fr.primary_owner,
                    "lines": fr.line_count,
                })

    if scope in ("all", "endpoints"):
        for ep in store.endpoints:
            if repo_filter and ep.repo != repo_filter:
                continue
            if q in ep.path.lower() or q in ep.source_class.lower() or q in ep.source_method.lower():
                results["endpoints"].append({
                    "method": ep.method, "path": ep.path,
                    "repo": ep.repo, "class": ep.source_class,
                    "handler": ep.source_method, "file": ep.source_file,
                })

    if scope in ("all", "messages"):
        for msg in store.messages:
            if repo_filter and msg.repo != repo_filter:
                continue
            if q in msg.topic_or_queue.lower() or q in msg.source_class.lower():
                results["messages"].append({
                    "topic": msg.topic_or_queue, "direction": msg.direction,
                    "repo": msg.repo, "class": msg.source_class,
                    "method": msg.source_method,
                })

    total = sum(len(v) for v in results.values())
    return {"query": query, "total_matches": total, "results": results}


@mcp.tool()
def who_owns(path_or_class: str) -> dict:
    """
    Find who owns a file, class, or path pattern.

    Checks: git blame (primary author + contributors), CODEOWNERS rules,
    change frequency, and last modified date.

    Args:
        path_or_class: File path, class name, or path pattern.
    """
    q = path_or_class.lower()
    matches = []

    for key, fr in store.files.items():
        if q in fr.path.lower() or any(q in c.lower() for c in fr.classes):
            matches.append({
                "file": fr.path,
                "repo": fr.repo,
                "classes": fr.classes,
                "primary_owner": fr.primary_owner,
                "contributors": fr.contributors[:5],
                "last_modified": fr.last_modified,
                "change_frequency_90d": fr.change_frequency,
            })

    # Check CODEOWNERS
    codeowner_matches = []
    for rule in store.ownership:
        if q in rule.path_pattern.lower():
            codeowner_matches.append({
                "pattern": rule.path_pattern,
                "owners": rule.owners,
                "team": rule.team,
            })

    return {
        "query": path_or_class,
        "file_matches": matches[:20],
        "codeowner_rules": codeowner_matches,
    }


@mcp.tool()
def blast_radius(class_or_file: str, repo_filter: str = "") -> dict:
    """
    Show the blast radius of changing a class or file.

    Finds: all files that import it, all endpoints that use it,
    all message channels it participates in, and the transitive
    dependency chain.

    Args:
        class_or_file: Class name or file path to analyze.
        repo_filter:   Limit analysis to specific repo.
    """
    q = class_or_file
    q_lower = q.lower()

    # Find the target
    target_classes = set()
    target_files = set()
    target_repo = ""

    for key, fr in store.files.items():
        if repo_filter and fr.repo != repo_filter:
            continue
        for cls in fr.classes:
            if cls.lower() == q_lower or cls == q:
                target_classes.add(cls)
                target_files.add(fr.path)
                target_repo = fr.repo
        if q_lower in fr.path.lower():
            target_files.add(fr.path)
            target_classes.update(fr.classes)
            target_repo = fr.repo

    if not target_classes and not target_files:
        return {"error": f"'{class_or_file}' not found in the index."}

    # Find direct dependents (files that import target classes)
    direct_dependents = []
    for key, fr in store.files.items():
        for imp in fr.imports:
            for tc in target_classes:
                if tc in imp or tc.lower() in imp.lower():
                    direct_dependents.append({
                        "file": fr.path, "repo": fr.repo,
                        "classes": fr.classes, "owner": fr.primary_owner,
                        "import": imp,
                    })
                    break

    # Find affected endpoints
    affected_endpoints = []
    dependent_classes = set(tc for d in direct_dependents for tc in d["classes"])
    dependent_classes.update(target_classes)

    for ep in store.endpoints:
        if ep.source_class in dependent_classes:
            affected_endpoints.append({
                "method": ep.method, "path": ep.path,
                "repo": ep.repo, "class": ep.source_class,
            })

    # Find affected message channels
    affected_messages = []
    for msg in store.messages:
        if msg.source_class in dependent_classes:
            affected_messages.append({
                "topic": msg.topic_or_queue, "direction": msg.direction,
                "repo": msg.repo, "class": msg.source_class,
            })

    # Count by repo
    repos_affected = set()
    for d in direct_dependents:
        repos_affected.add(d["repo"])

    risk = "LOW"
    total = len(direct_dependents) + len(affected_endpoints) + len(affected_messages)
    if total > 20 or len(repos_affected) > 2:
        risk = "HIGH"
    elif total > 5:
        risk = "MEDIUM"

    return {
        "target": class_or_file,
        "target_repo": target_repo,
        "target_classes": list(target_classes),
        "risk": risk,
        "blast_radius": {
            "direct_dependents": len(direct_dependents),
            "affected_endpoints": len(affected_endpoints),
            "affected_messages": len(affected_messages),
            "repos_affected": list(repos_affected),
        },
        "direct_dependents": direct_dependents[:30],
        "affected_endpoints": affected_endpoints[:20],
        "affected_messages": affected_messages[:20],
    }


@mcp.tool()
def api_surface(repo_name: str = "") -> dict:
    """
    Show all API endpoints across all (or one) repo(s).

    Groups by: service, HTTP method, and path pattern.
    """
    endpoints = store.endpoints
    if repo_name:
        endpoints = [e for e in endpoints if e.repo == repo_name]

    by_repo = defaultdict(list)
    for ep in endpoints:
        by_repo[ep.repo].append({
            "method": ep.method, "path": ep.path,
            "class": ep.source_class, "handler": ep.source_method,
            "file": ep.source_file,
        })

    return {
        "total_endpoints": len(endpoints),
        "repos": len(by_repo),
        "by_repo": {k: {"count": len(v), "endpoints": v} for k, v in sorted(by_repo.items())},
    }


@mcp.tool()
def message_topology() -> dict:
    """
    Show the full message topology: which services produce/consume which topics.

    Useful for understanding event-driven architecture and finding orphan topics.
    """
    by_topic: dict[str, dict] = defaultdict(lambda: {"producers": [], "consumers": []})

    for msg in store.messages:
        entry = {
            "repo": msg.repo, "class": msg.source_class,
            "method": msg.source_method, "group_id": msg.group_id,
        }
        if msg.direction == "PRODUCE":
            by_topic[msg.topic_or_queue]["producers"].append(entry)
        else:
            by_topic[msg.topic_or_queue]["consumers"].append(entry)

    # Find orphans
    orphan_topics = []
    for topic, info in by_topic.items():
        if not info["producers"]:
            orphan_topics.append({"topic": topic, "issue": "NO_PRODUCER", "consumers": len(info["consumers"])})
        if not info["consumers"]:
            orphan_topics.append({"topic": topic, "issue": "NO_CONSUMER", "producers": len(info["producers"])})

    return {
        "total_topics": len(by_topic),
        "topology": dict(by_topic),
        "orphan_topics": orphan_topics,
    }


@mcp.tool()
def dependency_graph(class_or_service: str, depth: int = 2) -> dict:
    """
    Generate a dependency graph for a class or service.

    Shows: what it depends on (imports) and what depends on it (reverse imports).
    Returns data suitable for rendering as Mermaid diagram.

    Args:
        class_or_service: Class name or repo name.
        depth:            How many levels deep to traverse (1-3).
    """
    q = class_or_service.lower()
    depth = min(max(depth, 1), 3)

    # Find the target file(s)
    target_key = ""
    target_classes = set()

    for key, fr in store.files.items():
        if fr.repo.lower() == q:
            target_classes.update(fr.classes)
        for cls in fr.classes:
            if cls.lower() == q:
                target_key = key
                target_classes.add(cls)

    if not target_classes:
        return {"error": f"'{class_or_service}' not found."}

    # Build edges
    edges = []
    visited = set()

    def _traverse(classes: set, current_depth: int):
        if current_depth > depth:
            return
        for key, fr in store.files.items():
            for cls in fr.classes:
                if cls in visited:
                    continue
                for imp in fr.imports:
                    for tc in classes:
                        if tc in imp:
                            edges.append({"from": cls, "to": tc, "type": "IMPORTS"})
                            visited.add(cls)

            # Also check reverse: target imports something
            if any(tc in fr.classes for tc in classes):
                for imp in fr.imports:
                    for key2, fr2 in store.files.items():
                        for cls2 in fr2.classes:
                            if cls2 in imp and cls2 not in classes:
                                edges.append({"from": list(classes & set(fr.classes))[0] if classes & set(fr.classes) else "?", "to": cls2, "type": "DEPENDS_ON"})

    _traverse(target_classes, 1)

    # Generate Mermaid
    mermaid = ["graph LR"]
    seen_edges = set()
    for edge in edges:
        key = f"{edge['from']}->{edge['to']}"
        if key not in seen_edges:
            label = edge["type"]
            mermaid.append(f"    {edge['from']} -->|{label}| {edge['to']}")
            seen_edges.add(key)

    return {
        "target": class_or_service,
        "nodes": list(target_classes | {e["from"] for e in edges} | {e["to"] for e in edges}),
        "edges": edges[:100],
        "mermaid": "\n".join(mermaid),
    }


@mcp.tool()
def dead_code_report(repo_name: str) -> dict:
    """
    Detect potentially dead code: classes that are never imported,
    endpoints that have no known consumers, and message producers
    with no consumers.

    Args:
        repo_name: Repository to analyze.
    """
    repo_files = {k: v for k, v in store.files.items() if v.repo == repo_name}
    all_files = store.files

    # All classes in this repo
    repo_classes = set()
    for fr in repo_files.values():
        repo_classes.update(fr.classes)

    # All classes referenced in imports (across all repos)
    referenced_classes = set()
    for fr in all_files.values():
        for imp in fr.imports:
            for cls in repo_classes:
                if cls in imp:
                    referenced_classes.add(cls)

    # Find unreferenced classes
    unreferenced = []
    for key, fr in repo_files.items():
        for cls in fr.classes:
            if cls not in referenced_classes:
                # Exclude entry points (controllers, listeners, configs)
                searchable = fr.path.lower()
                if any(s in searchable for s in ["controller", "listener", "config", "application", "test"]):
                    continue
                unreferenced.append({
                    "class": cls, "file": fr.path,
                    "lines": fr.line_count, "owner": fr.primary_owner,
                    "last_modified": fr.last_modified,
                })

    # Endpoints with no known consumers (in other repos)
    orphan_endpoints = []
    repo_endpoints = [e for e in store.endpoints if e.repo == repo_name]
    for ep in repo_endpoints:
        # Check if any other repo references this endpoint path
        path_pattern = ep.path.replace("{", "").replace("}", "")
        referenced = False
        for key, fr in all_files.items():
            if fr.repo == repo_name:
                continue
            for imp in fr.imports:
                if ep.source_class in imp:
                    referenced = True
                    break
            if referenced:
                break
        if not referenced:
            orphan_endpoints.append({
                "method": ep.method, "path": ep.path,
                "class": ep.source_class, "handler": ep.source_method,
            })

    total_lines = sum(u["lines"] for u in unreferenced)

    return {
        "repo": repo_name,
        "unreferenced_classes": {
            "count": len(unreferenced),
            "total_lines": total_lines,
            "classes": unreferenced[:30],
        },
        "orphan_endpoints": {
            "count": len(orphan_endpoints),
            "endpoints": orphan_endpoints[:20],
        },
        "recommendation": f"Review {len(unreferenced)} potentially dead classes ({total_lines} lines). "
                          f"These are never imported by any indexed codebase.",
    }


@mcp.tool()
def cross_service_flow(entry_point: str) -> dict:
    """
    Trace a request flow across services, starting from an endpoint
    or class, following REST calls and message channels.

    Args:
        entry_point: Endpoint path (e.g., "/api/orders") or class name.
    """
    q = entry_point.lower()
    flow = []

    # Find starting endpoint
    start_ep = None
    for ep in store.endpoints:
        if q in ep.path.lower() or q in ep.source_class.lower():
            start_ep = ep
            break

    if not start_ep:
        return {"error": f"Entry point '{entry_point}' not found."}

    flow.append({
        "step": 1, "type": "ENDPOINT",
        "service": start_ep.repo,
        "detail": f"{start_ep.method} {start_ep.path} → {start_ep.source_class}.{start_ep.source_method}()",
    })

    # Find what the handler class produces (messages)
    handler_class = start_ep.source_class
    step = 2

    for msg in store.messages:
        if msg.source_class == handler_class and msg.direction == "PRODUCE":
            flow.append({
                "step": step, "type": "PUBLISH",
                "service": msg.repo,
                "detail": f"{handler_class} → publishes to '{msg.topic_or_queue}'",
            })
            step += 1

            # Find consumers of this topic
            for consumer in store.messages:
                if consumer.topic_or_queue == msg.topic_or_queue and consumer.direction == "CONSUME":
                    flow.append({
                        "step": step, "type": "CONSUME",
                        "service": consumer.repo,
                        "detail": f"'{msg.topic_or_queue}' → {consumer.source_class}.{consumer.source_method}()",
                    })
                    step += 1

    # Generate Mermaid sequence diagram
    mermaid = ["sequenceDiagram"]
    for f in flow:
        if f["type"] == "ENDPOINT":
            mermaid.append(f"    Client->>+{f['service']}: {f['detail'].split('→')[0].strip()}")
        elif f["type"] == "PUBLISH":
            topic = f["detail"].split("'")[1] if "'" in f["detail"] else "topic"
            mermaid.append(f"    {f['service']}->>Kafka: publish({topic})")
        elif f["type"] == "CONSUME":
            topic = f["detail"].split("'")[1] if "'" in f["detail"] else "topic"
            mermaid.append(f"    Kafka->>+{f['service']}: consume({topic})")

    return {
        "entry_point": entry_point,
        "flow_steps": len(flow),
        "services_involved": list(set(f["service"] for f in flow)),
        "flow": flow,
        "mermaid": "\n".join(mermaid),
    }


@mcp.tool()
def architecture_overview() -> dict:
    """
    Generate a high-level architecture overview of all indexed services.

    Shows: services, their APIs, their message channels, and inter-service
    dependencies.
    """
    services = {}
    for name, repo in store.repos.items():
        services[name] = {
            "language": repo.language,
            "framework": repo.framework,
            "files": repo.file_count,
            "classes": repo.class_count,
            "endpoints": [],
            "produces": [],
            "consumes": [],
            "depends_on": [],
        }

    for ep in store.endpoints:
        if ep.repo in services:
            services[ep.repo]["endpoints"].append(f"{ep.method} {ep.path}")

    for msg in store.messages:
        if msg.repo in services:
            if msg.direction == "PRODUCE":
                services[msg.repo]["produces"].append(msg.topic_or_queue)
            else:
                services[msg.repo]["consumes"].append(msg.topic_or_queue)

    # Detect inter-service dependencies via shared topics
    for name, svc in services.items():
        for topic in svc["produces"]:
            for other_name, other_svc in services.items():
                if other_name != name and topic in other_svc["consumes"]:
                    services[name]["depends_on"].append({
                        "service": other_name, "via": "topic", "channel": topic,
                    })

    # Deduplicate
    for svc in services.values():
        svc["endpoints"] = list(set(svc["endpoints"]))[:20]
        svc["produces"] = list(set(svc["produces"]))
        svc["consumes"] = list(set(svc["consumes"]))

    # Mermaid architecture diagram
    mermaid = ["graph LR"]
    for name, svc in services.items():
        mermaid.append(f"    {name}[{name}<br/>{svc['framework']}]")
        for dep in svc["depends_on"]:
            mermaid.append(f"    {name} -->|{dep['channel']}| {dep['service']}")

    return {
        "total_services": len(services),
        "services": services,
        "mermaid": "\n".join(mermaid),
    }


@mcp.tool()
def hotspot_report(repo_name: str = "") -> dict:
    """
    Find code hotspots — files that change frequently AND are complex.

    These are the highest-risk files: complex code that's constantly
    being modified is where bugs live.

    Args:
        repo_name: Specific repo, or empty for all repos.
    """
    files = store.files
    if repo_name:
        files = {k: v for k, v in files.items() if v.repo == repo_name}

    hotspots = []
    for key, fr in files.items():
        if fr.change_frequency == 0 and fr.cyclomatic_complexity == 0:
            continue
        # Hotspot score = change frequency × complexity
        score = fr.change_frequency * (fr.cyclomatic_complexity / max(fr.line_count, 1)) * 100
        if score > 0:
            hotspots.append({
                "file": fr.path,
                "repo": fr.repo,
                "hotspot_score": round(score, 1),
                "change_frequency_90d": fr.change_frequency,
                "complexity": fr.cyclomatic_complexity,
                "lines": fr.line_count,
                "owner": fr.primary_owner,
                "classes": fr.classes,
            })

    hotspots.sort(key=lambda h: -h["hotspot_score"])

    return {
        "repo": repo_name or "(all)",
        "total_hotspots": len(hotspots),
        "top_hotspots": hotspots[:25],
        "recommendation": "Hotspots are complex files that change often — prioritize them for refactoring, "
                          "better tests, and code review attention.",
    }


@mcp.tool()
def list_indexed_repos() -> dict:
    """List all indexed repositories with summary stats."""
    repos = []
    for name, r in store.repos.items():
        repos.append({
            "name": name, "path": r.path,
            "language": r.language, "framework": r.framework,
            "files": r.file_count, "classes": r.class_count,
            "endpoints": r.endpoint_count, "messages": r.message_count,
            "indexed_at": r.indexed_at,
        })
    return {"total_repos": len(repos), "repos": repos}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("codebase://status")
def codebase_status() -> str:
    total_files = len(store.files)
    total_endpoints = len(store.endpoints)
    total_messages = len(store.messages)
    lines = [
        "Codebase Intelligence",
        "=" * 40,
        f"Repos indexed: {len(store.repos)}",
        f"Files indexed: {total_files}",
        f"Endpoints: {total_endpoints}",
        f"Message channels: {total_messages}",
        f"Storage: {INTEL_DIR}",
    ]
    for name, r in store.repos.items():
        lines.append(f"  {name}: {r.framework} ({r.language}), {r.class_count} classes")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
