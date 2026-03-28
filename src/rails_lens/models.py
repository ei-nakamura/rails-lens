"""rails-lens データモデル定義"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ============================================================
# 共通
# ============================================================

class SourceLocation(BaseModel):
    """ソースコード上の位置"""
    source_file: str
    source_line: int


class Conditions(BaseModel):
    """コールバック / バリデーションの条件"""
    if_condition: str | None = Field(None, alias="if")
    unless_condition: str | None = Field(None, alias="unless")

    model_config = ConfigDict(populate_by_name=True)


# ============================================================
# introspect_model 入力
# ============================================================

VALID_SECTIONS = [
    "associations", "callbacks", "validations", "scopes",
    "concerns", "enums", "schema", "sti", "delegations",
    "class_methods", "instance_methods",
]

class IntrospectModelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    model_name: str = Field(
        ...,
        description="ActiveRecord model name (e.g., 'User', 'Admin::Company')",
        min_length=1,
        max_length=200,
    )
    sections: list[str] | None = Field(
        default=None,
        description="Sections to include (default: all)",
    )


# ============================================================
# introspect_model 出力
# ============================================================

class Association(BaseModel):
    name: str
    type: str  # belongs_to, has_many, has_one, has_and_belongs_to_many
    class_name: str
    foreign_key: str | None = None
    through: str | None = None
    polymorphic: bool = False
    dependent: str | None = None
    has_scope: bool = False


class Callback(SourceLocation):
    kind: str  # before, after, around
    event: str
    method_name: str
    source_file: str
    source_line: int
    conditions: Conditions = Field(
        default_factory=lambda: Conditions(if_condition=None, unless_condition=None)  # type: ignore[call-arg]
    )
    defined_in_concern: str | None = None


class Validation(SourceLocation):
    type: str
    attributes: list[str]
    options: dict[str, Any] = Field(default_factory=dict)
    custom_validator: str | None = None
    source_file: str
    source_line: int


class Scope(SourceLocation):
    name: str
    source_file: str
    source_line: int


class ConcernInfo(BaseModel):
    name: str
    provided_methods: list[str] = Field(default_factory=list)
    source_file: str


class EnumInfo(BaseModel):
    name: str
    values: dict[str, Any]


class ColumnInfo(BaseModel):
    name: str
    type: str
    null: bool = True
    default: str | int | float | bool | None = None
    limit: int | None = None


class IndexInfo(BaseModel):
    name: str
    columns: list[str]
    unique: bool = False


class ForeignKeyInfo(BaseModel):
    from_column: str
    to_table: str
    to_column: str = "id"


class SchemaInfo(BaseModel):
    columns: list[ColumnInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)


class STIInfo(BaseModel):
    base_class: str
    descendants: list[str] = Field(default_factory=list)
    type_column: str = "type"


class Delegation(BaseModel):
    methods: list[str]
    to: str
    prefix: str | bool | None = None


class MethodInfo(SourceLocation):
    name: str
    source_file: str
    source_line: int


class IntrospectModelOutput(BaseModel):
    model_name: str
    table_name: str
    file_path: str
    associations: list[Association] = Field(default_factory=list)
    callbacks: list[Callback] = Field(default_factory=list)
    validations: list[Validation] = Field(default_factory=list)
    scopes: list[Scope] = Field(default_factory=list)
    concerns: list[ConcernInfo] = Field(default_factory=list)
    enums: list[EnumInfo] = Field(default_factory=list)
    schema: SchemaInfo = Field(default_factory=SchemaInfo)  # type: ignore[assignment]
    sti: STIInfo | None = None
    delegations: list[Delegation] = Field(default_factory=list)
    class_methods: list[MethodInfo] = Field(default_factory=list)
    instance_methods: list[MethodInfo] = Field(default_factory=list)


# ============================================================
# find_references 入力 / 出力
# ============================================================

class FindReferencesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(..., min_length=1, description="Search query")
    scope: str = Field("all", description="Search scope")
    type: str = Field("any", description="Search type")


class MatchContext(BaseModel):
    before: str = ""
    match: str = ""
    after: str = ""


class ReferenceMatch(BaseModel):
    file: str
    line: int
    column: int = 0
    context: MatchContext = Field(default_factory=MatchContext)
    match_type: str = "other"


class FindReferencesOutput(BaseModel):
    query: str
    total_matches: int = 0
    matches: list[ReferenceMatch] = Field(default_factory=list)


# ============================================================
# trace_callback_chain 入力 / 出力
# ============================================================

class TraceCallbackChainInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    model_name: str = Field(..., min_length=1, max_length=200)
    lifecycle_event: str


class CallbackStep(BaseModel):
    order: int
    kind: str
    method_name: str
    source_file: str
    source_line: int
    defined_in_concern: str | None = None
    conditions: Conditions = Field(
        default_factory=lambda: Conditions(if_condition=None, unless_condition=None)  # type: ignore[call-arg]
    )
    note: str | None = None


class TraceCallbackChainOutput(BaseModel):
    model_name: str
    lifecycle_event: str
    execution_order: list[CallbackStep] = Field(default_factory=list)
    mermaid_diagram: str = ""


# ============================================================
# dependency_graph 入力 / 出力
# ============================================================

class DependencyGraphInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    entry_point: str = Field(..., min_length=1)
    depth: int = Field(2, ge=1, le=5)
    format: str = Field("mermaid")


class GraphNode(BaseModel):
    id: str
    type: str  # model, controller, concern, service
    file_path: str = ""


class GraphEdge(BaseModel):
    from_node: str = Field(..., alias="from")
    to_node: str = Field(..., alias="to")
    relation: str  # association, callback, include, reference, inheritance
    label: str = ""

    model_config = ConfigDict(populate_by_name=True)


class DependencyGraphOutput(BaseModel):
    entry_point: str
    depth: int
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    mermaid_diagram: str | None = None


# ============================================================
# ユーティリティツール
# ============================================================

class ModelSummary(BaseModel):
    name: str
    table_name: str
    file_path: str


class ListModelsOutput(BaseModel):
    models: list[ModelSummary] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    code: str
    message: str
    suggestion: str | None = None


# ============================================================
# Phase 5: Method Resolution
# ============================================================

class AncestorEntry(BaseModel):
    name: str
    type: str  # "self" | "concern" | "gem_module" | "active_record_internal" | "ruby_core"
    source_file: str | None = None

class MethodResolutionInput(BaseModel):
    model_name: str
    method_name: str | None = None
    show_internal: bool = False

class MethodResolutionOutput(BaseModel):
    model_name: str
    ancestors: list[AncestorEntry] = []
    method_owner: str | None = None
    super_chain: list[str] = []
    monkey_patches: list[str] = []


# ============================================================
# Phase 5: Gem Introspect
# ============================================================

class GemMethod(BaseModel):
    gem_name: str
    method_name: str
    source_file: str | None = None

class GemCallback(BaseModel):
    gem_name: str
    kind: str
    event: str
    method_name: str

class GemRoute(BaseModel):
    gem_name: str
    path: str
    verb: str

class GemIntrospectInput(BaseModel):
    model_name: str
    gem_name: str | None = None

class GemIntrospectOutput(BaseModel):
    model_name: str
    gem_methods: list[GemMethod] = []
    gem_callbacks: list[GemCallback] = []
    gem_routes: list[GemRoute] = []


# ============================================================
# Phase 6: Impact Analysis
# ============================================================

class ImpactAnalysisInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    model_name: str = Field(
        ...,
        description="ActiveRecord model name (e.g., 'User', 'Admin::Company')",
        min_length=1,
        max_length=200,
    )
    target: str = Field(
        ...,
        description="Column or method name to analyze impact for",
        min_length=1,
    )
    change_type: str = Field(
        "modify",
        description="Type of change: remove, rename, type_change, modify",
    )


class ImpactItem(BaseModel):
    category: str  # "callback", "validation", "scope", "view", "mailer",
                   # "serializer", "job", "association_cascade", "controller"
    file: str
    line: int
    description: str  # 人間可読な影響の説明
    severity: str     # "breaking", "warning", "info"
    code_snippet: str = ""  # 該当コード断片


class CascadeEffect(BaseModel):
    source_model: str
    target_model: str
    relation: str   # "dependent_destroy", "dependent_nullify", "touch", etc.
    description: str


class ImpactAnalysisOutput(BaseModel):
    model_name: str
    target: str
    change_type: str
    target_type: str                                         # "column" or "method"
    direct_impacts: list[ImpactItem] = Field(default_factory=list)
    cascade_effects: list[CascadeEffect] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)  # 修正が必要なファイル一覧
    summary: str = ""
    mermaid_diagram: str = ""


# ============================================================
# Phase 6: Test Mapping
# ============================================================

class TestMappingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    target: str = Field(
        ...,
        description="Model name (e.g., 'User') or method spec (e.g., 'User#activate')",
        min_length=1,
    )
    include_indirect: bool = Field(
        True,
        description="Include indirectly testing specs (shared_examples, feature specs)",
    )


class TestFile(BaseModel):
    file: str
    type: str       # "unit", "request", "feature", "shared_example", "factory"
    relevance: str  # "direct", "indirect"
    matched_examples: list[str] = Field(default_factory=list)  # テストケース名


class TestMappingOutput(BaseModel):
    target: str
    test_framework: str  # "rspec" or "minitest"
    direct_tests: list[TestFile] = Field(default_factory=list)
    indirect_tests: list[TestFile] = Field(default_factory=list)
    factories: list[TestFile] = Field(default_factory=list)
    run_command: str = ""  # "bundle exec rspec <files>" 形式


# ============================================================
# Phase 7: Dead Code Detection
# ============================================================

class DeadCodeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    scope: str = Field(
        "models",
        description="Detection scope: models, controllers, all",
    )
    model_name: str | None = Field(
        None,
        description="Limit to specific model (optional)",
    )
    confidence: str = Field(
        "high",
        description="Confidence filter: high (certainly unused), medium (possibly dynamic)",
    )


class DeadCodeItem(BaseModel):
    type: str              # "method", "callback", "scope", "validation"
    name: str
    file: str
    line: int
    confidence: str        # "high", "medium"
    reason: str
    reference_count: int = 0
    dynamic_call_risk: bool = False


class DeadCodeOutput(BaseModel):
    scope: str
    model_name: str | None = None
    items: list[DeadCodeItem] = Field(default_factory=list)
    total_methods_analyzed: int = 0
    total_dead_code_found: int = 0
    summary: str = ""


# ============================================================
# Phase 7: Circular Dependencies
# ============================================================

class CircularDependenciesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    entry_point: str | None = Field(
        None,
        description="Filter cycles containing this model (optional)",
    )
    format: str = Field(
        "mermaid",
        description="Output format: mermaid or json",
    )


class CyclePath(BaseModel):
    models: list[str]
    edges: list[GraphEdge] = Field(default_factory=list)
    cycle_type: str   # "callback_mutual", "association_bidirectional", "validation_cross_reference"
    severity: str     # "critical", "warning"


class CircularDependenciesOutput(BaseModel):
    total_cycles: int = 0
    cycles: list[CyclePath] = Field(default_factory=list)
    mermaid_diagram: str | None = None
    summary: str = ""


# ============================================================
# Phase 7: Extract Concern Candidate
# ============================================================

class ExtractConcernInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    model_name: str = Field(
        ...,
        description="ActiveRecord model name",
        min_length=1,
        max_length=200,
    )
    min_cluster_size: int = Field(
        3,
        description="Minimum number of methods in a cluster",
        ge=2,
        le=10,
    )


class MethodNode(BaseModel):
    name: str
    type: str  # "instance_method", "class_method"
    accesses_columns: list[str] = Field(default_factory=list)
    calls_methods: list[str] = Field(default_factory=list)
    source_file: str
    source_line: int
    line_count: int = 0


class ConcernCandidate(BaseModel):
    suggested_name: str
    methods: list[str]
    shared_columns: list[str] = Field(default_factory=list)
    cohesion_score: float = 0.0
    rationale: str = ""
    existing_concern_overlap: list[str] = Field(default_factory=list)


class ExtractConcernOutput(BaseModel):
    model_name: str
    total_methods: int = 0
    total_lines: int = 0
    candidates: list[ConcernCandidate] = Field(default_factory=list)
    unclustered_methods: list[str] = Field(default_factory=list)
    summary: str = ""
    mermaid_diagram: str = ""


# ============================================================
# Phase 8: Data Flow
# ============================================================

class DataFlowInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    controller_action: str | None = Field(
        None,
        description="Controller#action (e.g., 'UsersController#create')",
    )
    model_name: str | None = Field(
        None,
        description="Model name (alternative to controller_action)",
    )
    attribute: str | None = Field(
        None,
        description="Attribute to trace (optional, traces all if omitted)",
    )


class RouteInfo(BaseModel):
    verb: str
    path: str
    controller: str
    action: str


class StrongParamsInfo(BaseModel):
    file: str
    line: int
    permitted_params: list[str]
    nested_params: dict[str, list[str]] = Field(default_factory=dict)


class CallbackTransform(BaseModel):
    kind: str              # "before_save", "before_validation", etc.
    method_name: str
    file: str
    line: int
    description: str


class DataFlowStep(BaseModel):
    order: int
    # "routing", "strong_params", "assignment", "callback", "nested_propagation"
    layer: str
    description: str
    file: str | None = None
    line: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DataFlowOutput(BaseModel):
    entry_point: str
    attribute: str | None = None
    route: RouteInfo | None = None
    strong_params: StrongParamsInfo | None = None
    callbacks: list[CallbackTransform] = Field(default_factory=list)
    flow_steps: list[DataFlowStep] = Field(default_factory=list)
    mermaid_diagram: str = ""


# ============================================================
# Phase 8: Migration Context
# ============================================================

class MigrationContextInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    table_name: str = Field(
        ...,
        description="Table name (e.g., 'users')",
        min_length=1,
    )
    operation: str = Field(
        "general",
        description=(
            "Planned operation type "
            "(add_column, remove_column, add_index, change_column, add_reference, general)"
        ),
    )


class MigrationHistoryItem(BaseModel):
    version: str           # "20260315120000"
    name: str              # "AddPhoneToUsers"
    file: str              # "db/migrate/20260315120000_add_phone_to_users.rb"
    operation_summary: str # "add_column :users, :phone, :string"


class MigrationWarning(BaseModel):
    type: str              # "large_table", "missing_index", "null_constraint", "foreign_key"
    message: str
    suggestion: str


class MigrationTemplate(BaseModel):
    description: str
    code: str              # マイグレーションファイルのテンプレート


class MigrationContextOutput(BaseModel):
    table_name: str
    operation: str
    schema: SchemaInfo = Field(default_factory=SchemaInfo)  # type: ignore[assignment]
    migration_history: list[MigrationHistoryItem] = Field(default_factory=list)
    warnings: list[MigrationWarning] = Field(default_factory=list)
    template: MigrationTemplate | None = None
    related_models: list[str] = Field(default_factory=list)
    estimated_row_count: int | None = None
    summary: str = ""
