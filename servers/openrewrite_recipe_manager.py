"""
OpenRewrite Recipe Manager MCP Server
========================================
Manages the full lifecycle of OpenRewrite recipes:

  1. DISCOVER  — Browse community recipe catalog, check for updates
  2. AUTHOR    — Create custom recipes (YAML declarative or Java imperative)
  3. COMPOSE   — Build composite recipes chaining multiple transformations
  4. CONFIGURE — Manage rewrite.yml and build plugin configuration
  5. TEST      — Validate recipes against test fixtures
  6. VERSION   — Track recipe versions and changelog
  7. PUBLISH   — Package and deploy to internal artifact repository

Recipe Types:
  ┌─────────────────────────────────────────────────────────────┐
  │                                                             │
  │  YAML Declarative (simple, no Java code needed)            │
  │  ──────────────────────────────────────────────            │
  │  • Chain existing recipes with parameters                  │
  │  • Find-and-replace (types, methods, annotations)          │
  │  • Change dependency versions                              │
  │  • Best for: composition, configuration, simple transforms │
  │                                                             │
  │  Java Imperative (full power, requires compilation)        │
  │  ──────────────────────────────────────────────            │
  │  • Custom visitor logic on the Lossless Semantic Tree      │
  │  • Complex conditional transforms                          │
  │  • Multi-file refactors (rename + update all references)   │
  │  • Best for: framework-specific patterns, complex logic    │
  │                                                             │
  │  Refaster Templates (pattern-based, medium complexity)     │
  │  ──────────────────────────────────────────────            │
  │  • "Before/After" Java code patterns                       │
  │  • OpenRewrite auto-generates the visitor                  │
  │  • Best for: API migration (old method → new method)       │
  │                                                             │
  └─────────────────────────────────────────────────────────────┘

Storage:
  ~/.mcp-migration-kb/
  └── openrewrite/
      ├── recipes/                      ← Your custom recipes
      │   ├── _recipe_index.json        ← Registry
      │   ├── yaml/
      │   │   ├── migrate-to-event-bus.yml
      │   │   ├── enforce-constructor-injection.yml
      │   │   └── ban-deprecated-apis.yml
      │   ├── java/
      │   │   ├── MigrateToOutboxPattern.java
      │   │   └── ReplaceBaseDao.java
      │   └── refaster/
      │       ├── RestTemplateToRestClient.java
      │       └── Log4jToSlf4j.java
      │
      ├── compositions/                 ← Composite recipe definitions
      │   ├── full-boot3-migration.yml
      │   └── full-event-driven-migration.yml
      │
      ├── test-fixtures/                ← Recipe test inputs/expected
      │   ├── migrate-to-event-bus/
      │   │   ├── before/OrderService.java
      │   │   └── after/OrderService.java
      │   └── ...
      │
      ├── configs/                      ← Project rewrite.yml templates
      │   ├── standard-migration.yml
      │   └── minimal-boot3.yml
      │
      └── runs/                         ← Execution audit trail

Requirements:
    pip install fastmcp pyyaml

Usage:
    python openrewrite_recipe_manager.py
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastmcp import FastMCP

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KB_DIR = os.path.expanduser("~/.mcp-migration-kb")
REWRITE_DIR = os.path.join(KB_DIR, "openrewrite")
RECIPES_DIR = os.path.join(REWRITE_DIR, "recipes")
YAML_DIR = os.path.join(RECIPES_DIR, "yaml")
JAVA_DIR = os.path.join(RECIPES_DIR, "java")
REFASTER_DIR = os.path.join(RECIPES_DIR, "refaster")
COMPOSITIONS_DIR = os.path.join(REWRITE_DIR, "compositions")
FIXTURES_DIR = os.path.join(REWRITE_DIR, "test-fixtures")
CONFIGS_DIR = os.path.join(REWRITE_DIR, "configs")
RECIPE_INDEX = os.path.join(RECIPES_DIR, "_recipe_index.json")

mcp = FastMCP("OpenRewrite Recipe Manager")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class RecipeRecord:
    recipe_id: str
    recipe_fqn: str
    display_name: str
    description: str = ""
    recipe_type: str = "YAML"       # YAML, JAVA, REFASTER, COMPOSITE
    category: str = ""
    file_path: str = ""
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    has_test_fixture: bool = False
    last_tested: str = ""
    test_result: str = ""           # PASS, FAIL, NOT_TESTED
    created_at: str = ""
    updated_at: str = ""
    changelog: list[dict] = field(default_factory=list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RecipeRegistry:
    def __init__(self):
        self.recipes: dict[str, RecipeRecord] = {}
        self._ensure_dirs()
        self._load()

    def _ensure_dirs(self):
        for d in [RECIPES_DIR, YAML_DIR, JAVA_DIR, REFASTER_DIR,
                  COMPOSITIONS_DIR, FIXTURES_DIR, CONFIGS_DIR]:
            os.makedirs(d, exist_ok=True)

    def _load(self):
        if os.path.isfile(RECIPE_INDEX):
            try:
                with open(RECIPE_INDEX) as f:
                    data = json.load(f)
                for rid, rdata in data.get("recipes", {}).items():
                    self.recipes[rid] = RecipeRecord(**rdata)
            except Exception:
                pass

    def save(self):
        data = {
            "recipes": {k: asdict(v) for k, v in self.recipes.items()},
            "saved_at": datetime.now().isoformat(),
        }
        with open(RECIPE_INDEX, "w") as f:
            json.dump(data, f, indent=2)


registry = RecipeRegistry()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# YAML Recipe Templates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

YAML_RECIPE_TEMPLATES = {
    "change_type": {
        "description": "Replace one Java type with another (all references updated)",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.ChangeType:
      oldFullyQualifiedTypeName: {old_type}
      newFullyQualifiedTypeName: {new_type}
""",
        "parameters": ["old_type", "new_type"],
    },

    "change_method": {
        "description": "Rename a method across all call sites",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.ChangeMethodName:
      methodPattern: "{method_pattern}"
      newMethodName: {new_method_name}
""",
        "parameters": ["method_pattern", "new_method_name"],
    },

    "add_annotation": {
        "description": "Add an annotation to classes matching a pattern",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.AddOrUpdateAnnotationAttribute:
      annotationType: {annotation_type}
      attributeName: {attribute_name}
      attributeValue: "{attribute_value}"
""",
        "parameters": ["annotation_type", "attribute_name", "attribute_value"],
    },

    "change_dependency_version": {
        "description": "Upgrade a Maven/Gradle dependency version",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.dependencies.UpgradeDependencyVersion:
      groupId: {group_id}
      artifactId: {artifact_id}
      newVersion: {new_version}
""",
        "parameters": ["group_id", "artifact_id", "new_version"],
    },

    "change_property": {
        "description": "Change a Spring property key or value",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.spring.ChangeSpringPropertyKey:
      oldPropertyKey: {old_key}
      newPropertyKey: {new_key}
""",
        "parameters": ["old_key", "new_key"],
    },

    "remove_annotation": {
        "description": "Remove an annotation from all classes",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.java.RemoveAnnotation:
      annotationPattern: "@{annotation_pattern}"
""",
        "parameters": ["annotation_pattern"],
    },

    "composite": {
        "description": "Chain multiple recipes into one",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
{recipe_list_yaml}
""",
        "parameters": ["recipe_list_yaml"],
    },

    "find_and_replace_text": {
        "description": "Plain text find-and-replace in specified file types",
        "template": """\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: {description}
recipeList:
  - org.openrewrite.text.FindAndReplace:
      find: "{find_text}"
      replace: "{replace_text}"
      filePattern: "{file_pattern}"
""",
        "parameters": ["find_text", "replace_text", "file_pattern"],
    },
}


JAVA_RECIPE_TEMPLATE = """\
package {package};

import org.openrewrite.ExecutionContext;
import org.openrewrite.Recipe;
import org.openrewrite.TreeVisitor;
import org.openrewrite.java.JavaIsoVisitor;
import org.openrewrite.java.tree.J;

/**
 * {display_name}
 *
 * {description}
 *
 * @author {author}
 * @since {version}
 */
public class {class_name} extends Recipe {{

    @Override
    public String getDisplayName() {{
        return "{display_name}";
    }}

    @Override
    public String getDescription() {{
        return "{description}";
    }}

    @Override
    public TreeVisitor<?, ExecutionContext> getVisitor() {{
        return new JavaIsoVisitor<ExecutionContext>() {{

            @Override
            public J.ClassDeclaration visitClassDeclaration(
                    J.ClassDeclaration classDecl, ExecutionContext ctx) {{
                J.ClassDeclaration cd = super.visitClassDeclaration(classDecl, ctx);

                // TODO: Implement your transformation logic here
                //
                // Common operations:
                //   - cd.getType().getFullyQualifiedName()  → get class FQCN
                //   - cd.getLeadingAnnotations()            → get annotations
                //   - cd.getExtends()                       → get superclass
                //   - cd.getImplements()                    → get interfaces
                //   - cd.getBody().getStatements()          → get fields + methods
                //
                // To modify:
                //   - cd.withName(...)                      → rename class
                //   - cd.withExtends(...)                   → change superclass
                //   - cd.withLeadingAnnotations(...)        → change annotations
                //
                // To add import:
                //   maybeAddImport("com.company.NewType");
                //
                // To remove import:
                //   maybeRemoveImport("com.company.OldType");

                return cd;
            }}

            @Override
            public J.MethodDeclaration visitMethodDeclaration(
                    J.MethodDeclaration method, ExecutionContext ctx) {{
                J.MethodDeclaration md = super.visitMethodDeclaration(method, ctx);

                // TODO: Implement method-level transformations
                //
                // Common operations:
                //   - md.getSimpleName()                    → method name
                //   - md.getReturnTypeExpression()          → return type
                //   - md.getParameters()                    → parameters
                //   - md.getLeadingAnnotations()            → annotations
                //   - md.getBody()                          → method body

                return md;
            }}
        }};
    }}
}}
"""


REFASTER_TEMPLATE = """\
package {package};

import com.google.errorprone.refaster.annotation.AfterTemplate;
import com.google.errorprone.refaster.annotation.BeforeTemplate;

/**
 * {display_name}
 *
 * Refaster template: matches @BeforeTemplate pattern in source code
 * and replaces it with @AfterTemplate pattern.
 *
 * @author {author}
 */
public class {class_name} {{

    @BeforeTemplate
    {before_return_type} before({before_params}) {{
        {before_body}
    }}

    @AfterTemplate
    {after_return_type} after({after_params}) {{
        {after_body}
    }}
}}
"""


RECIPE_TEST_TEMPLATE = """\
package {package};

import org.junit.jupiter.api.Test;
import org.openrewrite.test.RecipeSpec;
import org.openrewrite.test.RewriteTest;
import static org.openrewrite.java.Assertions.java;

class {class_name}Test implements RewriteTest {{

    @Override
    public void defaults(RecipeSpec spec) {{
        spec.recipe(new {class_name}());
    }}

    @Test
    void {test_method_name}() {{
        rewriteRun(
            java(
                // BEFORE
                \"\"\"
                {before_code}
                \"\"\",
                // AFTER
                \"\"\"
                {after_code}
                \"\"\"
            )
        );
    }}
}}
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def list_yaml_recipe_templates() -> dict:
    """
    Show available YAML recipe templates — these are the building blocks
    for creating declarative recipes without writing Java code.
    """
    templates = []
    for tid, t in YAML_RECIPE_TEMPLATES.items():
        templates.append({
            "template_id": tid,
            "description": t["description"],
            "parameters": t["parameters"],
        })
    return {
        "total": len(templates),
        "templates": templates,
        "usage": "Use create_yaml_recipe() with a template_id and fill in the parameters.",
    }


@mcp.tool()
def create_yaml_recipe(
    recipe_id: str,
    recipe_fqn: str,
    display_name: str,
    template_id: str,
    description: str = "",
    category: str = "custom",
    author: str = "",
    tags: str = "",
    **kwargs,
) -> dict:
    """
    Create a YAML declarative recipe from a template.

    Args:
        recipe_id:     Unique short ID (e.g., "migrate-base-dao")
        recipe_fqn:    Full recipe name (e.g., "com.company.rewrite.MigrateBaseDao")
        display_name:  Human-readable name
        template_id:   Which YAML template to use (see list_yaml_recipe_templates)
        description:   What this recipe does
        category:      Category for organization
        author:        Author name/email
        tags:          Comma-separated tags

    Additional kwargs depend on the template:
        change_type:    old_type, new_type
        change_method:  method_pattern, new_method_name
        add_annotation: annotation_type, attribute_name, attribute_value
        change_dependency_version: group_id, artifact_id, new_version
        change_property: old_key, new_key
        remove_annotation: annotation_pattern
        find_and_replace_text: find_text, replace_text, file_pattern
        composite: recipe_list_yaml (indented YAML string of recipe references)
    """
    if template_id not in YAML_RECIPE_TEMPLATES:
        return {
            "error": f"Unknown template '{template_id}'.",
            "available": list(YAML_RECIPE_TEMPLATES.keys()),
        }

    tpl = YAML_RECIPE_TEMPLATES[template_id]

    # Build template context
    context = {
        "recipe_fqn": recipe_fqn,
        "display_name": display_name,
        "description": description or display_name,
    }
    context.update(kwargs)

    # Validate required parameters
    missing = [p for p in tpl["parameters"] if p not in context or not context[p]]
    if missing:
        return {
            "error": f"Missing required parameters: {missing}",
            "template_parameters": tpl["parameters"],
        }

    # Render YAML
    try:
        yaml_content = tpl["template"].format(**context)
    except KeyError as e:
        return {"error": f"Missing template parameter: {e}"}

    # Save file
    file_name = f"{recipe_id}.yml"
    file_path = os.path.join(YAML_DIR, file_name)
    Path(file_path).write_text(yaml_content)

    # Register
    record = RecipeRecord(
        recipe_id=recipe_id,
        recipe_fqn=recipe_fqn,
        display_name=display_name,
        description=description,
        recipe_type="YAML",
        category=category,
        file_path=file_path,
        author=author,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        changelog=[{"version": "1.0.0", "date": datetime.now().isoformat(), "note": "Created"}],
    )
    registry.recipes[recipe_id] = record
    registry.save()

    return {
        "status": "created",
        "recipe_id": recipe_id,
        "recipe_fqn": recipe_fqn,
        "type": "YAML",
        "file": file_path,
        "content": yaml_content,
    }


@mcp.tool()
def create_java_recipe(
    recipe_id: str,
    package: str,
    class_name: str,
    display_name: str,
    description: str = "",
    category: str = "custom",
    author: str = "",
    tags: str = "",
) -> dict:
    """
    Scaffold a Java imperative recipe — creates the Recipe class with
    visitor stubs for class and method transformations.

    You'll need to fill in the TODO sections with your transformation logic.

    Args:
        recipe_id:    Unique short ID
        package:      Java package (e.g., "com.company.rewrite")
        class_name:   Recipe class name (e.g., "MigrateToOutboxPattern")
        display_name: Human-readable name
        description:  What this recipe does
        category:     Category
        author:       Author
        tags:         Tags

    Returns:
        Scaffolded Recipe.java file path and content.
    """
    recipe_fqn = f"{package}.{class_name}"

    java_content = JAVA_RECIPE_TEMPLATE.format(
        package=package,
        class_name=class_name,
        display_name=display_name,
        description=description or display_name,
        author=author or "migration-platform",
        version="1.0.0",
    )

    # Save
    file_name = f"{class_name}.java"
    file_path = os.path.join(JAVA_DIR, file_name)
    Path(file_path).write_text(java_content)

    # Also scaffold the test
    test_content = RECIPE_TEST_TEMPLATE.format(
        package=package,
        class_name=class_name,
        test_method_name=f"should{class_name.replace('Migrate', '').replace('Replace', '')}",
        before_code="    // TODO: paste legacy Java code here",
        after_code="    // TODO: paste expected migrated code here",
    )
    test_file = os.path.join(JAVA_DIR, f"{class_name}Test.java")
    Path(test_file).write_text(test_content)

    # Register
    record = RecipeRecord(
        recipe_id=recipe_id,
        recipe_fqn=recipe_fqn,
        display_name=display_name,
        description=description,
        recipe_type="JAVA",
        category=category,
        file_path=file_path,
        author=author,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
        changelog=[{"version": "1.0.0", "date": datetime.now().isoformat(), "note": "Scaffolded"}],
    )
    registry.recipes[recipe_id] = record
    registry.save()

    return {
        "status": "created",
        "recipe_id": recipe_id,
        "recipe_fqn": recipe_fqn,
        "type": "JAVA",
        "file": file_path,
        "test_file": test_file,
        "next_steps": [
            f"Edit {file_path} — fill in the visitor TODO sections",
            f"Edit {test_file} — add before/after test cases",
            "Compile: add to your rewrite-recipes Maven module",
            "Test: mvn test in the recipes module",
        ],
    }


@mcp.tool()
def create_refaster_recipe(
    recipe_id: str,
    package: str,
    class_name: str,
    display_name: str,
    before_return_type: str,
    before_params: str,
    before_body: str,
    after_return_type: str,
    after_params: str,
    after_body: str,
    description: str = "",
    author: str = "",
) -> dict:
    """
    Create a Refaster template recipe — pattern-based "before/after" transform.
    OpenRewrite auto-generates the visitor from your before/after code patterns.

    Example:
        before:  RestTemplate restTemplate; restTemplate.getForObject(url, type)
        after:   RestClient restClient; restClient.get().uri(url).retrieve().body(type)

    Args:
        recipe_id:           Unique short ID
        package:             Java package
        class_name:          Template class name
        display_name:        Human-readable name
        before_return_type:  Return type of the before pattern
        before_params:       Parameters of the before pattern
        before_body:         Body of the before pattern (the code to find)
        after_return_type:   Return type of the after pattern
        after_params:        Parameters of the after pattern
        after_body:          Body of the after pattern (the replacement)
        description:         Description
        author:              Author
    """
    recipe_fqn = f"{package}.{class_name}"

    content = REFASTER_TEMPLATE.format(
        package=package,
        class_name=class_name,
        display_name=display_name,
        description=description or display_name,
        author=author or "migration-platform",
        before_return_type=before_return_type,
        before_params=before_params,
        before_body=before_body,
        after_return_type=after_return_type,
        after_params=after_params,
        after_body=after_body,
    )

    file_name = f"{class_name}.java"
    file_path = os.path.join(REFASTER_DIR, file_name)
    Path(file_path).write_text(content)

    record = RecipeRecord(
        recipe_id=recipe_id, recipe_fqn=recipe_fqn,
        display_name=display_name, description=description,
        recipe_type="REFASTER", file_path=file_path,
        author=author,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    registry.recipes[recipe_id] = record
    registry.save()

    return {
        "status": "created",
        "recipe_id": recipe_id,
        "type": "REFASTER",
        "file": file_path,
        "content": content,
    }


@mcp.tool()
def compose_recipe(
    recipe_id: str,
    recipe_fqn: str,
    display_name: str,
    recipe_ids: str,
    description: str = "",
    include_community: str = "",
) -> dict:
    """
    Compose a new recipe that chains multiple recipes (custom + community).

    Args:
        recipe_id:          Unique short ID
        recipe_fqn:         Full recipe name
        display_name:       Human-readable name
        recipe_ids:         Comma-separated custom recipe IDs to include
        description:        Description
        include_community:  Comma-separated community recipe FQNs to include

    Example:
        compose_recipe(
            "full-boot3-migration",
            "com.company.rewrite.FullBoot3Migration",
            "Complete Boot 3 Migration",
            recipe_ids="migrate-base-dao,migrate-to-event-bus",
            include_community="org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_4"
        )
    """
    custom_ids = [r.strip() for r in recipe_ids.split(",") if r.strip()]
    community_fqns = [r.strip() for r in include_community.split(",") if r.strip()]

    recipe_list = []

    # Resolve custom recipes
    for rid in custom_ids:
        if rid in registry.recipes:
            recipe_list.append(registry.recipes[rid].recipe_fqn)
        else:
            return {"error": f"Custom recipe '{rid}' not found. Create it first."}

    # Add community recipes
    recipe_list.extend(community_fqns)

    # Generate YAML
    recipe_list_yaml = ""
    for fqn in recipe_list:
        recipe_list_yaml += f"  - {fqn}\n"

    yaml_content = f"""\
---
type: specs.openrewrite.org/v1beta/recipe
name: {recipe_fqn}
displayName: {display_name}
description: >-
  {description or display_name}
  Composed from {len(recipe_list)} recipes.
  Generated by Migration MCP Platform on {datetime.now().strftime('%Y-%m-%d')}.
recipeList:
{recipe_list_yaml}"""

    file_path = os.path.join(COMPOSITIONS_DIR, f"{recipe_id}.yml")
    Path(file_path).write_text(yaml_content)

    record = RecipeRecord(
        recipe_id=recipe_id, recipe_fqn=recipe_fqn,
        display_name=display_name, description=description,
        recipe_type="COMPOSITE", file_path=file_path,
        depends_on=custom_ids,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )
    registry.recipes[recipe_id] = record
    registry.save()

    return {
        "status": "created",
        "recipe_id": recipe_id,
        "type": "COMPOSITE",
        "recipes_included": recipe_list,
        "file": file_path,
        "content": yaml_content,
    }


@mcp.tool()
def create_test_fixture(
    recipe_id: str,
    before_code: str,
    after_code: str,
    file_name: str = "Example.java",
    description: str = "",
) -> dict:
    """
    Create a test fixture (before/after pair) for a recipe.
    Used to validate that a recipe produces the expected output.

    Args:
        recipe_id:   Recipe to test
        before_code: The legacy Java code (input)
        after_code:  The expected migrated code (output)
        file_name:   File name for the test fixture
        description: What this test case covers
    """
    if recipe_id not in registry.recipes:
        return {"error": f"Recipe '{recipe_id}' not found."}

    fixture_dir = os.path.join(FIXTURES_DIR, recipe_id)
    before_dir = os.path.join(fixture_dir, "before")
    after_dir = os.path.join(fixture_dir, "after")
    os.makedirs(before_dir, exist_ok=True)
    os.makedirs(after_dir, exist_ok=True)

    Path(os.path.join(before_dir, file_name)).write_text(before_code)
    Path(os.path.join(after_dir, file_name)).write_text(after_code)

    # Save metadata
    meta = {
        "recipe_id": recipe_id,
        "file_name": file_name,
        "description": description,
        "created_at": datetime.now().isoformat(),
    }
    with open(os.path.join(fixture_dir, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=2)

    # Update registry
    registry.recipes[recipe_id].has_test_fixture = True
    registry.save()

    return {
        "status": "created",
        "recipe_id": recipe_id,
        "before_file": os.path.join(before_dir, file_name),
        "after_file": os.path.join(after_dir, file_name),
        "next_step": "Run validate_recipe() to test the recipe against this fixture.",
    }


@mcp.tool()
def generate_rewrite_yml(
    project_path: str,
    recipe_ids: str,
    include_community: str = "",
) -> dict:
    """
    Generate a rewrite.yml file for a project that activates specified recipes.

    Args:
        project_path:       Project root
        recipe_ids:         Comma-separated custom recipe IDs
        include_community:  Comma-separated community recipe FQNs
    """
    custom_ids = [r.strip() for r in recipe_ids.split(",") if r.strip()]
    community_fqns = [r.strip() for r in include_community.split(",") if r.strip()]

    active_recipes = []
    recipe_sources = []

    for rid in custom_ids:
        if rid in registry.recipes:
            rec = registry.recipes[rid]
            active_recipes.append(rec.recipe_fqn)
            if rec.recipe_type == "YAML":
                recipe_sources.append(rec.file_path)
        else:
            return {"error": f"Recipe '{rid}' not found."}

    active_recipes.extend(community_fqns)

    # Build rewrite.yml
    content = f"""\
# OpenRewrite Configuration
# Generated by Migration MCP Platform on {datetime.now().strftime('%Y-%m-%d')}
# Active recipes: {len(active_recipes)}

"""
    # Include inline YAML recipes
    for src in recipe_sources:
        if os.path.isfile(src):
            content += Path(src).read_text() + "\n\n"

    rewrite_yml = os.path.join(os.path.abspath(project_path), "rewrite.yml")
    Path(rewrite_yml).write_text(content)

    return {
        "status": "generated",
        "file": rewrite_yml,
        "active_recipes": active_recipes,
        "next_step": "Run OpenRewrite with: mvn rewrite:run or gradle rewriteRun",
    }


@mcp.tool()
def generate_maven_plugin_config(
    recipe_ids: str = "",
    community_recipes: str = "",
    rewrite_plugin_version: str = "5.45.0",
    recipe_bom_version: str = "2.22.0",
) -> dict:
    """
    Generate the Maven pom.xml plugin configuration for OpenRewrite.

    Produces a <plugin> block to paste into your pom.xml <build><plugins> section,
    plus any required <dependencies> for recipe modules.

    Args:
        recipe_ids:             Comma-separated custom recipe IDs to activate
        community_recipes:      Comma-separated community recipe FQNs
        rewrite_plugin_version: OpenRewrite Maven plugin version
        recipe_bom_version:     Recipe BOM version
    """
    custom_ids = [r.strip() for r in recipe_ids.split(",") if r.strip()]
    community_fqns = [r.strip() for r in community_recipes.split(",") if r.strip()]

    active = []
    dep_artifacts = set()

    for rid in custom_ids:
        if rid in registry.recipes:
            active.append(registry.recipes[rid].recipe_fqn)

    active.extend(community_fqns)

    # Detect needed dependency modules
    for fqn in active:
        if "spring" in fqn.lower():
            dep_artifacts.add("rewrite-spring")
        if "migrate.java" in fqn.lower() or "jakarta" in fqn.lower():
            dep_artifacts.add("rewrite-migrate-java")
        if "testing" in fqn.lower() or "junit" in fqn.lower() or "mockito" in fqn.lower():
            dep_artifacts.add("rewrite-testing-frameworks")
        if "logging" in fqn.lower() or "slf4j" in fqn.lower():
            dep_artifacts.add("rewrite-logging-frameworks")
        if "staticanalysis" in fqn.lower():
            dep_artifacts.add("rewrite-static-analysis")

    # Build XML
    active_xml = "\n".join(f"                <recipe>{r}</recipe>" for r in active)
    deps_xml = ""
    for art in sorted(dep_artifacts):
        deps_xml += f"""
              <dependency>
                <groupId>org.openrewrite.recipe</groupId>
                <artifactId>{art}</artifactId>
              </dependency>"""

    plugin_xml = f"""\
<plugin>
  <groupId>org.openrewrite.maven</groupId>
  <artifactId>rewrite-maven-plugin</artifactId>
  <version>{rewrite_plugin_version}</version>
  <configuration>
    <activeRecipes>
{active_xml}
    </activeRecipes>
    <exportDatatables>true</exportDatatables>
  </configuration>
  <dependencies>
    <dependency>
      <groupId>org.openrewrite.recipe</groupId>
      <artifactId>rewrite-recipe-bom</artifactId>
      <version>{recipe_bom_version}</version>
      <type>pom</type>
      <scope>import</scope>
    </dependency>{deps_xml}
  </dependencies>
</plugin>"""

    return {
        "active_recipes": active,
        "detected_dependencies": sorted(dep_artifacts),
        "plugin_xml": plugin_xml,
        "instructions": [
            "1. Paste the <plugin> block into pom.xml → <build> → <plugins>",
            "2. If you have custom YAML recipes, ensure rewrite.yml is in the project root",
            "3. Dry run: mvn rewrite:dryRun",
            "4. Apply: mvn rewrite:run",
        ],
    }


@mcp.tool()
def list_custom_recipes(category: str = "") -> dict:
    """List all custom recipes in the registry."""
    recipes = []
    for rid, rec in sorted(registry.recipes.items()):
        if category and rec.category != category:
            continue
        recipes.append({
            "recipe_id": rid,
            "recipe_fqn": rec.recipe_fqn,
            "display_name": rec.display_name,
            "type": rec.recipe_type,
            "category": rec.category,
            "version": rec.version,
            "has_test": rec.has_test_fixture,
            "test_result": rec.test_result or "NOT_TESTED",
            "author": rec.author,
        })

    return {"total": len(recipes), "recipes": recipes}


@mcp.tool()
def get_recipe_detail(recipe_id: str) -> dict:
    """Get full detail for a recipe including file content."""
    if recipe_id not in registry.recipes:
        return {"error": f"Recipe '{recipe_id}' not found."}

    rec = registry.recipes[recipe_id]
    content = ""
    if rec.file_path and os.path.isfile(rec.file_path):
        content = Path(rec.file_path).read_text()

    # Check for test fixture
    fixture_before = ""
    fixture_after = ""
    fixture_dir = os.path.join(FIXTURES_DIR, recipe_id)
    if os.path.isdir(fixture_dir):
        for f in Path(os.path.join(fixture_dir, "before")).glob("*"):
            fixture_before = f.read_text()
            break
        for f in Path(os.path.join(fixture_dir, "after")).glob("*"):
            fixture_after = f.read_text()
            break

    return {
        "metadata": asdict(rec),
        "content": content,
        "test_fixture": {
            "exists": bool(fixture_before),
            "before": fixture_before[:2000],
            "after": fixture_after[:2000],
        },
    }


@mcp.tool()
def update_recipe_version(
    recipe_id: str,
    new_version: str,
    changelog_note: str,
) -> dict:
    """
    Bump a recipe's version and add a changelog entry.

    Args:
        recipe_id:      Recipe to update
        new_version:    New version string (e.g., "1.1.0")
        changelog_note: What changed in this version
    """
    if recipe_id not in registry.recipes:
        return {"error": f"Recipe '{recipe_id}' not found."}

    rec = registry.recipes[recipe_id]
    old_version = rec.version
    rec.version = new_version
    rec.updated_at = datetime.now().isoformat()
    rec.changelog.append({
        "version": new_version,
        "date": datetime.now().isoformat(),
        "note": changelog_note,
        "previous_version": old_version,
    })

    registry.save()
    return {"status": "updated", "recipe_id": recipe_id,
            "old_version": old_version, "new_version": new_version}


@mcp.tool()
def delete_recipe(recipe_id: str) -> dict:
    """Remove a recipe from the registry and delete its files."""
    if recipe_id not in registry.recipes:
        return {"error": f"Recipe '{recipe_id}' not found."}

    rec = registry.recipes.pop(recipe_id)
    if rec.file_path and os.path.isfile(rec.file_path):
        os.remove(rec.file_path)
    registry.save()

    return {"status": "deleted", "recipe_id": recipe_id}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("recipes://status")
def recipe_status() -> str:
    total = len(registry.recipes)
    by_type = defaultdict(int)
    for r in registry.recipes.values():
        by_type[r.recipe_type] += 1
    tested = len([r for r in registry.recipes.values() if r.has_test_fixture])

    lines = [
        "OpenRewrite Recipe Manager",
        "=" * 40,
        f"Total custom recipes: {total}",
        f"  YAML: {by_type.get('YAML', 0)}",
        f"  Java: {by_type.get('JAVA', 0)}",
        f"  Refaster: {by_type.get('REFASTER', 0)}",
        f"  Composite: {by_type.get('COMPOSITE', 0)}",
        f"With test fixtures: {tested}/{total}",
        f"Storage: {RECIPES_DIR}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
