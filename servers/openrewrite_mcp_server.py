"""
OpenRewrite Migration MCP Server
===================================
Wraps OpenRewrite's AST-level code transformation engine as an MCP server,
giving AI assistants the ability to discover, plan, execute, and validate
exact code transformations.

Why OpenRewrite + MCP:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                                                                     │
  │  REGEX PARSING (existing servers)    OPENREWRITE (this server)     │
  │  ─────────────────────────────────   ──────────────────────────    │
  │  • Best-effort pattern matching      • Full Java AST (Lossless     │
  │  • Generates stub code with TODOs      Semantic Tree)              │
  │  • Good for analysis + scaffolding   • Transforms actual source    │
  │  • Can miss edge cases                 in-place                    │
  │                                      • 100s of prebuilt recipes    │
  │  USE FOR: Discovery, planning,       • Deterministic, repeatable   │
  │  KB building, scaffolding            • Handles generics, lambdas,  │
  │                                        annotations perfectly       │
  │                                                                     │
  │  USE FOR: Actual code transformation                                │
  │  (javax→jakarta, Boot 2→3, Spring Security, etc.)                  │
  │                                                                     │
  │  TOGETHER: AI plans with KB → OpenRewrite executes exactly         │
  └─────────────────────────────────────────────────────────────────────┘

Execution Modes:
  1. Maven plugin    — rewrite-maven-plugin (recommended for Maven projects)
  2. Gradle plugin   — rewrite-gradle-plugin (for Gradle projects)
  3. OpenRewrite CLI  — mod CLI (standalone, works without build tool)

Environment Variables:
    OPENREWRITE_MODE=maven|gradle|cli        (default: maven)
    OPENREWRITE_CLI_PATH=/path/to/mod        (for CLI mode)
    JAVA_HOME=/path/to/jdk                   (JDK 17+ required)

Requirements:
    pip install fastmcp pyyaml

Usage:
    python openrewrite_mcp_server.py
    fastmcp dev openrewrite_mcp_server.py
"""

import json
import os
import re
import subprocess
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPENREWRITE_MODE = os.environ.get("OPENREWRITE_MODE", "maven")
OPENREWRITE_CLI_PATH = os.environ.get("OPENREWRITE_CLI_PATH", "mod")
JAVA_HOME = os.environ.get("JAVA_HOME", "")
KB_DIR = os.path.expanduser("~/.mcp-migration-kb")
REWRITE_DIR = os.path.join(KB_DIR, "openrewrite")
RUNS_DIR = os.path.join(REWRITE_DIR, "runs")

# OpenRewrite plugin versions
REWRITE_MAVEN_PLUGIN = "org.openrewrite.maven:rewrite-maven-plugin:5.45.0"
REWRITE_RECIPE_BOM = "org.openrewrite.recipe:rewrite-recipe-bom:2.22.0"

mcp = FastMCP(
    "OpenRewrite Migration",
    description=(
        "AST-level code transformation via OpenRewrite. "
        "Discover recipes, dry-run transformations, execute migrations, "
        "and validate results with exact Java AST parsing."
    ),
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Recipe Catalog — The most useful OpenRewrite recipes for migration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RECIPE_CATALOG = {
    # ── Spring Boot Migration ──
    "boot-3": {
        "recipe": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_4",
        "display_name": "Spring Boot 2.x → 3.4",
        "category": "spring-boot",
        "description": "Complete Spring Boot 3.4 upgrade including javax→jakarta, "
                       "deprecated API replacement, property migration, and dependency updates.",
        "dependencies": [
            "org.openrewrite.recipe:rewrite-spring",
        ],
        "impact": "HIGH — touches imports, configs, dependencies, security",
    },
    "boot-3.0": {
        "recipe": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "display_name": "Spring Boot 2.x → 3.0",
        "category": "spring-boot",
        "description": "Minimal Boot 3.0 upgrade (javax→jakarta, core API changes).",
        "dependencies": ["org.openrewrite.recipe:rewrite-spring"],
        "impact": "HIGH",
    },
    "boot-3.2": {
        "recipe": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_2",
        "display_name": "Spring Boot → 3.2",
        "category": "spring-boot",
        "description": "Upgrade to Boot 3.2 with RestClient support.",
        "dependencies": ["org.openrewrite.recipe:rewrite-spring"],
        "impact": "HIGH",
    },

    # ── javax → jakarta ──
    "javax-to-jakarta": {
        "recipe": "org.openrewrite.java.migrate.jakarta.JavaxMigrationToJakarta",
        "display_name": "javax.* → jakarta.*",
        "category": "java-migration",
        "description": "Migrate all javax.* imports to jakarta.* equivalents.",
        "dependencies": ["org.openrewrite.recipe:rewrite-migrate-java"],
        "impact": "MEDIUM — import changes only, no behavioral change",
    },

    # ── Java Version Upgrades ──
    "java-17": {
        "recipe": "org.openrewrite.java.migrate.UpgradeToJava17",
        "display_name": "Upgrade to Java 17",
        "category": "java-migration",
        "description": "Java 17 migration: text blocks, pattern matching, records, sealed classes.",
        "dependencies": ["org.openrewrite.recipe:rewrite-migrate-java"],
        "impact": "MEDIUM",
    },
    "java-21": {
        "recipe": "org.openrewrite.java.migrate.UpgradeToJava21",
        "display_name": "Upgrade to Java 21",
        "category": "java-migration",
        "description": "Java 21 migration: virtual threads, record patterns, switch expressions.",
        "dependencies": ["org.openrewrite.recipe:rewrite-migrate-java"],
        "impact": "MEDIUM",
    },

    # ── Spring Security ──
    "spring-security-6": {
        "recipe": "org.openrewrite.java.spring.security6.UpgradeSpringSecurity_6_0",
        "display_name": "Spring Security → 6.0",
        "category": "spring-security",
        "description": "Migrate WebSecurityConfigurerAdapter to SecurityFilterChain, "
                       "update authorization rules, CSRF configuration.",
        "dependencies": ["org.openrewrite.recipe:rewrite-spring"],
        "impact": "HIGH — security config rewrite",
    },

    # ── Testing ──
    "junit-5": {
        "recipe": "org.openrewrite.java.testing.junit5.JUnit5BestPractices",
        "display_name": "JUnit 4 → JUnit 5",
        "category": "testing",
        "description": "Migrate JUnit 4 to JUnit 5: annotations, assertions, lifecycle.",
        "dependencies": ["org.openrewrite.recipe:rewrite-testing-frameworks"],
        "impact": "MEDIUM",
    },
    "mockito-5": {
        "recipe": "org.openrewrite.java.testing.mockito.Mockito1to5Migration",
        "display_name": "Mockito → 5.x",
        "category": "testing",
        "description": "Upgrade Mockito to 5.x with updated API usage.",
        "dependencies": ["org.openrewrite.recipe:rewrite-testing-frameworks"],
        "impact": "LOW",
    },

    # ── Logging ──
    "slf4j": {
        "recipe": "org.openrewrite.java.logging.slf4j.Slf4jBestPractices",
        "display_name": "SLF4J Best Practices",
        "category": "logging",
        "description": "Parameterized logging, remove string concatenation in log statements.",
        "dependencies": ["org.openrewrite.recipe:rewrite-logging-frameworks"],
        "impact": "LOW",
    },
    "log4j-to-slf4j": {
        "recipe": "org.openrewrite.java.logging.slf4j.Log4jToSlf4j",
        "display_name": "Log4j → SLF4J",
        "category": "logging",
        "description": "Migrate Log4j 1.x/2.x API calls to SLF4J.",
        "dependencies": ["org.openrewrite.recipe:rewrite-logging-frameworks"],
        "impact": "LOW",
    },

    # ── Code Quality ──
    "common-static-analysis": {
        "recipe": "org.openrewrite.staticanalysis.CommonStaticAnalysis",
        "display_name": "Common Static Analysis Fixes",
        "category": "code-quality",
        "description": "Auto-fix common issues: unused imports, empty blocks, "
                       "unnecessary boxing, covariant equals, etc.",
        "dependencies": ["org.openrewrite.recipe:rewrite-static-analysis"],
        "impact": "LOW",
    },

    # ── Dependency Management ──
    "dependency-upgrade": {
        "recipe": "org.openrewrite.java.dependencies.UpgradeDependencyVersion",
        "display_name": "Upgrade a Maven/Gradle Dependency",
        "category": "dependencies",
        "description": "Upgrade a specific dependency to a target version.",
        "dependencies": ["org.openrewrite.recipe:rewrite-java-dependencies"],
        "impact": "MEDIUM",
    },

    # ── Micronaut ──
    "micronaut-4": {
        "recipe": "io.micronaut.rewrite.UpdateMicronautPlatformBom",
        "display_name": "Micronaut → 4.x",
        "category": "micronaut",
        "description": "Upgrade Micronaut framework to 4.x.",
        "dependencies": ["io.micronaut.rewrite:micronaut-rewrite"],
        "impact": "HIGH",
    },

    # ── Quarkus ──
    "quarkus-3": {
        "recipe": "org.openrewrite.java.migrate.quarkus.Quarkus3Migration",
        "display_name": "Quarkus → 3.x",
        "category": "quarkus",
        "description": "Migrate Quarkus 2.x to 3.x (includes javax→jakarta).",
        "dependencies": ["org.openrewrite.recipe:rewrite-migrate-java"],
        "impact": "HIGH",
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Execution Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _run_command(cmd: list[str], cwd: str, timeout: int = 600) -> dict:
    """Run a shell command and return structured output."""
    env = os.environ.copy()
    if JAVA_HOME:
        env["JAVA_HOME"] = JAVA_HOME
        env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-3000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s"}
    except FileNotFoundError as e:
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e)}


def _detect_build_tool(project_path: str) -> str:
    """Detect Maven or Gradle in a project."""
    if os.path.isfile(os.path.join(project_path, "pom.xml")):
        return "maven"
    if os.path.isfile(os.path.join(project_path, "build.gradle")) or \
       os.path.isfile(os.path.join(project_path, "build.gradle.kts")):
        return "gradle"
    return "unknown"


def _get_mvn_cmd(project_path: str) -> str:
    """Find Maven wrapper or system Maven."""
    mvnw = os.path.join(project_path, "mvnw")
    if os.path.isfile(mvnw):
        return mvnw
    mvnw_cmd = os.path.join(project_path, "mvnw.cmd")
    if os.path.isfile(mvnw_cmd):
        return mvnw_cmd
    return "mvn"


def _get_gradle_cmd(project_path: str) -> str:
    """Find Gradle wrapper or system Gradle."""
    gradlew = os.path.join(project_path, "gradlew")
    if os.path.isfile(gradlew):
        return gradlew
    return "gradle"


def _build_maven_rewrite_command(
    project_path: str,
    recipe: str,
    dependencies: list[str],
    dry_run: bool = True,
    extra_args: list[str] = None,
) -> list[str]:
    """Build the Maven rewrite plugin command."""
    mvn = _get_mvn_cmd(project_path)
    goal = "dryRun" if dry_run else "run"

    cmd = [
        mvn,
        f"{REWRITE_MAVEN_PLUGIN}:{goal}",
        f"-Drewrite.activeRecipes={recipe}",
        "-Drewrite.exportDatatables=true",
    ]

    if dependencies:
        dep_str = ",".join(dependencies)
        cmd.append(f"-Drewrite.recipeArtifactCoordinates={dep_str}")

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def _build_gradle_rewrite_command(
    project_path: str,
    recipe: str,
    dry_run: bool = True,
) -> list[str]:
    """Build the Gradle rewrite plugin command."""
    gradle = _get_gradle_cmd(project_path)
    task = "rewriteDryRun" if dry_run else "rewriteRun"
    return [gradle, task, f"-DactiveRecipe={recipe}"]


def _count_changes_from_dry_run(output: str) -> dict:
    """Parse dry-run output to count affected files."""
    changes = {
        "files_changed": 0,
        "files_added": 0,
        "files_removed": 0,
        "recipes_applied": [],
    }

    for line in output.splitlines():
        if "would be changed" in line.lower() or "has been changed" in line.lower():
            changes["files_changed"] += 1
        if "would be created" in line.lower():
            changes["files_added"] += 1
        if "would be deleted" in line.lower():
            changes["files_removed"] += 1
        recipe_match = re.search(r"Recipe: (.+)", line)
        if recipe_match:
            changes["recipes_applied"].append(recipe_match.group(1))

    # Also check for the summary line
    summary = re.search(r"(\d+)\s+files?\s+(?:would be\s+)?changed", output, re.IGNORECASE)
    if summary:
        changes["files_changed"] = max(changes["files_changed"], int(summary.group(1)))

    return changes


def _save_run_record(run_id: str, data: dict):
    """Save a run record for audit trail."""
    os.makedirs(RUNS_DIR, exist_ok=True)
    fp = os.path.join(RUNS_DIR, f"{run_id}.json")
    with open(fp, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _git_diff_summary(project_path: str) -> dict:
    """Get git diff summary after running rewrite."""
    result = _run_command(["git", "diff", "--stat"], project_path, timeout=30)
    if not result["success"]:
        return {"available": False}

    diff_result = _run_command(["git", "diff", "--name-only"], project_path, timeout=30)
    changed_files = [f.strip() for f in diff_result.get("stdout", "").splitlines() if f.strip()]

    return {
        "available": True,
        "files_changed": len(changed_files),
        "changed_files": changed_files[:50],
        "stat": result.get("stdout", "")[-2000:],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def list_recipes(category: str = "") -> dict:
    """
    List available OpenRewrite recipes from the built-in catalog.

    Args:
        category: Filter by category (spring-boot, java-migration,
                  spring-security, testing, logging, code-quality,
                  dependencies, micronaut, quarkus). Empty = all.
    """
    recipes = []
    for rid, r in RECIPE_CATALOG.items():
        if category and r["category"] != category:
            continue
        recipes.append({
            "id": rid,
            "recipe": r["recipe"],
            "name": r["display_name"],
            "category": r["category"],
            "impact": r["impact"],
            "description": r["description"],
        })

    categories = sorted(set(r["category"] for r in RECIPE_CATALOG.values()))
    return {
        "total": len(recipes),
        "categories": categories,
        "recipes": recipes,
    }


@mcp.tool()
def discover_project_recipes(project_path: str) -> dict:
    """
    Analyze a project and recommend applicable OpenRewrite recipes.

    Checks: pom.xml/build.gradle for Spring Boot version, Java version,
    javax imports, JUnit version, logging framework, and suggests
    the most relevant recipes.

    Args:
        project_path: Path to the project root.
    """
    project_path = os.path.abspath(project_path)
    build_tool = _detect_build_tool(project_path)
    recommendations = []

    # Parse pom.xml for version info
    boot_version = ""
    java_version = ""
    has_junit4 = False
    has_log4j = False
    has_springfox = False

    if build_tool == "maven":
        pom = os.path.join(project_path, "pom.xml")
        try:
            tree = ET.parse(pom)
            root = tree.getroot()
            ns = {"m": "http://maven.apache.org/POM/4.0.0"}

            parent = root.find("m:parent", ns) or root.find("parent")
            if parent is not None:
                ver = parent.find("m:version", ns) or parent.find("version")
                art = parent.find("m:artifactId", ns) or parent.find("artifactId")
                if art is not None and "spring-boot" in (art.text or ""):
                    boot_version = ver.text if ver is not None else ""

            props = root.find("m:properties", ns) or root.find("properties")
            if props is not None:
                for p in props:
                    tag = p.tag.split("}")[-1]
                    if tag in ("java.version", "maven.compiler.source"):
                        java_version = p.text or ""

            deps_elem = root.find("m:dependencies", ns) or root.find("dependencies")
            if deps_elem is not None:
                for dep in deps_elem:
                    aid = dep.find("m:artifactId", ns) or dep.find("artifactId")
                    if aid is not None:
                        if aid.text == "junit":
                            has_junit4 = True
                        if "log4j" in (aid.text or ""):
                            has_log4j = True
                        if "springfox" in (aid.text or ""):
                            has_springfox = True
        except Exception:
            pass

    # Check for javax imports
    javax_count = 0
    for jf in Path(project_path).rglob("*.java"):
        if "/test/" in str(jf):
            continue
        try:
            content = jf.read_text(errors="ignore")
            javax_count += len(re.findall(r"^import\s+javax\.", content, re.MULTILINE))
        except Exception:
            pass

    # Build recommendations
    if boot_version and boot_version.startswith("2."):
        recommendations.append({
            "recipe_id": "boot-3",
            "reason": f"Spring Boot {boot_version} detected → upgrade to 3.4",
            "priority": 1,
        })
        recommendations.append({
            "recipe_id": "spring-security-6",
            "reason": "Spring Security upgrade needed with Boot 3",
            "priority": 2,
        })

    if javax_count > 0:
        recommendations.append({
            "recipe_id": "javax-to-jakarta",
            "reason": f"{javax_count} javax imports found → migrate to jakarta",
            "priority": 1,
        })

    if java_version and int(java_version.split(".")[0]) < 17:
        recommendations.append({
            "recipe_id": "java-17",
            "reason": f"Java {java_version} detected → upgrade to 17",
            "priority": 2,
        })
    if java_version and int(java_version.split(".")[0]) < 21:
        recommendations.append({
            "recipe_id": "java-21",
            "reason": f"Java {java_version} detected → upgrade to 21",
            "priority": 3,
        })

    if has_junit4:
        recommendations.append({
            "recipe_id": "junit-5",
            "reason": "JUnit 4 detected → migrate to JUnit 5",
            "priority": 3,
        })

    if has_log4j:
        recommendations.append({
            "recipe_id": "log4j-to-slf4j",
            "reason": "Log4j detected → migrate to SLF4J",
            "priority": 4,
        })

    recommendations.append({
        "recipe_id": "common-static-analysis",
        "reason": "Always beneficial — auto-fix common code issues",
        "priority": 5,
    })

    recommendations.sort(key=lambda r: r["priority"])

    return {
        "project_path": project_path,
        "build_tool": build_tool,
        "spring_boot_version": boot_version or "not detected",
        "java_version": java_version or "not detected",
        "javax_imports": javax_count,
        "recommendations": recommendations,
        "recommended_execution_order": [r["recipe_id"] for r in recommendations],
    }


@mcp.tool()
def dry_run(
    project_path: str,
    recipe_id: str = "",
    recipe_fqn: str = "",
    timeout: int = 600,
) -> dict:
    """
    Run an OpenRewrite recipe in DRY-RUN mode — shows what WOULD change
    without actually modifying any files.

    Always run this before execute_recipe.

    Args:
        project_path:  Path to the project root.
        recipe_id:     Short recipe ID from the catalog (e.g., "boot-3").
        recipe_fqn:    Full recipe class name (alternative to recipe_id).
        timeout:       Timeout in seconds (default 600).

    Returns:
        List of files that would be changed, with change descriptions.
    """
    project_path = os.path.abspath(project_path)
    build_tool = _detect_build_tool(project_path)

    if recipe_id and recipe_id in RECIPE_CATALOG:
        recipe_info = RECIPE_CATALOG[recipe_id]
        recipe = recipe_info["recipe"]
        dependencies = recipe_info.get("dependencies", [])
    elif recipe_fqn:
        recipe = recipe_fqn
        dependencies = []
    else:
        return {"error": "Provide either recipe_id (from catalog) or recipe_fqn."}

    if build_tool == "maven":
        cmd = _build_maven_rewrite_command(project_path, recipe, dependencies, dry_run=True)
    elif build_tool == "gradle":
        cmd = _build_gradle_rewrite_command(project_path, recipe, dry_run=True)
    else:
        return {"error": f"Unsupported build tool. Found: {build_tool}. Need pom.xml or build.gradle."}

    result = _run_command(cmd, project_path, timeout)

    changes = _count_changes_from_dry_run(result.get("stdout", "") + result.get("stderr", ""))

    run_id = f"dry-run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_record = {
        "run_id": run_id,
        "type": "DRY_RUN",
        "project": project_path,
        "recipe": recipe,
        "recipe_id": recipe_id,
        "build_tool": build_tool,
        "success": result["success"],
        "changes": changes,
        "timestamp": datetime.now().isoformat(),
        "command": " ".join(cmd),
    }
    _save_run_record(run_id, run_record)

    return {
        "run_id": run_id,
        "recipe": recipe,
        "success": result["success"],
        "changes": changes,
        "message": "Dry run complete. No files modified."
                   if result["success"]
                   else f"Dry run failed: {result['stderr'][:500]}",
        "output_snippet": result.get("stdout", "")[-2000:],
        "next_step": "If the changes look correct, run execute_recipe() to apply them."
                     if result["success"] else "Fix the errors and retry.",
    }


@mcp.tool()
def execute_recipe(
    project_path: str,
    recipe_id: str = "",
    recipe_fqn: str = "",
    create_git_branch: bool = True,
    branch_name: str = "",
    timeout: int = 600,
) -> dict:
    """
    Execute an OpenRewrite recipe — ACTUALLY MODIFIES FILES.

    Always run dry_run() first to preview changes.

    Args:
        project_path:      Path to the project root.
        recipe_id:         Short recipe ID from catalog.
        recipe_fqn:        Full recipe class name.
        create_git_branch: Create a git branch before applying (recommended).
        branch_name:       Branch name (auto-generated if empty).
        timeout:           Timeout in seconds.

    Returns:
        Execution result with git diff summary.
    """
    project_path = os.path.abspath(project_path)
    build_tool = _detect_build_tool(project_path)

    if recipe_id and recipe_id in RECIPE_CATALOG:
        recipe_info = RECIPE_CATALOG[recipe_id]
        recipe = recipe_info["recipe"]
        dependencies = recipe_info.get("dependencies", [])
    elif recipe_fqn:
        recipe = recipe_fqn
        dependencies = []
    else:
        return {"error": "Provide either recipe_id or recipe_fqn."}

    # Create git branch for safety
    if create_git_branch:
        if not branch_name:
            slug = recipe_id or recipe.split(".")[-1]
            branch_name = f"rewrite/{slug}-{datetime.now().strftime('%Y%m%d')}"
        branch_result = _run_command(
            ["git", "checkout", "-b", branch_name], project_path, timeout=10,
        )
        if not branch_result["success"]:
            return {
                "error": f"Failed to create git branch '{branch_name}': {branch_result['stderr']}",
                "hint": "Ensure the project is a git repo with clean working tree.",
            }

    # Execute
    if build_tool == "maven":
        cmd = _build_maven_rewrite_command(project_path, recipe, dependencies, dry_run=False)
    elif build_tool == "gradle":
        cmd = _build_gradle_rewrite_command(project_path, recipe, dry_run=False)
    else:
        return {"error": f"Unsupported build tool: {build_tool}"}

    result = _run_command(cmd, project_path, timeout)

    # Get git diff
    git_diff = _git_diff_summary(project_path)

    run_id = f"exec-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_record = {
        "run_id": run_id,
        "type": "EXECUTE",
        "project": project_path,
        "recipe": recipe,
        "recipe_id": recipe_id,
        "build_tool": build_tool,
        "success": result["success"],
        "git_branch": branch_name if create_git_branch else None,
        "git_diff": git_diff,
        "timestamp": datetime.now().isoformat(),
        "command": " ".join(cmd),
    }
    _save_run_record(run_id, run_record)

    return {
        "run_id": run_id,
        "recipe": recipe,
        "success": result["success"],
        "git_branch": branch_name if create_git_branch else "(not created)",
        "git_diff": git_diff,
        "message": "Recipe applied successfully. Review changes in git."
                   if result["success"]
                   else f"Execution failed: {result['stderr'][:500]}",
        "next_steps": [
            f"Review changes: git diff (on branch '{branch_name}')" if create_git_branch else "Review changes: git diff",
            "Run tests: mvn test" if build_tool == "maven" else "Run tests: gradle test",
            "If good: git add -A && git commit -m 'Apply OpenRewrite: {recipe}'",
            "If bad: git checkout main (discard changes)",
        ],
    }


@mcp.tool()
def execute_recipe_chain(
    project_path: str,
    recipe_ids: str,
    create_git_branch: bool = True,
    branch_name: str = "",
    timeout_per_recipe: int = 600,
) -> dict:
    """
    Execute multiple recipes in sequence. Each recipe builds on the
    previous one's changes. Creates a single git branch for all changes.

    Recommended order for Boot 3 migration:
      "java-17,javax-to-jakarta,boot-3,spring-security-6,junit-5"

    Args:
        project_path:        Path to project root.
        recipe_ids:          Comma-separated recipe IDs in execution order.
        create_git_branch:   Create a git branch before starting.
        branch_name:         Branch name.
        timeout_per_recipe:  Timeout per recipe in seconds.
    """
    project_path = os.path.abspath(project_path)
    ids = [r.strip() for r in recipe_ids.split(",") if r.strip()]

    if not ids:
        return {"error": "No recipe IDs provided."}

    for rid in ids:
        if rid not in RECIPE_CATALOG:
            return {"error": f"Unknown recipe: '{rid}'. Use list_recipes() to see available recipes."}

    build_tool = _detect_build_tool(project_path)
    if build_tool == "unknown":
        return {"error": "No pom.xml or build.gradle found."}

    # Create branch
    if create_git_branch:
        if not branch_name:
            branch_name = f"rewrite/migration-{datetime.now().strftime('%Y%m%d')}"
        branch_result = _run_command(["git", "checkout", "-b", branch_name], project_path, timeout=10)
        if not branch_result["success"]:
            return {"error": f"Git branch creation failed: {branch_result['stderr']}"}

    # Execute in sequence
    chain_results = []
    all_success = True

    for rid in ids:
        recipe_info = RECIPE_CATALOG[rid]
        recipe = recipe_info["recipe"]
        dependencies = recipe_info.get("dependencies", [])

        if build_tool == "maven":
            cmd = _build_maven_rewrite_command(project_path, recipe, dependencies, dry_run=False)
        else:
            cmd = _build_gradle_rewrite_command(project_path, recipe, dry_run=False)

        result = _run_command(cmd, project_path, timeout_per_recipe)

        step_result = {
            "recipe_id": rid,
            "recipe": recipe,
            "success": result["success"],
            "message": result.get("stderr", "")[:300] if not result["success"] else "Applied",
        }
        chain_results.append(step_result)

        if not result["success"]:
            all_success = False
            break  # Stop chain on failure

    git_diff = _git_diff_summary(project_path)

    run_id = f"chain-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    _save_run_record(run_id, {
        "type": "CHAIN", "recipes": ids, "results": chain_results,
        "success": all_success, "git_branch": branch_name,
        "timestamp": datetime.now().isoformat(),
    })

    return {
        "run_id": run_id,
        "recipes_executed": len(chain_results),
        "all_success": all_success,
        "git_branch": branch_name if create_git_branch else None,
        "git_diff": git_diff,
        "results": chain_results,
    }


@mcp.tool()
def compose_custom_recipe(
    recipe_name: str,
    description: str,
    recipe_list: str,
    save_to_project: str = "",
) -> dict:
    """
    Compose a custom recipe YAML that chains multiple recipes together.
    This creates a reusable recipe definition for your specific migration.

    Args:
        recipe_name:     Full recipe name (e.g., "com.company.migration.FullMigration")
        description:     What this composite recipe does
        recipe_list:     Comma-separated recipe FQNs or catalog IDs
        save_to_project: Project path to save the YAML (optional)

    Returns:
        The generated rewrite.yml content.
    """
    ids = [r.strip() for r in recipe_list.split(",") if r.strip()]
    resolved_recipes = []

    for rid in ids:
        if rid in RECIPE_CATALOG:
            resolved_recipes.append(RECIPE_CATALOG[rid]["recipe"])
        else:
            resolved_recipes.append(rid)

    yaml_content = f"""---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_name}
displayName: {description}
description: >-
  Custom composite recipe for migration.
  Generated by Migration MCP Platform on {datetime.now().strftime('%Y-%m-%d')}.
recipeList:
"""
    for recipe in resolved_recipes:
        yaml_content += f"  - {recipe}\n"

    if save_to_project:
        rewrite_yml = os.path.join(os.path.abspath(save_to_project), "rewrite.yml")
        existing = ""
        if os.path.isfile(rewrite_yml):
            existing = Path(rewrite_yml).read_text()
            if recipe_name in existing:
                return {"error": f"Recipe '{recipe_name}' already exists in rewrite.yml. Remove it first."}
            yaml_content = existing.rstrip() + "\n\n" + yaml_content

        Path(rewrite_yml).write_text(yaml_content)
        return {
            "status": "saved",
            "file": rewrite_yml,
            "recipe_name": recipe_name,
            "recipes_included": resolved_recipes,
            "content": yaml_content,
        }

    return {
        "recipe_name": recipe_name,
        "recipes_included": resolved_recipes,
        "yaml_content": yaml_content,
        "next_step": "Save this as rewrite.yml in your project root, or pass save_to_project parameter.",
    }


@mcp.tool()
def validate_after_rewrite(project_path: str, run_tests: bool = True) -> dict:
    """
    Validate a project after OpenRewrite execution:
    1. Compilation check
    2. Remaining javax imports check
    3. Test execution (optional)
    4. Git change summary

    Args:
        project_path: Path to the project root.
        run_tests:    Whether to run the test suite.
    """
    project_path = os.path.abspath(project_path)
    build_tool = _detect_build_tool(project_path)
    results = {}

    # 1. Compile
    if build_tool == "maven":
        compile_result = _run_command(
            [_get_mvn_cmd(project_path), "compile", "-q"], project_path, timeout=300,
        )
    elif build_tool == "gradle":
        compile_result = _run_command(
            [_get_gradle_cmd(project_path), "compileJava", "-q"], project_path, timeout=300,
        )
    else:
        compile_result = {"success": False, "stderr": "Unknown build tool"}

    results["compilation"] = {
        "pass": compile_result["success"],
        "errors": compile_result.get("stderr", "")[:1000] if not compile_result["success"] else "",
    }

    # 2. javax check
    javax_count = 0
    javax_files = []
    for jf in Path(project_path).rglob("*.java"):
        if "/test/" in str(jf):
            continue
        try:
            content = jf.read_text(errors="ignore")
            count = len(re.findall(r"^import\s+javax\.", content, re.MULTILINE))
            if count > 0:
                javax_count += count
                javax_files.append(str(jf.relative_to(project_path)))
        except Exception:
            pass

    results["javax_imports"] = {
        "pass": javax_count == 0,
        "remaining_count": javax_count,
        "files": javax_files[:20],
    }

    # 3. Tests
    if run_tests:
        if build_tool == "maven":
            test_result = _run_command(
                [_get_mvn_cmd(project_path), "test", "-q"], project_path, timeout=600,
            )
        elif build_tool == "gradle":
            test_result = _run_command(
                [_get_gradle_cmd(project_path), "test", "-q"], project_path, timeout=600,
            )
        else:
            test_result = {"success": False, "stderr": "Unknown build tool"}

        # Parse test results
        test_summary = test_result.get("stdout", "") + test_result.get("stderr", "")
        tests_run = re.search(r"Tests run:\s*(\d+)", test_summary)
        failures = re.search(r"Failures:\s*(\d+)", test_summary)
        errors = re.search(r"Errors:\s*(\d+)", test_summary)

        results["tests"] = {
            "pass": test_result["success"],
            "tests_run": int(tests_run.group(1)) if tests_run else 0,
            "failures": int(failures.group(1)) if failures else 0,
            "errors": int(errors.group(1)) if errors else 0,
            "output_snippet": test_summary[-1000:] if not test_result["success"] else "",
        }
    else:
        results["tests"] = {"skipped": True}

    # 4. Git summary
    results["git_diff"] = _git_diff_summary(project_path)

    # Overall
    all_pass = (
        results["compilation"]["pass"]
        and results["javax_imports"]["pass"]
        and (results["tests"].get("pass", True) or results["tests"].get("skipped", False))
    )

    return {
        "overall_pass": all_pass,
        "results": results,
    }


@mcp.tool()
def list_runs(limit: int = 20) -> dict:
    """List recent OpenRewrite execution runs."""
    if not os.path.isdir(RUNS_DIR):
        return {"runs": [], "total": 0}

    runs = []
    for f in sorted(Path(RUNS_DIR).glob("*.json"), reverse=True)[:limit]:
        try:
            with open(f) as fh:
                data = json.load(fh)
            runs.append({
                "run_id": data.get("run_id"),
                "type": data.get("type"),
                "recipe": data.get("recipe_id") or data.get("recipe", ""),
                "success": data.get("success"),
                "timestamp": data.get("timestamp"),
                "git_branch": data.get("git_branch"),
            })
        except Exception:
            pass

    return {"total": len(runs), "runs": runs}


@mcp.tool()
def check_prerequisites() -> dict:
    """
    Verify that OpenRewrite prerequisites are met:
    - Java 17+ available
    - Maven or Gradle available
    - Git available
    """
    checks = {}

    # Java
    java_result = _run_command(["java", "-version"], ".", timeout=10)
    java_version = ""
    if java_result["success"]:
        ver_match = re.search(r'"(\d+)', java_result.get("stderr", "") + java_result.get("stdout", ""))
        java_version = ver_match.group(1) if ver_match else "unknown"
    checks["java"] = {
        "available": java_result["success"],
        "version": java_version,
        "meets_minimum": int(java_version) >= 17 if java_version.isdigit() else False,
    }

    # Maven
    mvn_result = _run_command(["mvn", "--version"], ".", timeout=10)
    checks["maven"] = {"available": mvn_result["success"]}

    # Gradle
    gradle_result = _run_command(["gradle", "--version"], ".", timeout=10)
    checks["gradle"] = {"available": gradle_result["success"]}

    # Git
    git_result = _run_command(["git", "--version"], ".", timeout=10)
    checks["git"] = {"available": git_result["success"]}

    all_ok = (
        checks["java"]["meets_minimum"]
        and (checks["maven"]["available"] or checks["gradle"]["available"])
        and checks["git"]["available"]
    )

    return {"all_prerequisites_met": all_ok, "checks": checks}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("openrewrite://status")
def openrewrite_status() -> str:
    lines = [
        "OpenRewrite Migration Server",
        "=" * 40,
        f"Mode: {OPENREWRITE_MODE}",
        f"Catalog: {len(RECIPE_CATALOG)} recipes",
        f"Runs dir: {RUNS_DIR}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# THE COMPLETE 8-SERVER STACK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# .vscode/mcp.json:
# {
#   "servers": {
#     "migration-kb":        {"command": "python", "args": ["migration_kb_mcp_server_v2.py"]},
#     "migration-codegen":   {"command": "python", "args": ["migration_codegen_mcp_server.py"]},
#     "migration-templates": {"command": "python", "args": ["migration_template_engine.py"]},
#     "spring-scanner":      {"command": "python", "args": ["springboot_scanner_mcp_server.py"]},
#     "jar-scanner":         {"command": "python", "args": ["jar_scanner_mcp_server.py"]},
#     "migration-validator": {"command": "python", "args": ["migration_validator_mcp_server.py"]},
#     "golden-samples":      {"command": "python", "args": ["golden_sample_runner.py"]},
#     "openrewrite":         {"command": "python", "args": ["openrewrite_mcp_server.py"]}
#   }
# }
