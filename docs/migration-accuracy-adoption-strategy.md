# Migration Accuracy & Organization-Wide Adoption Strategy

## Ensuring 100% Accurate Migration with Enterprise Governance

---

## 1. The Accuracy Problem

Code generation is only as good as its inputs. Here's where accuracy can break down and how to address each gap:

```
┌────────────────────────────────────────────────────────────────────┐
│                    ACCURACY RISK CHAIN                              │
│                                                                    │
│  Scanning ──► Knowledge ──► Mapping ──► Generation ──► Validation  │
│                                                                    │
│  Risk 1:      Risk 2:      Risk 3:     Risk 4:       Risk 5:     │
│  Incomplete   Stale KB     Wrong rule  Template bug  No behavioral│
│  parsing      entries      match       or missing    parity test  │
│                                        edge case                   │
│                                                                    │
│  EACH LAYER NEEDS ITS OWN VALIDATION STRATEGY                     │
└────────────────────────────────────────────────────────────────────┘
```

The strategy below introduces **six new MCP servers** that form a validation and governance layer on top of the existing five.

---

## 2. Architecture — The Complete Platform

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│  LAYER 1: SCANNING (existing)                                          │
│  ┌──────────────┐  ┌──────────────┐                                   │
│  │ jar-scanner  │  │ spring-      │                                   │
│  │              │  │ scanner      │                                   │
│  └──────────────┘  └──────────────┘                                   │
│                                                                         │
│  LAYER 2: KNOWLEDGE (existing)                                         │
│  ┌──────────────────────────────────────────┐                          │
│  │ migration-kb                              │                          │
│  └──────────────────────────────────────────┘                          │
│                                                                         │
│  LAYER 3: MAPPING + GENERATION (existing)                              │
│  ┌──────────────────┐  ┌──────────────────┐                           │
│  │ migration-codegen │  │ migration-       │                           │
│  │                   │  │ templates        │                           │
│  └──────────────────┘  └──────────────────┘                           │
│                                                                         │
│  ═══════════════════════════════════════════════════════════            │
│  NEW — LAYER 4: VALIDATION & GOVERNANCE                                │
│  ═══════════════════════════════════════════════════════════            │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │ migration-   │  │ migration-   │  │ migration-   │                │
│  │ validator    │  │ test-gen     │  │ dashboard    │                │
│  │              │  │              │  │              │                │
│  │ Compile check│  │ Contract     │  │ Tracking &   │                │
│  │ Parity check │  │ tests       │  │ reporting    │                │
│  │ Rule audit   │  │ Behavioral  │  │ Org-wide     │                │
│  │ Lint         │  │ parity      │  │ metrics      │                │
│  └──────────────┘  └──────────────┘  └──────────────┘                │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │ migration-   │  │ migration-   │  │ migration-   │                │
│  │ approval     │  │ golden-      │  │ regression   │                │
│  │              │  │ samples      │  │              │                │
│  │ Review gate  │  │              │  │ CI/CD        │                │
│  │ Sign-off     │  │ Verified     │  │ pipeline     │                │
│  │ workflow     │  │ reference    │  │ integration  │                │
│  │              │  │ migrations   │  │              │                │
│  └──────────────┘  └──────────────┘  └──────────────┘                │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Accuracy Layer 1 — Scanner Validation

### Problem
Regex-based Java parsing can miss edge cases: multi-line annotations, complex generics, annotation processors, Lombok-generated code that's not in source.

### Solution: Scanner Accuracy Test Suite

Create a **reference project** with known, hand-verified class metadata. Run the scanner against it and compare output to expected results.

```
~/.mcp-migration-kb/
└── validation/
    └── scanner-test-project/
        ├── src/main/java/
        │   ├── SimpleService.java          ← basic @Service + @Autowired
        │   ├── LombokService.java          ← @RequiredArgsConstructor + final fields
        │   ├── MultiAnnotationDao.java     ← @Repository + @Transactional + @Cacheable
        │   ├── GenericBaseClass.java        ← BaseService<T extends BaseEntity, ID>
        │   ├── NestedGenerics.java          ← Map<String, List<Optional<Order>>>
        │   ├── MultiLineAnnotation.java     ← @RequestMapping(\n    value = "/api"\n)
        │   ├── JavaxImports.java            ← javax.persistence.*, javax.validation.*
        │   └── ComplexInheritance.java       ← extends + implements + generics
        └── expected/
            ├── SimpleService.expected.json
            ├── LombokService.expected.json
            └── ...
```

**Validation process:**
1. Scan the reference project
2. Compare scanner output JSON against expected JSON
3. Report accuracy percentage per field (annotations: 98%, deps: 95%, etc.)
4. Any accuracy drop blocks template updates

```python
# scanner_accuracy_test.py (run in CI)
def test_scanner_accuracy():
    actual = scan_spring_project("scanner-test-project")
    for class_name, expected in load_expected_results():
        actual_class = actual["beans"][class_name]
        assert actual_class["layer"] == expected["layer"]
        assert set(actual_class["annotations"]) == set(expected["annotations"])
        assert set(actual_class["all_dependency_types"]) == set(expected["dependencies"])
```

### Recommended Enhancement: AST-Based Fallback

For classes where regex parsing fails, add a fallback that shells out to `tree-sitter` (for source) or `javap -verbose` (for bytecode) to get a second opinion:

```python
@mcp.tool()
def verify_scan_accuracy(project_name: str, sample_size: int = 10) -> dict:
    """Cross-validate scanner output against tree-sitter AST for a sample of classes."""
```

---

## 4. Accuracy Layer 2 — Knowledge Base Integrity

### Problem
KB can go stale if someone pushes code changes without rescanning. Cross-project references can be broken if a library is updated but consuming apps aren't re-scanned.

### Solution: KB Freshness Checks

```python
@mcp.tool()
def check_kb_freshness() -> dict:
    """
    Compare KB file hashes against current git HEAD for each project.
    Returns: which projects are stale and need rescanning.
    """
    stale = []
    for project_name, project in kb.projects.items():
        current_hash = compute_project_hash(project.path)
        if current_hash != project.file_hash:
            stale.append({
                "project": project_name,
                "last_scanned": project.scanned_at,
                "status": "STALE"
            })
    return {"stale_projects": stale}
```

### Solution: Git Hook Integration

```bash
#!/bin/bash
# .git/hooks/post-commit (in each legacy repo)
# Auto-trigger KB rescan when Java files change

CHANGED_JAVA=$(git diff --name-only HEAD~1 HEAD | grep "\.java$" | wc -l)
if [ "$CHANGED_JAVA" -gt "0" ]; then
    echo "Java files changed — triggering KB rescan..."
    python /path/to/migration_kb_mcp_server.py --rescan $(basename $(pwd))
fi
```

### Solution: KB Consistency Validator

```python
@mcp.tool()
def validate_kb_consistency() -> dict:
    """
    Check for:
    1. Dangling references (class depends on something not in KB)
    2. Duplicate FQCNs across projects
    3. Missing cross-project links
    4. Interface without implementations
    """
```

---

## 5. Accuracy Layer 3 — Mapping Rule Validation

### Problem
A wrong mapping rule silently generates incorrect code for every class it matches. One bad rule can corrupt hundreds of generated files.

### Solution: Golden Sample Testing

The **golden sample** pattern is the most critical accuracy mechanism. For each mapping rule, maintain a hand-verified, architect-approved reference migration:

```
~/.mcp-migration-kb/
└── golden-samples/
    ├── rule-dao-to-reactive-repo/
    │   ├── legacy/
    │   │   └── OrderDao.java               ← Real legacy class
    │   ├── expected/
    │   │   └── OrderRepository.java         ← Hand-verified correct migration
    │   └── metadata.json                    ← Rule ID, approved by, date
    │
    ├── rule-service-to-event-service/
    │   ├── legacy/
    │   │   └── OrderService.java
    │   ├── expected/
    │   │   └── OrderService.java            ← Architect-approved target
    │   └── metadata.json
    │
    └── rule-listener-to-kafka/
        ├── legacy/
        │   └── OrderEventHandler.java
        ├── expected/
        │   └── OrderEventConsumer.java
        └── metadata.json
```

**Validation process:**
1. Run `generate_migration` against each golden sample's legacy class
2. Compare generated output against the `expected/` file
3. Diff must be empty (or only contain expected TODO markers)
4. Run this in CI on every mapping rule or template change

```python
@mcp.tool()
def validate_golden_samples() -> dict:
    """
    Run all golden sample tests. For each:
    1. Load legacy class from golden-samples/rule-X/legacy/
    2. Generate migration code using the corresponding mapping rule
    3. Compare against golden-samples/rule-X/expected/
    4. Report: PASS, FAIL, or DRIFT (generated differs from expected)
    """
```

### Solution: Rule Coverage Report

```python
@mcp.tool()
def mapping_rule_coverage(project_name: str) -> dict:
    """
    For every class in a project, show:
    - Which mapping rules match
    - Which classes have NO matching rules (gap)
    - Which rules match nothing (dead rules)
    """
```

This prevents two failure modes: classes that fall through the cracks (no rule matches) and rules that are never triggered (potentially misconfigured).

---

## 6. Accuracy Layer 4 — Generated Code Validation

### Problem
Generated code may compile but behave differently from the legacy code.

### Solution: Multi-Level Validation Pipeline

```
Generated Code
      │
      ▼
┌──────────────┐
│ Level 1:     │  Does it compile?
│ COMPILATION  │  javac + dependency resolution
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 2:     │  Does it follow framework conventions?
│ LINT / STYLE │  Checkstyle, custom rules
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 3:     │  Does it wire correctly?
│ SPRING       │  ApplicationContext loads, all beans resolve
│ CONTEXT      │
└──────┬───────┘
       ▼
┌──────────────┐
│ Level 4:     │  Does it behave the same?
│ BEHAVIORAL   │  Contract tests, dual-run comparison
│ PARITY       │
└──────────────┘
```

### Level 1: Compilation Check

```python
@mcp.tool()
def validate_compilation(output_dir: str, classpath: str = "") -> dict:
    """
    Compile all generated .java files.
    Returns: compilation errors grouped by file.
    """
    # Uses subprocess to call javac with the target framework on classpath
```

### Level 2: Convention Lint

```python
@mcp.tool()
def validate_conventions(output_dir: str) -> dict:
    """
    Check generated code against your framework's conventions:
    - Every @Service has constructor injection (no @Autowired fields)
    - Every mutating method publishes a domain event
    - Every Kafka consumer has manual acknowledgment
    - Every repository extends the correct base type
    - No javax.* imports remain
    - All TODO markers are tagged with migration ticket IDs
    """
```

### Level 3: Spring Context Validation

Generate a minimal Spring Boot test that loads the application context with all generated beans:

```java
@SpringBootTest
class MigrationContextTest {
    @Test
    void contextLoads() {
        // If this passes, all beans wire correctly
    }
}
```

### Level 4: Behavioral Parity — Contract Tests

This is the gold standard for accuracy. For every migrated class, generate a **contract test** that:

1. Calls the same method with the same inputs on both legacy and migrated code
2. Compares outputs
3. Compares side effects (DB writes, events published)

```python
@mcp.tool()
def generate_contract_test(
    project_name: str,
    class_name: str,
    legacy_test_data_path: str = ""
) -> dict:
    """
    Generate a behavioral parity test that:
    1. Sets up identical state
    2. Calls legacy method via legacy service
    3. Calls migrated method via new service
    4. Asserts outputs match
    5. Asserts side effects match
    """
```

---

## 7. Organization-Wide Adoption — Governance Model

### Migration Control Board

```
┌─────────────────────────────────────────────────────────────┐
│                MIGRATION GOVERNANCE STRUCTURE                │
│                                                              │
│  ┌─────────────────┐                                        │
│  │ Migration        │  Owns: rules, templates, golden       │
│  │ Control Board    │  samples, approval workflow            │
│  │ (Architecture)   │                                        │
│  └────────┬────────┘                                        │
│           │                                                  │
│  ┌────────┼─────────────────────────────────┐               │
│  │        ▼                                  │               │
│  │ ┌──────────────┐  ┌──────────────┐       │               │
│  │ │ Rule Owners  │  │ Template     │       │               │
│  │ │ (per domain) │  │ Owners       │       │               │
│  │ │              │  │              │       │               │
│  │ │ Order domain │  │ Service tpl  │       │               │
│  │ │ Payment domain│ │ DAO tpl      │       │               │
│  │ │ Inventory    │  │ Kafka tpl    │       │               │
│  │ └──────────────┘  └──────────────┘       │               │
│  │                                           │               │
│  │  Platform Team                            │               │
│  └───────────────────────────────────────────┘               │
│           │                                                  │
│  ┌────────┼─────────────────────────────────┐               │
│  │        ▼                                  │               │
│  │  Application Teams                        │               │
│  │  • Use approved rules & templates         │               │
│  │  • Cannot modify rules without approval   │               │
│  │  • Generate → validate → submit PR        │               │
│  └───────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### Role Definitions

| Role | Responsibility | Access Level |
|------|----------------|-------------|
| **Migration Architect** | Owns mapping rules, approves golden samples, signs off on template changes | Full: all 11 servers |
| **Platform Team** | Maintains MCP servers, CI pipeline, KB infrastructure | Full: all servers + CI |
| **Template Owner** | Owns specific templates, reviews generated code quality | Codegen + Templates |
| **Rule Owner** (per domain) | Owns mapping rules for their domain, verifies golden samples | Codegen (own rules only) |
| **Application Developer** | Scans their app, generates code, submits for review | Scanner + KB (read) + Codegen (generate only) |

### Approval Workflow

```
Developer                    Rule Owner              Migration Architect
    │                            │                          │
    │  1. Generate code          │                          │
    │  (dry_run=True)            │                          │
    │                            │                          │
    │  2. Review generated       │                          │
    │     code locally           │                          │
    │                            │                          │
    │  3. Submit PR ────────────►│                          │
    │     (generated code +      │                          │
    │      validation report)    │                          │
    │                            │                          │
    │                            │  4. Review against       │
    │                            │     golden sample        │
    │                            │                          │
    │                            │  5. Run contract tests   │
    │                            │                          │
    │                            │  6. Approve / Request ──►│
    │                            │     changes              │
    │                            │                          │
    │                            │                     7. Final sign-off
    │                            │                        (for HIGH risk)
    │                            │                          │
    │  8. Merge + deploy         │                          │
    │◄───────────────────────────┤                          │
```

---

## 8. CI/CD Pipeline Integration

### Pipeline Architecture

```yaml
# .github/workflows/migration-validation.yml

name: Migration Validation Pipeline

on:
  pull_request:
    paths:
      - 'src/main/java/**'
      - '.mcp-migration-kb/**'

jobs:
  # ── Stage 1: KB Freshness ──
  kb-freshness:
    runs-on: ubuntu-latest
    steps:
      - name: Check KB is up to date
        run: python migration_kb_mcp_server.py --check-freshness
      - name: Fail if stale
        run: |
          if [ "$(cat freshness-report.json | jq '.stale_projects | length')" -gt "0" ]; then
            echo "KB is stale. Run rescan before migrating."
            exit 1
          fi

  # ── Stage 2: Golden Sample Tests ──
  golden-samples:
    runs-on: ubuntu-latest
    steps:
      - name: Run golden sample validation
        run: python validate_golden_samples.py
      - name: Fail on drift
        run: |
          if grep -q "FAIL" golden-sample-report.json; then
            echo "Golden sample mismatch. Review mapping rules."
            exit 1
          fi

  # ── Stage 3: Compile Generated Code ──
  compile-check:
    runs-on: ubuntu-latest
    steps:
      - name: Compile generated sources
        run: mvn compile -pl generated-module -am
      - name: Check for javax imports
        run: |
          JAVAX_COUNT=$(grep -r "import javax\." src/main/java/ | wc -l)
          if [ "$JAVAX_COUNT" -gt "0" ]; then
            echo "javax imports detected in generated code!"
            exit 1
          fi

  # ── Stage 4: Convention Lint ──
  convention-check:
    runs-on: ubuntu-latest
    steps:
      - name: Run migration convention checks
        run: python validate_conventions.py --dir src/main/java/
      - name: Verify no field injection
        run: |
          FIELD_INJECT=$(grep -r "@Autowired" src/main/java/ \
            | grep -v "test" | wc -l)
          if [ "$FIELD_INJECT" -gt "0" ]; then
            echo "Field injection detected! Use constructor injection."
            exit 1
          fi

  # ── Stage 5: Spring Context Load ──
  context-test:
    runs-on: ubuntu-latest
    services:
      kafka:
        image: confluentinc/cp-kafka:7.5.0
      mongodb:
        image: mongo:7.0
    steps:
      - name: Run context load test
        run: mvn test -Dtest=MigrationContextTest

  # ── Stage 6: Contract Tests ──
  contract-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Run behavioral parity tests
        run: mvn test -Dtest=*ContractTest
      - name: Publish parity report
        uses: actions/upload-artifact@v3
        with:
          name: parity-report
          path: target/parity-report.html
```

### PR Template for Migration PRs

```markdown
## Migration PR Checklist

**Migrated class:** `com.company.service.OrderService`
**Applied rules:** `service-to-event-service`, `outbox-pattern`
**Template used:** `event_driven_service`

### Validation Results
- [ ] Golden sample test: PASS
- [ ] Compilation: PASS
- [ ] Convention lint: PASS (0 violations)
- [ ] Spring context loads: PASS
- [ ] Contract tests: PASS (X/Y methods verified)
- [ ] javax import check: PASS (0 javax imports)

### Generated Files
- `OrderService.java` — migrated service with OutboxPublisher
- `OrderCreatedEvent.java` — domain event record
- `OrderUpdatedEvent.java` — domain event record
- `OrderServiceIntegrationTest.java` — test scaffold

### Manual Review Items
- [ ] Business logic TODOs filled in
- [ ] Event payloads reviewed by domain owner
- [ ] Error handling matches legacy behavior
- [ ] Logging matches observability standards
```

---

## 9. Tracking & Reporting — Migration Dashboard

### Metrics to Track

```python
@mcp.tool()
def migration_progress_report() -> dict:
    """
    Organization-wide migration progress:
    - Per project: % classes migrated, % tests passing
    - Per layer: DAO migration %, Service migration %, etc.
    - Per rule: how many classes each rule has processed
    - Blockers: classes with no matching rules
    - Quality: golden sample pass rate, compilation success rate
    """
```

### Dashboard Data Model

```
┌──────────────────────────────────────────────────────────────────┐
│               MIGRATION DASHBOARD                                 │
│                                                                   │
│  Overall Progress                                                │
│  ████████████████████░░░░░░░░░░  62% (341 / 548 classes)        │
│                                                                   │
│  By Project                                                      │
│  common-lib        ██████████████████████  100% ✓               │
│  order-service     ████████████████░░░░░░   75%                 │
│  payment-service   ████████████░░░░░░░░░░   55%                 │
│  inventory-service ████████░░░░░░░░░░░░░░   40%                 │
│  shipping-service  ░░░░░░░░░░░░░░░░░░░░░░    0% (not started)  │
│                                                                   │
│  By Layer                                                        │
│  Entities          ██████████████████████  100% ✓               │
│  DAOs              ████████████████████░░   90%                 │
│  Services          ████████████████░░░░░░   72%                 │
│  Messaging         ████████████░░░░░░░░░░   50%                 │
│  Controllers       ████░░░░░░░░░░░░░░░░░░   20%                │
│                                                                   │
│  Quality Gates                                                   │
│  Golden samples    18/18 PASS  ✓                                │
│  Compilation       97% success                                   │
│  Contract tests    89% parity                                    │
│  Unmapped classes  23 (need new rules)                           │
└──────────────────────────────────────────────────────────────────┘
```

### Tracking Schema

```json
{
  "project": "order-service",
  "class_fqcn": "com.company.service.OrderService",
  "status": "MIGRATED",
  "migration_date": "2026-03-15",
  "rules_applied": ["service-to-event-service"],
  "template_used": "event_driven_service",
  "generated_files": ["OrderService.java", "OrderCreatedEvent.java"],
  "validation": {
    "golden_sample": "PASS",
    "compilation": "PASS",
    "convention_lint": "PASS",
    "context_load": "PASS",
    "contract_tests": "12/12 PASS"
  },
  "review": {
    "reviewed_by": "architect@company.com",
    "approved_date": "2026-03-17",
    "pr_url": "https://github.com/company/order-service-v2/pull/42"
  }
}
```

---

## 10. Org-Wide Rollout Strategy

### Phase 1 — Pilot (Weeks 1–4)

**Goal:** Prove accuracy with one application.

| Activity | Who | Deliverable |
|----------|-----|-------------|
| Select pilot app (lowest risk, fewest deps) | Architecture | Selected: `inventory-service` |
| Scan common-lib + pilot app | Platform Team | KB populated |
| Scan target framework | Platform Team | Framework indexed |
| Define mapping rules (full set) | Migration Architect | Rules in `_mappings.json` |
| Create golden samples for each rule | Migration Architect + Domain Owners | Golden sample directory |
| Bootstrap and customize templates | Platform Team | Templates tailored to framework |
| Generate pilot app migration | App Team + Platform Team | Generated code |
| Run full validation pipeline | CI/CD | All 4 levels PASS |
| Manual review + business logic fill-in | App Team | Production-ready code |
| Deploy pilot to staging | App Team | Dual-run validation started |
| Measure: golden sample pass rate, compilation rate, parity | Platform Team | Accuracy baseline established |

**Exit criteria:** 100% golden sample PASS, 100% compilation, 90%+ contract test parity.

### Phase 2 — Expand (Weeks 5–10)

**Goal:** Prove scalability with 3+ applications.

| Activity | Who | Deliverable |
|----------|-----|-------------|
| Onboard 3 more app teams | Platform Team | Teams trained |
| Each team scans their app | App Teams | KB expanded |
| Generate migration code (batch) | App Teams | Code generated |
| Run validation pipeline | CI/CD | Quality metrics |
| Refine rules/templates based on edge cases | Migration Architect | Rules v2 |
| Update golden samples | Rule Owners | Expanded coverage |
| Deploy apps to staging | App Teams | Dual-run for all |

**Exit criteria:** All 4 apps migrated, consistent quality metrics, no new rule gaps.

### Phase 3 — Organization-Wide (Weeks 11+)

**Goal:** Self-service migration for all teams.

| Activity | Who | Deliverable |
|----------|-----|-------------|
| Publish internal docs + training | Platform Team | Migration playbook |
| Set up self-service CI pipeline | Platform Team | Template pipeline |
| Lock mapping rules (change requires MCB approval) | Migration Architect | Governance enforced |
| Track progress on migration dashboard | Platform Team | Org-wide visibility |
| Weekly migration stand-up | All teams | Coordination |
| Monthly rule review | Migration Control Board | Rule health check |

---

## 11. Version Control Strategy for Migration Artifacts

### What to Version Control

```
your-company/migration-platform/          ← Dedicated repo
├── servers/
│   ├── jar_scanner_mcp_server.py
│   ├── springboot_scanner_mcp_server.py
│   ├── migration_kb_mcp_server.py
│   ├── migration_codegen_mcp_server.py
│   └── migration_template_engine.py
│
├── templates/                             ← Copy from ~/.mcp-migration-kb/templates
│   ├── event_driven_service.java.tpl
│   ├── reactive_repository.java.tpl
│   ├── command_handler.java.tpl
│   ├── kafka_consumer.java.tpl
│   ├── saga_participant.java.tpl
│   └── your_custom_template.java.tpl
│
├── mapping-rules/
│   └── _mappings.json                     ← Mapping rules (reviewed + approved)
│
├── golden-samples/
│   ├── rule-dao-to-reactive-repo/
│   │   ├── legacy/OrderDao.java
│   │   ├── expected/OrderRepository.java
│   │   └── metadata.json
│   ├── rule-service-to-event-service/
│   │   ├── legacy/OrderService.java
│   │   ├── expected/OrderService.java
│   │   └── metadata.json
│   └── ...
│
├── validation/
│   ├── scanner-test-project/              ← Scanner accuracy tests
│   ├── validate_golden_samples.py
│   ├── validate_conventions.py
│   └── validate_compilation.py
│
├── ci/
│   ├── migration-validation.yml           ← GitHub Actions pipeline
│   └── pr-template.md
│
├── docs/
│   ├── migration-guide.md                 ← The technical guide
│   ├── template-authoring.md
│   ├── rule-authoring.md
│   └── onboarding.md
│
└── dashboard/
    └── migration_dashboard.py             ← Progress tracking
```

### Branch Protection Rules

| Branch | Who can merge | Required checks |
|--------|--------------|-----------------|
| `main` (rules + templates) | Migration Architect only | Golden sample tests PASS + 2 reviewer approvals |
| `feature/*` (new rules) | Rule Owner → MCB review | All validation stages PASS |
| `templates/*` (template changes) | Template Owner → MCB review | Golden sample tests PASS |

---

## 12. Making It Truly 100% Accurate

No automated system is 100% accurate out of the box. The path to near-100% accuracy is:

```
                        ┌──────────────────────────┐
                        │                          │
                   ┌────▼────┐                     │
                   │ Generate│                     │
                   │ code    │                     │
                   └────┬────┘                     │
                        │                          │
                   ┌────▼────┐                     │
                   │ Validate│                     │
                   │ (4 levels)                    │
                   └────┬────┘                     │
                        │                          │
                  ┌─────▼──────┐                   │
                  │ Failures?  │───── No ──► Ship  │
                  └─────┬──────┘                   │
                        │ Yes                      │
                        ▼                          │
                  ┌──────────────┐                 │
                  │ Fix rule or  │                 │
                  │ template     │─────────────────┘
                  │              │
                  │ Update golden│     CONTINUOUS FEEDBACK LOOP
                  │ sample       │
                  └──────────────┘
```

**The accuracy formula:**

1. **Golden samples** catch rule/template bugs before they affect real migrations
2. **Compilation checks** catch syntax and import errors
3. **Convention lint** catches framework-specific violations
4. **Contract tests** catch behavioral divergence
5. **Dual-run validation** catches production-level edge cases
6. **Every failure improves a rule, template, or golden sample** — the system gets more accurate over time

The realistic target is **95% generated code accuracy** (compiles, wires correctly, follows conventions) with **5% manual fill-in** (complex business logic, edge-case error handling). The contract tests and dual-run validation then verify the manual fill-in.

---

## 13. Quick-Start Commands for Teams

```bash
# ── Platform Team: Initial Setup ──
pip install fastmcp pyyaml jinja2
python migration_template_engine.py  # → init_templates()

# ── App Team: Daily Workflow ──

# 1. Scan my app (takes ~30 seconds)
"Scan my project at /repos/order-service"

# 2. Check what rules match my Service classes
"Preview mapping for OrderService in order-service"

# 3. Generate migration code (dry run first)
"Generate migration for OrderService, dry run"

# 4. Review the output, then generate for real
"Generate all Services in order-service to /repos/order-service-v2/src/main/java"

# 5. Fill in TODOs (business logic)
# 6. Run local validation
mvn compile && mvn test -Dtest=MigrationContextTest

# 7. Submit PR with validation report
```

---

*Strategy Version: 1.0*
*Covers: Scanning accuracy, KB integrity, mapping validation, code validation,
governance model, CI/CD integration, org-wide rollout, version control strategy*
