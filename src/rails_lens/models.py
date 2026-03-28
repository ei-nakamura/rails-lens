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
