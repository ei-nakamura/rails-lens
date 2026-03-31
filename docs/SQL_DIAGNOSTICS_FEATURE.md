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
4. [検出ルール一覧](#4-検出ルール一覧)
5. [実装方針](#5-実装方針)
6. [ダッシュボードページ設計](#6-ダッシュボードページ設計)
7. [既存ツールとの連携](#7-既存ツールとの連携)
8. [ディレクトリ構造](#8-ディレクトリ構造)
9. [実装の難易度と工数](#9-実装の難易度と工数)
10. [設計上の注意点](#10-設計上の注意点)

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

## 5. 実装方針

### 5.1 schema_audit の実装

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

### 5.2 query_audit の実装

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

## 6. ダッシュボードページ設計

### 6.1 SQL 診断ページ

| 項目 | 内容 |
|---|---|
| **URL** | `GET /sql` |
| **目的** | スキーマ診断とクエリパターン診断の結果を統合表示する |
| **使用 MCP ツール** | `rails_lens_schema_audit` + `rails_lens_query_audit` |

### 6.2 表示内容

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

各タブ内ではさらに重大度フィルタ（critical / warning / info / all）で絞り込み可能。タブとフィルタはクエリパラメータ `?tab=schema&severity=critical` で URL に反映し、ブラウザの戻るボタンで前の状態に戻れるようにする。

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

### 6.3 内部 API エンドポイント

| メソッド | パス | 処理内容 | レスポンス形式 |
|---|---|---|---|
| `GET` | `/sql` | SQL 診断結果の統合表示 | HTML (Jinja2) |

```python
@app.get("/sql", response_class=HTMLResponse)
async def sql_diagnostics(
    request: Request,
    tab: str = "all",          # "all", "schema", "query"
    severity: str = "all",     # "all", "critical", "warning", "info"
    table: str | None = None,  # 特定テーブルに絞る
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

    return templates.TemplateResponse("sql.html", {
        "request": request,
        "tab": tab,
        "severity": severity,
        "all_issues": all_issues,
        "schema_summary": schema_result.summary,
        "query_summary": query_result.summary,
        "migration_hints": migration_hints,
    })
```

### 6.4 ナビゲーションへの追加

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

### 6.5 テンプレート構造

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
  </ul>
</nav>

{# 重大度フィルタ #}
<fieldset role="group">
  <a href="?tab={{ tab }}&severity=all" role="button" {% if severity == 'all' %}class="contrast"{% else %}class="outline"{% endif %}>全て</a>
  <a href="?tab={{ tab }}&severity=critical" role="button" {% if severity == 'critical' %}class="contrast"{% else %}class="outline"{% endif %}>Critical</a>
  <a href="?tab={{ tab }}&severity=warning" role="button" {% if severity == 'warning' %}class="contrast"{% else %}class="outline"{% endif %}>Warning</a>
  <a href="?tab={{ tab }}&severity=info" role="button" {% if severity == 'info' %}class="contrast"{% else %}class="outline"{% endif %}>Info</a>
</fieldset>

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
{% endblock %}
```

---

## 7. 既存ツールとの連携

| 既存ツール | 連携方法 |
|---|---|
| `introspect_model` | スキーマ情報（columns, indexes, foreign_keys）とバリデーション情報をキャッシュから取得し、`IDX001`（外部キーインデックス）や `COL004`（NOT NULL 不一致）の突き合わせに使用 |
| `find_references` | `query_audit` の grep 処理で `find_references` の内部実装（`analyzers/grep_search.py`）を再利用 |
| `n_plus_one_detector` | `PERF005`（N+1 兆候）の検出ロジックの一部を共有。ただし scope が異なる（`n_plus_one_detector` はコントローラ起点、`query_audit` は全ファイル対象） |
| `redundancy_detector` | `PERF004`（count の繰り返し）は `redundancy_detector` の検出対象とも重なる。両ツールが同じ問題を報告した場合、ダッシュボード側で重複排除する |

### 重複排除の方針

ダッシュボードの `/sql` ページでは、同一ファイル・同一行に対して複数ツールから報告が上がった場合、以下の優先順位で 1 つのみ表示する:

1. `query_audit` の結果を優先（より詳細な修正提案を含むため）
2. 重複と判定する基準: `file` + `line` が一致する場合

---

## 8. ディレクトリ構造

```
src/rails_lens/
├── analyzers/
│   ├── schema_audit.py      # db/schema.rb の解析と診断ルール
│   └── query_audit.py       # Ruby ソースの静的解析と診断ルール
├── tools/
│   ├── schema_audit.py      # MCP ツール定義（register パターン）
│   └── query_audit.py       # MCP ツール定義（register パターン）
└── web/
    └── templates/
        └── sql.html          # SQL診断ダッシュボードページ

ruby/
└── dump_schema_raw.rb        # schema.rb の内容をそのまま JSON 化（ランタイム解析が必要な場合のフォールバック）
```

### 各ファイルの責務

| ファイル | 責務 |
|---|---|
| `analyzers/schema_audit.py` | `db/schema.rb` の正規表現パース、テーブル/カラム/インデックス情報の構造化、診断ルールの適用 |
| `analyzers/query_audit.py` | Ruby ソースファイルの grep 検索、正規表現パターンマッチング、診断ルールの適用 |
| `tools/schema_audit.py` | `register(mcp, get_deps)` パターンでの MCP ツール登録、`SchemaAuditInput` / `SchemaAuditOutput` の定義 |
| `tools/query_audit.py` | `register(mcp, get_deps)` パターンでの MCP ツール登録、`QueryAuditInput` / `QueryAuditOutput` の定義 |
| `web/templates/sql.html` | Jinja2 テンプレート、PicoCSS ベースの HTML |
| `ruby/dump_schema_raw.rb` | `db/schema.rb` を Ruby の DSL として評価し、構造化データとして JSON 出力するフォールバックスクリプト |

---

## 9. 実装の難易度と工数

| ツール | 難易度 | 工数 | 備考 |
|---|---|---|---|
| `schema_audit` | M | 中 | `schema.rb` の正規表現パースが主な工数。ルール自体はシンプル |
| `query_audit` | M | 中 | grep パターンの網羅性と誤検知抑制のバランスが肝 |
| ダッシュボードページ | S | 小 | 既存パターン（PicoCSS + Jinja2）の踏襲で済む |

### 実装フェーズ

| フェーズ | 内容 | 成果物 |
|---|---|---|
| Phase 1 | `schema_audit` の基本実装（`IDX001`, `IDX002`, `COL001`, `TBL001`） | `analyzers/schema_audit.py`, `tools/schema_audit.py` |
| Phase 2 | `query_audit` の基本実装（`SEC001`, `SEC002`, `PERF001`, `PERF003`） | `analyzers/query_audit.py`, `tools/query_audit.py` |
| Phase 3 | 残りのルール追加 + `introspect_model` 連携ルール（`IDX004`, `COL004`） | ルール追加、キャッシュ連携 |
| Phase 4 | ダッシュボードページ + マイグレーションヒント生成 | `web/templates/sql.html`, エンドポイント追加 |
| Phase 5 | 誤検知抑制（`rails-lens:disable` コメント対応）+ テスト | テストケース、ドキュメント |

---

## 10. 設計上の注意点

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
- ソースファイルの mtime が変わらない限りキャッシュから返す

### AI による自発的な呼び出し

AI が `schema_audit` を自発的に呼ぶよう、ツールの description に「マイグレーションを作成する前に必ずこのツールでスキーマの問題を確認すること」と明記する（ツール定義の docstring に記載済み）。
