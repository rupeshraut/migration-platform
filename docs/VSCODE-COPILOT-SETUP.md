# Using MCP Migration Platform with VS Code + GitHub Copilot

## Step-by-Step Setup Guide

---

## Prerequisites

- **VS Code 1.99+** (MCP support was introduced in 1.99)
- **GitHub Copilot extension** (Copilot Free, Pro, Pro+, Business, or Enterprise)
- **Python 3.10+**
- **JDK 17+** (for jar-scanner, openrewrite, test-quality servers)

> **Note:** If you're on a Copilot Business or Enterprise plan through an org,
> the **"MCP servers in Copilot"** policy must be enabled by your admin.

---

## Step 1: Install Python Dependencies

```bash
cd mcp-migration-platform
pip install -r requirements.txt
```

Or minimal install (no MongoDB/Redis):
```bash
pip install fastmcp pyyaml jinja2
```

---

## Step 2: Create the MCP Configuration File

Create `.vscode/mcp.json` in your **workspace root** (the project you're working on):

```json
{
  "servers": {
    "jar-scanner": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/jar_scanner_mcp_server.py"]
    },
    "spring-scanner": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/springboot_scanner_mcp_server.py"]
    },
    "migration-kb": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/migration_kb_mcp_server.py"]
    },
    "migration-codegen": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/migration_codegen_mcp_server.py"]
    },
    "migration-templates": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/migration_template_engine.py"]
    },
    "migration-validator": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/migration_validator_mcp_server.py"]
    },
    "golden-samples": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/golden_sample_runner.py"]
    },
    "openrewrite": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/openrewrite_mcp_server.py"]
    },
    "recipe-manager": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/openrewrite_recipe_manager.py"]
    },
    "test-quality": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/test_quality_mcp_server.py"]
    },
    "codebase-intel": {
      "command": "python",
      "args": ["/absolute/path/to/mcp-migration-platform/servers/codebase_intel_mcp_server.py"]
    }
  }
}
```

> **Important:** Replace `/absolute/path/to/` with the actual path where you
> extracted the zip. Use forward slashes even on Windows.

### With Environment Variables (for MongoDB/Redis):

```json
{
  "servers": {
    "migration-kb": {
      "command": "python",
      "args": ["/path/to/servers/migration_kb_mcp_server.py"],
      "env": {
        "MIGRATION_KB_STORAGE": "mongodb",
        "MONGODB_URI": "mongodb://localhost:27017/migration_kb",
        "REDIS_URL": "redis://localhost:6379/0"
      }
    }
  }
}
```

---

## Step 3: Start the MCP Servers

1. Open VS Code and open your project workspace
2. VS Code auto-detects `.vscode/mcp.json` and shows a **"Start"** button
   at the top of the file — click it
3. Alternatively, open the **Command Palette** (`Ctrl+Shift+P` / `Cmd+Shift+P`)
   and run: **`MCP: List Servers`**
4. You'll see all 11 servers listed — start the ones you need

> **First time:** VS Code will ask you to **trust** each server before starting.
> Review the config and click Allow.

---

## Step 4: Switch Copilot to Agent Mode

This is the critical step — MCP tools are only available in **Agent Mode**:

1. Open **Copilot Chat** (click the Copilot icon in the title bar, or `Ctrl+Shift+I`)
2. At the bottom of the chat panel, find the mode dropdown
3. **Change from "Ask" or "Edit" to "Agent"**
4. You should see a **tools icon (🛠️)** appear in the chat input area

```
┌─────────────────────────────────────────────────┐
│  Copilot Chat                                    │
│                                                  │
│  Mode: [Ask ▾]  ← Change this                   │
│        [Ask]                                     │
│        [Edit]                                    │
│        [Agent] ← Select this                     │
│                                                  │
│  🛠️ ← Tools icon appears in Agent mode          │
└─────────────────────────────────────────────────┘
```

---

## Step 5: Verify Tools Are Available

1. In Agent mode, click the **tools icon (🛠️)** in the chat input
2. You should see your MCP servers listed with their tools:

```
MCP Server: jar-scanner
  ☑ scan_jar
  ☑ scan_jar_package
  ☑ find_spring_components
  ☑ extract_class_hierarchy
  ...

MCP Server: migration-kb
  ☑ scan_library
  ☑ scan_application
  ☑ search_knowledge_base
  ...

MCP Server: codebase-intel
  ☑ index_repository
  ☑ search_codebase
  ☑ who_owns
  ☑ blast_radius
  ...
```

You can toggle individual tools on/off here.

---

## Step 6: Start Using It

Just type natural language prompts in Agent mode. Copilot will automatically
choose which MCP tools to invoke.

### Example Prompts

**Codebase Understanding:**
```
Index this repository and show me the architecture overview
```
```
Who owns the payment processing code? Show me the blast radius
of changing PaymentService
```
```
What Kafka topics does order-service produce and consume?
Find any orphan topics
```
```
Show me the hotspots — files that change frequently and are complex
```

**Migration Analysis:**
```
Scan the src/main/java directory as a Spring Boot application called
"order-service" and show me the migration landscape
```
```
Find all javax imports that need to migrate to jakarta
```
```
What shared library classes does order-service depend on?
Show the cross-project impact
```

**Code Generation:**
```
Generate an event-driven service for OrderService using our
migration templates
```
```
Create a Kafka consumer for the order-events topic based on the
existing OrderListener
```

**OpenRewrite:**
```
What OpenRewrite recipes should I apply to this project?
Do a dry run of the Spring Boot 3 upgrade
```
```
Create a custom YAML recipe that replaces our BaseDao with
ReactiveMongoRepository
```

**Test Quality:**
```
Analyze mutation coverage for this project and show me the
highest-risk untested code
```
```
Generate tests for the top 3 priority classes based on
mutation gaps
```

**Validation:**
```
Run the full validation pipeline on order-service
```
```
What's the migration progress across all projects?
```

---

## Tips for Best Results

### 1. Start with Indexing
Before asking questions, index your repos:
```
Index /path/to/order-service as "order-service"
Index /path/to/shared-library as "payment-commons"
```

### 2. Be Specific About Projects
```
Scan order-service at /repos/order-service and find all
classes that need javax-to-jakarta migration
```

### 3. Chain Operations
Copilot can chain multiple tools in one conversation:
```
Scan order-service, find all DAOs, generate reactive repository
replacements, and validate the generated code
```

### 4. You Don't Need All 11 Servers Running
Start only what you need:

| Task | Servers Needed |
|------|---------------|
| Codebase exploration | codebase-intel |
| Migration analysis | spring-scanner + migration-kb |
| Code generation | migration-kb + migration-codegen + migration-templates |
| OpenRewrite transforms | openrewrite + recipe-manager |
| Full migration pipeline | All migration servers |
| Test quality | test-quality |

### 5. Confirmation Prompts
Copilot will ask for confirmation before running tools that modify files
(like `execute_recipe`). Review the parameters before clicking "Continue".

---

## Troubleshooting

### Servers Not Starting
```
# Check Python is on PATH
python --version

# Test a server manually
python /path/to/servers/codebase_intel_mcp_server.py
```

### Tools Not Showing in Agent Mode
1. Ensure VS Code is **1.99+**: `Help → About`
2. Ensure you're in **Agent** mode, not Ask or Edit
3. Check `MCP: List Servers` in Command Palette — servers must show "Running"
4. Check server logs: Click the server name in the MCP list → "Show Output"

### "MCP servers in Copilot" Policy Error
If you're on a Copilot Business/Enterprise org plan, ask your admin to enable
the **"MCP servers in Copilot"** policy in GitHub org settings.

### Python Path Issues
If VS Code can't find Python, set the full path in mcp.json:
```json
{
  "servers": {
    "codebase-intel": {
      "command": "/usr/bin/python3",
      "args": ["/path/to/servers/codebase_intel_mcp_server.py"]
    }
  }
}
```

On Windows:
```json
{
  "servers": {
    "codebase-intel": {
      "command": "C:\\Python312\\python.exe",
      "args": ["C:\\tools\\mcp-migration-platform\\servers\\codebase_intel_mcp_server.py"]
    }
  }
}
```

### Server Crashes or Timeouts
Check the output panel: `View → Output → select the MCP server from dropdown`

Common causes:
- Missing dependency: `pip install fastmcp pyyaml jinja2`
- JDK not found: Set `JAVA_HOME` in the `env` block of mcp.json
- Large repo scan timeout: Start with a smaller repo or specific package

---

## VS Code Settings (Optional)

Add to your VS Code `settings.json` for better MCP experience:

```json
{
  "chat.mcp.enabled": true,
  "chat.agent.enabled": true
}
```

---

## User Settings vs Workspace Settings

| Location | Scope | Use When |
|----------|-------|----------|
| `.vscode/mcp.json` (workspace) | This project only | Project-specific servers |
| User `settings.json` | All projects | Servers you always want available |

To add servers to user settings, open Command Palette → **"MCP: Open User Configuration"**.

---

## Architecture: How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  VS Code                                                     │
│                                                              │
│  ┌──────────────┐     ┌──────────────────────────────────┐ │
│  │ GitHub       │────▶│ MCP Client (built into VS Code)  │ │
│  │ Copilot      │◀────│                                  │ │
│  │ (Agent Mode) │     │ Discovers tools from mcp.json    │ │
│  └──────────────┘     │ Routes tool calls to servers     │ │
│                        └──────┬───────────────────────────┘ │
│                               │ stdio (stdin/stdout)        │
│                               │                             │
└───────────────────────────────┼─────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │ Python      │     │ Python      │     │ Python      │
   │ MCP Server  │     │ MCP Server  │     │ MCP Server  │
   │             │     │             │     │             │
   │ codebase-   │     │ migration-  │     │ openrewrite │
   │ intel       │     │ kb          │     │             │
   └─────────────┘     └─────────────┘     └─────────────┘
          │                     │
          └──────────┬──────────┘
                     ▼
         ┌─────────────────────┐
         │ ~/.mcp-migration-kb │
         │ (shared storage)    │
         └─────────────────────┘
```

Each server runs as a separate Python process. VS Code communicates
with them via **stdio** (standard input/output). Copilot in Agent Mode
sees all the tools and decides which to call based on your prompt.
