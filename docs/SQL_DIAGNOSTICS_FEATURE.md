# rails-lens SQL診断機能設計書

> **バージョン**: 1.0.0
> **最終更新**: 2026-03-31
> **ステータス**: 設計確定・実装前
> **前提ドキュメント**: [REQUIREMENTS.md](./REQUIREMENTS.md), [WEB_DASHBOARD_DESIGN.md](./WEB_DASHBOARD_DESIGN.md)

---

## 目次

1. [概要](#1-概要)
2. [背景と課題](#2-背景と課題)
3. [ツール仕様](#3-ツール仕様)
   - [ツール1: rails_lens_schema_audit](#ツール1-rails_lens_schema_audit)
   - [ツール2: rails_lens_query_audit](#ツール2-rails_lens_query_audit)
   - [ツール3: rails_lens_query_preview](#ツール3-rails_lens_query_preview)
   - [ツール4: rails_lens_query_preview_snippet](#ツール4-rails_lens_query_preview_snippet)
4. [検出ルール一覧](#4-検出ルール一覧)
5. [ActiveRecord メソッド → SQL 変換ルール](#5-activerecord-メソッド--sql-変換ルール)
6. [実装方針](#6-実装方針)
7. [ダッシュボードページ設計](#7-ダッシュボードページ設計)
8. [既存ツールとの連携](#8-既存ツールとの連携)
9. [ディレクトリ構造](#9-ディレクトリ構造)
10. [実装の難易度と工数](#10-実装の難易度と工数)
11. [設計上の注意点](#11-設計上の注意点)

---

## 1. 概要

### 背景

本機能は **DB 接続を一切必要としない**静的な SQL 診断ツールである。`db/schema.rb` の解析と Ruby ソースコードの grep/正規表現ベースの静的解析のみで、SQL に関する潜在的な問題を検出する。

### 設計原則

既存アーキテクチャの原則を踏襲する:

| 原則 | 内容 |
|---|---|
| **ハイブリッド構成** | Python（FastMCP + ツール定義）+ Ruby（ランタイムイントロスペクション） |
| **静的解析主体** | 本機能は `rails runner` を使わず、`db/schema.rb` と Ruby ソースコードの静的解析のみで完結する |
| **ツール登録パターン** | `register(mcp, get_deps)` による遅延初期化 |
| **キャッシュ戦略** | ファイルベース JSON キャッシュ + mtime 自動無効化 |
| **構造化出力** | Pydantic モデルによる型安全な入出力 + JSON 文字列返却 |
| **アノテーション** | 全ツール `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True` |

---

## 2. 背景と課題

### Rails 開発者が SQL の問題に気づけない理由

ActiveRecord は Ruby のメソッドチェーンを SQL に変換する ORM であり、開発者は SQL を直接書かずに DB 操作ができる。これにより生産性は向上するが、以下の問題が不可視になる:

1. **インデックスの欠如**: `belongs_to :company` と書けば `company_id` カラムが作られるが、インデックスは自動では作られない（Rails 5 以降は `add_reference` で自動追加されるが、手動で `add_column` した場合は忘れがち）
2. **不要な全カラム取得**: `User.all` や `User.where(...)` は `SELECT *` を発行する。必要なカラムが 3 つでも 100 カラム全部取得する
3. **安全でないクエリ構築**: `where("name LIKE '%#{params[:q]}%'")` のような文字列補間は SQL インジェクションの温床
4. **非効率なバッチ処理**: `User.all.each { |u| ... }` は全レコードをメモリにロードする。10 万件あればメモリが溢れる
5. **カウンタキャッシュの未使用**: `user.orders.count` を毎回呼ぶと毎回 `SELECT COUNT(*)` が走る
6. **型の不整合**: 外部キーと参照先の主キーで型が違うと JOIN のパフォーマンスが劣化する

### この機能が解決すること

`db/schema.rb`（DB の構造定義）と Ruby ソースコード（クエリの書き方）を静的に解析し、上記の問題を**コードを実行せずに**検出する。DB への接続は不要。

---

## 3. ツール仕様

### ツール1: rails_lens_schema_audit

#### 解決する課題と利用シーン

- **課題**: `db/schema.rb` に定義されたスキーマの設計上の問題（インデックス不足、型の不一致、制約の欠如）は、開発中に気づきにくい。問題が顕在化するのは本番環境でデータ量が増えた後であり、対応コストが高い
- **利用シーン**: マイグレーション作成前の事前チェック、コードレビュー時のスキーマ検証、新規参画メンバーによるプロジェクト品質把握

#### 入力スキーマ

```python
class SchemaAuditInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    scope: str = Field(
        default="all",
        description="診断範囲。'all' で全テーブル、テーブル名を指定すると単一テーブルのみ診断"
    )
    severity_filter: str = Field(
        default="all",
        description="重大度フィルタ: 'all', 'critical', 'warning', 'info'"
    )
```

#### 出力スキーマ

```python
class SchemaAuditSummary(BaseModel):
    tables_analyzed: int
    total_issues: int
    critical: int
    warning: int
    info: int

class SchemaIssue(BaseModel):
    rule_id: str
    severity: SeverityLevel
    table: str
    column: str | None = None
    columns: list[str] | None = None
    message: str
    impact: str
    suggestion: str
    migration_hint: str

class SchemaAuditOutput(BaseModel):
    summary: SchemaAuditSummary
    issues: list[SchemaIssue]
```

#### 出力例

```json
{
  "summary": {
    "tables_analyzed": 15,
    "total_issues": 12,
    "critical": 3,
    "warning": 6,
    "info": 3
  },
  "issues": [
    {
      "rule_id": "IDX001",
      "severity": "critical",
      "table": "orders",
      "column": "shipping_address_id",
      "message": "外部キー shipping_address_id にインデックスがありません",
      "impact": "Address との JOIN やこのカラムでの検索がフルテーブルスキャンになります",
      "suggestion": "add_index :orders, :shipping_address_id",
      "migration_hint": "rails generate migration AddIndexToOrdersShippingAddressId"
    },
    {
      "rule_id": "IDX002",
      "severity": "critical",
      "table": "comments",
      "columns": ["commentable_type", "commentable_id"],
      "message": "ポリモーフィック関連に複合インデックスがありません",
      "impact": "コメントの検索時に type と id の両方で絞り込めず、テーブルスキャンが発生します",
      "suggestion": "add_index :comments, [:commentable_type, :commentable_id]",
      "migration_hint": "rails generate migration AddIndexToCommentsCommentable"
    },
    {
      "rule_id": "COL004",
      "severity": "warning",
      "table": "users",
      "column": "name",
      "message": "validates :name, presence: true がありますが、DB レベルでは NULL が許容されています",
      "impact": "ActiveRecord を介さない DB 操作（バッチ処理、直接 SQL）で NULL が入る可能性があります",
      "suggestion": "change_column_null :users, :name, false",
      "migration_hint": "rails generate migration ChangeUsersNameNotNull"
    }
  ]
}
```

#### ツール定義

```python
@mcp.tool(
    name="rails_lens_schema_audit",
    annotations={
        "title": "Schema Audit",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def schema_audit(params: SchemaAuditInput) -> str:
    '''db/schema.rb を解析してスキーマ設計上の問題を検出する。
    DB への接続は不要。インデックス不足、型の不一致、制約の欠如などを報告する。
    検出された問題にはマイグレーションコマンドの提案も含まれる。
    マイグレーションを作成する前に必ずこのツールでスキーマの問題を確認すること。
    '''
    ...
```

---

### ツール2: rails_lens_query_audit

#### 解決する課題と利用シーン

- **課題**: ActiveRecord のクエリパターンに潜む SQL インジェクションリスク、メモリ過大消費、N+1 クエリ、データ整合性の欠如は、コードレビューで見逃されやすい
- **利用シーン**: セキュリティ監査、パフォーマンス改善の起点探し、新規参画メンバーのコード品質チェック

#### 入力スキーマ

```python
class QueryAuditInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    scope: str = Field(
        default="all",
        description="診断範囲: 'all', 'models', 'controllers', 'jobs'"
    )
    model_name: Optional[str] = Field(
        default=None,
        description="特定モデルに関連するクエリに絞る"
    )
    severity_filter: str = Field(
        default="all",
        description="重大度フィルタ: 'all', 'critical', 'warning', 'info'"
    )
```

#### 出力スキーマ

```python
class QueryAuditSummary(BaseModel):
    files_analyzed: int
    total_issues: int
    critical: int
    warning: int
    info: int

class QueryIssue(BaseModel):
    rule_id: str
    severity: SeverityLevel
    file: str
    line: int
    code: str
    message: str
    suggestion: str
    safe_alternative: str | None = None
    impact: str | None = None

class QueryAuditOutput(BaseModel):
    summary: QueryAuditSummary
    issues: list[QueryIssue]
```

#### 出力例

```json
{
  "summary": {
    "files_analyzed": 42,
    "total_issues": 18,
    "critical": 2,
    "warning": 11,
    "info": 5
  },
  "issues": [
    {
      "rule_id": "SEC001",
      "severity": "critical",
      "file": "app/models/user.rb",
      "line": 45,
      "code": "where(\"name LIKE '%#{query}%'\")",
      "message": "SQL インジェクションのリスクがあります。文字列補間で生 SQL を構築しています",
      "suggestion": "where(\"name LIKE ?\", \"%#{query}%\") またはサニタイズメソッドを使用してください",
      "safe_alternative": "where(\"name LIKE ?\", \"%#{ActiveRecord::Base.sanitize_sql_like(query)}%\")"
    },
    {
      "rule_id": "PERF001",
      "severity": "critical",
      "file": "app/models/order.rb",
      "line": 80,
      "code": "Order.where(status: :pending).each do |order|",
      "message": "全件をメモリにロードしてからイテレーションしています。大量レコードでメモリ不足になります",
      "suggestion": "Order.where(status: :pending).find_each do |order|",
      "impact": "10万件のレコードがある場合、find_each は1000件ずつバッチ処理するため、メモリ使用量が約1/100になります"
    },
    {
      "rule_id": "PERF003",
      "severity": "warning",
      "file": "app/controllers/api/v1/users_controller.rb",
      "line": 12,
      "code": "User.active.map(&:email)",
      "message": "全カラムを取得してから Ruby 側で email だけを抽出しています",
      "suggestion": "User.active.pluck(:email)",
      "impact": "pluck は SELECT email FROM users のみを発行し、ActiveRecord オブジェクトを生成しません。メモリと速度の両方が改善します"
    },
    {
      "rule_id": "PERF007",
      "severity": "warning",
      "file": "app/models/user.rb",
      "line": 70,
      "code": "company.users.count >= company.max_users",
      "message": "件数確認に .count を使用しています。毎回 COUNT クエリが発行されます",
      "suggestion": "存在確認なら .exists? を、上限チェックなら .limit(max_users + 1).count を使用するか、カウンタキャッシュの導入を検討してください"
    },
    {
      "rule_id": "DATA001",
      "severity": "warning",
      "file": "app/models/order.rb",
      "line": 40,
      "code": "user.update_columns(sign_in_count: user.orders.count, ...)",
      "message": "update_columns はバリデーションとコールバックをスキップします",
      "suggestion": "意図的にスキップしている場合はコメントでその理由を明記してください。意図的でない場合は update または update! を使用してください"
    }
  ]
}
```

#### ツール定義

```python
@mcp.tool(
    name="rails_lens_query_audit",
    annotations={
        "title": "Query Pattern Audit",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def query_audit(params: QueryAuditInput) -> str:
    '''Ruby ソースコードを静的解析し、ActiveRecord の危険・非効率なクエリパターンを検出する。
    DB への接続は不要。SQL インジェクションリスク、全件取得ループ、不要な SELECT * などを報告する。
    検出された問題には安全で効率的な代替コードの提案も含まれる。
    '''
    ...
```

---

### ツール3: rails_lens_query_preview

#### 解決する課題と利用シーン

- **課題**: ActiveRecord は SQL を隠蔽するため、開発者が自分の書いたコードがどんなクエリを発行しているか意識しにくい。スコープを複数チェーンした結果が想定より複雑な SQL になっている、`includes` と `joins` の違いによる発行クエリ数の変化を知らない、enum のシンボル（`:active`）が整数値（`0`）に変換されることを知らない、`has_many :through` の裏で中間テーブルの JOIN が発生していることに気づかない、`default_scope` や `acts_as_paranoid` による暗黙の WHERE 句を見落とす等のケースで問題になる
- **利用シーン**: 指定したモデルに定義されている全スコープとクエリ関連メソッドについて、予測 SQL を一覧表示する。クエリの最適化やインデックス設計の判断に使用する

#### 入力スキーマ

```python
class QueryPreviewInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    model_name: str = Field(
        ...,
        description="対象モデル名 (例: 'Order')",
        min_length=1,
        max_length=200
    )
    include_associations: bool = Field(
        default=True,
        description="association アクセス時のクエリも含めるか"
    )
    include_chain_examples: bool = Field(
        default=True,
        description="コードベースで実際に使われているスコープチェーンの例も含めるか"
    )
```

#### 出力スキーマ

```python
class EnumResolution(BaseModel):
    column: str
    mapping: dict[str, int]

class ImplicitCondition(BaseModel):
    source: str
    sql_fragment: str
    note: str

class ScopePreview(BaseModel):
    name: str
    ruby_definition: str
    location: FileLocation
    predicted_sql: str
    parameters: list[str] | None = None
    enum_resolution: dict[str, dict[str, int]] | None = None
    index_used: str | None = None
    index_exists: bool
    warning: str | None = None

class ScopeChainExample(BaseModel):
    ruby: str
    found_in: str
    predicted_sql: str
    index_used: str | None = None
    index_exists: bool
    efficiency: str

class AssociationQuery(BaseModel):
    association: str
    type: str
    access_pattern: str
    predicted_sql: str
    index_used: str | None = None
    index_exists: bool

class QueryPreviewOutput(BaseModel):
    model_name: str
    table_name: str
    default_scope: str | None = None
    implicit_conditions: list[ImplicitCondition]
    scopes: list[ScopePreview]
    scope_chains: list[ScopeChainExample]
    association_queries: list[AssociationQuery]
    accuracy: str = "predicted (static analysis)"
```

#### 出力例

```json
{
  "model_name": "Order",
  "table_name": "orders",
  "default_scope": null,
  "implicit_conditions": [
    {
      "source": "acts_as_paranoid (gem)",
      "sql_fragment": "WHERE \"orders\".\"deleted_at\" IS NULL",
      "note": "全クエリに暗黙的に追加される"
    }
  ],
  "scopes": [
    {
      "name": "pending",
      "ruby_definition": "scope :pending, -> { where(status: :pending) }",
      "location": { "file": "app/models/order.rb", "line": 12 },
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE \"orders\".\"status\" = 0",
      "enum_resolution": { "status": { "pending": 0 } },
      "index_used": "index_orders_on_status (predicted)",
      "index_exists": true
    },
    {
      "name": "confirmed",
      "ruby_definition": "scope :confirmed, -> { where(status: :confirmed) }",
      "location": { "file": "app/models/order.rb", "line": 13 },
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE \"orders\".\"status\" = 1",
      "enum_resolution": { "status": { "confirmed": 1 } },
      "index_used": "index_orders_on_status (predicted)",
      "index_exists": true
    },
    {
      "name": "for_user",
      "ruby_definition": "scope :for_user, ->(uid) { where(user_id: uid) }",
      "location": { "file": "app/models/order.rb", "line": 16 },
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE \"orders\".\"user_id\" = $1",
      "parameters": ["uid"],
      "index_used": "index_orders_on_user_id (predicted)",
      "index_exists": true
    },
    {
      "name": "high_value",
      "ruby_definition": "scope :high_value, -> { where(\"total_amount > ?\", 10000) }",
      "location": { "file": "app/models/order.rb", "line": 17 },
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE (total_amount > 10000)",
      "index_used": null,
      "index_exists": false,
      "warning": "total_amount にインデックスがありません。データ量が多い場合はフルテーブルスキャンになります"
    },
    {
      "name": "recent",
      "ruby_definition": "scope :recent, -> { order(created_at: :desc).limit(10) }",
      "location": { "file": "app/models/order.rb", "line": 15 },
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" ORDER BY \"orders\".\"created_at\" DESC LIMIT 10",
      "index_used": null,
      "index_exists": false,
      "warning": "created_at に降順インデックスがありません。ORDER BY + LIMIT の効率化にインデックスが有効です"
    }
  ],
  "scope_chains": [
    {
      "ruby": "Order.pending.for_user(user_id).recent",
      "found_in": "app/controllers/api/v1/orders_controller.rb:12",
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE \"orders\".\"status\" = 0 AND \"orders\".\"user_id\" = $1 ORDER BY \"orders\".\"created_at\" DESC LIMIT 10",
      "index_used": "index_orders_on_user_id_and_status (predicted)",
      "index_exists": true,
      "efficiency": "good"
    }
  ],
  "association_queries": [
    {
      "association": "order_items",
      "type": "has_many",
      "access_pattern": "order.order_items",
      "predicted_sql": "SELECT \"order_items\".* FROM \"order_items\" WHERE \"order_items\".\"order_id\" = $1",
      "index_used": "index_order_items_on_order_id (predicted)",
      "index_exists": true
    },
    {
      "association": "products (through: order_items)",
      "type": "has_many :through",
      "access_pattern": "order.products",
      "predicted_sql": "SELECT \"products\".* FROM \"products\" INNER JOIN \"order_items\" ON \"products\".\"id\" = \"order_items\".\"product_id\" WHERE \"order_items\".\"order_id\" = $1",
      "index_used": "index_order_items_on_order_id + index_order_items_on_product_id (predicted)",
      "index_exists": true
    }
  ],
  "accuracy": "predicted (static analysis)"
}
```

#### ツール定義

```python
@mcp.tool(
    name="rails_lens_query_preview",
    annotations={
        "title": "ActiveRecord Query SQL Preview",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def query_preview(params: QueryPreviewInput) -> str:
    '''ActiveRecord のスコープや関連アクセスが発行する SQL を予測して表示する。
    DB への接続は不要。enum の値解決、暗黙の条件（default_scope 等）の検出、
    インデックスの適用可否判定も含む。
    クエリの最適化やインデックス設計の判断に使用する。
    '''
    ...
```

---

### ツール4: rails_lens_query_preview_snippet

#### 解決する課題と利用シーン

- **課題**: Ruby のコードスニペット（ActiveRecord のメソッドチェーン）がどのような SQL を発行するか、実行前に把握したい。特に `includes` と `joins` の違いによるクエリ数の変化、enum シンボルの整数変換、メソッドチェーンの各部分が SQL のどの句に対応するかを理解したい
- **利用シーン**: コードレビュー時の SQL 確認、ActiveRecord クエリの最適化検討、学習目的での SQL 理解

#### 入力スキーマ

```python
class QueryPreviewSnippetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(
        ...,
        description="ActiveRecord のメソッドチェーン (例: 'User.where(status: :active).order(:name)')",
        min_length=1,
        max_length=2000
    )
    model_name: str | None = Field(
        default=None,
        description="モデル名。code から推定できない場合に指定する"
    )
```

#### 出力スキーマ

```python
class MethodBreakdown(BaseModel):
    method: str
    sql_fragment: str

class IndexApplicability(BaseModel):
    name: str
    applicable: bool
    reason: str | None = None

class AlternativeQuery(BaseModel):
    method: str
    predicted_sql: str
    difference: str

class PredictedQuery(BaseModel):
    query_number: int
    purpose: str
    predicted_sql: str
    enum_resolutions: dict[str, dict[str, int]] | None = None
    note: str | None = None
    method_breakdown: list[MethodBreakdown]
    indexes_used: list[IndexApplicability] | None = None
    alternative: AlternativeQuery | None = None

class QueryPreviewSnippetOutput(BaseModel):
    input_code: str
    queries: list[PredictedQuery]
    total_queries: int
    select_star_warning: str | None = None
    recommendations: list[str]
    accuracy: str = "predicted (static analysis)"
```

#### 出力例

```json
{
  "input_code": "User.where(status: :active).includes(:orders).where(company_id: 5).order(:name).limit(10)",
  "queries": [
    {
      "query_number": 1,
      "purpose": "メインクエリ（Users 取得）",
      "predicted_sql": "SELECT \"users\".* FROM \"users\" WHERE \"users\".\"status\" = 0 AND \"users\".\"company_id\" = 5 ORDER BY \"users\".\"name\" ASC LIMIT 10",
      "enum_resolutions": { "status": { "active": 0 } },
      "method_breakdown": [
        { "method": ".where(status: :active)", "sql_fragment": "WHERE \"users\".\"status\" = 0" },
        { "method": ".where(company_id: 5)", "sql_fragment": "AND \"users\".\"company_id\" = 5" },
        { "method": ".order(:name)", "sql_fragment": "ORDER BY \"users\".\"name\" ASC" },
        { "method": ".limit(10)", "sql_fragment": "LIMIT 10" }
      ],
      "indexes_used": [
        { "name": "index_users_on_email_and_company_id", "applicable": false, "reason": "先頭カラムが email（company_id ではない）のため、この WHERE には使えない" },
        { "name": "index_users_on_company_id", "applicable": true }
      ]
    },
    {
      "query_number": 2,
      "purpose": "eager loading（Orders 一括取得）",
      "predicted_sql": "SELECT \"orders\".* FROM \"orders\" WHERE \"orders\".\"user_id\" IN ($1, $2, $3, ...)",
      "note": "includes(:orders) により、取得した User の ID リストで Orders を一括取得する。N+1 を防止している",
      "method_breakdown": [
        { "method": ".includes(:orders)", "sql_fragment": "WHERE \"orders\".\"user_id\" IN (...)" }
      ],
      "alternative": {
        "method": ".joins(:orders) を使った場合",
        "predicted_sql": "SELECT \"users\".* FROM \"users\" INNER JOIN \"orders\" ON \"orders\".\"user_id\" = \"users\".\"id\" WHERE \"users\".\"status\" = 0 AND \"users\".\"company_id\" = 5 ORDER BY \"users\".\"name\" ASC LIMIT 10",
        "difference": "includes は 2 クエリ発行（Users + Orders 別々）。joins は 1 クエリ（INNER JOIN）だが、Orders のデータは取得しない。関連データを使うなら includes、フィルタ目的なら joins が適切"
      }
    }
  ],
  "total_queries": 2,
  "select_star_warning": "SELECT * を使用しています。必要なカラムが限定的なら .select(:id, :name, :email) で指定するとパフォーマンスが向上します",
  "recommendations": [
    "company_id 単独のインデックスは存在しますが、(company_id, status, name) の複合インデックスがあると、この特定のクエリは WHERE + ORDER BY をインデックスだけで解決できます"
  ],
  "accuracy": "predicted (static analysis)"
}
```

#### ツール定義

```python
@mcp.tool(
    name="rails_lens_query_preview_snippet",
    annotations={
        "title": "ActiveRecord Code to SQL Converter",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def query_preview_snippet(params: QueryPreviewSnippetInput) -> str:
    '''ActiveRecord のメソッドチェーンのコードスニペットを入力すると、
    発行される SQL を予測して返す。各メソッドが SQL のどの句に対応するかの
    分解表示も含む。includes と joins の違いによるクエリ数の変化も説明する。
    '''
    ...
```

---

## 4. 検出ルール一覧

### 共通型定義

```python
from enum import Enum

class SeverityLevel(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
```

### 4.1 スキーマ診断ルール（rails_lens_schema_audit）

#### インデックス関連

| ルールID | 検出内容 | 重大度 | 説明 |
|---|---|---|---|
| `IDX001` | 外部キーにインデックスがない | critical | `belongs_to` の外部キーカラムにインデックスが存在しない。JOIN や WHERE でフルテーブルスキャンが発生する |
| `IDX002` | ポリモーフィック関連のインデックス不足 | critical | `commentable_type` + `commentable_id` のペアに複合インデックスがない。ポリモーフィック関連の検索が極めて遅くなる |
| `IDX003` | 複合インデックスの順序が非効率 | warning | 複合インデックスの先頭カラムがカーディナリティの低いカラム（例: `boolean` の `active` フラグ）になっている |
| `IDX004` | ユニーク制約がインデックスなし | warning | `validates :email, uniqueness: true` があるのに `email` カラムにユニークインデックスがない。DB レベルでの一意性保証がなく、競合状態でデータが重複する可能性がある |
| `IDX005` | 不要な重複インデックス | info | `index [:company_id]` と `index [:company_id, :email]` が両方存在する。前者は後者の左端プレフィックスなので冗長 |
| `IDX006` | STI の type カラムにインデックスがない | warning | STI（単一テーブル継承）を使用しているが、`type` カラムにインデックスがない |

#### カラム・型関連

| ルールID | 検出内容 | 重大度 | 説明 |
|---|---|---|---|
| `COL001` | 外部キーと参照先の型不一致 | warning | 外部キーが `integer` だが参照先の主キーが `bigint`（またはその逆）。JOIN 時に暗黙の型変換が発生しパフォーマンスが劣化する |
| `COL002` | string カラムに limit 未設定 | info | `varchar` カラムに `limit` が設定されていない。DB によっては不必要に大きな領域を確保する |
| `COL003` | decimal カラムの precision/scale 未設定 | warning | 金額等に使う `decimal` カラムに `precision` と `scale` が明示されていない。意図しない丸めが発生する可能性 |
| `COL004` | NOT NULL 制約なしの必須カラム | warning | `validates :name, presence: true` があるが、DB レベルでは `NULL` が許容されている。アプリを介さない直接の DB 操作で `NULL` が入る可能性がある |

#### テーブル設計関連

| ルールID | 検出内容 | 重大度 | 説明 |
|---|---|---|---|
| `TBL001` | 外部キー制約なし | info | `belongs_to` に対応する DB レベルの外部キー制約が未定義。参照整合性が保証されない |
| `TBL002` | カラム数過多 | warning | テーブルのカラム数が 30 を超えている。テーブル分割やカラムの整理を検討すべき |
| `TBL003` | created_at / updated_at 欠如 | info | タイムスタンプカラムがないテーブル。デバッグやデータ追跡が困難になる |

### 4.2 クエリ診断ルール（rails_lens_query_audit）

#### セキュリティ

| ルールID | 検出内容 | 重大度 | 検出パターン（grep/正規表現） |
|---|---|---|---|
| `SEC001` | SQL インジェクションリスク | critical | `where("...#{...}...")` — 文字列補間を使った生 SQL |
| `SEC002` | 安全でない order 指定 | critical | `order(params[:sort])` — ユーザー入力をそのまま ORDER BY に渡している |
| `SEC003` | 安全でない pluck/select | warning | `select(params[:fields])` — ユーザー入力をそのまま SELECT に渡している |

#### パフォーマンス

| ルールID | 検出内容 | 重大度 | 検出パターン |
|---|---|---|---|
| `PERF001` | 全件取得してのイテレーション | critical | `Model.all.each` / `Model.where(...).each` — `find_each` / `in_batches` を使うべき |
| `PERF002` | SELECT * の暗黙使用 | warning | `Model.where(...)` で `.select()` も `.pluck()` も使わずに結果を使用している |
| `PERF003` | map でのカラム抽出 | warning | `.map(&:column_name)` / `.map { \|r\| r.column }` — `.pluck(:column_name)` の方が効率的 |
| `PERF004` | count の繰り返し呼び出し | warning | 同一スコープに対する `.count` が複数箇所で呼ばれている。カウンタキャッシュの導入候補 |
| `PERF005` | N+1 クエリの兆候 | warning | ループ内での関連アクセス（`items.each { \|i\| i.product.name }`）で `includes` / `preload` がない |
| `PERF006` | 不要な reload | info | `.reload` の呼び出し。本当に必要か確認すべき |
| `PERF007` | 非効率な存在確認 | warning | `.count > 0` / `.length > 0` / `.present?`（コレクション）— `.exists?` の方が効率的（`SELECT 1 LIMIT 1` vs `SELECT COUNT(*)` or 全件ロード） |
| `PERF008` | update_all / delete_all の未使用 | info | ループ内で個別に `update` / `destroy` している箇所。バルク操作で効率化できる可能性 |

#### データ整合性

| ルールID | 検出内容 | 重大度 | 検出パターン |
|---|---|---|---|
| `DATA001` | update_columns / update_column の使用 | warning | `update_columns` はバリデーションとコールバックをスキップする。意図的でない場合はデータ不整合の原因になる |
| `DATA002` | delete vs destroy の混用 | warning | `delete` / `delete_all` は `dependent: :destroy` のコールバックを発火しない |
| `DATA003` | トランザクションなしの複数モデル更新 | warning | 1 つのメソッド内で複数モデルの `save` / `update` が `transaction` ブロックなしで行われている |

---

## 5. ActiveRecord メソッド → SQL 変換ルール

`query_preview` / `query_preview_snippet` が内部に持つ変換ルールテーブル:

| ActiveRecord メソッド | SQL 句 | 備考 |
|---|---|---|
| `.where(col: val)` | `WHERE "table"."col" = val` | Hash 形式 |
| `.where("col > ?", val)` | `WHERE (col > val)` | 文字列形式（プレースホルダ） |
| `.where.not(col: val)` | `WHERE "table"."col" != val` | |
| `.or(other_scope)` | `OR (...)` | |
| `.order(:col)` | `ORDER BY "table"."col" ASC` | |
| `.order(col: :desc)` | `ORDER BY "table"."col" DESC` | |
| `.limit(n)` | `LIMIT n` | |
| `.offset(n)` | `OFFSET n` | |
| `.select(:col1, :col2)` | `SELECT "table"."col1", "table"."col2"` | |
| `.pluck(:col)` | `SELECT "table"."col"` | ActiveRecord オブジェクトを作らない |
| `.count` | `SELECT COUNT(*)` | |
| `.sum(:col)` | `SELECT SUM("table"."col")` | |
| `.average(:col)` | `SELECT AVG("table"."col")` | |
| `.minimum(:col)` / `.maximum(:col)` | `SELECT MIN/MAX("table"."col")` | |
| `.exists?` | `SELECT 1 ... LIMIT 1` | |
| `.distinct` | `SELECT DISTINCT ...` | |
| `.group(:col)` | `GROUP BY "table"."col"` | |
| `.having(...)` | `HAVING ...` | |
| `.joins(:assoc)` | `INNER JOIN ...` | association の外部キーから JOIN 条件を解決 |
| `.left_joins(:assoc)` | `LEFT OUTER JOIN ...` | |
| `.includes(:assoc)` | 別クエリ: `WHERE ... IN (...)` | preload 戦略（デフォルト） |
| `.eager_load(:assoc)` | `LEFT OUTER JOIN ...` | 1クエリで eager load |
| `.preload(:assoc)` | 別クエリ: `WHERE ... IN (...)` | 常に別クエリ |
| `.find(id)` | `WHERE "table"."id" = id LIMIT 1` | |
| `.find_by(col: val)` | `WHERE "table"."col" = val LIMIT 1` | |
| `.first` / `.last` | `ORDER BY "id" ASC/DESC LIMIT 1` | |
| `.destroy_all` | `SELECT` → 各レコードに `DELETE`（コールバックあり） | |
| `.delete_all` | `DELETE FROM "table" WHERE ...`（コールバックなし） | |
| `.update_all(col: val)` | `UPDATE "table" SET "col" = val WHERE ...` | |

### enum の値解決

- `where(status: :active)` → `introspect_model` の enum 定義から `:active` を `0` に変換
- 変換元情報を `enum_resolutions` として出力に含める

### 暗黙の条件

- `default_scope` がある場合、全クエリに暗黙的に追加される条件として表示
- `acts_as_paranoid` / `discard` 等の論理削除 Gem がある場合、`WHERE deleted_at IS NULL` が暗黙追加されることを検出して表示

### インデックスの適用可否判定

- 予測 SQL の WHERE 句と ORDER BY 句に対して、`introspect_model` のスキーマ情報（indexes）からインデックスが使えるかを推定
- 複合インデックスの左端プレフィックスルールを考慮する
- 使えるインデックスがない場合は `warning` を出す

---

## 6. 実装方針

### 6.1 schema_audit の実装

#### schema.rb のパース戦略

`db/schema.rb` は Ruby の DSL だが、パターンが限定的（`create_table`, `add_index`, `add_foreign_key` 等）であるため、正規表現ベースの解析で十分な精度が出る。`rails runner` を経由しないため高速。

主要な正規表現パターン:

```python
# テーブル定義の検出
TABLE_PATTERN = re.compile(
    r'create_table\s+"(\w+)".*?end',
    re.DOTALL
)

# カラム定義の検出
COLUMN_PATTERN = re.compile(
    r't\.(\w+)\s+"(\w+)"(?:,\s*(.+?))?$',
    re.MULTILINE
)

# インデックス定義の検出
INDEX_PATTERN = re.compile(
    r'add_index\s+"(\w+)",\s*(\[.*?\]|"(\w+)")',
)

# 外部キー定義の検出
FOREIGN_KEY_PATTERN = re.compile(
    r'add_foreign_key\s+"(\w+)",\s*"(\w+)"',
)
```

#### ルール別の実装方針

| ルールID | 実装方法 |
|---|---|
| `IDX001` | `_id` で終わるカラムを抽出し、対応するインデックスが存在するか確認 |
| `IDX002` | `_type` + `_id` のペア（ポリモーフィックカラム）を検出し、複合インデックスの有無を確認 |
| `IDX003` | 複合インデックスの先頭カラムの型が `boolean` かどうかを確認 |
| `IDX004` | `introspect_model` のバリデーション情報から `uniqueness` バリデーションを取得し、対応するユニークインデックスの有無を確認 |
| `IDX005` | 全インデックスの左端プレフィックスを比較し、冗長なものを検出 |
| `IDX006` | `type` カラムを持つテーブル（STI テーブル）のインデックスを確認 |
| `COL001` | 外部キーカラムの型と参照先テーブルの主キーの型を比較 |
| `COL004` | `introspect_model` のバリデーション情報から `presence` バリデーションを取得し、DB の `null` 制約と突き合わせ |
| `TBL002` | テーブルごとのカラム数をカウントし、閾値（30）を超えるものを報告 |

#### キャッシュとの連携

- `schema.rb` の mtime をキャッシュキーとして使用
- `introspect_model` のキャッシュからバリデーション情報を取得（`IDX004`, `COL004` の検出に必要）
- モデルファイルの mtime が変わった場合もキャッシュを無効化する（バリデーション追加でルール適用結果が変わるため）

### 6.2 query_audit の実装

#### 静的解析アプローチ

全ルールを grep + 正規表現で実装する。AST パースは理想的だが、grep で十分な精度が出るルールが大半である。

主要な正規表現パターン:

```python
# SEC001: SQL インジェクション（文字列補間を使った where）
SEC001_PATTERN = re.compile(
    r'\.where\(\s*"[^"]*#\{.*?\}[^"]*"'
)

# SEC002: 安全でない order（params を直接渡す）
SEC002_PATTERN = re.compile(
    r'\.order\(\s*params\['
)

# PERF001: 全件取得してのイテレーション
PERF001_PATTERN = re.compile(
    r'\.(all|where\(.*?\))\.each\b'
)

# PERF003: map でのカラム抽出
PERF003_PATTERN = re.compile(
    r'\.map\(\s*&:(\w+)\s*\)'
)

# PERF007: 非効率な存在確認
PERF007_PATTERN = re.compile(
    r'\.(count|length|size)\s*>\s*0|\.(count|length|size)\s*>=\s*1'
)

# DATA001: update_columns の使用
DATA001_PATTERN = re.compile(
    r'\.update_columns?\('
)

# DATA003: トランザクションなしの複数モデル更新
# メソッド内に .save / .update が複数回出現し、transaction ブロックがないことを検出
DATA003_PATTERN = re.compile(
    r'def\s+\w+.*?end',
    re.DOTALL
)
```

#### スコープによるファイル対象の絞り込み

```python
SCOPE_DIRS = {
    "all": ["app/models", "app/controllers", "app/jobs", "app/services", "lib"],
    "models": ["app/models"],
    "controllers": ["app/controllers"],
    "jobs": ["app/jobs"],
}
```

#### 誤検知（false positive）の抑制

- `# rails-lens:disable RULE_ID` コメントで個別のルールを無効化できる仕組みを導入する
- `PERF001`: `.find_each` や `.in_batches` が同一メソッド内にある場合はスキップ
- `DATA001`（`update_columns`）は意図的に使っているケースが多いため、デフォルトでは `info` にダウングレードするオプションも検討
- `PERF005`（N+1 兆候）の検出では、同一ファイル内に `includes` / `preload` / `eager_load` がある場合は重大度を下げる

#### 既存解析基盤の再利用

`find_references` の内部実装（`analyzers/grep_search.py`）を再利用する。具体的には:

- `grep_search.py` のファイル検索・パターンマッチング機能を共通ユーティリティとして利用
- `rg`（ripgrep）が利用可能な環境では `rg` を使用し、フォールバックとして Python の `re` モジュールを使用

---

### 6.3 query_preview / query_preview_snippet の実装

#### モード1（スコープ一覧）: ハイブリッド

- **スコープ定義の取得**: `introspect_model` のキャッシュから `scopes` セクションを取得
- **スコープの Ruby コード取得**: 静的解析（`find_references` の grep 基盤）でスコープの定義本体を抽出
- **SQL 予測**: Python 側で変換ルールテーブル（§5）を使って機械的に変換
- **enum 解決**: `introspect_model` の `enums` セクションから値マッピングを取得
- **インデックス判定**: `introspect_model` の `schema.indexes` から判定
- **スコープチェーンの検出**: コードベースを grep して、対象モデルのスコープが実際にチェーンされている箇所を見つける

#### モード2（コードスニペット）: 静的解析のみ

- 入力の Ruby コードを正規表現でトークン化し、メソッドチェーンを分解
- 各メソッドを変換ルールテーブル（§5）で SQL に変換
- モデル名からテーブル名を推定（`User` → `users`、`OrderItem` → `order_items`）
- `includes` / `joins` / `eager_load` は association 情報（`introspect_model` のキャッシュ）から JOIN 条件を解決

#### ランタイム精度向上オプション

`rails runner` で `Model.scope_name.to_sql` を実行すれば100%正確な SQL が得られる。静的解析で推測した SQL と突き合わせて精度を検証するモードも将来的に提供可能。ただし初期実装は静的解析のみ。

#### メソッドチェーン分解の正規表現

```python
# メソッドチェーンの分解（トークン化）
METHOD_CHAIN_PATTERN = re.compile(
    r'\.(where|order|limit|offset|select|pluck|count|sum|average|'
    r'minimum|maximum|exists\?|distinct|group|having|'
    r'joins|left_joins|includes|eager_load|preload|'
    r'find|find_by|first|last|'
    r'destroy_all|delete_all|update_all|not)\b'
    r'(\(.*?\))?',
    re.DOTALL
)

# モデル名の抽出（チェーンの先頭）
MODEL_NAME_PATTERN = re.compile(
    r'^([A-Z][A-Za-z0-9]*(?:::[A-Z][A-Za-z0-9]*)*)\.'
)

# スコープ定義の抽出
SCOPE_DEFINITION_PATTERN = re.compile(
    r'scope\s+:(\w+)\s*,\s*->\s*(?:\((.*?)\))?\s*\{(.*?)\}',
    re.DOTALL
)
```

#### キャッシュ戦略

- モード1の結果はモデル単位でキャッシュする
- キャッシュキー: モデルファイルの mtime + `db/schema.rb` の mtime
- スコープチェーン検索（`include_chain_examples=True`）の結果は、スコープ内の Ruby ファイルの最新 mtime をキャッシュキーとする
- モード2はキャッシュしない（入力が任意のコードであるため）

---

## 7. ダッシュボードページ設計

### 7.1 SQL 診断ページ

| 項目 | 内容 |
|---|---|
| **URL** | `GET /sql` |
| **目的** | スキーマ診断、クエリパターン診断、クエリプレビューの結果を統合表示する |
| **使用 MCP ツール** | `rails_lens_schema_audit` + `rails_lens_query_audit` + `rails_lens_query_preview` + `rails_lens_query_preview_snippet` |

### 7.2 表示内容

#### サマリーカード（ページ上部）

```
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Critical: 5    │ │  Warning: 11    │ │  Info: 8        │
│                 │ │                 │ │                 │
│ スキーマ: 3     │ │ スキーマ: 4     │ │ スキーマ: 2     │
│ クエリ:   2     │ │ クエリ:   7     │ │ クエリ:   6     │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

重大度ごとに背景色を変える（PicoCSS のカラーユーティリティを使用）:
- Critical: 赤系 (`--pico-color-red-*`)
- Warning: 黄系 (`--pico-color-yellow-*`)
- Info: グレー系 (`--pico-color-grey-*`)

#### タブ切り替え

- **全て**: スキーマ + クエリの全問題を重大度順に表示
- **スキーマ診断**: `schema_audit` の結果のみ
- **クエリ診断**: `query_audit` の結果のみ
- **クエリプレビュー**: `query_preview` / `query_preview_snippet` の結果（モデル選択 + コードスニペット入力）

各タブ内ではさらに重大度フィルタ（critical / warning / info / all）で絞り込み可能（クエリプレビュータブでは重大度フィルタは非表示）。タブとフィルタはクエリパラメータ `?tab=schema&severity=critical` で URL に反映し、ブラウザの戻るボタンで前の状態に戻れるようにする。

#### 問題カード

各問題を PicoCSS の `<article>` カードとして表示:

```html
<article>
  <header>
    <span class="badge critical">CRITICAL</span>
    <span class="rule-id">SEC001</span>
    <strong>SQL インジェクションリスク</strong>
  </header>
  <p>
    <code>app/models/user.rb:45</code><br>
    <pre>where("name LIKE '%#{query}%'")</pre>
  </p>
  <footer>
    <strong>修正案:</strong>
    <pre>where("name LIKE ?", "%#{ActiveRecord::Base.sanitize_sql_like(query)}%")</pre>
  </footer>
</article>
```

#### テーブル別サマリー（スキーマ診断セクション）

各テーブルの「健全度」を一覧表示:

| テーブル名 | カラム数 | インデックス数 | 外部キー制約 | 問題数 | 健全度 |
|---|---|---|---|---|---|
| users | 17 | 5 | 1 | 2 | Warning |
| orders | 13 | 4 | 2 | 3 | Critical |
| products | 5 | 1 | 0 | 1 | Warning |
| profiles | 4 | 1 | 1 | 0 | OK |

テーブル名クリックで既存の `/models/{model_name}` に遷移。

健全度の判定基準:
- Critical（赤）: critical な問題が 1 つ以上ある
- Warning（黄）: warning な問題が 1 つ以上ある（critical なし）
- OK（緑）: 問題なし、または info のみ

#### マイグレーション生成ヒント（スキーマ診断セクション）

スキーマ診断で検出された問題に対して、修正用のマイグレーションコマンドをまとめて表示する「一括コピー」セクション:

```
# 以下のコマンドでインデックスを追加できます:
rails generate migration AddMissingIndexes

# マイグレーション内容:
add_index :orders, :shipping_address_id
add_index :comments, [:commentable_type, :commentable_id]
add_index :users, :type
change_column_null :users, :name, false
```

「コピー」ボタンで一括コピーできるようにする（素の JavaScript の `navigator.clipboard.writeText`）。

### 7.3 内部 API エンドポイント

| メソッド | パス | 処理内容 | レスポンス形式 |
|---|---|---|---|
| `GET` | `/sql` | SQL 診断結果の統合表示 | HTML (Jinja2) |

```python
@app.get("/sql", response_class=HTMLResponse)
async def sql_diagnostics(
    request: Request,
    tab: str = "all",          # "all", "schema", "query", "preview"
    severity: str = "all",     # "all", "critical", "warning", "info"
    table: str | None = None,  # 特定テーブルに絞る
    model: str | None = None,  # クエリプレビュー用: 対象モデル名
    code: str | None = None,   # クエリプレビュー用: コードスニペット入力
):
    schema_result = await _call_schema_audit(
        scope=table or "all",
        severity_filter=severity,
    )
    query_result = await _call_query_audit(
        scope="all",
        severity_filter=severity,
    )

    # 統合してソート（critical -> warning -> info）
    all_issues = []
    if tab in ("all", "schema"):
        all_issues.extend(
            {"source": "schema", **issue} for issue in schema_result.issues
        )
    if tab in ("all", "query"):
        all_issues.extend(
            {"source": "query", **issue} for issue in query_result.issues
        )

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    all_issues.sort(key=lambda x: severity_order.get(x["severity"], 3))

    # マイグレーションヒントの生成（スキーマ診断の修正コマンドを集約）
    migration_hints = [
        issue["suggestion"]
        for issue in schema_result.issues
        if issue.get("suggestion") and issue["severity"] in ("critical", "warning")
    ]

    # クエリプレビュータブ
    preview_result = None
    snippet_result = None
    if tab == "preview" and model:
        preview_result = await _call_query_preview(model_name=model)
        if code:
            snippet_result = await _call_query_preview_snippet(
                code=code, model_name=model
            )

    models_list = await _call_list_models()

    return templates.TemplateResponse("sql.html", {
        "request": request,
        "tab": tab,
        "severity": severity,
        "all_issues": all_issues,
        "schema_summary": schema_result.summary,
        "query_summary": query_result.summary,
        "migration_hints": migration_hints,
        "models": models_list.models,
        "current_model": model,
        "preview_result": preview_result,
        "snippet_result": snippet_result,
        "code": code,
    })
```

### 7.4 ナビゲーションへの追加

既存のナビゲーションに「SQL診断」リンクを追加する:

```html
<nav>
    <ul>
        <li><a href="/">ダッシュボード</a></li>
        <li><a href="/models">モデル</a></li>
        <li><a href="/er">ER図</a></li>
        <li><a href="/screens">画面台帳</a></li>
        <li><a href="/sql">SQL診断</a></li>   {# 追加 #}
        <li><a href="/gems">Gem</a></li>
        <li><a href="/cache">キャッシュ</a></li>
    </ul>
</nav>
```

### 7.5 テンプレート構造

```html
{% extends "base.html" %}
{% block title %}SQL診断{% endblock %}
{% block content %}
<h1>SQL診断</h1>

{# サマリーカード #}
<div class="grid">
  <article data-severity="critical">
    <strong>Critical: {{ schema_summary.critical + query_summary.critical }}</strong>
    <small>スキーマ: {{ schema_summary.critical }} / クエリ: {{ query_summary.critical }}</small>
  </article>
  <article data-severity="warning">
    <strong>Warning: {{ schema_summary.warning + query_summary.warning }}</strong>
    <small>スキーマ: {{ schema_summary.warning }} / クエリ: {{ query_summary.warning }}</small>
  </article>
  <article data-severity="info">
    <strong>Info: {{ schema_summary.info + query_summary.info }}</strong>
    <small>スキーマ: {{ schema_summary.info }} / クエリ: {{ query_summary.info }}</small>
  </article>
</div>

{# タブ切り替え #}
<nav>
  <ul>
    <li><a href="?tab=all&severity={{ severity }}" {% if tab == 'all' %}aria-current="page"{% endif %}>全て</a></li>
    <li><a href="?tab=schema&severity={{ severity }}" {% if tab == 'schema' %}aria-current="page"{% endif %}>スキーマ診断</a></li>
    <li><a href="?tab=query&severity={{ severity }}" {% if tab == 'query' %}aria-current="page"{% endif %}>クエリ診断</a></li>
    <li><a href="?tab=preview" {% if tab == 'preview' %}aria-current="page"{% endif %}>クエリプレビュー</a></li>
  </ul>
</nav>

{# 重大度フィルタ（クエリプレビュータブでは非表示） #}
{% if tab != 'preview' %}
<fieldset role="group">
  <a href="?tab={{ tab }}&severity=all" role="button" {% if severity == 'all' %}class="contrast"{% else %}class="outline"{% endif %}>全て</a>
  <a href="?tab={{ tab }}&severity=critical" role="button" {% if severity == 'critical' %}class="contrast"{% else %}class="outline"{% endif %}>Critical</a>
  <a href="?tab={{ tab }}&severity=warning" role="button" {% if severity == 'warning' %}class="contrast"{% else %}class="outline"{% endif %}>Warning</a>
  <a href="?tab={{ tab }}&severity=info" role="button" {% if severity == 'info' %}class="contrast"{% else %}class="outline"{% endif %}>Info</a>
</fieldset>
{% endif %}

{# クエリプレビュータブ #}
{% if tab == 'preview' %}

{# モデル選択セクション #}
<form method="get" action="/sql">
  <input type="hidden" name="tab" value="preview">
  <select name="model" required>
    <option value="">モデルを選択...</option>
    {% for m in models %}
    <option value="{{ m.name }}" {% if m.name == current_model %}selected{% endif %}>
      {{ m.name }}
    </option>
    {% endfor %}
  </select>
  <button type="submit">プレビュー</button>
</form>

{% if preview_result %}

{# 暗黙の条件警告 #}
{% if preview_result.implicit_conditions %}
<article aria-label="warning">
  <header><strong>⚠️ このモデルには暗黙の条件があります</strong></header>
  {% for cond in preview_result.implicit_conditions %}
  <p>
    <code>{{ cond.sql_fragment }}</code><br>
    <small>{{ cond.source }} 由来。{{ cond.note }}</small>
  </p>
  {% endfor %}
</article>
{% endif %}

{# 精度に関する注記 #}
<p><small>この SQL は静的解析による予測です。実際のクエリは DB アダプタやバージョンによって異なる場合があります。</small></p>

{# スコープ一覧テーブル #}
<h3>スコープ一覧</h3>
<table>
  <thead>
    <tr>
      <th>スコープ名</th>
      <th>Ruby 定義</th>
      <th>予測 SQL</th>
      <th>インデックス</th>
      <th>状態</th>
    </tr>
  </thead>
  <tbody>
    {% for scope in preview_result.scopes %}
    <tr {% if not scope.index_exists and scope.index_used is none %}class="warning-row"{% endif %}>
      <td><code>{{ scope.name }}</code></td>
      <td><code>{{ scope.ruby_definition }}</code></td>
      <td><pre>{{ scope.predicted_sql }}</pre></td>
      <td>
        {% if scope.index_exists %}
        ✅ <code>{{ scope.index_used }}</code>
        {% else %}
        ❌ なし
        {% endif %}
      </td>
      <td>
        {% if scope.warning %}🟡{% else %}🟢{% endif %}
      </td>
    </tr>
    {% if scope.warning %}
    <tr>
      <td colspan="5"><small>⚠️ {{ scope.warning }}</small></td>
    </tr>
    {% endif %}
    {% endfor %}
  </tbody>
</table>

{# association クエリセクション #}
{% if preview_result.association_queries %}
<h3>Association クエリ</h3>
{% for aq in preview_result.association_queries %}
<article>
  <header>
    <strong>{{ aq.access_pattern }}</strong>
    <small>({{ aq.type }})</small>
  </header>
  <pre>{{ aq.predicted_sql }}</pre>
  <footer>
    {% if aq.index_exists %}
    📌 <code>{{ aq.index_used }}</code> ✅
    {% else %}
    📌 インデックスなし ❌
    {% endif %}
  </footer>
</article>
{% endfor %}
{% endif %}

{# スコープチェーン例 #}
{% if preview_result.scope_chains %}
<h3>スコープチェーンの使用例（コードから検出）</h3>
{% for chain in preview_result.scope_chains %}
<article>
  <header>
    <code>{{ chain.ruby }}</code>
    <small>{{ chain.found_in }}</small>
  </header>
  <pre>{{ chain.predicted_sql }}</pre>
  <footer>
    効率: {{ chain.efficiency }}
    {% if chain.index_exists %} / 📌 <code>{{ chain.index_used }}</code> ✅{% endif %}
  </footer>
</article>
{% endfor %}
{% endif %}

{# コードスニペット入力フォーム #}
<details {% if snippet_result %}open{% endif %}>
  <summary>カスタムクエリを入力して SQL を予測する</summary>
  <form method="get" action="/sql">
    <input type="hidden" name="tab" value="preview">
    <input type="hidden" name="model" value="{{ current_model }}">
    <textarea name="code" rows="3" placeholder="例: {{ current_model }}.where(status: :active).includes(:order_items)">{{ code or '' }}</textarea>
    <button type="submit">SQL を予測</button>
  </form>
</details>

{# コードスニペットの予測結果 #}
{% if snippet_result %}
<h3>コードスニペット予測結果</h3>
<p><strong>入力:</strong> <code>{{ snippet_result.input_code }}</code></p>
<p><small>発行クエリ数: {{ snippet_result.total_queries }}</small></p>

{% for q in snippet_result.queries %}
<article>
  <header>
    <strong>クエリ {{ q.query_number }}/{{ snippet_result.total_queries }}: {{ q.purpose }}</strong>
  </header>
  <pre>{{ q.predicted_sql }}</pre>

  {# メソッド分解表示 #}
  <table>
    <thead><tr><th>メソッド</th><th>SQL 句</th></tr></thead>
    <tbody>
    {% for mb in q.method_breakdown %}
    <tr>
      <td><code>{{ mb.method }}</code></td>
      <td><code>{{ mb.sql_fragment }}</code></td>
    </tr>
    {% endfor %}
    </tbody>
  </table>

  {% if q.note %}
  <p><small>📝 {{ q.note }}</small></p>
  {% endif %}

  {% if q.alternative %}
  <details>
    <summary>{{ q.alternative.method }}</summary>
    <pre>{{ q.alternative.predicted_sql }}</pre>
    <p><small>{{ q.alternative.difference }}</small></p>
  </details>
  {% endif %}
</article>
{% endfor %}

{% if snippet_result.select_star_warning %}
<p>⚠️ {{ snippet_result.select_star_warning }}</p>
{% endif %}

{% if snippet_result.recommendations %}
<h4>推奨事項</h4>
<ul>
{% for rec in snippet_result.recommendations %}
  <li>{{ rec }}</li>
{% endfor %}
</ul>
{% endif %}
{% endif %}

{% endif %}{# end if preview_result #}

{% else %}{# 全て / スキーマ診断 / クエリ診断タブ #}

{# 問題カード一覧 #}
{% for issue in all_issues %}
<article>
  <header>
    <span class="badge {{ issue.severity }}">{{ issue.severity | upper }}</span>
    <span class="rule-id">{{ issue.rule_id }}</span>
    <strong>{{ issue.message }}</strong>
  </header>
  {% if issue.source == "query" %}
  <p><code>{{ issue.file }}:{{ issue.line }}</code></p>
  <pre>{{ issue.code }}</pre>
  {% else %}
  <p>テーブル: <code>{{ issue.table }}</code>
    {% if issue.column %} / カラム: <code>{{ issue.column }}</code>{% endif %}
  </p>
  {% endif %}
  <footer>
    <strong>修正案:</strong>
    <pre>{{ issue.suggestion }}</pre>
  </footer>
</article>
{% endfor %}

{# マイグレーションヒント（スキーマ診断タブ時のみ表示） #}
{% if tab in ('all', 'schema') and migration_hints %}
<article>
  <header><strong>マイグレーション生成ヒント</strong></header>
  <pre id="migration-hints">{% for hint in migration_hints %}{{ hint }}
{% endfor %}</pre>
  <footer>
    <button onclick="navigator.clipboard.writeText(document.getElementById('migration-hints').textContent)">
      コピー
    </button>
  </footer>
</article>
{% endif %}

{% endif %}{# end tab switch #}
{% endblock %}
```

---

## 8. 既存ツールとの連携

| 既存ツール | 連携方法 |
|---|---|
| `introspect_model` | スキーマ情報（columns, indexes, foreign_keys）とバリデーション情報をキャッシュから取得し、`IDX001`（外部キーインデックス）や `COL004`（NOT NULL 不一致）の突き合わせに使用。`query_preview` では enum 値マッピング、スコープ定義、association 情報、スキーマ（indexes, columns）をキャッシュから取得 |
| `find_references` | `query_audit` の grep 処理で `find_references` の内部実装（`analyzers/grep_search.py`）を再利用。`query_preview` ではスコープチェーンの検出に使用 |
| `n_plus_one_detector` | `PERF005`（N+1 兆候）の検出ロジックの一部を共有。ただし scope が異なる（`n_plus_one_detector` はコントローラ起点、`query_audit` は全ファイル対象） |
| `redundancy_detector` | `PERF004`（count の繰り返し）は `redundancy_detector` の検出対象とも重なる。両ツールが同じ問題を報告した場合、ダッシュボード側で重複排除する |
| `schema_audit` | `query_preview` のインデックス不足の警告と連動。`query_preview` で「このクエリにはインデックスがない」と指摘した箇所は `schema_audit` でも同様の指摘がある |
| `query_audit` | `query_preview` の recommendations として `PERF002`（SELECT *）や `PERF007`（非効率な存在確認）の検出と連動 |
| `gem_introspect` | `query_preview` で `default_scope` や論理削除 Gem（`acts_as_paranoid` / `discard`）による暗黙条件の検出に使用 |

### 重複排除の方針

ダッシュボードの `/sql` ページでは、同一ファイル・同一行に対して複数ツールから報告が上がった場合、以下の優先順位で 1 つのみ表示する:

1. `query_audit` の結果を優先（より詳細な修正提案を含むため）
2. 重複と判定する基準: `file` + `line` が一致する場合

---

## 9. ディレクトリ構造

```
src/rails_lens/
├── analyzers/
│   ├── schema_audit.py      # db/schema.rb の解析と診断ルール
│   ├── query_audit.py       # Ruby ソースの静的解析と診断ルール
│   └── query_preview.py     # ActiveRecord メソッドチェーン → SQL 変換エンジン
├── tools/
│   ├── schema_audit.py      # MCP ツール定義（register パターン）
│   ├── query_audit.py       # MCP ツール定義（register パターン）
│   └── query_preview.py     # MCP ツール定義（query_preview + query_preview_snippet）
└── web/
    └── templates/
        └── sql.html          # SQL診断ダッシュボードページ（クエリプレビュータブ含む）

ruby/
└── dump_schema_raw.rb        # schema.rb の内容をそのまま JSON 化（ランタイム解析が必要な場合のフォールバック）
```

### 各ファイルの責務

| ファイル | 責務 |
|---|---|
| `analyzers/schema_audit.py` | `db/schema.rb` の正規表現パース、テーブル/カラム/インデックス情報の構造化、診断ルールの適用 |
| `analyzers/query_audit.py` | Ruby ソースファイルの grep 検索、正規表現パターンマッチング、診断ルールの適用 |
| `analyzers/query_preview.py` | ActiveRecord メソッドチェーンの正規表現トークン化、変換ルールテーブル（§5）による SQL 生成、enum 値解決、暗黙条件検出、インデックス適用可否判定 |
| `tools/schema_audit.py` | `register(mcp, get_deps)` パターンでの MCP ツール登録、`SchemaAuditInput` / `SchemaAuditOutput` の定義 |
| `tools/query_audit.py` | `register(mcp, get_deps)` パターンでの MCP ツール登録、`QueryAuditInput` / `QueryAuditOutput` の定義 |
| `tools/query_preview.py` | `register(mcp, get_deps)` パターンでの MCP ツール登録、`QueryPreviewInput` / `QueryPreviewOutput` / `QueryPreviewSnippetInput` / `QueryPreviewSnippetOutput` の定義 |
| `web/templates/sql.html` | Jinja2 テンプレート、PicoCSS ベースの HTML（クエリプレビュータブ含む） |
| `ruby/dump_schema_raw.rb` | `db/schema.rb` を Ruby の DSL として評価し、構造化データとして JSON 出力するフォールバックスクリプト |

---

## 10. 実装の難易度と工数

| ツール | 難易度 | 工数 | 備考 |
|---|---|---|---|
| `schema_audit` | M | 中 | `schema.rb` の正規表現パースが主な工数。ルール自体はシンプル |
| `query_audit` | M | 中 | grep パターンの網羅性と誤検知抑制のバランスが肝 |
| `query_preview` | L | 大 | メソッドチェーン分解・SQL 変換ルール・enum 解決・インデックス判定と多岐にわたる。`introspect_model` のキャッシュとの連携が複雑 |
| `query_preview_snippet` | M | 中 | `query_preview` の変換エンジンを再利用。入力パースとメソッド分解が主な工数 |
| ダッシュボードページ | S | 小 | 既存パターン（PicoCSS + Jinja2）の踏襲で済む。クエリプレビュータブの追加分のみ |

### 実装フェーズ

| フェーズ | 内容 | 成果物 |
|---|---|---|
| Phase 1 | `schema_audit` の基本実装（`IDX001`, `IDX002`, `COL001`, `TBL001`） | `analyzers/schema_audit.py`, `tools/schema_audit.py` |
| Phase 2 | `query_audit` の基本実装（`SEC001`, `SEC002`, `PERF001`, `PERF003`） | `analyzers/query_audit.py`, `tools/query_audit.py` |
| Phase 3 | 残りのルール追加 + `introspect_model` 連携ルール（`IDX004`, `COL004`） | ルール追加、キャッシュ連携 |
| Phase 4 | ダッシュボードページ + マイグレーションヒント生成 | `web/templates/sql.html`, エンドポイント追加 |
| Phase 5 | 誤検知抑制（`rails-lens:disable` コメント対応）+ テスト | テストケース、ドキュメント |
| Phase 6 | `query_preview` の基本実装（変換ルールテーブル、スコープ一覧、enum 解決、インデックス判定） | `analyzers/query_preview.py`, `tools/query_preview.py` |
| Phase 7 | `query_preview_snippet` の実装 + スコープチェーン検出 + ダッシュボードのクエリプレビュータブ | `tools/query_preview.py` 拡張, `sql.html` 拡張 |

---

## 11. 設計上の注意点

### structure.sql への対応

`db/schema.rb` が存在しない（`db/structure.sql` を使っている）プロジェクトも考慮する。`structure.sql` の場合は SQL の直接パースが必要になるため、最初のバージョンでは以下のエラーメッセージを返す:

```json
{
  "error": "schema.rb が見つかりません。db/structure.sql を使用しているプロジェクトには現在未対応です。",
  "hint": "config/application.rb で config.active_record.schema_format = :ruby に変更すると schema.rb が生成されます"
}
```

### 誤検知への対応方針

- 重大度 `critical` のルール（`SEC001`, `SEC002`, `PERF001`）は精度を最優先する
- 重大度 `info` のルールは多少の誤検知を許容する
- `# rails-lens:disable SEC001` のようなインラインコメントでルールを無効化できる仕組みは Phase 5 で実装する

### キャッシュ戦略

- 診断結果は `CacheManager` を使用してキャッシュする
- `schema_audit`: `db/schema.rb` の mtime をキャッシュキーとする
- `query_audit`: スコープ内の Ruby ファイルの最新 mtime をキャッシュキーとする
- `query_preview`: モデルファイルの mtime + `db/schema.rb` の mtime をキャッシュキーとする
- ソースファイルの mtime が変わらない限りキャッシュから返す

### AI による自発的な呼び出し

AI が `schema_audit` を自発的に呼ぶよう、ツールの description に「マイグレーションを作成する前に必ずこのツールでスキーマの問題を確認すること」と明記する（ツール定義の docstring に記載済み）。

### 予測 SQL の精度

- 予測 SQL は100%正確ではないことを明示する。出力に `"accuracy": "predicted (static analysis)"` を含め、ダッシュボードでも「この SQL は静的解析による予測です。実際のクエリは DB アダプタやバージョンによって異なる場合があります」と注記する
- `db/schema.rb` が MySQL 用か PostgreSQL 用かでクォーティングが異なる（MySQL: バッククォート、PostgreSQL: ダブルクォート）。`schema.rb` の `ActiveRecord::Schema` 定義からアダプタを推定する。不明な場合は PostgreSQL 形式（ダブルクォート）をデフォルトとする

### includes の挙動

- `includes` の挙動は Rails のバージョンと条件によって `preload`（2クエリ）か `eager_load`（1クエリ JOIN）に内部的に切り替わる。予測では `preload` 戦略（2クエリ）をデフォルトとし、JOIN が必要な条件（`.where` で関連テーブルのカラムを参照している場合）では `eager_load` 戦略に切り替えることを注記する

### コードスニペット入力の安全性

- コードスニペット入力（モード2）は任意の Ruby コードを受け取るが、実行はしない。正規表現でメソッドチェーンを分解するだけ
- 複雑な制御構文（if 分岐、ループ内のクエリ等）は解析対象外とし、「このコードは複雑すぎて静的解析では予測できません」と返す

### スコープチェーン検索のパフォーマンス

- スコープチェーンの例（`include_chain_examples`）は `find_references` の grep で対象モデルのスコープ呼び出しを検索して構築する。大規模コードベースでは検索に時間がかかるため、キャッシュ必須
