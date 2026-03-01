# MCP Migration & Developer Productivity Platform

## 11 MCP Servers · 95+ Tools · Enterprise Java Migration + Codebase Intelligence

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run any server
python servers/migration_kb_mcp_server.py
```

## Register All Servers

### VS Code — `.vscode/mcp.json`

```json
{
  "servers": {
    "jar-scanner":         {"command": "python", "args": ["servers/jar_scanner_mcp_server.py"]},
    "spring-scanner":      {"command": "python", "args": ["servers/springboot_scanner_mcp_server.py"]},
    "migration-kb":        {"command": "python", "args": ["servers/migration_kb_mcp_server.py"]},
    "migration-codegen":   {"command": "python", "args": ["servers/migration_codegen_mcp_server.py"]},
    "migration-templates": {"command": "python", "args": ["servers/migration_template_engine.py"]},
    "migration-validator": {"command": "python", "args": ["servers/migration_validator_mcp_server.py"]},
    "golden-samples":      {"command": "python", "args": ["servers/golden_sample_runner.py"]},
    "openrewrite":         {"command": "python", "args": ["servers/openrewrite_mcp_server.py"]},
    "recipe-manager":      {"command": "python", "args": ["servers/openrewrite_recipe_manager.py"]},
    "test-quality":        {"command": "python", "args": ["servers/test_quality_mcp_server.py"]},
    "codebase-intel":      {"command": "python", "args": ["servers/codebase_intel_mcp_server.py"]}
  }
}
```

### Claude Code

```bash
claude mcp add jar-scanner python servers/jar_scanner_mcp_server.py
claude mcp add spring-scanner python servers/springboot_scanner_mcp_server.py
claude mcp add migration-kb python servers/migration_kb_mcp_server.py
claude mcp add migration-codegen python servers/migration_codegen_mcp_server.py
claude mcp add migration-templates python servers/migration_template_engine.py
claude mcp add migration-validator python servers/migration_validator_mcp_server.py
claude mcp add golden-samples python servers/golden_sample_runner.py
claude mcp add openrewrite python servers/openrewrite_mcp_server.py
claude mcp add recipe-manager python servers/openrewrite_recipe_manager.py
claude mcp add test-quality python servers/test_quality_mcp_server.py
claude mcp add codebase-intel python servers/codebase_intel_mcp_server.py
```

---

## Server Inventory

| # | Server | File | Tools | Purpose |
|---|--------|------|-------|---------|
| 1 | JAR Scanner | `jar_scanner_mcp_server.py` | 11 | Bytecode analysis via `javap` (no source needed) |
| 2 | Spring Boot Scanner | `springboot_scanner_mcp_server.py` | 15 | Source-level Spring Boot analysis |
| 3 | Migration KB | `migration_kb_mcp_server.py` | 14 | Persistent cross-project KB (local/MongoDB/Redis) |
| 4 | Migration Codegen | `migration_codegen_mcp_server.py` | 8 | Mapping rules engine |
| 5 | Template Engine | `migration_template_engine.py` | 9 | Jinja2 code generation (8 templates, 12 filters) |
| 6 | Migration Validator | `migration_validator_mcp_server.py` | 8 | KB freshness, convention lint, compilation, tracking |
| 7 | Golden Sample Runner | `golden_sample_runner.py` | 9 | Architect-approved reference tests, drift detection |
| 8 | OpenRewrite Executor | `openrewrite_mcp_server.py` | 10 | AST-level code transformation via OpenRewrite |
| 9 | Recipe Manager | `openrewrite_recipe_manager.py` | 14 | Author, test, compose, version OpenRewrite recipes |
| 10 | Test Quality | `test_quality_mcp_server.py` | 8 | Mutation testing, risk-based test gap analysis |
| 11 | Codebase Intelligence | `codebase_intel_mcp_server.py` | 12 | Codebase knowledge graph, ownership, blast radius |

---

## Documentation

| Document | Description |
|----------|-------------|
| `spring-boot-migration-mcp-guide.md` | Technical user guide (v3.0, 18 sections) |
| `migration-accuracy-adoption-strategy.md` | Accuracy & org-wide adoption strategy |
| `migration-plugin-architecture.md` | Plugin architecture for multi-language migration |
| `developer-productivity-platform-strategy.md` | Full platform vision (22 server roadmap) |
| `JAR_SCANNER_GUIDE.md` | JAR Scanner detailed reference |

---

## Architecture

```
LAYER 1: SCANNING
├── jar-scanner (bytecode)
└── spring-scanner (source)

LAYER 2: KNOWLEDGE
└── migration-kb (MongoDB/Redis/local)

LAYER 3: TRANSFORMATION
├── migration-codegen (mapping rules)
├── migration-templates (Jinja2 code gen)
├── openrewrite-executor (AST transforms)
└── recipe-manager (recipe lifecycle)

LAYER 4: VALIDATION
├── migration-validator (multi-level quality gates)
├── golden-sample-runner (accuracy testing)
└── test-quality (mutation testing)

LAYER 5: INTELLIGENCE
└── codebase-intel (knowledge graph)
```

Shared persistent storage: `~/.mcp-migration-kb/`

---

## Container Deployment (MongoDB + Redis)

```bash
MIGRATION_KB_STORAGE=mongodb \
MONGODB_URI=mongodb://mongo:27017/migration_kb \
REDIS_URL=redis://redis:6379/0 \
python servers/migration_kb_mcp_server.py
```

See `migration_kb_mcp_server.py` bottom for Docker Compose example.

---

## System Requirements

- Python 3.10+
- JDK 17+ (for jar-scanner, openrewrite, test-quality)
- Git (for codebase-intel ownership)
- Maven or Gradle (for openrewrite and test-quality)
