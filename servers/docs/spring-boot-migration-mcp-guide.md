# AI-Assisted Legacy Spring Boot Migration — Technical Guide

## MCP Server Ecosystem for Migrating to an In-House Event-Driven Framework

---

## 1. Overview

This guide covers a purpose-built ecosystem of seven Python MCP (Model Context Protocol) servers that work together to analyze legacy Spring Boot applications, build a persistent cross-project knowledge base, map legacy patterns to your in-house target framework, generate migration code using customizable Jinja2 templates, and validate accuracy through golden sample testing and multi-level quality gates.

The system is designed for a common enterprise migration scenario: multiple Spring Boot 1.x/2.x applications share a common Java library, and all need to be migrated to Spring Boot 3.x running on a new in-house event-driven framework with Kafka, MongoDB, and domain event patterns.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   MIGRATION LANDSCAPE                                   │
│                                                                         │
│   ┌──────────────┐                                                     │
│   │ common-lib   │◄── Shared library (DAOs, base classes, interfaces)  │
│   └──────┬───────┘                                                     │
│          │ used by                                                      │
│          ├──────────────┬──────────────┬──────────────┐                │
│          ▼              ▼              ▼              ▼                │
│   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐        │
│   │ order-svc  │ │ payment-svc│ │ inventory  │ │ shipping   │        │
│   │ Boot 2.7   │ │ Boot 2.7   │ │ Boot 2.5   │ │ Boot 2.6   │        │
│   └────────────┘ └────────────┘ └────────────┘ └────────────┘        │
│          │              │              │              │                │
│          └──────────────┴──────────────┴──────────────┘                │
│                                  │                                     │
│                     ┌────────────▼────────────┐                       │
│                     │  In-House Event Platform │                       │
│                     │  Framework (target)      │                       │
│                     │  • EventBusConnector     │                       │
│                     │  • OutboxPublisher       │                       │
│                     │  • ReactiveRepository    │                       │
│                     │  • @CommandHandler       │                       │
│                     └─────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The Seven MCP Servers

Each server has a distinct responsibility. They share a persistent knowledge base on disk at `~/.mcp-migration-kb/` and communicate indirectly through that shared state.

```
┌──────────────┐  ┌──────────────┐
│ jar-scanner  │  │ spring-      │     LAYER 1: SCANNING
│ (bytecode)   │  │ scanner      │     Analyze legacy code
└──────┬───────┘  │ (source)     │
       │          └──────┬───────┘
       └──────┬──────────┘
              ▼
┌─────────────────────────┐
│   migration-kb          │            LAYER 2: KNOWLEDGE
│   Persistent KB on disk │            Cross-project resolution
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│   migration-codegen     │            LAYER 3: MAPPING
│   Mapping Rules Engine  │            Legacy pattern → target pattern
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│   migration-templates   │            LAYER 4: CODE GENERATION
│   Jinja2 Template Engine│            Custom templates → Java code
└────────────┬────────────┘
             ▼
┌──────────────┐  ┌──────────────┐
│ migration-   │  │ golden-      │     LAYER 5: VALIDATION & GOVERNANCE
│ validator    │  │ sample-      │     Accuracy gates, tracking, CI/CD
│              │  │ runner       │
└──────────────┘  └──────────────┘
```

### Server Summary

| Server | File | Purpose | Key Capability |
|--------|------|---------|----------------|
| **jar-scanner** | `jar_scanner_mcp_server.py` | Scan compiled JAR bytecode | `javap`-based class analysis when no source is available |
| **spring-scanner** | `springboot_scanner_mcp_server.py` | Scan Spring Boot source code | Annotation, injection, config, and endpoint analysis |
| **migration-kb** | `migration_kb_mcp_server.py` | Persistent cross-project knowledge base | Survives restarts, resolves deps across repos |
| **migration-codegen** | `migration_codegen_mcp_server.py` | Map legacy patterns to target framework | Rule-based pattern matching and code generation |
| **migration-templates** | `migration_template_engine.py` | Custom Jinja2 template rendering | Your framework conventions as reusable templates |
| **migration-validator** | `migration_validator_mcp_server.py` | Validate KB, rules, and generated code | Freshness, consistency, conventions, compilation, tracking |
| **golden-sample-runner** | `golden_sample_runner.py` | Architect-approved reference testing | Drift detection, approval workflow, CI gate |

---

## 3. Installation

### Prerequisites

- Python 3.10+
- JDK 17+ (for `javap` in the JAR scanner)
- Git repos checked out locally (legacy apps, shared library, target framework)

### Install Dependencies

```bash
pip install fastmcp pyyaml jinja2
```

### Register All Servers

**VS Code — `.vscode/mcp.json`:**

```json
{
  "servers": {
    "migration-kb": {
      "command": "python",
      "args": ["/path/to/migration_kb_mcp_server.py"]
    },
    "migration-codegen": {
      "command": "python",
      "args": ["/path/to/migration_codegen_mcp_server.py"]
    },
    "migration-templates": {
      "command": "python",
      "args": ["/path/to/migration_template_engine.py"]
    },
    "spring-scanner": {
      "command": "python",
      "args": ["/path/to/springboot_scanner_mcp_server.py"]
    },
    "jar-scanner": {
      "command": "python",
      "args": ["/path/to/jar_scanner_mcp_server.py"]
    },
    "migration-validator": {
      "command": "python",
      "args": ["/path/to/migration_validator_mcp_server.py"]
    },
    "golden-samples": {
      "command": "python",
      "args": ["/path/to/golden_sample_runner.py"]
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add migration-kb python /path/to/migration_kb_mcp_server.py
claude mcp add migration-codegen python /path/to/migration_codegen_mcp_server.py
claude mcp add migration-templates python /path/to/migration_template_engine.py
claude mcp add spring-scanner python /path/to/springboot_scanner_mcp_server.py
claude mcp add jar-scanner python /path/to/jar_scanner_mcp_server.py
claude mcp add migration-validator python /path/to/migration_validator_mcp_server.py
claude mcp add golden-samples python /path/to/golden_sample_runner.py
```

**Cursor — `.cursor/mcp.json`:**

Same structure as VS Code, but with the key `"mcpServers"` instead of `"servers"`.

### Interactive Testing

Any server can be tested independently with the FastMCP dev inspector:

```bash
fastmcp dev migration_kb_mcp_server.py
fastmcp dev migration_template_engine.py
```

---

## 4. Server 1 — JAR Scanner (`jar_scanner_mcp_server.py`)

### When to Use

Use this when you only have compiled JARs (no source code). Typical scenario: third-party or internal JARs in `WEB-INF/lib` where the original source repo is unavailable.

### How It Works

The server uses `javap` (JDK's bytecode disassembler) to read `.class` files inside JARs. Regular scan extracts field types, method signatures, annotations, and class hierarchy. Deep scan (`deep_scan=True`) parses the constant pool to catch every class reference including method-local usage.

```
JAR file (ZIP)
  └── com/company/dao/OrderDao.class
        │
        ▼ javap -p -cp order-dao.jar com.company.dao.OrderDao
        │
        ▼ Parse output → ClassInfo
           • superclass: BaseDao
           • annotations: @Repository
           • fields: [OrderRepository orderRepo]
           • methods: [findByCustomerId, saveOrder]
           • layer: DAO (auto-classified)
```

### Tools (11 tools)

| Tool | Description |
|------|-------------|
| `scan_jar` | Scan a single JAR, index all classes |
| `scan_jar_directory` | Scan all JARs in a directory (e.g., `WEB-INF/lib`) |
| `get_class_info` | Detailed metadata for a specific class |
| `find_dao_service_relationships` | Discover DAO→Service injection dependencies |
| `find_layer_dependencies` | Dependencies between any two layers |
| `find_classes_by_layer` | List all classes in a layer |
| `find_ejb_components` | Find and categorize EJB beans |
| `generate_dependency_graph` | Mermaid diagram of class dependencies |
| `migration_impact_report` | "What breaks if I migrate this class?" |
| `suggest_migration_order` | Wave-based migration ordering |
| `reset_registry` | Clear scanned data |

### Regular vs Deep Scan

| Mode | Speed | Catches |
|------|-------|---------|
| Regular | Fast | Fields, method signatures, annotations, superclass, interfaces |
| Deep (`deep_scan=True`) | Slower | All above + constant pool references (every class referenced in method bodies, casts, exception handlers) |

Use deep scan when services call DAO methods without having a DAO field, when classes use factory patterns, or when you need to catch transitive dependencies.

---

## 5. Server 2 — Spring Boot Scanner (`springboot_scanner_mcp_server.py`)

### When to Use

Use this for Spring Boot applications where you have full source code. This is the primary scanner for your migration project.

### How It Works

Parses `.java` source files using regex-based structural analysis to extract Spring-specific metadata. Also parses `application.yml`/`application.properties`, `pom.xml`/`build.gradle`, and detects Spring Boot version, Java version, and dependency migration needs.

### What It Detects

**Injection patterns (all three Spring styles):**
- Constructor injection (explicit and Lombok `@RequiredArgsConstructor` with `private final` fields)
- Field injection (`@Autowired`, `@Inject`, `@Resource`)
- `@Value("${...}")` property injection

**Spring Boot 3.x migration concerns:**
- `javax.*` → `jakarta.*` import detection
- Deprecated patterns: `WebSecurityConfigurerAdapter`, `RestTemplate`, `WebMvcConfigurerAdapter`
- `pom.xml` dependency upgrades (Springfox → SpringDoc, javax → jakarta artifacts)

**Architectural analysis:**
- REST endpoint extraction from `@GetMapping`, `@PostMapping`, etc.
- `@Transactional`, `@Async`, `@Scheduled`, `@EventListener` method detection
- Configuration parsing categorized by concern (database, kafka, security, etc.)

### Tools (15 tools)

| Tool | Description |
|------|-------------|
| `scan_spring_project` | Full project scan: source, config, dependencies |
| `find_dao_service_relationships` | DAO→Service injection map |
| `find_layer_dependencies` | Any layer → any layer dependencies |
| `get_bean_info` | Full metadata for a specific Spring bean |
| `find_beans_by_layer` | List beans in a layer |
| `analyze_rest_endpoints` | Extract all REST API endpoints |
| `analyze_configuration` | Parse and categorize application.yml properties |
| `analyze_dependencies` | Maven/Gradle dependencies with upgrade flags |
| `find_javax_imports` | Classes needing javax→jakarta migration |
| `find_deprecated_patterns` | Deprecated API usage (Boot 3.x incompatible) |
| `migration_impact_report` | Upstream/downstream impact of migrating a class |
| `suggest_event_driven_migration` | Sync coupling → event choreography suggestions |
| `generate_dependency_graph` | Mermaid diagram of bean wiring |
| `suggest_migration_order` | Wave-based migration order |
| `reset_registry` | Clear scanned data |

### Layer Classification Priority

The scanner classifies every class into an architectural layer using three tiers:

```
Priority 1 — Annotations (most reliable)
  @Entity, @Table, @Document           → ENTITY
  @Repository                          → DAO
  @Service                             → SERVICE
  @Controller, @RestController, @Path  → CONTROLLER
  @KafkaListener, @JmsListener         → MESSAGING

Priority 2 — Interfaces / Superclass
  implements MessageListener           → MESSAGING
  extends HttpServlet                  → CONTROLLER
  extends CrudRepository               → DAO

Priority 3 — Package naming (fallback)
  .dao., .repository.                  → DAO
  .service.                            → SERVICE
  .controller., .rest., .api.          → CONTROLLER
  .entity., .model., .domain.          → ENTITY
```

### Event-Driven Migration Suggestions

The `suggest_event_driven_migration` tool analyzes the current synchronous architecture and identifies specific migration targets:

- **Service-to-service coupling** → decouple via domain events
- **`@Transactional` methods with DAO deps** → Outbox pattern candidates
- **Mutating methods** (`createOrder`, `savePayment`) → domain event emission points with auto-generated event names (`OrderCreatedEvent`)
- **`@Scheduled` tasks** → event-triggered replacements

---

## 6. Server 3 — Migration Knowledge Base (`migration_kb_mcp_server.py`)

### Why a Persistent KB Instead of `server-memory`

The `@modelcontextprotocol/server-memory` stores unstructured text blobs. For migration analysis you need structured, queryable, cross-project type resolution. This KB provides:

| Capability | `server-memory` | Migration KB |
|---|---|---|
| Storage format | Flat text | Structured JSON (typed ClassRecord) |
| Survives restart | Yes | Yes (`~/.mcp-migration-kb/*.json`) |
| Cross-project type resolution | Manual | Automatic (resolves `OrderService → BaseDao` across repos) |
| "Who implements this interface?" | You remember | `find_interface_implementations("BaseDao")` |
| Change detection | None | File hash — skips unchanged projects |
| Queryable | Text search | Filter by project, layer, class type, annotation |

### Storage Layout

```
~/.mcp-migration-kb/
├── _index.json              # Project registry
├── common-lib.json          # Class records for shared library
├── order-service.json       # Class records for order-service
├── payment-service.json     # Class records for payment-service
├── _target_framework.json   # Scanned framework metadata
├── _mappings.json           # Mapping rules
└── templates/               # Custom Jinja2 templates
    ├── _template_index.json
    ├── event_driven_service.java.tpl
    ├── reactive_repository.java.tpl
    └── ...
```

Each project gets its own JSON file. The knowledge base loads from disk on server startup, so everything persists across sessions.

### How It Works

**Scan once, use forever:**

```
scan_library("common-lib", "/repos/common-lib")
  → Indexes 85 classes: interfaces, abstract classes, DAOs, utils
  → Persists to ~/.mcp-migration-kb/common-lib.json

scan_application("order-service", "/repos/order-service")
  → Scans Spring Boot app
  → AUTO-DETECTS: depends on common-lib
  → AUTO-RESOLVES: OrderDao extends BaseDao (from common-lib)

# Next session: KB already loaded from disk
scan_application("payment-service", "/repos/payment-service")
  → Immediately resolves against common-lib knowledge
```

**Change detection:** The KB computes an MD5 hash of all Java file timestamps. If nothing changed since last scan, it returns immediately with `UNCHANGED` status.

### Tools (13 tools)

| Tool | Description |
|------|-------------|
| `scan_library` | Scan a shared library, persist to KB |
| `scan_application` | Scan a Spring Boot app, auto-resolve cross-project deps |
| `find_cross_project_dependencies` | All deps from an app to shared libraries |
| `find_library_impact` | "If I change BaseDao, what breaks across ALL apps?" |
| `find_interface_implementations` | Find all implementations across ALL projects |
| `find_base_class_usage` | Find all subclasses across ALL projects |
| `find_dao_service_relationships` | DAO→Service with cross-project resolution |
| `search_knowledge_base` | Search by class name, annotation, or pattern |
| `list_projects` | List all projects with summary stats |
| `get_class_detail` | Full detail with cross-project implementors/subclasses |
| `rescan_project` | Force rescan (ignores hash cache) |
| `remove_project` | Remove a project from KB |
| `migration_landscape_report` | Full landscape: project graph, javax scope, migration order |

### Library Impact Analysis

The most powerful tool for planning: `find_library_impact` shows the blast radius of changing a shared library class across your entire application portfolio.

```
"If I change BaseDao in common-lib, what breaks?"

→ find_library_impact("common-lib", "BaseDao")

Result:
  12 classes across 3 applications EXTEND BaseDao — HIGH RISK
  • order-service:    OrderDao, CustomerDao, ProductDao
  • payment-service:  PaymentDao, TransactionDao
  • inventory-service: InventoryDao, WarehouseDao
  ...
```

---

## 7. Server 4 — Migration Code Generator (`migration_codegen_mcp_server.py`)

### When to Use

After the KB is populated and the target framework is scanned, use the codegen server to define mapping rules and generate migration code.

### How Mapping Rules Work

Each rule says: "When you see THIS in legacy code, generate THAT in target framework."

```
Rule: "service-to-event-service"
  MATCH:  layer=SERVICE AND annotation=@Transactional
  TRANSFORM:
    annotation  → @Service
    inject      → EventBusConnector, OutboxPublisher
    template    → event_driven_service
```

Rules are composable — multiple rules can match the same class, and their transforms are merged. Rules persist to `~/.mcp-migration-kb/_mappings.json`.

### Tools (8 tools)

| Tool | Description |
|------|-------------|
| `scan_target_framework` | Scan your in-house framework source code |
| `add_mapping_rule` | Define a legacy→target mapping rule |
| `auto_discover_mappings` | Auto-suggest rules by comparing KB with framework |
| `list_mapping_rules` | List all configured rules |
| `remove_mapping_rule` | Delete a rule |
| `preview_mapping` | Preview what rules match a class WITHOUT generating code |
| `generate_migration` | Generate code for a single legacy class |
| `generate_project_migration` | Generate code for all classes in specific layers |

### Mapping Rule Parameters

**Legacy match criteria (all optional, combined with AND):**

| Parameter | Description | Example |
|-----------|-------------|---------|
| `legacy_layer` | Match by architectural layer | `"SERVICE"` |
| `legacy_annotation` | Match by annotation | `"@Transactional"` |
| `legacy_extends` | Match by superclass | `"BaseDao"` |
| `legacy_implements` | Match by interface | `"MessageListener"` |
| `legacy_has_dependency` | Match if class injects this type (regex) | `".*Repository"` |
| `legacy_method_pattern` | Match method names (regex) | `"create.*\|save.*"` |

**Target transform:**

| Parameter | Description | Example |
|-----------|-------------|---------|
| `target_extends` | Superclass in target framework | `"ReactiveMongoRepository"` |
| `target_implements` | Comma-separated interfaces | `"EventSourcedAggregate"` |
| `target_annotation` | Class annotation | `"@CommandHandler"` |
| `target_inject` | Additional deps to inject | `"EventBusConnector,OutboxPublisher"` |
| `target_template` | Code template name | `"event_driven_service"` |
| `target_additional_imports` | Extra imports | `"com.company.framework.EventBus"` |

### Auto-Discovery

`auto_discover_mappings()` compares the legacy KB with the target framework extension points using two heuristics:

- **Layer matching:** legacy DAO ↔ framework DATA_ACCESS, legacy SERVICE ↔ framework HANDLER
- **Name similarity:** `*Dao` ↔ `*Repository`, `*Service` ↔ `*CommandHandler`, `*Listener` ↔ `*EventHandler`

Returns suggestions with confidence levels (HIGH/MEDIUM) for review before confirmation.

---

## 8. Server 5 — Template Engine (`migration_template_engine.py`)

### When to Use

When the built-in code generation in the codegen server doesn't match your framework's specific conventions. The template engine gives you full control over every line of generated code using Jinja2 templates.

### Built-In Templates (8 templates)

| Template ID | Output | Use Case |
|-------------|--------|----------|
| `event_driven_service` | Service with event publishing + outbox pattern | Legacy `@Service` → event-driven service |
| `reactive_repository` | Spring Data reactive repository interface | Legacy DAO → reactive repository |
| `domain_event` | Java record with factory method + audit fields | Auto-generated from mutating service methods |
| `command_handler` | CQRS command handler with event emission | Service → command/query separation |
| `kafka_consumer` | Kafka listener with manual ack, DLT, metrics | Legacy JMS/message listener → Kafka |
| `saga_participant` | Choreography saga with compensation handlers | Distributed transaction participant |
| `integration_test` | SpringBootTest with Testcontainers scaffold | Test scaffold for every migrated class |
| `application_config` | Spring Boot application.yml | Config template with Kafka, MongoDB, actuator |

### Tools (9 tools)

| Tool | Description |
|------|-------------|
| `init_templates` | Bootstrap templates directory with defaults |
| `list_templates` | List all registered templates |
| `add_custom_template` | Create a new template with Jinja2 content |
| `get_template` | Read a template's content and metadata |
| `update_template` | Update an existing template |
| `render_from_template` | Render a template for a specific legacy class |
| `render_batch` | Render for ALL matching classes in a project |
| `preview_template_context` | Show all variables available for template debugging |
| `delete_template` | Remove a template |

### Template Variables

All templates receive a structured context object with three namespaces:

**`legacy.*` — The source legacy class:**

```
{{ legacy.fqcn }}                  com.company.service.OrderService
{{ legacy.simple_name }}           OrderService
{{ legacy.package }}               com.company.service
{{ legacy.layer }}                 SERVICE
{{ legacy.stereotype }}            @Service
{{ legacy.superclass }}            BaseService
{{ legacy.interfaces }}            ["Serializable"]
{{ legacy.annotations }}           ["@Service", "@Transactional"]
{{ legacy.entity_name }}           Order (auto-stripped from class name)
{{ legacy.constructor_deps }}      [{"type": "OrderDao", "name": "orderDao"}]
{{ legacy.field_deps }}            [{"type": "AuditService", "name": "auditService"}]
{{ legacy.public_methods }}        [{"name": "createOrder", "return_type": "Order", ...}]
{{ legacy.javax_imports }}         ["javax.persistence.Entity"]
{{ legacy.migration_notes }}       ["javax.persistence → jakarta.persistence"]
```

**`target.*` — The generation target (from mapping rules):**

```
{{ target.package }}               com.company.order
{{ target.class_name }}            OrderService (cleaned, no Impl/Bean suffix)
{{ target.extends }}               AbstractCommandHandler
{{ target.extends_clause }}         extends AbstractCommandHandler
{{ target.implements }}            ["EventSourcedAggregate"]
{{ target.implements_clause }}      implements EventSourcedAggregate
{{ target.annotations }}           ["@Service"]
{{ target.all_deps }}              [{"type": "OrderRepository", "name": "orderRepository"}, ...]
{{ target.imports }}               ["com.company.framework.EventBus"]
```

**`meta.*` — Generation metadata:**

```
{{ meta.date }}                    2026-02-28
{{ meta.rules_applied }}           ["service-to-event-service"]
{{ meta.generator_version }}       2.0.0
```

### Custom Jinja2 Filters

12 Java-specific filters for code generation:

| Filter | Input → Output |
|--------|----------------|
| `camel_case` | `OrderService` → `orderService` |
| `pascal_case` | `order_service` → `OrderService` |
| `snake_case` | `OrderService` → `order_service` |
| `upper_snake` | `OrderService` → `ORDER_SERVICE` |
| `first_lower` | `Order` → `order` |
| `first_upper` | `order` → `Order` |
| `strip_suffix('Impl','Bean')` | `OrderServiceImpl` → `OrderService` |
| `to_event_name('Order')` | `createOrder` → `OrderCreatedEvent` |
| `to_topic_name` | `OrderService` → `order.events` |
| `is_mutating` | `True` for create/save/update/delete methods |
| `is_query` | `True` for find/get/search/list methods |
| `java_type` | `com.company.Order` → `Order` |

### Template Example

```jinja
{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    @Transactional
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        // Business logic from legacy {{ legacy.simple_name }}.{{ method.name }}()

        // Publish domain event via outbox
        outboxPublisher.publish(
            {{ method.name | to_event_name(legacy.entity_name) }}.create(...)
        );
    }
{% elif method.name | is_query %}
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        return {{ target.all_deps[0].name }}.{{ method.name }}(...);
    }
{% endif %}
{% endfor %}
```

### Creating a Custom Template

```
"Create a template for our PSF event participant pattern"

→ add_custom_template(
    template_id="psf-participant",
    file_name="psf_participant.java.tpl",
    content="package {{ target.package }}; ...",
    description="PSF event participant for our platform",
    auto_match_layer="SERVICE",
    tags="psf,event,participant"
  )
```

Templates are saved as `.tpl` files in `~/.mcp-migration-kb/templates/` and can be version-controlled by copying the directory into your repository.

---

## 9. Server 6 — Migration Validator (`migration_validator_mcp_server.py`)

### When to Use

Run validation checks before generating code (is the KB fresh?), after generating code (does it compile and follow conventions?), and throughout the migration lifecycle (track progress org-wide). This server is the quality backbone of the platform.

### What It Validates

```
Generated Code
      │
      ▼
┌──────────────┐
│ Level 1:     │  Is the KB up to date with git?
│ KB FRESHNESS │  Hash comparison per project
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 2:     │  Dangling refs, duplicate FQCNs,
│ KB           │  orphan interfaces, missing links
│ CONSISTENCY  │
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 3:     │  Unmapped classes, dead rules,
│ RULE         │  coverage % per layer
│ COVERAGE     │
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 4:     │  javax imports, field injection,
│ CODE         │  missing events, naming, logging,
│ QUALITY      │  compilation via javac
└──────────────┘
```

### Tools (8 tools)

| Tool | Description |
|------|-------------|
| `check_kb_freshness` | Compare KB hashes against current source on disk |
| `validate_kb_consistency` | Dangling refs, duplicates, orphan interfaces/base classes |
| `validate_rule_coverage` | Unmapped classes (gaps), dead rules, multi-matched classes |
| `validate_generated_code` | Convention lint: javax, field injection, events, naming, logging |
| `validate_compilation` | Compile generated code with `javac` |
| `run_full_validation` | Run all four levels in sequence, produce overall PASS/FAIL |
| `track_class_migration` | Record per-class migration status for progress tracking |
| `migration_progress_report` | Org-wide dashboard: per-project, per-layer completion % |

### Convention Checks (Level 4 Detail)

The `validate_generated_code` tool scans generated `.java` files for:

| Rule | Severity | What It Catches |
|------|----------|-----------------|
| `NO_JAVAX_IMPORTS` | CRITICAL | Any `javax.*` import (must be `jakarta.*` for Boot 3.x) |
| `NO_FIELD_INJECTION` | HIGH | `@Autowired` on fields (must use constructor injection) |
| `NO_INJECT_ANNOTATION` | HIGH | `@Inject` field injection |
| `MISSING_EVENT_PUBLISHING` | MEDIUM | Mutating methods (create/save/update/delete) without event emission |
| `MISSING_CONSTRUCTOR` | HIGH | `private final` fields without constructor or `@RequiredArgsConstructor` |
| `MISSING_LOGGER` | LOW | No `@Slf4j` or `Logger` in a class |
| `NAMING_CONVENTION` | LOW | Service class not ending in `Service`/`Handler`, Repository not ending in `Repository` |
| `UNRESOLVED_TODOS` | INFO | Count of `// TODO` markers (expected in generated code, tracked for completion) |

### Migration Progress Tracking

The `track_class_migration` and `migration_progress_report` tools maintain a tracking file at `~/.mcp-migration-kb/validation/_tracking.json`. Each class has a status lifecycle:

```
NOT_STARTED → IN_PROGRESS → GENERATED → VALIDATED → MIGRATED → DEPLOYED
```

The progress report shows per-project and per-layer completion percentages, enabling org-wide dashboard views.

### Validation Reports

All validation runs save timestamped JSON reports to `~/.mcp-migration-kb/validation/reports/`:

```
reports/
├── kb_freshness_20260315-140000.json
├── kb_consistency_20260315-140001.json
├── rule_coverage_20260315-140002.json
├── code_quality_20260315-140003.json
├── full_validation_20260315-140004.json
└── progress_20260315-150000.json
```

---

## 10. Server 7 — Golden Sample Runner (`golden_sample_runner.py`)

### When to Use

This is the **primary accuracy gate** for the entire migration platform. Use it to create architect-approved reference migrations, test that rules and templates still produce correct output after changes, and gate CI pipelines.

### How Golden Samples Work

```
┌─────────────────────────────────────────────────────────────┐
│                     GOLDEN SAMPLE                            │
│                                                              │
│  ┌──────────────────┐         ┌──────────────────┐         │
│  │ legacy/           │         │ expected/          │         │
│  │ OrderDao.java     │   ──►   │ OrderRepository.java│        │
│  │ (real legacy)     │ mapping │ (architect-approved) │        │
│  │                   │ rules + │                      │        │
│  │                   │ template│                      │        │
│  └──────────────────┘         └──────────────────┘         │
│                                        ▲                    │
│                                        │ compare            │
│                                        │                    │
│                               ┌────────┴─────────┐         │
│                               │ actual/            │         │
│                               │ OrderRepository.java│        │
│                               │ (regenerated now)   │        │
│                               └──────────────────┘         │
│                                                              │
│  If expected == actual → PASS                               │
│  If expected != actual → DRIFT (blocks CI)                  │
└─────────────────────────────────────────────────────────────┘
```

For each mapping rule, you maintain a hand-verified, architect-approved reference migration. When a rule or template changes, the runner regenerates code and compares against the approved expected output. If there's a diff (DRIFT), it blocks until an architect reviews and either reverts the change or re-approves the new baseline.

### Storage Layout

```
~/.mcp-migration-kb/golden-samples/
├── _golden_index.json
├── dao-to-reactive-repo-order/
│   ├── legacy/
│   │   ├── OrderDao.java            ← Original source (snapshot)
│   │   └── class_data.json          ← KB class record (for rendering)
│   ├── expected/
│   │   └── OrderRepository.java     ← Architect-approved correct output
│   ├── actual/
│   │   └── OrderRepository.java     ← Latest regeneration (for diff)
│   ├── archive/
│   │   └── 20260315-140000/         ← Previous expected versions
│   └── metadata.json                ← Rule, template, approver, dates
├── service-to-event-order/
│   ├── ...
└── listener-to-kafka-payment/
    ├── ...
```

### Tools (9 tools)

| Tool | Description |
|------|-------------|
| `create_golden_sample` | Create a sample with manually provided approved code |
| `create_golden_from_generation` | Generate code now, save as baseline for review |
| `run_golden_sample` | Test one sample: regenerate and compare |
| `run_all_golden_samples` | Test ALL samples — the CI gate |
| `approve_drift` | Architect re-approves after intentional change (archives old expected) |
| `list_golden_samples` | List all samples with last test status |
| `get_golden_sample_detail` | View expected and actual code for a sample |
| `delete_golden_sample` | Remove a sample |
| `golden_sample_coverage` | Which rules/templates have golden samples and which don't |

### Comparison Engine

The diff engine normalizes generated code before comparison to avoid false positives:

| Normalization | Default | Purpose |
|---------------|---------|---------|
| `ignore_timestamps` | `true` | Generation date strings (`2026-03-15`) → `YYYY-MM-DD` |
| `ignore_todos` | `false` | Skip `// TODO` lines entirely |
| `ignore_whitespace` | `true` | Collapse consecutive blank lines |

When drift is detected, the output shows a unified diff limited to the meaningful changes.

### The Approval Workflow

```
1. Developer changes a mapping rule or template

2. CI runs: run_all_golden_samples()
   → Sample "dao-to-reactive-repo-order": DRIFT detected
   → 3 lines differ in generated OrderRepository.java
   → CI BLOCKS the merge

3. Architect reviews the diff:
   - Is this an intentional improvement? → approve_drift()
   - Is this a regression? → Revert the rule/template change

4. approve_drift("dao-to-reactive-repo-order", "architect@company.com")
   → Archives old expected/ to archive/20260315-140000/
   → Copies actual/ → expected/
   → Re-records approver and date

5. CI re-runs: PASS
   → Merge allowed
```

### Golden Sample Coverage

Use `golden_sample_coverage()` to ensure every mapping rule and template has at least one golden sample. Uncovered rules are a risk — they can produce wrong code without detection.

```
"Check golden sample coverage"
→ golden_sample_coverage()

Result:
  Rules: 6/8 covered (75%)
  Uncovered: "scheduled-to-event-triggered", "security-update"
  Templates: 5/8 covered (62.5%)
  Uncovered: "saga_participant", "application_config", "command_handler"
```

---

## 11. End-to-End Migration Workflow

### Phase 1 — Build the Knowledge Base

```
SESSION 1: Scan everything
━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 1: Scan the shared library
  Prompt: "Scan our common library at /repos/common-lib"
  Tool:   scan_library("common-lib", "/repos/common-lib")
  Result: 85 classes indexed (18 interfaces, 12 abstract classes, 55 classes)

Step 2: Scan each Spring Boot application
  Prompt: "Scan order-service"
  Tool:   scan_application("order-service", "/repos/order-service")
  Result: 147 classes, auto-detected dependency on common-lib

  Prompt: "Scan payment-service"
  Tool:   scan_application("payment-service", "/repos/payment-service")

  Prompt: "Scan inventory-service"
  Tool:   scan_application("inventory-service", "/repos/inventory-svc")

Step 3: Scan third-party JARs (if needed)
  Prompt: "Scan the legacy JARs in WEB-INF/lib"
  Tool:   scan_jar_directory("/opt/legacy/WEB-INF/lib", deep_scan=True)
```

### Phase 2 — Analyze the Landscape

```
SESSION 2: Understand the migration scope
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 4: Get the full landscape report
  Prompt: "Show me the migration landscape"
  Tool:   migration_landscape_report()
  Result: Project dependency graph, javax scope, cross-project refs

Step 5: Analyze library impact
  Prompt: "What breaks if I change BaseDao in common-lib?"
  Tool:   find_library_impact("common-lib", "BaseDao")
  Result: 12 classes across 3 apps extend BaseDao — HIGH RISK

Step 6: Deep-dive a specific application
  Prompt: "Analyze order-service for event-driven migration"
  Tool:   suggest_event_driven_migration() [via spring-scanner]
  Result: 8 sync couplings to break, 15 outbox candidates, 3 scheduled tasks

Step 7: Check javax migration scope
  Prompt: "How many javax imports need migration?"
  Tool:   find_javax_imports() [via spring-scanner]
  Result: 42 classes with 120+ javax imports across the portfolio
```

### Phase 3 — Scan Target Framework and Define Mappings

```
SESSION 3: Map legacy → target
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 8: Scan your in-house framework
  Prompt: "Scan our event platform framework"
  Tool:   scan_target_framework("/repos/event-platform", "event-platform")
  Result: 65 framework classes (22 interfaces, 15 abstract, 8 annotations)

Step 9: Auto-discover possible mappings
  Prompt: "Suggest mapping rules"
  Tool:   auto_discover_mappings()
  Result: 12 suggestions with confidence levels

Step 10: Define confirmed mapping rules
  Tool:   add_mapping_rule(
            rule_id="dao-to-reactive-repo",
            description="Legacy DAO → reactive repository",
            legacy_layer="DAO",
            target_extends="ReactiveMongoRepository",
            target_template="reactive_repository"
          )

  Tool:   add_mapping_rule(
            rule_id="service-to-event-service",
            description="Transactional service → event-driven service",
            legacy_layer="SERVICE",
            legacy_annotation="@Transactional",
            target_annotation="@Service",
            target_inject="EventBusConnector,OutboxPublisher",
            target_template="event_driven_service"
          )

  Tool:   add_mapping_rule(
            rule_id="listener-to-kafka-consumer",
            description="JMS/message listener → Kafka consumer",
            legacy_layer="MESSAGING",
            target_annotation="@Component",
            target_inject="MeterRegistry",
            target_template="kafka_consumer"
          )
```

### Phase 4 — Initialize Templates and Generate Code

```
SESSION 4: Generate migration code
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 11: Bootstrap template directory
  Tool:   init_templates()
  Result: 8 default templates created in ~/.mcp-migration-kb/templates/

Step 12: Customize templates for your framework (optional)
  Edit files directly: ~/.mcp-migration-kb/templates/event_driven_service.java.tpl
  Or use: update_template("event_driven_service", "new content...")
  Or add: add_custom_template("psf-participant", "psf.java.tpl", "...")

Step 13: Preview before generating
  Prompt: "Preview what rules match OrderService"
  Tool:   preview_mapping("order-service", "OrderService")
  Result: Rules matched: service-to-event-service

  Prompt: "Show me the template context for OrderService"
  Tool:   preview_template_context("order-service", "OrderService")
  Result: Full legacy.*, target.*, meta.* context dump

Step 14: Generate code for a single class
  Tool:   render_from_template(
            "event_driven_service",
            "order-service",
            "OrderService",
            target_package="com.company.order",
            output_dir="/repos/order-service-v2/src/main/java"
          )
  Result: OrderService.java, OrderCreatedEvent.java, OrderServiceIntegrationTest.java

Step 15: Batch generate all DAOs and Services
  Tool:   render_batch(
            "reactive_repository",
            "order-service",
            layer_filter="DAO",
            target_package="com.company.order.repository",
            output_dir="/repos/order-service-v2/src/main/java"
          )

  Tool:   render_batch(
            "event_driven_service",
            "order-service",
            layer_filter="SERVICE",
            target_package="com.company.order.service",
            output_dir="/repos/order-service-v2/src/main/java"
          )
```

### Phase 5 — Validate and Track

```
SESSION 5: Ensure accuracy
━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 16: Run full validation pipeline
  Tool:   run_full_validation(
            project_name="order-service",
            output_dir="/repos/order-service-v2/src/main/java"
          )
  Result: KB fresh ✓, KB consistent ✓, rule coverage 92% ✓, code quality PASS ✓

Step 17: Create golden samples for each rule (first time only)
  Tool:   create_golden_from_generation(
            "dao-to-repo-order",
            "dao-to-reactive-repo",
            "reactive_repository",
            "order-service", "OrderDao"
          )
  → Review expected/ output, have architect approve

Step 18: Run all golden sample tests (every time rules/templates change)
  Tool:   run_all_golden_samples()
  Result: 6/6 PASS — no drift detected

Step 19: Check golden sample coverage
  Tool:   golden_sample_coverage()
  Result: 6/8 rules covered, 2 need golden samples

Step 20: Validate generated code quality
  Tool:   validate_generated_code("/repos/order-service-v2/src/main/java")
  Result: 0 CRITICAL, 0 HIGH, 3 MEDIUM (missing event publishing), 12 INFO (TODOs)

Step 21: Try compilation
  Tool:   validate_compilation(
            "/repos/order-service-v2/src/main/java",
            classpath="/repos/event-platform/target/classes"
          )
  Result: PASS — all files compile

Step 22: Track migration progress
  Tool:   track_class_migration("order-service", "com.company.OrderService",
            status="VALIDATED", rules_applied="service-to-event-service",
            template_used="event_driven_service")

Step 23: View org-wide progress
  Tool:   migration_progress_report()
  Result: Overall 62% — order-service 75%, payment-service 55%, ...
```

---

## 12. Architecture Mapping Reference

### Spring Boot 2.x → Spring Boot 3.x + Event-Driven

| Legacy Pattern | Target Pattern | Mapping Rule |
|----------------|----------------|--------------|
| `@Service` + `@Transactional` | `@Service` + `OutboxPublisher` | `service-to-event-service` |
| `@Repository` / DAO | `ReactiveMongoRepository` interface | `dao-to-reactive-repo` |
| `@RestController` | `@RestController` (updated imports) | `controller-update` |
| `@JmsListener` / `MessageListener` | `@KafkaListener` + manual ack + DLT | `listener-to-kafka-consumer` |
| `@Scheduled` | Event-triggered or Spring Batch | `scheduled-to-event-triggered` |
| `javax.persistence.*` | `jakarta.persistence.*` | Mechanical find-replace |
| `WebSecurityConfigurerAdapter` | `SecurityFilterChain` `@Bean` | `security-update` |
| `RestTemplate` | `RestClient` (Boot 3.2+) | `resttemplate-update` |
| Service-to-service sync call | Domain event choreography | `sync-to-async` |
| `@Transactional` + DB write | Transactional Outbox pattern | `outbox-pattern` |

### Spring Boot 3.x Migration Waves

| Wave | Scope | Description |
|------|-------|-------------|
| 0 | Build | Upgrade Boot parent, Java 21, update pom.xml/build.gradle deps |
| 1 | Imports | `javax.*` → `jakarta.*` (mechanical, project-wide find-replace) |
| 2 | Entities | Entity/DTO/Value Object classes (no behavioral change) |
| 3 | DAOs | Repositories → Spring Data reactive interfaces |
| 4 | Services | Business logic + Outbox pattern + event publishing |
| 5 | Messaging | JMS listeners → Kafka consumers with retry/DLT |
| 6 | Controllers | REST API updates, OpenAPI migration |
| 7 | Security | `WebSecurityConfigurerAdapter` → `SecurityFilterChain` |
| 8 | Config | Infrastructure, utilities, schedulers |

### Project-Level Migration Order

Libraries must be migrated before applications. Applications with fewer library dependencies should be migrated first.

```
Wave 1: common-lib (all apps depend on it)
Wave 2: inventory-service (fewest deps, lowest risk)
Wave 3: order-service
Wave 4: payment-service (most deps, highest risk)
```

---

## 13. Event-Driven Patterns

### Transactional Outbox Pattern

The Outbox pattern ensures zero message loss by writing the domain event to an `outbox` table in the same database transaction as the business state change. A separate poller reads the outbox and publishes to Kafka.

```
Service method:
  @Transactional
  1. Execute business logic
  2. Save entity to main table
  3. Save event to outbox table (same transaction)

Outbox Poller (separate thread):
  1. SELECT * FROM outbox WHERE published = false
  2. Publish to Kafka topic
  3. UPDATE outbox SET published = true
```

The `event_driven_service` template generates this pattern with `OutboxPublisher` injected into every migrated service.

### Saga Choreography

For distributed transactions spanning multiple services, the `saga_participant` template generates a choreography-based saga participant that:

1. Listens for a trigger event
2. Executes a local transaction
3. Publishes a success event or compensation event on failure

### Tiered Retry with Dead Letter Topics

The `kafka_consumer` template generates consumers with:

- Manual acknowledgment (no auto-commit)
- Configurable retry via `retryableKafkaListenerContainerFactory`
- Dead Letter Topic (DLT) routing for permanent failures
- Micrometer metrics for consumed events (success/error counters, duration timers)
- Idempotency check scaffold (event ID deduplication)

---

## 14. Copilot / Claude Prompt Library

### Discovery Prompts

```
"Scan our shared library at /repos/common-lib"
"Scan order-service at /repos/order-service"
"Show me the full migration landscape"
"What breaks if I change BaseDao in common-lib?"
"Who implements the AuditAware interface across all projects?"
"Find all classes extending AbstractRestController"
"How many javax imports need migration in order-service?"
"What deprecated patterns exist in payment-service?"
```

### Mapping Prompts

```
"Scan our target framework at /repos/event-platform"
"Auto-discover mapping rules between legacy and target"
"Add a rule: legacy DAO → ReactiveMongoRepository"
"Add a rule: @Transactional service → @Service + OutboxPublisher"
"Preview what rules match OrderService in order-service"
"List all mapping rules"
```

### Code Generation Prompts

```
"Initialize the default templates"
"Show me the template context for OrderService"
"Generate migration code for OrderService using event_driven_service template"
"Generate all DAOs in order-service using reactive_repository template"
"Generate all services in order-service to /repos/order-service-v2/src/main/java"
"Create a custom template for our PSF event participant pattern"
```

### Analysis Prompts

```
"Analyze order-service for event-driven migration opportunities"
"Show all DAO-to-Service relationships with cross-project resolution"
"Generate a Mermaid dependency graph for order-service"
"What is the migration impact of changing OrderDao?"
"Suggest the optimal migration order for order-service"
"Show all REST endpoints in order-service"
"Analyze the application.yml configuration"
```

### Validation & Governance Prompts

```
"Check if the KB is up to date"
"Validate KB consistency — any dangling references?"
"Check mapping rule coverage for order-service"
"Validate the generated code in /repos/order-service-v2"
"Try compiling the generated code"
"Run the full validation pipeline for order-service"
"Run all golden sample tests"
"Check golden sample coverage — which rules are uncovered?"
"Create a golden sample for the DAO-to-repo rule using OrderDao"
"Approve the drift in golden sample dao-to-repo-order"
"Show migration progress for all projects"
"Track OrderService as VALIDATED in order-service"
```

---

## 15. Testing Strategy

### Generated Test Scaffolds

The `integration_test` template auto-generates a test class for every migrated class with:

- `@SpringBootTest` + `@ActiveProfiles("test")` setup
- `@Autowired` injection of all dependencies
- One test method per legacy public method
- Awaitility assertions for async event verification
- Clear TODO markers for implementation

### Recommended Test Distribution

| Type | Coverage | Focus |
|------|----------|-------|
| Unit tests | 50% | Domain logic in isolation |
| Component tests | 25% | Service layer with mocked dependencies |
| Integration tests | 20% | Real Kafka + MongoDB via Testcontainers |
| E2E / Dual-run | 5% | Legacy vs migrated side-by-side comparison |

### Dual-Run Validation

Before cutover, run both legacy and migrated services in parallel for 2+ weeks:

1. Route production traffic to legacy (primary)
2. Mirror traffic to migrated service (shadow)
3. Compare outputs — log discrepancies
4. Fix behavioral gaps
5. Gradually shift traffic: 5% → 25% → 50% → 100%

---

## 16. Production Cutover Checklist

| Step | Action |
|------|--------|
| 1 | All integration tests passing |
| 2 | Dual-run comparison clean for 2+ weeks |
| 3 | Feature flags configured for instant rollback |
| 4 | Kafka consumer lag monitoring in place |
| 5 | Error rate and latency dashboards ready |
| 6 | Canary rollout: 5% traffic to new service |
| 7 | Monitor 24h at each stage |
| 8 | Escalate: 25% → 50% → 100% |
| 9 | 48h stabilization with on-call coverage |
| 10 | Decommission legacy service |

---

## 17. Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `javap not found` | JDK not on PATH | `export JAVA_HOME=/path/to/jdk && export PATH=$JAVA_HOME/bin:$PATH` |
| KB returns `UNCHANGED` | File hash matches last scan | Use `rescan_project("project-name")` to force |
| No mapping rules match | Rules don't match class metadata | Use `preview_mapping()` to inspect, adjust rule criteria |
| Template render fails | Missing Jinja2 or variable error | `pip install jinja2`, use `preview_template_context()` to debug |
| Cross-project deps not detected | Library not scanned first | Always `scan_library` before `scan_application` |
| Classes show as UNKNOWN layer | No matching annotations or naming | Check with `get_bean_info()`, add package naming convention |
| Inner classes skipped in JAR scan | By design (reduces noise) | Modify `scan_jar` to include `$` classes if needed |
| Lombok deps not detected | `@RequiredArgsConstructor` not in source | Ensure source has Lombok annotations (not just delombok output) |
| Golden sample DRIFT on every run | Generation date in output changes | Set `ignore_timestamps=True` on the golden sample (default) |
| Validation reports missing | Reports dir not created | Run any validation tool once — creates `~/.mcp-migration-kb/validation/reports/` |
| Rule coverage shows 0% | Mapping rules not defined | Use `migration-codegen` to `add_mapping_rule` first |
| Golden sample ERROR status | Template rendering failed | Check template exists in `~/.mcp-migration-kb/templates/`, run `init_templates()` |
| Compilation fails on generated code | Missing classpath dependencies | Pass target framework JARs via `classpath` parameter |

---

## 18. File Reference

| File | Server | pip deps |
|------|--------|----------|
| `jar_scanner_mcp_server.py` | JAR Scanner | `fastmcp` + JDK |
| `springboot_scanner_mcp_server.py` | Spring Scanner | `fastmcp`, `pyyaml` |
| `migration_kb_mcp_server.py` | Knowledge Base | `fastmcp`, `pyyaml` |
| `migration_codegen_mcp_server.py` | Code Generator | `fastmcp`, `pyyaml` |
| `migration_template_engine.py` | Template Engine | `fastmcp`, `pyyaml`, `jinja2` |
| `migration_validator_mcp_server.py` | Validator | `fastmcp`, `pyyaml` |
| `golden_sample_runner.py` | Golden Sample Runner | `fastmcp`, `pyyaml`, `jinja2` |

All servers use stdio transport (local subprocess communication) and require no network access or cloud dependencies.

---

*Guide Version: 3.0 — Spring Boot Legacy Migration Stack with Validation & Governance*
*Generated: 2026-02-28*
*Servers: 7 | Tools: 73 | Templates: 8 | Custom Filters: 12*
