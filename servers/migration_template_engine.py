"""
Migration Template Engine MCP Server
======================================
Extends the code generator with a fully customizable template system.

Users define their own code generation templates as .java.tpl files in a
templates directory. Templates use a Jinja2-like syntax with variables
populated from legacy class metadata + mapping rules + target framework.

Architecture:
  ~/.mcp-migration-kb/
  └── templates/                          ← YOUR custom templates
      ├── _template_index.json            ← Template registry
      ├── event_driven_service.java.tpl   ← Service template
      ├── reactive_repository.java.tpl    ← DAO → Repository
      ├── command_handler.java.tpl        ← Command handler
      ├── domain_event.java.tpl           ← Event record
      ├── saga_participant.java.tpl       ← Saga participant
      ├── outbox_service.java.tpl         ← Outbox pattern
      ├── integration_test.java.tpl       ← Test scaffold
      ├── kafka_consumer.java.tpl         ← Kafka consumer
      ├── rest_controller.java.tpl        ← REST API
      ├── application_config.yaml.tpl     ← Config template
      └── README.md                       ← Template authoring guide

Template Variable Reference:
  {{ legacy.fqcn }}                  - Full legacy class name
  {{ legacy.simple_name }}           - Simple class name
  {{ legacy.package }}               - Package
  {{ legacy.layer }}                 - DAO, SERVICE, CONTROLLER, etc.
  {{ legacy.stereotype }}            - @Service, @Repository, etc.
  {{ legacy.superclass }}            - Superclass
  {{ legacy.interfaces }}            - List of interfaces
  {{ legacy.annotations }}           - List of annotations
  {{ legacy.constructor_deps }}      - Constructor injection deps
  {{ legacy.field_deps }}            - Field injection deps
  {{ legacy.public_methods }}        - List of public methods
  {{ legacy.transactional_methods }} - @Transactional methods
  {{ legacy.scheduled_methods }}     - @Scheduled methods

  {{ target.package }}               - Target package
  {{ target.class_name }}            - Generated class name
  {{ target.extends }}               - From mapping rule
  {{ target.implements }}            - From mapping rule
  {{ target.annotations }}           - From mapping rule
  {{ target.injected_deps }}         - Additional deps from rules
  {{ target.framework_name }}        - Target framework name

  {{ meta.date }}                    - Generation date
  {{ meta.rules_applied }}           - Applied rule IDs
  {{ meta.generator_version }}       - Generator version

  {% for method in legacy.public_methods %}
  {% if method.is_mutating %}
  {% endfor %}
  {% endif %}

Requirements:
    pip install fastmcp pyyaml jinja2

Usage:
    python migration_template_engine.py
    fastmcp dev migration_template_engine.py
"""

import json
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

try:
    from jinja2 import Environment, FileSystemLoader, BaseLoader, select_autoescape
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KB_DIR = os.path.expanduser("~/.mcp-migration-kb")
TEMPLATES_DIR = os.path.join(KB_DIR, "templates")
TEMPLATE_INDEX = os.path.join(TEMPLATES_DIR, "_template_index.json")
GENERATOR_VERSION = "2.0.0"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mcp = FastMCP(
    "Migration Template Engine",
    description=(
        "Custom code generation template engine for migration. "
        "Define your own .tpl templates with Jinja2 syntax, "
        "map them to legacy patterns, and generate framework-specific code."
    ),
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class TemplateRecord:
    """Registered template metadata."""

    template_id: str
    file_name: str              # e.g., "event_driven_service.java.tpl"
    description: str = ""
    output_suffix: str = ".java"  # Output file extension
    target_layer: str = ""      # Which layer this template targets
    variables: list[str] = field(default_factory=list)  # Expected variables
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    # When this template should auto-apply
    auto_match: dict = field(default_factory=dict)
    # auto_match examples:
    #   {"legacy_layer": "SERVICE"}
    #   {"legacy_layer": "DAO", "legacy_extends": "BaseDao"}
    #   {"mapping_rule": "service-to-event-handler"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Template Registry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TemplateRegistry:
    """Manages custom templates on disk."""

    def __init__(self):
        self.templates: dict[str, TemplateRecord] = {}
        self._ensure_dirs()
        self._load_index()
        self._setup_jinja()

    def _ensure_dirs(self):
        os.makedirs(TEMPLATES_DIR, exist_ok=True)

    def _load_index(self):
        if os.path.isfile(TEMPLATE_INDEX):
            try:
                with open(TEMPLATE_INDEX, "r") as f:
                    data = json.load(f)
                for tid, tdata in data.get("templates", {}).items():
                    self.templates[tid] = TemplateRecord(**tdata)
            except Exception:
                pass

    def _save_index(self):
        data = {
            "templates": {k: asdict(v) for k, v in self.templates.items()},
            "saved_at": datetime.now().isoformat(),
        }
        with open(TEMPLATE_INDEX, "w") as f:
            json.dump(data, f, indent=2)

    def _setup_jinja(self):
        """Initialize Jinja2 environment with custom filters."""
        if HAS_JINJA2:
            self.jinja_env = Environment(
                loader=FileSystemLoader(TEMPLATES_DIR),
                trim_blocks=True,
                lstrip_blocks=True,
                keep_trailing_newline=True,
            )
            # ── Custom filters for Java code generation ──
            self.jinja_env.filters["camel_case"] = _camel_case
            self.jinja_env.filters["pascal_case"] = _pascal_case
            self.jinja_env.filters["snake_case"] = _snake_case
            self.jinja_env.filters["upper_snake"] = _upper_snake_case
            self.jinja_env.filters["first_lower"] = lambda s: s[0].lower() + s[1:] if s else ""
            self.jinja_env.filters["first_upper"] = lambda s: s[0].upper() + s[1:] if s else ""
            self.jinja_env.filters["strip_suffix"] = _strip_suffix
            self.jinja_env.filters["to_event_name"] = _to_event_name
            self.jinja_env.filters["to_topic_name"] = _to_topic_name
            self.jinja_env.filters["java_type"] = _java_type_shortname
            self.jinja_env.filters["is_mutating"] = _is_mutating_method
            self.jinja_env.filters["is_query"] = _is_query_method
            self.jinja_env.globals["now"] = datetime.now
        else:
            self.jinja_env = None

    def render(self, template_id: str, context: dict) -> str:
        """Render a template with given context."""
        if not HAS_JINJA2:
            return _fallback_render(template_id, context)

        record = self.templates.get(template_id)
        if not record:
            raise ValueError(f"Template '{template_id}' not found.")

        template = self.jinja_env.get_template(record.file_name)
        return template.render(**context)

    def render_string(self, template_str: str, context: dict) -> str:
        """Render a template from a string (for inline/ad-hoc templates)."""
        if not HAS_JINJA2:
            return _fallback_render_string(template_str, context)

        from jinja2 import Template
        # Register filters on ad-hoc template
        tpl = self.jinja_env.from_string(template_str)
        return tpl.render(**context)


tpl_registry = TemplateRegistry()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Custom Jinja2 Filters for Java Code
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _camel_case(value: str) -> str:
    """OrderService → orderService"""
    if not value:
        return ""
    return value[0].lower() + value[1:]


def _pascal_case(value: str) -> str:
    """order_service → OrderService"""
    return "".join(w.capitalize() for w in re.split(r"[_\-\s]+", value))


def _snake_case(value: str) -> str:
    """OrderService → order_service"""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _upper_snake_case(value: str) -> str:
    """OrderService → ORDER_SERVICE"""
    return _snake_case(value).upper()


def _strip_suffix(value: str, *suffixes) -> str:
    """OrderServiceImpl | strip_suffix('Impl','Bean','EJB') → OrderService"""
    for suffix in suffixes:
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _to_event_name(method_name: str, class_context: str = "") -> str:
    """createOrder → OrderCreatedEvent"""
    verbs = {
        "create": "Created", "save": "Saved", "update": "Updated",
        "delete": "Deleted", "process": "Processed", "submit": "Submitted",
        "cancel": "Cancelled", "approve": "Approved", "reject": "Rejected",
        "complete": "Completed", "assign": "Assigned", "remove": "Removed",
        "add": "Added", "register": "Registered", "activate": "Activated",
        "deactivate": "Deactivated", "publish": "Published",
    }
    for verb, past in verbs.items():
        if method_name.lower().startswith(verb):
            entity = method_name[len(verb):] or class_context
            return f"{entity}{past}Event"
    return f"{method_name}CompletedEvent"


def _to_topic_name(class_name: str) -> str:
    """OrderService → order-service.events"""
    snake = _snake_case(class_name.replace("Service", "").replace("Impl", ""))
    return f"{snake.replace('_', '-')}.events"


def _java_type_shortname(fqcn: str) -> str:
    """com.company.Order → Order"""
    return fqcn.rsplit(".", 1)[-1] if "." in fqcn else fqcn


def _is_mutating_method(method: dict) -> bool:
    """Check if a method is a mutating (write) operation."""
    name = method.get("name", "").lower()
    return any(name.startswith(p) for p in [
        "create", "save", "update", "delete", "process", "submit",
        "cancel", "approve", "reject", "complete", "assign", "remove",
        "add", "register", "activate", "deactivate", "publish",
    ])


def _is_query_method(method: dict) -> bool:
    """Check if a method is a query (read) operation."""
    name = method.get("name", "").lower()
    return any(name.startswith(p) for p in [
        "find", "get", "search", "list", "count", "exists", "fetch",
        "load", "read", "query", "lookup", "retrieve",
    ])


def _fallback_render(template_id: str, context: dict) -> str:
    """Simple string replacement fallback when Jinja2 is not installed."""
    record = tpl_registry.templates.get(template_id)
    if not record:
        return f"// ERROR: Template '{template_id}' not found."
    tpl_path = os.path.join(TEMPLATES_DIR, record.file_name)
    if not os.path.isfile(tpl_path):
        return f"// ERROR: Template file not found: {tpl_path}"
    content = Path(tpl_path).read_text()
    return _fallback_render_string(content, context)


def _fallback_render_string(template_str: str, context: dict) -> str:
    """Bare-minimum {{ var }} replacement without Jinja2."""
    result = template_str
    for key, value in _flatten_context(context).items():
        result = result.replace("{{ " + key + " }}", str(value))
        result = result.replace("{{" + key + "}}", str(value))
    # Strip unresolved Jinja blocks
    result = re.sub(r"\{%.*?%\}", "", result)
    result = re.sub(r"\{\{.*?\}\}", "", result)
    return result


def _flatten_context(ctx: dict, prefix: str = "") -> dict:
    """Flatten nested dict: {"legacy": {"fqcn": "x"}} → {"legacy.fqcn": "x"}"""
    flat = {}
    for k, v in ctx.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_context(v, full_key))
        elif isinstance(v, list):
            flat[full_key] = ", ".join(str(i) for i in v)
        else:
            flat[full_key] = v
    return flat


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KB Reader
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _load_kb_project(project_name: str) -> dict:
    fp = os.path.join(KB_DIR, f"{project_name}.json")
    if os.path.isfile(fp):
        try:
            with open(fp, "r") as f:
                return json.load(f).get("classes", {})
        except Exception:
            pass
    return {}


def _load_kb_index() -> dict:
    fp = os.path.join(KB_DIR, "_index.json")
    if os.path.isfile(fp):
        try:
            with open(fp, "r") as f:
                return json.load(f).get("projects", {})
        except Exception:
            pass
    return {}


def _load_mappings() -> dict:
    fp = os.path.join(KB_DIR, "_mappings.json")
    if os.path.isfile(fp):
        try:
            with open(fp, "r") as f:
                return json.load(f).get("rules", {})
        except Exception:
            pass
    return {}


def _load_target_framework() -> dict:
    fp = os.path.join(KB_DIR, "_target_framework.json")
    if os.path.isfile(fp):
        try:
            with open(fp, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Default Templates (bootstrapped on first run)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEFAULT_TEMPLATES = {
    "event_driven_service": {
        "file_name": "event_driven_service.java.tpl",
        "description": "Service with event publishing and outbox pattern",
        "output_suffix": ".java",
        "target_layer": "SERVICE",
        "tags": ["service", "events", "outbox"],
        "auto_match": {"legacy_layer": "SERVICE"},
        "content": '''\
package {{ target.package }};

{% for imp in target.imports %}
import {{ imp }};
{% endfor %}
import java.time.Instant;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

/**
 * Migrated from: {{ legacy.fqcn }}
 * Original: {{ legacy.stereotype }} in project "{{ legacy.project }}"
 * Generated: {{ meta.date }}
 * Rules applied: {{ meta.rules_applied | join(", ") }}
 *
 * Migration notes:
{% for note in legacy.migration_notes %}
 *   - {{ note }}
{% endfor %}
 */
@Slf4j
@RequiredArgsConstructor
{{ target.annotations | join("\\n") }}
public class {{ target.class_name }}{{ target.extends_clause }}{{ target.implements_clause }} {

{% for dep in target.all_deps %}
    private final {{ dep.type }} {{ dep.name }};
{% endfor %}

{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    /**
     * Migrated from {{ legacy.simple_name }}.{{ method.name }}()
     * TODO: Implement business logic and event publishing
     */
{% if "@Transactional" in method.annotations %}
    @Transactional
{% endif %}
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        log.info("Processing {{ method.name }}, correlationId={}",
            UUID.randomUUID());

        // TODO: Business logic from legacy {{ legacy.simple_name }}.{{ method.name }}()

        // Publish domain event via outbox
        // outboxPublisher.publish(new {{ method.name | to_event_name(legacy.entity_name) }}(
        //     UUID.randomUUID().toString(),
        //     /* event fields from method params */
        //     Instant.now(),
        //     UUID.randomUUID().toString()
        // ));

{% if method.return_type != "void" %}
        throw new UnsupportedOperationException("TODO: {{ method.name }}");
{% endif %}
    }

{% elif method.name | is_query %}
    /**
     * Query method — migrated as-is from {{ legacy.simple_name }}.
     */
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        // TODO: Implement query logic
        throw new UnsupportedOperationException("TODO: {{ method.name }}");
    }

{% else %}
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        // TODO: Migrate from legacy
        throw new UnsupportedOperationException("TODO: {{ method.name }}");
    }

{% endif %}
{% endfor %}
}
''',
    },

    "reactive_repository": {
        "file_name": "reactive_repository.java.tpl",
        "description": "Spring Data reactive repository from legacy DAO",
        "output_suffix": ".java",
        "target_layer": "DATA_ACCESS",
        "tags": ["dao", "repository", "reactive"],
        "auto_match": {"legacy_layer": "DAO"},
        "content": '''\
package {{ target.package }};

{% for imp in target.imports %}
import {{ imp }};
{% endfor %}

/**
 * Migrated from: {{ legacy.fqcn }}
 * Legacy: {{ legacy.stereotype }} {{ legacy.superclass }}
 * Entity: {{ legacy.entity_name }}
 * Generated: {{ meta.date }}
 */
public interface {{ target.class_name }} extends {{ target.base_repository }}<{{ legacy.entity_name }}, {{ legacy.id_type | default("String") }}> {

{% for method in legacy.public_methods %}
{% if method.name | is_query %}
    /**
     * Migrated from legacy {{ legacy.simple_name }}.{{ method.name }}()
     */
    {{ method.return_type }} {{ method.name }}({{ method.parameters }});

{% endif %}
{% endfor %}
{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    // Legacy mutating method {{ method.name }}() is now handled by
    // {{ legacy.entity_name }}Service with Outbox pattern
{% endif %}
{% endfor %}
}
''',
    },

    "domain_event": {
        "file_name": "domain_event.java.tpl",
        "description": "Domain event record generated from service methods",
        "output_suffix": ".java",
        "target_layer": "EVENTING",
        "tags": ["event", "record", "domain"],
        "auto_match": {},
        "content": '''\
package {{ target.package }}.events;

import java.time.Instant;
import java.util.UUID;

/**
 * Domain event: {{ event.name }}
 * Source: {{ legacy.simple_name }}.{{ event.source_method }}()
 * Generated: {{ meta.date }}
 */
public record {{ event.name }}(
    String eventId,
{% for field in event.fields %}
    {{ field.type }} {{ field.name }}{{ "," if not loop.last else "," }}
{% endfor %}
    Instant timestamp,
    String correlationId,
    int version
) {
    /**
     * Compact constructor with defaults.
     */
    public {{ event.name }} {
        eventId = eventId != null ? eventId : UUID.randomUUID().toString();
        timestamp = timestamp != null ? timestamp : Instant.now();
        version = version > 0 ? version : 1;
    }

    /**
     * Convenience factory from command parameters.
     */
    public static {{ event.name }} create(
{% for field in event.fields %}
            {{ field.type }} {{ field.name }}{{ "," if not loop.last else "" }}
{% endfor %}
    ) {
        return new {{ event.name }}(
            null,
{% for field in event.fields %}
            {{ field.name }},
{% endfor %}
            null,
            UUID.randomUUID().toString(),
            1
        );
    }
}
''',
    },

    "command_handler": {
        "file_name": "command_handler.java.tpl",
        "description": "CQRS command handler replacing legacy service",
        "output_suffix": ".java",
        "target_layer": "HANDLER",
        "tags": ["cqrs", "command", "handler"],
        "auto_match": {"legacy_layer": "SERVICE", "mapping_rule": "service-to-command-handler"},
        "content": '''\
package {{ target.package }}.handler;

{% for imp in target.imports %}
import {{ imp }};
{% endfor %}
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

/**
 * Command Handler — migrated from {{ legacy.fqcn }}
 * Handles commands and publishes domain events.
 * Generated: {{ meta.date }}
 */
@Slf4j
@RequiredArgsConstructor
{{ target.annotations | join("\\n") }}
public class {{ target.class_name }} {

{% for dep in target.all_deps %}
    private final {{ dep.type }} {{ dep.name }};
{% endfor %}

{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    /**
     * Handle: {{ method.name | pascal_case }}Command
     * Emits: {{ method.name | to_event_name(legacy.entity_name) }}
     */
    public {{ method.return_type }} handle({{ method.name | pascal_case }}Command command) {
        log.info("Handling {{ method.name | pascal_case }}Command: {}", command);

        // 1. Validate command
        // TODO: validation logic from legacy {{ legacy.simple_name }}.{{ method.name }}()

        // 2. Execute business logic
        // TODO: domain logic

        // 3. Persist state change
        // TODO: repository.save(...)

        // 4. Publish event
        // eventPublisher.publish({{ method.name | to_event_name(legacy.entity_name) }}.create(...));

        throw new UnsupportedOperationException("TODO: handle {{ method.name | pascal_case }}Command");
    }

{% endif %}
{% endfor %}
{% for method in legacy.public_methods %}
{% if method.name | is_query %}
    /**
     * Query: {{ method.name }}
     * Consider moving to a dedicated QueryHandler/ReadModel for CQRS.
     */
    public {{ method.return_type }} {{ method.name }}({{ method.parameters }}) {
        throw new UnsupportedOperationException("TODO: {{ method.name }} — move to QueryHandler");
    }

{% endif %}
{% endfor %}
}
''',
    },

    "kafka_consumer": {
        "file_name": "kafka_consumer.java.tpl",
        "description": "Kafka event consumer with manual ack and DLT",
        "output_suffix": ".java",
        "target_layer": "MESSAGING",
        "tags": ["kafka", "consumer", "messaging"],
        "auto_match": {"legacy_layer": "MESSAGING"},
        "content": '''\
package {{ target.package }}.consumer;

{% for imp in target.imports %}
import {{ imp }};
{% endfor %}
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;

/**
 * Kafka Consumer — migrated from {{ legacy.fqcn }}
 * Legacy: {{ legacy.stereotype }} {{ legacy.superclass }}
 * Generated: {{ meta.date }}
 */
@Slf4j
@RequiredArgsConstructor
@Component
public class {{ target.class_name }} {

{% for dep in target.all_deps %}
    private final {{ dep.type }} {{ dep.name }};
{% endfor %}
    private final MeterRegistry meterRegistry;

{% for event_type in target.consumed_events %}
    @KafkaListener(
        topics = "${app.kafka.topics.{{ event_type | snake_case }}}",
        groupId = "${spring.application.name}",
        containerFactory = "retryableKafkaListenerContainerFactory"
    )
    public void handle{{ event_type }}(
            ConsumerRecord<String, {{ event_type }}> record,
            Acknowledgment acknowledgment) {

        var timer = Timer.start(meterRegistry);
        var event = record.value();

        try {
            log.info("Consuming {{ event_type }}: key={}, partition={}, offset={}",
                record.key(), record.partition(), record.offset());

            // Idempotency check
            // if (processedEventStore.exists(event.eventId())) {
            //     log.warn("Duplicate {{ event_type }}: {}", event.eventId());
            //     acknowledgment.acknowledge();
            //     return;
            // }

            // TODO: Process event — migrated from legacy {{ legacy.simple_name }}
            process{{ event_type }}(event);

            acknowledgment.acknowledge();
            meterRegistry.counter("events.consumed",
                "type", "{{ event_type }}", "status", "success").increment();

        } catch (Exception e) {
            log.error("Error consuming {{ event_type }}: {}", event, e);
            meterRegistry.counter("events.consumed",
                "type", "{{ event_type }}", "status", "error").increment();
            // Don't ack — will be retried or sent to DLT
            throw e;
        } finally {
            timer.stop(meterRegistry.timer("events.consumed.duration",
                "type", "{{ event_type }}"));
        }
    }

    private void process{{ event_type }}({{ event_type }} event) {
        // TODO: Implement — migrated from legacy
        throw new UnsupportedOperationException("TODO: process {{ event_type }}");
    }

{% endfor %}
}
''',
    },

    "saga_participant": {
        "file_name": "saga_participant.java.tpl",
        "description": "Choreography-based saga participant",
        "output_suffix": ".java",
        "target_layer": "SAGA",
        "tags": ["saga", "choreography", "compensation"],
        "auto_match": {},
        "content": '''\
package {{ target.package }}.saga;

{% for imp in target.imports %}
import {{ imp }};
{% endfor %}
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;

/**
 * Saga Participant — {{ target.class_name }}
 * Migrated from: {{ legacy.fqcn }}
 * Generated: {{ meta.date }}
 *
 * Listens for upstream events, performs local transaction,
 * publishes result event or compensation event on failure.
 */
@Slf4j
@RequiredArgsConstructor
@Component
public class {{ target.class_name }} {

{% for dep in target.all_deps %}
    private final {{ dep.type }} {{ dep.name }};
{% endfor %}

{% for step in saga.steps %}
    /**
     * Step: {{ step.name }}
     * Trigger: {{ step.trigger_event }}
     * Success: publishes {{ step.success_event }}
     * Failure: publishes {{ step.failure_event }}
     */
    @KafkaListener(topics = "${app.kafka.topics.{{ step.trigger_event | snake_case }}}")
    public void on{{ step.trigger_event }}({{ step.trigger_event }} event, Acknowledgment ack) {
        try {
            log.info("Saga step '{{ step.name }}' triggered by {{ step.trigger_event }}");

            // TODO: Local transaction
            // {{ step.action_description }}

            // Publish success
            // kafkaTemplate.send("{{ step.success_event | snake_case }}",
            //     event.correlationId(),
            //     new {{ step.success_event }}(event.correlationId(), ...));

            ack.acknowledge();

        } catch (Exception e) {
            log.error("Saga step '{{ step.name }}' failed", e);

            // Publish compensation event
            // kafkaTemplate.send("{{ step.failure_event | snake_case }}",
            //     event.correlationId(),
            //     new {{ step.failure_event }}(event.correlationId(), e.getMessage()));

            ack.acknowledge();
        }
    }

{% endfor %}
{% for comp in saga.compensations %}
    /**
     * Compensation: {{ comp.name }}
     * Triggered by: {{ comp.trigger_event }}
     */
    @KafkaListener(topics = "${app.kafka.topics.{{ comp.trigger_event | snake_case }}}")
    public void compensate{{ comp.name }}({{ comp.trigger_event }} event, Acknowledgment ack) {
        log.info("Compensating '{{ comp.name }}' for correlationId={}", event.correlationId());
        // TODO: Undo {{ comp.action_description }}
        ack.acknowledge();
    }

{% endfor %}
}
''',
    },

    "integration_test": {
        "file_name": "integration_test.java.tpl",
        "description": "Integration test with Testcontainers and EmbeddedKafka",
        "output_suffix": "Test.java",
        "target_layer": "TEST",
        "tags": ["test", "integration", "testcontainers"],
        "auto_match": {},
        "content": '''\
package {{ target.package }};

import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import static org.junit.jupiter.api.Assertions.*;
import static org.awaitility.Awaitility.*;
import java.time.Duration;

/**
 * Integration test for {{ target.class_name }}
 * Validates behavior parity with legacy {{ legacy.fqcn }}
 * Generated: {{ meta.date }}
 */
@SpringBootTest
@ActiveProfiles("test")
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class {{ target.class_name }}Test {

{% for dep in target.all_deps %}
    @Autowired
    private {{ dep.type }} {{ dep.name }};
{% endfor %}

{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    @Test
    @Order({{ loop.index }})
    @DisplayName("{{ method.name }}() — migrated from {{ legacy.simple_name }}")
    void should{{ method.name | first_upper }}Successfully() {
        // Given — set up test data matching legacy behavior
        // TODO: Create test fixtures

        // When — execute the migrated method
        // var result = service.{{ method.name }}(...);

        // Then — assert behavior parity with legacy
        // TODO: Assert same outcomes as legacy {{ legacy.simple_name }}.{{ method.name }}()

{% if method.name | is_mutating %}
        // Verify domain event was published
        // await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> {
        //     // Assert {{ method.name | to_event_name(legacy.entity_name) }} was published
        // });
{% endif %}

        fail("TODO: Implement test for {{ method.name }}");
    }

{% elif method.name | is_query %}
    @Test
    @DisplayName("{{ method.name }}() — query parity check")
    void should{{ method.name | first_upper }}() {
        // TODO: Assert query returns same results as legacy
        fail("TODO: Implement test for {{ method.name }}");
    }

{% endif %}
{% endfor %}
}
''',
    },

    "application_config": {
        "file_name": "application_config.yaml.tpl",
        "description": "Spring Boot application.yml for migrated service",
        "output_suffix": ".yml",
        "target_layer": "CONFIG",
        "tags": ["config", "yaml", "spring"],
        "auto_match": {},
        "content": '''\
# Generated configuration for {{ target.service_name }}
# Migrated from: {{ legacy.project }}
# Generated: {{ meta.date }}

spring:
  application:
    name: {{ target.service_name | snake_case | replace("_", "-") }}

  kafka:
    bootstrap-servers: ${KAFKA_BOOTSTRAP_SERVERS:localhost:9092}
    producer:
      key-serializer: org.apache.kafka.common.serialization.StringSerializer
      value-serializer: org.springframework.kafka.support.serializer.JsonSerializer
      acks: all
      properties:
        enable.idempotence: true
    consumer:
      group-id: ${spring.application.name}
      auto-offset-reset: earliest
      enable-auto-commit: false
      properties:
        spring.json.trusted.packages: "{{ target.package }}.**"

{% if target.uses_mongodb %}
  data:
    mongodb:
      uri: ${MONGODB_URI:mongodb://localhost:27017/{{ target.service_name | snake_case | replace("_", "-") }}}
{% endif %}

{% if target.uses_jpa %}
  datasource:
    url: ${DATABASE_URL:jdbc:postgresql://localhost:5432/{{ target.service_name | snake_case }}}
    driver-class-name: org.postgresql.Driver
  jpa:
    hibernate:
      ddl-auto: validate
    open-in-view: false
{% endif %}

management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  metrics:
    tags:
      application: ${spring.application.name}

app:
  kafka:
    topics:
{% for topic in target.kafka_topics %}
      {{ topic.name }}: {{ topic.value }}
{% endfor %}
  outbox:
    poll-interval: 1000
    batch-size: 100
''',
    },
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MCP Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def init_templates(force: bool = False) -> dict:
    """
    Bootstrap the templates directory with default templates.
    Run this once to create ~/.mcp-migration-kb/templates/ with starter templates
    that you can then customize.

    Args:
        force: If True, overwrite existing templates.
    """
    created = []
    skipped = []

    for tpl_id, tpl_data in DEFAULT_TEMPLATES.items():
        file_path = os.path.join(TEMPLATES_DIR, tpl_data["file_name"])

        if os.path.isfile(file_path) and not force:
            skipped.append(tpl_data["file_name"])
            # Still register if not in index
            if tpl_id not in tpl_registry.templates:
                tpl_registry.templates[tpl_id] = TemplateRecord(
                    template_id=tpl_id,
                    file_name=tpl_data["file_name"],
                    description=tpl_data["description"],
                    output_suffix=tpl_data["output_suffix"],
                    target_layer=tpl_data["target_layer"],
                    tags=tpl_data["tags"],
                    auto_match=tpl_data["auto_match"],
                    created_at=datetime.now().isoformat(),
                )
            continue

        # Write template file
        Path(file_path).write_text(tpl_data["content"])
        created.append(tpl_data["file_name"])

        # Register
        tpl_registry.templates[tpl_id] = TemplateRecord(
            template_id=tpl_id,
            file_name=tpl_data["file_name"],
            description=tpl_data["description"],
            output_suffix=tpl_data["output_suffix"],
            target_layer=tpl_data["target_layer"],
            tags=tpl_data["tags"],
            auto_match=tpl_data["auto_match"],
            created_at=datetime.now().isoformat(),
        )

    tpl_registry.save_index()

    # Write README
    readme_path = os.path.join(TEMPLATES_DIR, "README.md")
    if not os.path.isfile(readme_path) or force:
        Path(readme_path).write_text(_TEMPLATE_README)

    return {
        "templates_dir": TEMPLATES_DIR,
        "created": created,
        "skipped": skipped,
        "total_registered": len(tpl_registry.templates),
        "next_step": "Edit templates in the templates directory, then use render_from_template.",
    }


@mcp.tool()
def list_templates() -> dict:
    """List all registered templates with their metadata."""
    templates = []
    for tid, trec in tpl_registry.templates.items():
        file_path = os.path.join(TEMPLATES_DIR, trec.file_name)
        templates.append({
            "template_id": tid,
            "file_name": trec.file_name,
            "description": trec.description,
            "target_layer": trec.target_layer,
            "tags": trec.tags,
            "auto_match": trec.auto_match,
            "file_exists": os.path.isfile(file_path),
        })

    return {
        "templates_dir": TEMPLATES_DIR,
        "total": len(templates),
        "jinja2_available": HAS_JINJA2,
        "templates": templates,
    }


@mcp.tool()
def add_custom_template(
    template_id: str,
    file_name: str,
    content: str,
    description: str = "",
    output_suffix: str = ".java",
    target_layer: str = "",
    tags: str = "",
    auto_match_layer: str = "",
    auto_match_rule: str = "",
) -> dict:
    """
    Create a new custom template.

    Args:
        template_id:      Unique ID (e.g., "psf-participant")
        file_name:        Template file name (e.g., "psf_participant.java.tpl")
        content:          Template content using Jinja2 syntax
        description:      What this template generates
        output_suffix:    Output file extension (.java, .yml, .xml)
        target_layer:     Which layer this targets
        tags:             Comma-separated tags
        auto_match_layer: Auto-apply when legacy class is in this layer
        auto_match_rule:  Auto-apply when this mapping rule matches

    Returns:
        Saved template record.

    Template Variables Available:
        {{ legacy.fqcn }}, {{ legacy.simple_name }}, {{ legacy.package }}
        {{ legacy.layer }}, {{ legacy.stereotype }}, {{ legacy.superclass }}
        {{ legacy.interfaces }}, {{ legacy.annotations }}
        {{ legacy.constructor_deps }}, {{ legacy.field_deps }}
        {{ legacy.public_methods }}, {{ legacy.entity_name }}
        {{ target.package }}, {{ target.class_name }}
        {{ target.extends_clause }}, {{ target.implements_clause }}
        {{ target.annotations }}, {{ target.all_deps }}, {{ target.imports }}
        {{ meta.date }}, {{ meta.rules_applied }}, {{ meta.generator_version }}

    Filters Available:
        {{ name | camel_case }}         OrderService → orderService
        {{ name | pascal_case }}        order_service → OrderService
        {{ name | snake_case }}         OrderService → order_service
        {{ name | upper_snake }}        OrderService → ORDER_SERVICE
        {{ name | first_lower }}        Order → order
        {{ name | first_upper }}        order → Order
        {{ name | strip_suffix('Impl') }}  OrderServiceImpl → OrderService
        {{ method | to_event_name('Order') }}  createOrder → OrderCreatedEvent
        {{ name | to_topic_name }}      OrderService → order.events
        {{ method | is_mutating }}      True for create/save/update/delete
        {{ method | is_query }}         True for find/get/search/list
    """
    file_path = os.path.join(TEMPLATES_DIR, file_name)
    Path(file_path).write_text(content)

    auto_match = {}
    if auto_match_layer:
        auto_match["legacy_layer"] = auto_match_layer
    if auto_match_rule:
        auto_match["mapping_rule"] = auto_match_rule

    record = TemplateRecord(
        template_id=template_id,
        file_name=file_name,
        description=description,
        output_suffix=output_suffix,
        target_layer=target_layer,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        auto_match=auto_match,
        created_at=datetime.now().isoformat(),
    )

    tpl_registry.templates[template_id] = record
    tpl_registry.save_index()

    return {
        "status": "created",
        "template_id": template_id,
        "file_path": file_path,
        "record": asdict(record),
    }


@mcp.tool()
def get_template(template_id: str) -> dict:
    """
    Read a template's content and metadata.

    Args:
        template_id: Template ID to read.
    """
    if template_id not in tpl_registry.templates:
        return {"error": f"Template '{template_id}' not found."}

    record = tpl_registry.templates[template_id]
    file_path = os.path.join(TEMPLATES_DIR, record.file_name)

    content = ""
    if os.path.isfile(file_path):
        content = Path(file_path).read_text()

    return {
        "template_id": template_id,
        "metadata": asdict(record),
        "content": content,
        "file_path": file_path,
    }


@mcp.tool()
def update_template(template_id: str, content: str) -> dict:
    """
    Update an existing template's content.

    Args:
        template_id: Template to update
        content:     New template content
    """
    if template_id not in tpl_registry.templates:
        return {"error": f"Template '{template_id}' not found."}

    record = tpl_registry.templates[template_id]
    file_path = os.path.join(TEMPLATES_DIR, record.file_name)
    Path(file_path).write_text(content)

    return {"status": "updated", "template_id": template_id, "file_path": file_path}


@mcp.tool()
def render_from_template(
    template_id: str,
    project_name: str,
    class_name: str,
    target_package: str = "",
    extra_context: str = "",
    output_dir: str = "",
) -> dict:
    """
    Render a template for a specific legacy class from the KB.

    Args:
        template_id:    Which template to use
        project_name:   Legacy project in KB
        class_name:     Legacy class to migrate (FQCN or simple name)
        target_package: Target Java package
        extra_context:  JSON string of additional template variables
        output_dir:     Write file here (empty = dry run, return content only)

    Returns:
        Rendered code with file path.
    """
    if template_id not in tpl_registry.templates:
        return {"error": f"Template '{template_id}' not found. Run init_templates() first."}

    # Load legacy class from KB
    classes = _load_kb_project(project_name)
    if not classes:
        return {"error": f"Project '{project_name}' not in KB."}

    legacy = None
    for fqcn, cls in classes.items():
        if fqcn == class_name or cls.get("simple_name") == class_name:
            legacy = cls
            legacy["fqcn"] = fqcn
            break

    if not legacy:
        return {"error": f"Class '{class_name}' not found in '{project_name}'."}

    # Build context
    if not target_package:
        target_package = legacy.get("package", "com.company.migrated")

    ctx = _build_render_context(legacy, target_package, project_name, extra_context)

    # Render
    try:
        rendered = tpl_registry.render(template_id, ctx)
    except Exception as e:
        return {"error": f"Template rendering failed: {str(e)}"}

    # Output
    record = tpl_registry.templates[template_id]
    target_class = ctx["target"]["class_name"]
    file_name = f"{target_class}{record.output_suffix}"
    rel_path = os.path.join(target_package.replace(".", os.sep), file_name)

    if output_dir:
        full_path = os.path.join(os.path.abspath(output_dir), rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Path(full_path).write_text(rendered)

    return {
        "template_used": template_id,
        "legacy_class": legacy["fqcn"],
        "target_class": f"{target_package}.{target_class}",
        "file_path": rel_path,
        "written": bool(output_dir),
        "content": rendered,
    }


@mcp.tool()
def render_batch(
    template_id: str,
    project_name: str,
    layer_filter: str = "",
    target_package: str = "",
    output_dir: str = "",
) -> dict:
    """
    Render a template for ALL matching classes in a project.

    Args:
        template_id:    Template to apply
        project_name:   Legacy project in KB
        layer_filter:   Only process classes in this layer (e.g., "SERVICE")
        target_package: Base target package
        output_dir:     Output directory (empty = dry run)

    Returns:
        Summary of all generated files.
    """
    classes = _load_kb_project(project_name)
    if not classes:
        return {"error": f"Project '{project_name}' not in KB."}

    results = []
    for fqcn, cls in classes.items():
        if layer_filter and cls.get("layer") != layer_filter.upper():
            continue

        pkg = target_package or cls.get("package", "com.company.migrated")

        result = render_from_template(
            template_id=template_id,
            project_name=project_name,
            class_name=fqcn,
            target_package=pkg,
            output_dir=output_dir,
        )

        if "error" not in result:
            results.append({
                "legacy": fqcn,
                "target": result.get("target_class"),
                "file": result.get("file_path"),
            })

    return {
        "template": template_id,
        "project": project_name,
        "layer_filter": layer_filter or "(all)",
        "files_generated": len(results),
        "dry_run": not bool(output_dir),
        "results": results,
    }


@mcp.tool()
def preview_template_context(project_name: str, class_name: str) -> dict:
    """
    Show the full template context that would be available when rendering
    a template for a legacy class. Useful for writing/debugging templates.

    Args:
        project_name: Legacy project in KB
        class_name:   Class to inspect
    """
    classes = _load_kb_project(project_name)
    legacy = None
    for fqcn, cls in classes.items():
        if fqcn == class_name or cls.get("simple_name") == class_name:
            legacy = cls
            legacy["fqcn"] = fqcn
            break

    if not legacy:
        return {"error": f"'{class_name}' not found in '{project_name}'."}

    ctx = _build_render_context(legacy, legacy.get("package", ""), project_name, "")

    return {
        "message": "This is the full context available in templates.",
        "context": ctx,
    }


@mcp.tool()
def delete_template(template_id: str) -> dict:
    """Remove a custom template."""
    if template_id not in tpl_registry.templates:
        return {"error": f"Template '{template_id}' not found."}

    record = tpl_registry.templates.pop(template_id)
    tpl_registry.save_index()

    file_path = os.path.join(TEMPLATES_DIR, record.file_name)
    if os.path.isfile(file_path):
        os.remove(file_path)

    return {"status": "deleted", "template_id": template_id}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Context Builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_render_context(
    legacy: dict, target_package: str,
    project_name: str, extra_json: str,
) -> dict:
    """Build the full template rendering context from legacy class + rules."""

    simple = legacy.get("simple_name", "Unknown")
    # Clean up target class name
    target_class = simple
    for suffix in ("Bean", "Impl", "EJB"):
        if target_class.endswith(suffix):
            target_class = target_class[:-len(suffix)]

    # Guess entity name from class name
    entity_name = target_class
    for suffix in ("Service", "Dao", "DAO", "Repository", "Handler", "Controller", "Listener"):
        if entity_name.endswith(suffix):
            entity_name = entity_name[:-len(suffix)]
            break

    # Load mapping rules
    mappings = _load_mappings()
    matched_rules = _match_mapping_rules(legacy, mappings)
    rule_ids = [r["rule_id"] for r in matched_rules]

    # Merge transforms from rules
    merged = {"extends": "", "implements": [], "annotations": ["@Service"],
              "inject": [], "imports": []}
    for rule in matched_rules:
        t = rule.get("target_transform", {})
        if t.get("extends"):
            merged["extends"] = t["extends"]
        if t.get("implements"):
            merged["implements"].extend(t["implements"])
        if t.get("annotation"):
            merged["annotations"] = [t["annotation"]]
        if t.get("inject"):
            merged["inject"].extend(t["inject"])
        if t.get("additional_imports"):
            merged["imports"].extend(t["additional_imports"])

    # Build deps list
    all_deps = []
    for dep in legacy.get("constructor_deps", []) + legacy.get("field_deps", []):
        dep_type = dep.get("type", "")
        if dep_type.endswith("Dao"):
            dep_type = dep_type.replace("Dao", "Repository")
        elif dep_type.endswith("DAO"):
            dep_type = dep_type.replace("DAO", "Repository")
        all_deps.append({"type": dep_type, "name": dep.get("name", "")})

    for inj in merged["inject"]:
        name = inj[0].lower() + inj[1:]
        if name not in [d["name"] for d in all_deps]:
            all_deps.append({"type": inj, "name": name})

    extends_clause = f" extends {merged['extends']}" if merged["extends"] else ""
    impl_list = merged["implements"]
    implements_clause = f" implements {', '.join(impl_list)}" if impl_list else ""

    # Enrich methods with computed properties
    enriched_methods = []
    for m in legacy.get("public_methods", []):
        em = dict(m)
        em["is_mutating"] = _is_mutating_method(m)
        em["is_query"] = _is_query_method(m)
        enriched_methods.append(em)

    # Load framework info
    fwk = _load_target_framework()

    ctx = {
        "legacy": {
            **legacy,
            "project": project_name,
            "entity_name": entity_name,
            "public_methods": enriched_methods,
            "migration_notes": legacy.get("migration_notes", [])
                             + legacy.get("javax_imports", []),
        },
        "target": {
            "package": target_package,
            "class_name": target_class,
            "extends": merged["extends"],
            "extends_clause": extends_clause,
            "implements": merged["implements"],
            "implements_clause": implements_clause,
            "annotations": merged["annotations"],
            "all_deps": all_deps,
            "imports": sorted(set(merged["imports"])),
            "base_repository": merged["extends"] or "ReactiveMongoRepository",
            "framework_name": fwk.get("name", ""),
            "service_name": target_class,
            "consumed_events": [],
            "uses_mongodb": True,
            "uses_jpa": False,
            "kafka_topics": [
                {"name": _snake_case(entity_name), "value": f"{_snake_case(entity_name)}.events"},
            ],
        },
        "meta": {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "rules_applied": rule_ids,
            "generator_version": GENERATOR_VERSION,
        },
        "event": {
            "name": f"{entity_name}CreatedEvent",
            "source_method": "create",
            "fields": [
                {"type": "String", "name": f"{entity_name[0].lower()}{entity_name[1:]}Id"},
            ],
        },
        "saga": {
            "steps": [],
            "compensations": [],
        },
    }

    # Merge extra context
    if extra_json:
        try:
            extra = json.loads(extra_json)
            _deep_merge(ctx, extra)
        except Exception:
            pass

    return ctx


def _match_mapping_rules(legacy: dict, rules: dict) -> list[dict]:
    """Match mapping rules against a legacy class."""
    matched = []
    for rid, rule in sorted(rules.items(), key=lambda x: x[1].get("priority", 100)):
        if not rule.get("enabled", True):
            continue
        match = rule.get("legacy_match", {})
        ok = True
        if "layer" in match and match["layer"] != legacy.get("layer"):
            ok = False
        if "annotation" in match:
            if match["annotation"] not in " ".join(legacy.get("annotations", [])):
                ok = False
        if "extends" in match and match["extends"] != legacy.get("superclass"):
            ok = False
        if "implements" in match:
            if match["implements"] not in legacy.get("interfaces", []):
                ok = False
        if ok:
            rule["rule_id"] = rid
            matched.append(rule)
    return matched


def _deep_merge(base: dict, override: dict):
    """Recursively merge override into base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# README for template authors
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_TEMPLATE_README = """\
# Migration Templates — Authoring Guide

## Directory Structure

Each `.tpl` file is a Jinja2 template that generates one output file.
Templates are registered in `_template_index.json`.

## Quick Start

1. Copy an existing `.tpl` file
2. Edit it with your framework-specific patterns
3. Register with `add_custom_template()` or edit `_template_index.json`
4. Test with `render_from_template(template_id, project, class)`

## Template Variables

### `legacy.*` — The source legacy class
| Variable | Type | Example |
|----------|------|---------|
| `legacy.fqcn` | string | `com.company.service.OrderService` |
| `legacy.simple_name` | string | `OrderService` |
| `legacy.package` | string | `com.company.service` |
| `legacy.layer` | string | `SERVICE` |
| `legacy.stereotype` | string | `@Service` |
| `legacy.superclass` | string | `BaseService` |
| `legacy.interfaces` | list[str] | `["Serializable"]` |
| `legacy.annotations` | list[str] | `["@Service", "@Transactional"]` |
| `legacy.entity_name` | string | `Order` (auto-stripped from class name) |
| `legacy.constructor_deps` | list[dict] | `[{"type": "OrderDao", "name": "orderDao"}]` |
| `legacy.field_deps` | list[dict] | Same format |
| `legacy.public_methods` | list[dict] | `[{"name": "createOrder", "return_type": "Order", "parameters": "...", "annotations": [...]}]` |

### `target.*` — The generation target
| Variable | Type | Source |
|----------|------|--------|
| `target.package` | string | User-specified or auto |
| `target.class_name` | string | Cleaned from legacy (no Impl/Bean suffix) |
| `target.extends` | string | From mapping rule |
| `target.implements` | list[str] | From mapping rule |
| `target.annotations` | list[str] | From mapping rule |
| `target.all_deps` | list[dict] | Legacy deps + rule-injected deps |
| `target.imports` | list[str] | From mapping rule |

### `meta.*` — Generation metadata
| Variable | Type |
|----------|------|
| `meta.date` | string (`YYYY-MM-DD`) |
| `meta.rules_applied` | list[str] |
| `meta.generator_version` | string |

## Custom Filters

| Filter | Input → Output |
|--------|----------------|
| `camel_case` | `OrderService` → `orderService` |
| `pascal_case` | `order_service` → `OrderService` |
| `snake_case` | `OrderService` → `order_service` |
| `upper_snake` | `OrderService` → `ORDER_SERVICE` |
| `first_lower` | `Order` → `order` |
| `first_upper` | `order` → `Order` |
| `strip_suffix('Impl')` | `OrderServiceImpl` → `OrderService` |
| `to_event_name('Order')` | `createOrder` → `OrderCreatedEvent` |
| `to_topic_name` | `OrderService` → `order.events` |
| `is_mutating` | True for create/save/update/delete methods |
| `is_query` | True for find/get/search/list methods |

## Control Flow

```jinja
{% for method in legacy.public_methods %}
{% if method.name | is_mutating %}
    // This is a write method
{% elif method.name | is_query %}
    // This is a read method
{% endif %}
{% endfor %}
```

## Extra Context

Pass additional variables via `extra_context` (JSON string):
```json
{"saga": {"steps": [{"name": "reserve", "trigger_event": "OrderCreated"}]}}
```
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resources
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.resource("templates://status")
def templates_status() -> str:
    lines = [
        "Migration Template Engine",
        "=" * 40,
        f"Templates dir: {TEMPLATES_DIR}",
        f"Jinja2 available: {HAS_JINJA2}",
        f"Templates registered: {len(tpl_registry.templates)}",
        "",
    ]
    for tid, trec in tpl_registry.templates.items():
        exists = "✓" if os.path.isfile(os.path.join(TEMPLATES_DIR, trec.file_name)) else "✗"
        lines.append(f"  [{exists}] {tid:30} {trec.description}")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    mcp.run()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# THE COMPLETE 5-SERVER STACK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# .vscode/mcp.json:
# {
#   "servers": {
#     "migration-kb": {
#       "command": "python",
#       "args": ["/path/to/migration_kb_mcp_server.py"]
#     },
#     "migration-codegen": {
#       "command": "python",
#       "args": ["/path/to/migration_codegen_mcp_server.py"]
#     },
#     "migration-templates": {
#       "command": "python",
#       "args": ["/path/to/migration_template_engine.py"]
#     },
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
