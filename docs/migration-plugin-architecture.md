# Extending the Migration Platform — Plugin Architecture

## Multi-Language, Multi-Framework Code Migration

---

## 1. Plugin Architecture Overview

The migration platform becomes a **generic migration engine** with pluggable components for each migration use case. Each plugin provides five things:

```
┌──────────────────────────────────────────────────────────────────────┐
│                    MIGRATION PLATFORM CORE                           │
│                    (language-agnostic)                                │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  Knowledge Base   │  │  Mapping Rules   │  │  Template Engine │  │
│  │  (MongoDB/Redis)  │  │  Engine          │  │  (Jinja2)        │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  Golden Sample    │  │  Validator       │  │  Progress        │  │
│  │  Runner           │  │  Framework       │  │  Tracking        │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                      │
│  ═══════════════════════════════════════════════════════════════════ │
│                                                                      │
│  PLUGIN INTERFACE — each use case provides:                         │
│                                                                      │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │
│  │ 1. Parser   │ │ 2. Layer    │ │ 3. Templates│ │ 4. Lint     │  │
│  │    Module   │ │    Taxonomy │ │    (.tpl)   │ │    Rules    │  │
│  │             │ │             │ │             │ │             │  │
│  │ Reads source│ │ Defines     │ │ Code gen    │ │ Convention  │  │
│  │ or bytecode │ │ layers for  │ │ patterns    │ │ checks      │  │
│  │ → ClassRecord│ │ this stack │ │ for target  │ │ for target  │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘  │
│                                                                      │
│  ┌─────────────┐                                                    │
│  │ 5. Compile  │                                                    │
│  │    Command  │                                                    │
│  │             │                                                    │
│  │ How to      │                                                    │
│  │ verify build│                                                    │
│  └─────────────┘                                                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Plugin Interface

Every migration plugin implements this contract:

```python
class MigrationPlugin(ABC):
    """Base interface for all migration plugins."""

    # ── Identity ──
    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique plugin identifier, e.g., 'spring-boot-3', 'dotnet-8', 'django-fastapi'"""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g., 'Spring Boot 2→3 + Event-Driven'"""

    @property
    @abstractmethod
    def source_language(self) -> str:
        """Source language: 'java', 'csharp', 'python', 'typescript', 'sql'"""

    @property
    @abstractmethod
    def target_language(self) -> str:
        """Target language (often same as source)"""

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions to scan: ['.java'], ['.cs'], ['.py'], ['.ts', '.tsx']"""

    # ── 1. Parser ──
    @abstractmethod
    def parse_file(self, file_path: str, project_name: str) -> Optional[dict]:
        """
        Parse a single source file into a normalized ClassRecord dict.

        The dict MUST contain at minimum:
          fqcn, simple_name, package, project, file_path,
          class_type, layer, annotations, superclass, interfaces,
          public_methods, all_dependency_types

        Additional fields are plugin-specific and will be stored as-is.
        """

    # ── 2. Layer Taxonomy ──
    @abstractmethod
    def get_layer_taxonomy(self) -> dict[str, str]:
        """
        Return the layer classification for this stack.
        Keys are layer names, values are descriptions.

        Example (Java):
          {"DAO": "Data access", "SERVICE": "Business logic", ...}

        Example (Python/Django):
          {"MODEL": "Django model", "VIEW": "Django view", "SERIALIZER": "DRF serializer", ...}

        Example (C#/.NET):
          {"REPOSITORY": "Data access", "SERVICE": "Business logic",
           "CONTROLLER": "API controller", "ENTITY": "EF Core entity", ...}
        """

    @abstractmethod
    def classify_layer(self, record: dict) -> str:
        """Classify a parsed record into one of the taxonomy layers."""

    # ── 3. Templates ──
    @abstractmethod
    def get_default_templates(self) -> dict[str, dict]:
        """
        Return built-in templates for this plugin.
        Same structure as DEFAULT_TEMPLATES in the template engine.

        Key: template_id
        Value: {"file_name", "description", "content", "output_suffix", "target_layer", ...}
        """

    # ── 4. Lint Rules ──
    @abstractmethod
    def get_lint_rules(self) -> list[dict]:
        """
        Return convention check rules for generated code.

        Each rule:
          {"rule_id": "NO_JAVAX", "severity": "CRITICAL",
           "pattern": regex, "message": "...", "fix": "..."}
        """

    # ── 5. Compile/Build Command ──
    @abstractmethod
    def get_build_command(self, output_dir: str, **kwargs) -> list[str]:
        """
        Return the shell command to compile/verify generated code.

        Java:    ["javac", "-cp", classpath, ...]
        Python:  ["python", "-m", "py_compile", ...]
        C#:      ["dotnet", "build", output_dir]
        TypeScript: ["npx", "tsc", "--noEmit", ...]
        """

    # ── Optional hooks ──
    def post_scan_hook(self, project_name: str, classes: dict) -> dict:
        """Called after scanning a project. Can enrich or transform records."""
        return classes

    def pre_generate_hook(self, legacy: dict, context: dict) -> dict:
        """Called before template rendering. Can modify the template context."""
        return context

    def get_dependency_parser(self) -> Optional[callable]:
        """
        Return a function that parses dependency files:
          Java: pom.xml / build.gradle
          Python: requirements.txt / pyproject.toml
          C#: *.csproj
          Node: package.json
        """
        return None
```

---

## 3. Example Plugins

### Plugin: Spring Boot 2→3 + Event-Driven (current, refactored)

```python
class SpringBootPlugin(MigrationPlugin):
    plugin_id = "spring-boot-3"
    display_name = "Spring Boot 2.x → 3.x + Event-Driven"
    source_language = "java"
    target_language = "java"
    file_extensions = [".java"]

    def parse_file(self, file_path, project_name):
        # Existing regex-based Java parser
        return _parse_java_source(file_path, project_name)

    def get_layer_taxonomy(self):
        return {
            "ENTITY": "JPA/Mongo entity or DTO",
            "DAO": "Repository / Data Access",
            "SERVICE": "Business logic",
            "CONTROLLER": "REST API endpoint",
            "MESSAGING": "Kafka/JMS listener",
            "CONFIG": "Spring configuration",
            "SECURITY": "Security configuration",
            "EXCEPTION": "Exception handlers",
            "INFRASTRUCTURE": "Utilities and helpers",
        }

    def classify_layer(self, record):
        # Existing classification logic
        return _classify_layer(record)

    def get_default_templates(self):
        # Return the 8 existing templates
        return {
            "event_driven_service": {...},
            "reactive_repository": {...},
            "domain_event": {...},
            "kafka_consumer": {...},
            ...
        }

    def get_lint_rules(self):
        return [
            {"rule_id": "NO_JAVAX_IMPORTS", "severity": "CRITICAL",
             "pattern": r"^import\s+javax\.", "message": "Use jakarta.*"},
            {"rule_id": "NO_FIELD_INJECTION", "severity": "HIGH",
             "pattern": r"@Autowired\s+private", "message": "Use constructor injection"},
            ...
        ]

    def get_build_command(self, output_dir, **kwargs):
        classpath = kwargs.get("classpath", "")
        return ["javac", "-proc:none", "-cp", classpath] + glob(f"{output_dir}/**/*.java")
```

### Plugin: .NET Framework → .NET 8

```python
class DotNetPlugin(MigrationPlugin):
    plugin_id = "dotnet-8"
    display_name = ".NET Framework 4.x → .NET 8"
    source_language = "csharp"
    target_language = "csharp"
    file_extensions = [".cs"]

    def parse_file(self, file_path, project_name):
        content = Path(file_path).read_text()

        # C# parsing via regex (similar approach to Java)
        namespace = re.search(r"namespace\s+([\w.]+)", content)
        class_decl = re.search(
            r"((?:\[[\w.]+(?:\(.*?\))?\]\s*)*)"
            r"(?:public|internal|private)?\s*"
            r"(?:abstract\s+|sealed\s+|static\s+|partial\s+)*"
            r"(class|interface|enum|struct|record)\s+(\w+)"
            r"(?:<([^>]+)>)?"
            r"(?:\s*:\s*([\w.<>,\s]+))?",
            content,
        )

        if not class_decl:
            return None

        return {
            "fqcn": f"{namespace.group(1)}.{class_decl.group(3)}" if namespace else class_decl.group(3),
            "simple_name": class_decl.group(3),
            "package": namespace.group(1) if namespace else "",
            "project": project_name,
            "file_path": file_path,
            "class_type": class_decl.group(2).upper(),
            "annotations": re.findall(r"\[(\w+)", class_decl.group(1) or ""),
            # ... extract dependencies from constructor, [Inject], etc.
        }

    def get_layer_taxonomy(self):
        return {
            "ENTITY": "EF Core entity / model",
            "REPOSITORY": "Data access (Repository pattern or DbContext)",
            "SERVICE": "Business logic / application service",
            "CONTROLLER": "API Controller / MVC Controller",
            "MIDDLEWARE": "HTTP middleware",
            "BACKGROUND": "Hosted service / background worker",
            "CONFIG": "Startup / Program.cs configuration",
            "DTO": "Data transfer object / API model",
        }

    def classify_layer(self, record):
        name = record.get("fqcn", "").lower()
        annotations = record.get("annotations", [])

        if "ApiController" in annotations or "Controller" in annotations:
            return "CONTROLLER"
        if "DbContext" in record.get("superclass", ""):
            return "REPOSITORY"
        if any(s in name for s in [".repository.", ".dal.", ".data."]):
            return "REPOSITORY"
        if any(s in name for s in [".service.", ".application."]):
            return "SERVICE"
        if any(s in name for s in [".entity.", ".model.", ".domain."]):
            return "ENTITY"
        if any(s in name for s in [".middleware."]):
            return "MIDDLEWARE"
        if "BackgroundService" in record.get("superclass", ""):
            return "BACKGROUND"
        return "UNKNOWN"

    def get_default_templates(self):
        return {
            "minimal_api_controller": {
                "file_name": "minimal_api_controller.cs.tpl",
                "description": "Migrate MVC controller to .NET 8 Minimal API",
                "content": '''
using Microsoft.AspNetCore.Mvc;

namespace {{ target.namespace }};

/// <summary>
/// Migrated from: {{ legacy.fqcn }}
/// </summary>
public static class {{ target.class_name }}Endpoints
{
    public static void Map{{ legacy.entity_name }}Endpoints(this WebApplication app)
    {
        var group = app.MapGroup("/api/{{ legacy.entity_name | lower }}s");

        {% for method in legacy.public_methods %}
        {% if "HttpGet" in method.annotations %}
        group.MapGet("/", {{ target.class_name }}Handlers.{{ method.name }});
        {% elif "HttpPost" in method.annotations %}
        group.MapPost("/", {{ target.class_name }}Handlers.{{ method.name }});
        {% endif %}
        {% endfor %}
    }
}
''',
            },
            "ef_core_repository": {...},
            "mediatr_handler": {...},
            "integration_test_dotnet": {...},
        }

    def get_lint_rules(self):
        return [
            {"rule_id": "NO_SYSTEM_WEB", "severity": "CRITICAL",
             "pattern": r"using\s+System\.Web", "message": "System.Web not available in .NET 8"},
            {"rule_id": "NO_SYNC_OVER_ASYNC", "severity": "HIGH",
             "pattern": r"\.Result\b|\.Wait\(\)", "message": "Use await instead of .Result/.Wait()"},
            {"rule_id": "NO_NEWTONSOFT_DEFAULT", "severity": "MEDIUM",
             "pattern": r"using\s+Newtonsoft\.Json", "message": "Prefer System.Text.Json in .NET 8"},
        ]

    def get_build_command(self, output_dir, **kwargs):
        return ["dotnet", "build", output_dir, "--no-restore"]

    def get_dependency_parser(self):
        def parse_csproj(project_path):
            # Parse *.csproj for PackageReference, TargetFramework
            ...
        return parse_csproj
```

### Plugin: Python Django → FastAPI

```python
class DjangoToFastAPIPlugin(MigrationPlugin):
    plugin_id = "django-fastapi"
    display_name = "Django → FastAPI + SQLAlchemy"
    source_language = "python"
    target_language = "python"
    file_extensions = [".py"]

    def parse_file(self, file_path, project_name):
        import ast
        content = Path(file_path).read_text()
        tree = ast.parse(content)

        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [self._get_name(b) for b in node.bases]
                decorators = [self._get_name(d) for d in node.decorator_list]
                methods = [
                    {"name": n.name, "is_async": isinstance(n, ast.AsyncFunctionDef)}
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                return {
                    "fqcn": f"{file_path}::{node.name}",
                    "simple_name": node.name,
                    "class_type": "CLASS",
                    "superclass": bases[0] if bases else "",
                    "interfaces": bases,
                    "annotations": decorators,
                    "public_methods": methods,
                    # ...
                }
        return None

    def get_layer_taxonomy(self):
        return {
            "MODEL": "Django model → SQLAlchemy model",
            "VIEW": "Django view/viewset → FastAPI router",
            "SERIALIZER": "DRF serializer → Pydantic model",
            "URL_CONF": "urls.py → FastAPI router registration",
            "MIDDLEWARE": "Django middleware → FastAPI middleware",
            "CELERY_TASK": "Celery task → background task / message handler",
            "ADMIN": "Django admin (may not have direct equivalent)",
            "MANAGEMENT_CMD": "Management command → CLI or scheduled task",
            "SIGNAL": "Django signal → event handler",
            "FORM": "Django form → Pydantic model",
        }

    def get_default_templates(self):
        return {
            "fastapi_router": {
                "file_name": "fastapi_router.py.tpl",
                "content": '''
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/{{ legacy.entity_name | lower }}s", tags=["{{ legacy.entity_name }}"])

{% for method in legacy.public_methods %}
{% if method.name.startswith("list") or method.name.startswith("get") %}
@router.get("/")
async def {{ method.name }}(db: AsyncSession = Depends(get_db)):
    """Migrated from {{ legacy.simple_name }}.{{ method.name }}"""
    # TODO: Implement
    pass
{% elif method.name.startswith("create") %}
@router.post("/", status_code=status.HTTP_201_CREATED)
async def {{ method.name }}(db: AsyncSession = Depends(get_db)):
    """Migrated from {{ legacy.simple_name }}.{{ method.name }}"""
    # TODO: Implement
    pass
{% endif %}
{% endfor %}
''',
            },
            "sqlalchemy_model": {...},
            "pydantic_schema": {...},
            "pytest_integration": {...},
        }

    def get_lint_rules(self):
        return [
            {"rule_id": "NO_DJANGO_IMPORTS", "severity": "CRITICAL",
             "pattern": r"from\s+django\.", "message": "Django import in FastAPI code"},
            {"rule_id": "ASYNC_REQUIRED", "severity": "HIGH",
             "pattern": r"def\s+(?!__)\w+\(.*\):", "message": "Use async def for route handlers"},
        ]

    def get_build_command(self, output_dir, **kwargs):
        return ["python", "-m", "py_compile"] + glob(f"{output_dir}/**/*.py")
```

### Plugin: Angular → React

```python
class AngularToReactPlugin(MigrationPlugin):
    plugin_id = "angular-react"
    display_name = "Angular → React + TypeScript"
    source_language = "typescript"
    target_language = "typescript"
    file_extensions = [".ts", ".tsx", ".component.ts", ".service.ts"]

    def parse_file(self, file_path, project_name):
        content = Path(file_path).read_text()

        # TypeScript/Angular component detection
        component_match = re.search(
            r"@Component\(\{([^}]+)\}\)\s*"
            r"export\s+class\s+(\w+)",
            content, re.DOTALL,
        )
        service_match = re.search(
            r"@Injectable\([^)]*\)\s*"
            r"export\s+class\s+(\w+)",
            content,
        )
        # ... parse selector, templateUrl, inputs/outputs, methods, injections

    def get_layer_taxonomy(self):
        return {
            "COMPONENT": "Angular component → React component",
            "SERVICE": "Injectable service → React hook / context",
            "MODULE": "NgModule → removed (React doesn't need modules)",
            "PIPE": "Angular pipe → custom hook or utility",
            "DIRECTIVE": "Angular directive → React HOC or hook",
            "GUARD": "Route guard → React router middleware",
            "INTERCEPTOR": "HTTP interceptor → Axios interceptor",
            "RESOLVER": "Route resolver → React Query / loader",
            "STORE": "NgRx store → Zustand / Redux Toolkit",
        }

    def get_default_templates(self):
        return {
            "react_functional_component": {
                "file_name": "react_component.tsx.tpl",
                "content": '''
import React from 'react';
{% for dep in target.hook_deps %}
import { {{ dep.hook_name }} } from '{{ dep.import_path }}';
{% endfor %}

interface {{ target.class_name }}Props {
{% for input in legacy.inputs %}
  {{ input.name }}{{ "?" if input.optional else "" }}: {{ input.type }};
{% endfor %}
}

/**
 * Migrated from Angular: {{ legacy.fqcn }}
 * Selector: {{ legacy.selector }}
 */
export const {{ target.class_name }}: React.FC<{{ target.class_name }}Props> = ({
{% for input in legacy.inputs %}
  {{ input.name }},
{% endfor %}
}) => {
{% for dep in target.hook_deps %}
  const {{ dep.var_name }} = {{ dep.hook_name }}();
{% endfor %}

  // TODO: Migrate lifecycle hooks
{% if legacy.has_on_init %}
  React.useEffect(() => {
    // Migrated from ngOnInit()
  }, []);
{% endif %}

  return (
    <div>
      {/* TODO: Migrate template from {{ legacy.template_url }} */}
    </div>
  );
};
''',
            },
            "custom_hook_from_service": {...},
            "react_context_from_store": {...},
        }

    def get_lint_rules(self):
        return [
            {"rule_id": "NO_ANGULAR_IMPORTS", "severity": "CRITICAL",
             "pattern": r"from\s+'@angular/", "message": "Angular import in React code"},
            {"rule_id": "NO_CLASS_COMPONENTS", "severity": "HIGH",
             "pattern": r"class\s+\w+\s+extends\s+React\.Component",
             "message": "Use functional components with hooks"},
        ]

    def get_build_command(self, output_dir, **kwargs):
        return ["npx", "tsc", "--noEmit", "--project", output_dir]
```

### Plugin: Oracle SQL → PostgreSQL

```python
class OracleToPostgresPlugin(MigrationPlugin):
    plugin_id = "oracle-postgres"
    display_name = "Oracle → PostgreSQL"
    source_language = "sql"
    target_language = "sql"
    file_extensions = [".sql", ".pks", ".pkb", ".prc", ".fnc"]

    def parse_file(self, file_path, project_name):
        content = Path(file_path).read_text()
        # Detect: CREATE TABLE, CREATE PROCEDURE, CREATE PACKAGE, sequences, triggers
        # Return: table definitions, stored procedure signatures, data types

    def get_layer_taxonomy(self):
        return {
            "TABLE": "Table definition (DDL)",
            "VIEW": "View definition",
            "PROCEDURE": "Stored procedure → PostgreSQL function",
            "FUNCTION": "Oracle function → PostgreSQL function",
            "PACKAGE": "Oracle package → PostgreSQL schema + functions",
            "TRIGGER": "Oracle trigger → PostgreSQL trigger",
            "SEQUENCE": "Oracle sequence → PostgreSQL sequence / IDENTITY",
            "TYPE": "Oracle TYPE → PostgreSQL composite type",
            "SYNONYM": "Oracle synonym → PostgreSQL search_path",
        }

    def get_default_templates(self):
        return {
            "postgres_table": {
                "content": '''
-- Migrated from Oracle: {{ legacy.fqcn }}
CREATE TABLE {{ target.schema }}.{{ legacy.table_name }} (
{% for col in legacy.columns %}
    {{ col.name }} {{ col.type | oracle_to_pg_type }}{{ " NOT NULL" if col.not_null else "" }},
{% endfor %}
    PRIMARY KEY ({{ legacy.pk_columns | join(", ") }})
);

{% for idx in legacy.indexes %}
CREATE INDEX idx_{{ legacy.table_name }}_{{ idx.name }}
    ON {{ target.schema }}.{{ legacy.table_name }} ({{ idx.columns | join(", ") }});
{% endfor %}
''',
            },
            "postgres_function_from_procedure": {...},
            "flyway_migration_script": {...},
        }

    def get_lint_rules(self):
        return [
            {"rule_id": "NO_ORACLE_TYPES", "severity": "CRITICAL",
             "pattern": r"\bNUMBER\b|\bVARCHAR2\b|\bNVARCHAR2\b",
             "message": "Oracle data type detected — use PostgreSQL equivalents"},
            {"rule_id": "NO_DUAL", "severity": "MEDIUM",
             "pattern": r"FROM\s+DUAL", "message": "Remove FROM DUAL (not needed in PostgreSQL)"},
            {"rule_id": "NO_SYSDATE", "severity": "MEDIUM",
             "pattern": r"\bSYSDATE\b", "message": "Use CURRENT_TIMESTAMP instead of SYSDATE"},
        ]

    def get_build_command(self, output_dir, **kwargs):
        pg_uri = kwargs.get("pg_uri", "postgresql://localhost/test")
        return ["psql", pg_uri, "-f", f"{output_dir}/verify.sql", "--set", "ON_ERROR_STOP=on"]
```

---

## 4. Plugin Registry and Discovery

```
~/.mcp-migration-kb/
├── plugins/
│   ├── _plugin_registry.json        ← Which plugins are active
│   ├── spring-boot-3/
│   │   ├── plugin.py                ← Plugin implementation
│   │   ├── templates/               ← Plugin-specific templates
│   │   ├── golden-samples/          ← Plugin-specific golden samples
│   │   └── lint-rules.json          ← Convention rules
│   ├── dotnet-8/
│   │   ├── plugin.py
│   │   ├── templates/
│   │   └── ...
│   ├── django-fastapi/
│   │   ├── plugin.py
│   │   └── ...
│   └── angular-react/
│       ├── plugin.py
│       └── ...
├── templates/                        ← Shared + active plugin templates
├── golden-samples/                   ← Shared + active plugin golden samples
└── ...
```

Plugin registry JSON:

```json
{
  "active_plugins": ["spring-boot-3", "dotnet-8"],
  "plugins": {
    "spring-boot-3": {
      "path": "plugins/spring-boot-3/plugin.py",
      "class": "SpringBootPlugin",
      "enabled": true,
      "installed_at": "2026-03-01"
    },
    "dotnet-8": {
      "path": "plugins/dotnet-8/plugin.py",
      "class": "DotNetPlugin",
      "enabled": true,
      "installed_at": "2026-03-15"
    }
  }
}
```

---

## 5. Changes to Existing Servers

### Knowledge Base — No Changes Needed

The KB stores `dict` records, not Java-specific objects. The `ClassRecord` is already a generic bag of properties. A C# class, a Python class, and a Java class all map to the same structure:

```json
{
  "fqcn": "com.company.OrderService",       // Java
  "fqcn": "Company.Order.OrderService",      // C#
  "fqcn": "app.services.order_service::OrderService",  // Python
  "fqcn": "src/app/order/order.component.ts::OrderComponent",  // Angular

  "simple_name": "OrderService",
  "package": "com.company",
  "project": "order-service",
  "layer": "SERVICE",
  "class_type": "CLASS",
  "superclass": "...",
  "annotations": [...],
  "public_methods": [...],
  "all_dependency_types": [...]
}
```

The KB just stores and retrieves dicts — it doesn't care what language they came from. Add a `"language": "java"` field and you can have multi-language projects in the same KB.

### Mapping Rules Engine — One New Field

Add `plugin_id` to mapping rules so rules are scoped to their use case:

```json
{
  "rule_id": "dao-to-reactive-repo",
  "plugin_id": "spring-boot-3",
  "legacy_match": {"layer": "DAO"},
  "target_transform": {"extends": "ReactiveMongoRepository"}
}
```

Rules with `plugin_id` only match classes from projects scanned with that plugin.

### Template Engine — Already Plugin-Ready

Templates are already files on disk. Each plugin simply registers its templates in the same template index. The `auto_match` field already supports `{"legacy_layer": "SERVICE"}` — just add `{"plugin_id": "dotnet-8", "legacy_layer": "SERVICE"}`.

### Validator — Delegate Lint Rules to Plugin

Instead of hardcoded Java checks, the validator calls `plugin.get_lint_rules()` and applies them:

```python
def validate_generated_code(output_dir, plugin_id):
    plugin = load_plugin(plugin_id)
    rules = plugin.get_lint_rules()

    for file in Path(output_dir).rglob(f"*{plugin.file_extensions[0]}"):
        content = file.read_text()
        for rule in rules:
            if re.search(rule["pattern"], content, re.MULTILINE):
                violations.append(rule)
```

### Golden Sample Runner — No Changes Needed

Golden samples already store arbitrary expected output. A C# golden sample works identically to a Java one — the diff engine compares text, not language-specific structures.

---

## 6. Multi-Plugin Scanning

A single scan command can use different plugins for different parts of a monorepo:

```
scan_project("ecommerce-platform", "/repos/ecommerce", plugins={
    "src/backend/java/**":  "spring-boot-3",
    "src/backend/dotnet/**": "dotnet-8",
    "src/frontend/angular/**": "angular-react",
    "db/oracle/**": "oracle-postgres",
})
```

All classes go into the same KB, all tagged with their `plugin_id` and `language`. Cross-language dependency resolution:

```
"The Angular OrderComponent calls the Java OrderService REST API"
"The .NET PaymentService depends on the same Oracle tables as the Java legacy"
```

---

## 7. Recommended Implementation Sequence

| Phase | What to Build | Effort |
|-------|--------------|--------|
| **Phase 1** | Extract current Java logic into SpringBootPlugin class, add `plugin_id` field to KB records and mapping rules | 2-3 days |
| **Phase 2** | Create plugin loader + registry, refactor validator to use `plugin.get_lint_rules()` | 2-3 days |
| **Phase 3** | Build your second plugin (whichever migration is next) | 3-5 days |
| **Phase 4** | Multi-plugin scanning for monorepos | 2-3 days |
| **Phase 5** | Plugin marketplace (shared across org via Git repo) | 1-2 days |

### Phase 1 is the only breaking change.

You add `plugin_id` and `language` fields to every KB record and mapping rule. Existing data without these fields defaults to `plugin_id="spring-boot-3"` and `language="java"`. Everything else is additive.

---

## 8. MCP Server Changes Summary

| Server | Change Required | Effort |
|--------|----------------|--------|
| Knowledge Base | Add `plugin_id` + `language` fields, no logic changes | Minimal |
| Code Generator | Add `plugin_id` filter to rule matching | Minimal |
| Template Engine | Already supports custom templates — just register plugin templates | None |
| Validator | Delegate lint to `plugin.get_lint_rules()`, delegate compile to `plugin.get_build_command()` | Small |
| Golden Sample Runner | No changes — already language-agnostic | None |
| JAR Scanner | Wrap as plugin with `plugin_id="jar-bytecode"` | Small |
| Spring Scanner | Refactor parser into `SpringBootPlugin.parse_file()` | Medium |

**Total estimated effort: 2-3 weeks to make the platform fully pluggable, then 3-5 days per new migration use case.**
