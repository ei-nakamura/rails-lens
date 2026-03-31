# rails-lens 画面マッピング機能設計書

> **バージョン**: 1.0.0
> **最終更新**: 2026-03-31
> **ステータス**: 設計確定・実装前
> **前提ドキュメント**: [REQUIREMENTS.md](./REQUIREMENTS.md), [ADDITIONAL_FEATURES.md](./ADDITIONAL_FEATURES.md), [API_MIGRATION_FEATURES.md](./API_MIGRATION_FEATURES.md)

---

## 目次

1. [概要](#1-概要)
2. [背景と課題](#2-背景と課題)
3. [ツール仕様](#3-ツール仕様)
   - [H-1. rails_lens_screen_map — モード1: screen_to_source（画面→ソース）](#h-1-モード1-screen_to_source画面ソース)
   - [H-1. rails_lens_screen_map — モード2: source_to_screens（ソース→画面）](#h-1-モード2-source_to_screensソース画面)
   - [H-1. rails_lens_screen_map — モード3: full_inventory（画面台帳の自動生成）](#h-1-モード3-full_inventory画面台帳の自動生成)
4. [実装方針](#4-実装方針)
5. [既存ツールとの連携](#5-既存ツールとの連携)
6. [実装フェーズとマイルストーン](#6-実装フェーズとマイルストーン)

---

## 1. 概要

### 背景

Rails アプリケーションにおける「画面」と「ソースコード」の双方向マッピングを提供する機能である。ドキュメントが整備されていないプロジェクトでも、URL・コントローラ・ビュー・パーシャル・ヘルパー・i18n・モデルの関係を自動的に可視化する。

### 設計原則

既存アーキテクチャの原則を踏襲する:

| 原則 | 内容 |
|---|---|
| **ハイブリッド構成** | Python（FastMCP + ツール定義）+ Ruby（ランタイムイントロスペクション）|
| **共通ブリッジパターン** | 全ランタイム解析は `bridge/runner.py` 経由で `bundle exec rails runner` を実行 |
| **ツール登録パターン** | `register(mcp, get_deps)` による遅延初期化 |
| **キャッシュ戦略** | ファイルベースJSONキャッシュ + mtime自動無効化 |
| **構造化出力** | Pydanticモデルによる型安全な入出力 + JSON文字列返却 |
| **アノテーション** | 全ツール `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True` |

### 本ドキュメント固有の設計制約

- ERB / Haml / Slim の3つのテンプレートエンジンに対応すること。プロジェクトによって混在している場合もある
- `render` の呼び出しパターンは多様（省略記法、コレクション、ローカル変数付き等）なので、パターンマッチは網羅的に行うこと
- 逆引きインデックスの構築は初回コストが高い。プログレス報告（`ctx.report_progress`）を使ってユーザーに進捗を見せること
- `full_inventory` の markdown 出力は、そのままプロジェクトの `docs/` に配置できる品質にすること（自動生成であることの注記、生成日時を含む）
- APIエンドポイント（`respond_to :json` のみのアクション）はテンプレートを持たないので、代わりにシリアライザ（ActiveModelSerializers, Blueprinter, jbuilder 等）を検出して出力に含めること
- SPA フロントエンドを持つプロジェクトの場合、Rails のビューは API レスポンスのみになる。この場合は `full_inventory` が API エンドポイントの台帳として機能する
- 全モードの出力は、大規模アプリでは**要約 → 詳細の段階的開示パターン**を採用し、AIのコンテキストウィンドウ消費を制御する

### ツール一覧

| ID | ツール名 | モード | 解析方式 | 難易度 |
|---|---|---|---|---|
| H-1a | `rails_lens_screen_map` | `screen_to_source` | ハイブリッド（ランタイム + 静的解析） | M |
| H-1b | `rails_lens_screen_map` | `source_to_screens` | ハイブリッド（ランタイム + 静的解析） | L |
| H-1c | `rails_lens_screen_map` | `full_inventory` | ハイブリッド（ランタイム + 静的解析） | M |

---

## 2. 背景と課題

### ドキュメントがないRailsプロジェクトで起きること

巨大な Rails アプリで「この画面のソースはどこ？」「このファイルを変えたらどの画面が壊れる？」が即答できないことは非常に多い。原因は以下の構造的な問題にある:

| 問題 | 具体例 | AIへの影響 |
|---|---|---|
| 1画面が複数ファイルで構成される | layout + テンプレート + 複数パーシャル + ヘルパー + decorator/presenter が組み合わさって1つの画面を構成 | 画面に対応する「1つのファイル」を特定できず、修正漏れが発生する |
| パーシャルの共有 | `shared/_navigation.html.erb` が複数画面で使われるが、逆引きできない | パーシャル変更の影響範囲を把握できない |
| ヘルパーメソッドの間接参照 | 画面に表示されるテキストやHTMLがヘルパーメソッドに隠れている | grep で画面上のテキストを検索してもヒットしない |
| i18n の未使用 / 部分使用 | 日本語テキストがビューに直書きされているプロジェクトでは、i18n キーからの逆引きができない | 翻訳キーとビューの対応関係が不明確 |
| render の明示指定 | `render "users/profile"` のように規約外のテンプレートをレンダリングしている | コントローラ名とテンプレート名が一致せず、ファイルを特定できない |

### この機能が解決すること

**方向1（URL/画面 → ソース）**: 「この画面のソースはどこ？」に答える。URL やコントローラ名を入力すると、その画面を構成する全ファイル（テンプレート、パーシャル、ヘルパー、使用モデル、i18n キー）を返す。

**方向2（ソース → 画面）**: 「このファイルを変えたらどの画面に影響する？」に答える。パーシャルやヘルパーのファイルパスを入力すると、それを使用している全画面の一覧を返す。

**画面名の自動推定**: URL やコントローラ名から人間が読める画面名を自動的に推定し、画面一覧をドキュメントの代替として提供する。

---

## 3. ツール仕様

### 共通: Pydanticモデル定義（入力）

```python
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional


class ScreenMapMode(str, Enum):
    SCREEN_TO_SOURCE = "screen_to_source"
    SOURCE_TO_SCREENS = "source_to_screens"
    FULL_INVENTORY = "full_inventory"


class ScreenMapGroupBy(str, Enum):
    NAMESPACE = "namespace"
    RESOURCE = "resource"
    FLAT = "flat"


class ScreenMapInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mode: ScreenMapMode = Field(
        ...,
        description="実行モード: screen_to_source（画面→ソース）, source_to_screens（ソース→画面）, full_inventory（画面台帳）"
    )

    # screen_to_source 用
    url: str | None = Field(
        default=None,
        description="画面のURLパス (例: '/users/123')",
        max_length=500,
    )
    controller_action: str | None = Field(
        default=None,
        description="コントローラ#アクション (例: 'UsersController#show')",
        max_length=200,
    )

    # source_to_screens 用
    file_path: str | None = Field(
        default=None,
        description="ソースファイルのパス (例: 'app/views/shared/_navigation.html.erb')",
        max_length=500,
    )
    method_name: str | None = Field(
        default=None,
        description="ヘルパーメソッド名 (例: 'user_status_badge')。file_path と併用。",
        max_length=200,
    )

    # full_inventory 用
    format: str | None = Field(
        default="json",
        description="出力形式: 'json' or 'markdown'",
    )
    include_api: bool | None = Field(
        default=True,
        description="APIエンドポイントも含めるか",
    )
    group_by: ScreenMapGroupBy | None = Field(
        default=ScreenMapGroupBy.NAMESPACE,
        description="グルーピング方法",
    )
    locale: str | None = Field(
        default="ja",
        description="画面名推定の言語",
    )
```

### 共通: MCPツール登録

```python
@mcp.tool(
    name="rails_lens_screen_map",
    annotations={
        "title": "Screen-Source Bidirectional Map",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def screen_map(params: ScreenMapInput) -> str:
    '''画面とソースコードの双方向マッピングを提供する。

    3つのモードがある:
    - screen_to_source: URL またはコントローラ名から、その画面を構成する全ファイルを返す
    - source_to_screens: ファイルパスから、そのファイルが使われている全画面を返す
    - full_inventory: 全画面の台帳を自動生成する（ドキュメントがないプロジェクトの全体把握に有効）

    画面を変更する前にこのツールで影響範囲を確認すること。
    特にパーシャルやヘルパーの変更は複数画面に影響する可能性がある。
    '''
    ...
```

---

### H-1. モード1: screen_to_source（画面→ソース）

#### 解決する課題と利用シーン

**課題**: 1つの画面が layout + テンプレート + パーシャル + ヘルパー + decorator/presenter + i18n で構成されており、「この画面を構成するファイルは何か」を人手で辿るのは困難。さらに `render "users/profile"` のように規約外のテンプレートを使用しているケースでは、コントローラ名からテンプレートを推測することすらできない。

**利用シーン**:
- AIが「ユーザー詳細画面を変更して」と依頼されたとき、まず `screen_to_source` で画面を構成する全ファイルを取得し、変更対象を特定する
- バグ報告で「/users/123 の画面でレイアウトが崩れている」と言われたとき、関連するテンプレートとパーシャルを即座に特定する
- 画面に表示される文言を変更するとき、i18n キーとハードコードされたテキストの両方を把握して漏れなく修正する

#### 入力スキーマ

```json
{
  "type": "object",
  "required": ["mode"],
  "properties": {
    "mode": {
      "type": "string",
      "const": "screen_to_source"
    },
    "url": {
      "type": "string",
      "description": "画面のURLパス (例: '/users/123')",
      "maxLength": 500
    },
    "controller_action": {
      "type": "string",
      "description": "コントローラ#アクション (例: 'UsersController#show')",
      "maxLength": 200
    }
  }
}
```

`url` または `controller_action` のいずれか一方が必須。両方が指定された場合は `controller_action` を優先する。

#### Pydanticモデル定義（出力）

```python
class ScreenInfo(BaseModel):
    url_pattern: str                   # "/users/:id"
    http_method: str                   # "GET"
    controller_action: str             # "UsersController#show"
    screen_name: str                   # "ユーザー詳細"
    screen_name_source: str            # "i18n:users.show.title" / "h1_tag" / "restful_convention"


class LayoutInfo(BaseModel):
    file: str                          # "app/views/layouts/application.html.erb"
    content_for_blocks: list[str] = Field(default_factory=list)  # ["header", "sidebar"]


class TemplateInfo(BaseModel):
    file: str                          # "app/views/users/show.html.erb"
    explicitly_specified: bool = False  # render で明示指定されているか


class PartialInfo(BaseModel):
    name: str                          # "_header"
    file: str                          # "app/views/users/_header.html.erb"
    called_from: str                   # "app/views/users/show.html.erb:3"
    locals_passed: list[str] = Field(default_factory=list)
    collection: bool = False
    note: str = ""
    nested_partials: list["PartialInfo"] = Field(default_factory=list)


class HelperUsage(BaseModel):
    method: str                        # "user_status_badge"
    file: str                          # "app/helpers/users_helper.rb"
    line: int
    called_from: str                   # "app/views/users/show.html.erb:8"


class DecoratorPresenterUsage(BaseModel):
    class_name: str                    # "UserDecorator"
    file: str                          # "app/decorators/user_decorator.rb"
    methods_used: list[str] = Field(default_factory=list)


class ModelReference(BaseModel):
    model: str                         # "User"
    attributes_accessed: list[str] = Field(default_factory=list)
    associations_accessed: list[str] = Field(default_factory=list)
    methods_called: list[str] = Field(default_factory=list)


class I18nKeyUsage(BaseModel):
    key: str                           # "users.show.title"
    value: str                         # "ユーザー詳細"
    file: str                          # "config/locales/ja.yml"


class HardcodedText(BaseModel):
    text: str                          # "名前:"
    file: str
    line: int


class AssetInfo(BaseModel):
    stylesheets: list[str] = Field(default_factory=list)
    javascripts: list[str] = Field(default_factory=list)
    stimulus_controllers: list[str] = Field(default_factory=list)


class ScreenToSourceOutput(BaseModel):
    screen: ScreenInfo
    layout: LayoutInfo | None = None
    template: TemplateInfo
    partials: list[PartialInfo] = Field(default_factory=list)
    helpers_used: list[HelperUsage] = Field(default_factory=list)
    decorators_presenters: list[DecoratorPresenterUsage] = Field(default_factory=list)
    models_referenced: list[ModelReference] = Field(default_factory=list)
    i18n_keys: list[I18nKeyUsage] = Field(default_factory=list)
    hardcoded_text: list[HardcodedText] = Field(default_factory=list)
    assets: AssetInfo = Field(default_factory=AssetInfo)
```

#### 出力例

```json
{
  "screen": {
    "url_pattern": "/users/:id",
    "http_method": "GET",
    "controller_action": "UsersController#show",
    "screen_name": "ユーザー詳細",
    "screen_name_source": "i18n:users.show.title"
  },
  "layout": {
    "file": "app/views/layouts/application.html.erb",
    "content_for_blocks": ["header", "sidebar", "footer"]
  },
  "template": {
    "file": "app/views/users/show.html.erb",
    "explicitly_specified": false
  },
  "partials": [
    {
      "name": "_header",
      "file": "app/views/users/_header.html.erb",
      "called_from": "app/views/users/show.html.erb:3",
      "locals_passed": ["user"],
      "collection": false,
      "note": "",
      "nested_partials": []
    },
    {
      "name": "_post_card",
      "file": "app/views/posts/_post_card.html.erb",
      "called_from": "app/views/users/show.html.erb:28",
      "locals_passed": ["post"],
      "collection": true,
      "note": "",
      "nested_partials": [
        {
          "name": "_comment_count",
          "file": "app/views/shared/_comment_count.html.erb",
          "called_from": "app/views/posts/_post_card.html.erb:12",
          "locals_passed": [],
          "collection": false,
          "note": "",
          "nested_partials": []
        }
      ]
    },
    {
      "name": "_navigation",
      "file": "app/views/shared/_navigation.html.erb",
      "called_from": "app/views/layouts/application.html.erb:15",
      "locals_passed": [],
      "collection": false,
      "note": "layout経由で全画面に含まれる",
      "nested_partials": []
    }
  ],
  "helpers_used": [
    {
      "method": "user_status_badge",
      "file": "app/helpers/users_helper.rb",
      "line": 3,
      "called_from": "app/views/users/show.html.erb:8"
    },
    {
      "method": "format_datetime",
      "file": "app/helpers/application_helper.rb",
      "line": 20,
      "called_from": "app/views/users/show.html.erb:15"
    }
  ],
  "decorators_presenters": [
    {
      "class_name": "UserDecorator",
      "file": "app/decorators/user_decorator.rb",
      "methods_used": ["formatted_name", "avatar_url"]
    }
  ],
  "models_referenced": [
    {
      "model": "User",
      "attributes_accessed": ["name", "email", "role", "status", "company_name", "created_at"],
      "associations_accessed": ["orders", "authored_posts", "tags", "company"],
      "methods_called": ["display_name", "admin?", "can_place_order?"]
    },
    {
      "model": "Order",
      "attributes_accessed": ["order_number", "status", "total_amount", "created_at"],
      "associations_accessed": [],
      "methods_called": []
    }
  ],
  "i18n_keys": [
    { "key": "users.show.title", "value": "ユーザー詳細", "file": "config/locales/ja.yml" },
    { "key": "users.show.edit_button", "value": "編集", "file": "config/locales/ja.yml" }
  ],
  "hardcoded_text": [
    { "text": "名前:", "file": "app/views/users/show.html.erb", "line": 10 },
    { "text": "メールアドレス:", "file": "app/views/users/show.html.erb", "line": 11 },
    { "text": "Recent Orders", "file": "app/views/users/show.html.erb", "line": 20 }
  ],
  "assets": {
    "stylesheets": ["app/assets/stylesheets/users.scss"],
    "javascripts": ["app/javascript/controllers/users_controller.js"],
    "stimulus_controllers": ["users", "clipboard"]
  }
}
```

#### 画面名の推定ロジック

優先順位順に以下のソースから画面名を推定する:

| 優先度 | ソース | 例 | `screen_name_source` の値 |
|---|---|---|---|
| 1 | i18n ファイル（タイトルキー） | `users.show.title` → "ユーザー詳細" | `i18n:users.show.title` |
| 2 | テンプレート内の `<title>` / `content_for :title` / `provide :title` | `content_for :title, "ユーザー詳細"` | `content_for_title` |
| 3 | テンプレート内の最初の `<h1>` タグ | `<h1>ユーザー詳細</h1>` | `h1_tag` |
| 4 | RESTful 規約からの自動生成 | `UsersController#show` → "ユーザー詳細" | `restful_convention` |

i18n のキー探索パターン（優先順位順）:

```
{controller}.{action}.title
{controller}.{action}.page_title
titles.{controller}.{action}
views.{controller}.{action}.title
```

RESTful 規約からの自動生成テーブル:

| アクション | 日本語推定（`locale: "ja"`） | 英語推定（`locale: "en"`） |
|---|---|---|
| index | {リソース名}一覧 | {Resource} List |
| show | {リソース名}詳細 | {Resource} Detail |
| new | {リソース名}新規作成 | New {Resource} |
| edit | {リソース名}編集 | Edit {Resource} |
| create | {リソース名}作成（処理） | Create {Resource} (action) |
| update | {リソース名}更新（処理） | Update {Resource} (action) |
| destroy | {リソース名}削除（処理） | Delete {Resource} (action) |

名前空間がある場合はプレフィックスを付与:
- `Admin::UsersController#index` → `管理画面 - ユーザー一覧`
- `Api::V1::UsersController#index` → `ユーザー一覧 (API v1)`

#### 実装方針: ハイブリッド（ランタイム + 静的解析）

**ランタイム解析**（Rubyスクリプト: `ruby/dump_view_mapping.rb`）:
1. `Rails.application.routes.routes` を走査し、URL パターンからコントローラ・アクションを特定
2. コントローラクラスの `_layout` メソッドを呼び出し、使用されている layout を解決
3. コントローラのアクション内で `render` が明示指定されている場合、そのテンプレートパスを取得
4. `I18n.backend.translations` から画面名推定に必要なキーと値をダンプ

**静的解析**（Python側: `analyzers/view_resolver.py`）:
1. テンプレート内の `render partial:` / `render "xxx"` / `render collection:` パターンを正規表現で抽出
2. 抽出したパーシャルに対して再帰的に同じ処理を実行（循環参照検出付き）
3. テンプレート内のヘルパーメソッド呼び出し（`<%= method_name(...) %>` パターン）を検出し、ヘルパーファイル内の定義と照合
4. `@model.attribute`、`@model.association` パターンからモデル参照を抽出
5. `<title>`、`<h1>`、`content_for :title`、`provide :title` を抽出し画面名推定に使用
6. 日本語・英語のハードコードテキスト（HTMLタグの外にある平文テキスト）を検出
7. `data-controller="xxx"` パターンから Stimulus コントローラを検出
8. `app/decorators/`、`app/presenters/` ディレクトリから Decorator/Presenter を走査

**処理フロー**:

```
1. bridge.execute("dump_view_mapping.rb", [controller_action_or_url])
   → ルーティング解決、layout 解決、i18n ダンプ、明示 render の検出
2. テンプレートファイルの特定
   → 規約ベース（controller/action.html.{erb,haml,slim}）または明示指定
3. テンプレート + layout のパーシャル再帰的解決（Python 静的解析）
4. 全テンプレート・パーシャルからヘルパー呼び出し検出
5. 全テンプレート・パーシャルからモデル参照抽出
6. i18n キー照合 + ハードコードテキスト検出
7. アセット検出（stylesheet/javascript/stimulus）
8. 結果を ScreenToSourceOutput にマージして返却
```

#### 既存ツールとの連携

| 既存ツール | 連携方法 |
|---|---|
| `rails_lens_get_routes` | ルーティング情報の取得に使用。URL からコントローラ・アクションを特定する |
| `rails_lens_introspect_model` | モデルの属性・メソッド情報をキャッシュから照合し、ビュー内で使われている属性を正確に特定する |
| `rails_lens_find_references` | ヘルパーメソッドの定義箇所の特定に `GrepSearch` を共有 |

#### 難易度と工数: M（中）

- パーシャルの再帰的解決が主な工数。循環参照検出と複数テンプレートエンジン対応が必要
- ヘルパーメソッドの呼び出し検出は正規表現ベースで、完全な精度は求めない（80%カバレッジ目標）
- layout 解決のランタイム解析は新規 Ruby スクリプトが必要だが、`dump_routes.rb` の拡張で対応可能
- i18n ダンプは既存の Rails API を呼ぶだけなのでコスト低

---

### H-1. モード2: source_to_screens（ソース→画面）

#### 解決する課題と利用シーン

**課題**: パーシャルやヘルパーメソッドは複数画面で共有されるが、「このファイルを変更したらどの画面に影響するか」を知る方法がない。パーシャルは使う側が `render partial:` で呼んでいるだけなので逆引きが困難であり、ヘルパーメソッドも同様に呼び出し元を追跡する必要がある。

**利用シーン**:
- AIが共有パーシャル（`shared/_navigation.html.erb`）を変更する前に、影響を受ける全画面を把握する
- ヘルパーメソッドのシグネチャを変更するとき、呼び出し元の全画面を確認して互換性を維持する
- モデルの属性名を変更するとき、その属性を表示している全画面を特定して修正漏れを防ぐ
- リファクタリングの影響範囲を定量化する（`impact_level` で優先度判断）

#### 入力スキーマ

```json
{
  "type": "object",
  "required": ["mode", "file_path"],
  "properties": {
    "mode": {
      "type": "string",
      "const": "source_to_screens"
    },
    "file_path": {
      "type": "string",
      "description": "ソースファイルのパス (例: 'app/views/shared/_navigation.html.erb')",
      "maxLength": 500
    },
    "method_name": {
      "type": "string",
      "description": "ヘルパーメソッド名（file_path と併用。省略時はファイル内の全メソッドを対象とする）",
      "maxLength": 200
    }
  }
}
```

#### Pydanticモデル定義（出力）

```python
class ScreenReference(BaseModel):
    screen_name: str                      # "ユーザー詳細"
    controller_action: str | None = None  # "UsersController#show"
    url_pattern: str | None = None        # "/users/:id"
    included_via: str | None = None       # "app/views/users/show.html.erb:3"
    inclusion_chain: list[str] = Field(default_factory=list)
    via_partial: bool = False
    is_api: bool = False
    note: str = ""
    # モデル参照の場合のみ
    attributes_used: list[str] = Field(default_factory=list)
    methods_used: list[str] = Field(default_factory=list)


class MethodScreenMapping(BaseModel):
    method_name: str
    line: int
    used_in_screens: list[ScreenReference] = Field(default_factory=list)
    total_screen_count: int = 0
    impact_level: str = "low"             # "critical", "high", "moderate", "low"


class SourceToScreensOutput(BaseModel):
    source_file: str
    source_type: str                      # "partial", "helper", "model", "decorator", "presenter"
    # パーシャル / decorator / presenter の場合
    used_in_screens: list[ScreenReference] = Field(default_factory=list)
    # ヘルパーの場合（メソッド単位）
    methods: list[MethodScreenMapping] = Field(default_factory=list)
    total_screen_count: int | str = 0     # 数値 or "all (layout)"
    impact_level: str = "low"
```

#### 出力例（パーシャルの場合）

```json
{
  "source_file": "app/views/shared/_navigation.html.erb",
  "source_type": "partial",
  "used_in_screens": [
    {
      "screen_name": "（全画面 — layout経由）",
      "controller_action": null,
      "url_pattern": null,
      "included_via": "app/views/layouts/application.html.erb:15",
      "inclusion_chain": [
        "app/views/layouts/application.html.erb"
      ],
      "via_partial": false,
      "is_api": false,
      "note": ""
    }
  ],
  "methods": [],
  "total_screen_count": "all (layout)",
  "impact_level": "critical"
}
```

#### 出力例（ヘルパーメソッドの場合）

```json
{
  "source_file": "app/helpers/users_helper.rb",
  "source_type": "helper",
  "used_in_screens": [],
  "methods": [
    {
      "method_name": "user_status_badge",
      "line": 3,
      "used_in_screens": [
        {
          "screen_name": "ユーザー詳細",
          "controller_action": "UsersController#show",
          "url_pattern": "/users/:id",
          "included_via": null,
          "inclusion_chain": [],
          "via_partial": false,
          "is_api": false,
          "note": "",
          "attributes_used": [],
          "methods_used": []
        },
        {
          "screen_name": "ユーザー一覧",
          "controller_action": "UsersController#index",
          "url_pattern": "/users",
          "included_via": null,
          "inclusion_chain": [],
          "via_partial": true,
          "is_api": false,
          "note": "",
          "attributes_used": [],
          "methods_used": []
        }
      ],
      "total_screen_count": 2,
      "impact_level": "moderate"
    }
  ],
  "total_screen_count": 2,
  "impact_level": "moderate"
}
```

#### 出力例（モデルの場合）

```json
{
  "source_file": "app/models/user.rb",
  "source_type": "model",
  "used_in_screens": [
    {
      "screen_name": "ユーザー一覧",
      "controller_action": "UsersController#index",
      "url_pattern": "/users",
      "included_via": null,
      "inclusion_chain": [],
      "via_partial": false,
      "is_api": false,
      "note": "",
      "attributes_used": ["name", "email", "role"],
      "methods_used": ["display_name"]
    },
    {
      "screen_name": "ユーザー詳細",
      "controller_action": "UsersController#show",
      "url_pattern": "/users/:id",
      "included_via": null,
      "inclusion_chain": [],
      "via_partial": false,
      "is_api": false,
      "note": "",
      "attributes_used": ["name", "email", "role", "status", "created_at"],
      "methods_used": ["display_name", "admin?", "can_place_order?", "company_name"]
    },
    {
      "screen_name": "注文詳細",
      "controller_action": "OrdersController#show",
      "url_pattern": "/orders/:id",
      "included_via": null,
      "inclusion_chain": [],
      "via_partial": false,
      "is_api": false,
      "note": "Order の belongs_to :user 経由で参照",
      "attributes_used": ["name", "email"],
      "methods_used": []
    },
    {
      "screen_name": "ユーザー一覧 (API)",
      "controller_action": "Api::V1::UsersController#index",
      "url_pattern": "/api/v1/users",
      "included_via": null,
      "inclusion_chain": [],
      "via_partial": false,
      "is_api": true,
      "note": "",
      "attributes_used": ["name", "email", "role"],
      "methods_used": []
    }
  ],
  "methods": [],
  "total_screen_count": 4,
  "impact_level": "high"
}
```

#### impact_level の判定基準

| レベル | 条件 | 意味 |
|---|---|---|
| `critical` | layout 経由で全画面に影響、または10画面以上で使用 | 変更時は全画面のリグレッションテストが必要 |
| `high` | 5〜9画面で使用 | 変更時は広範な影響確認が必要 |
| `moderate` | 2〜4画面で使用 | 変更時は関連画面の確認が必要 |
| `low` | 1画面でのみ使用 | 影響は局所的 |

#### 実装方針: ハイブリッド（ランタイム + 静的解析）

`source_to_screens` は全画面のマッピング情報を事前に構築して逆引きインデックスを作る必要がある。

**逆引きインデックス構築フロー**:

```
1. bridge.execute("dump_view_mapping.rb", ["all"])
   → 全ルーティングを取得
2. 各ルーティングに対して screen_to_source の処理を実行（キャッシュ保存）
3. 全結果から逆引きインデックスを構築:
   - partial_file → [screen1, screen2, ...]
   - helper_method → [screen1, screen3, ...]
   - model_name → [screen1, screen2, screen4, ...]
4. インデックスをキャッシュに保存（.rails-lens/cache/screen_reverse_index.json）
```

**プログレス報告**: インデックス構築は全画面を走査するためコストが高い。`ctx.report_progress` を使ってユーザーに進捗を報告する:

```python
total = len(all_routes)
for i, route in enumerate(all_routes):
    await ctx.report_progress(progress=i, total=total)
    # screen_to_source の処理
```

**キャッシュ戦略**: `rails_lens_refresh_cache` と連動し、初回構築後はキャッシュから返す。以下のファイルの mtime が変更された場合にキャッシュを無効化する:
- `config/routes.rb`（および `config/routes/*.rb`）
- `app/views/**/*`
- `app/helpers/**/*`
- `config/locales/**/*`

#### 既存ツールとの連携

| 既存ツール | 連携方法 |
|---|---|
| `rails_lens_get_routes` | 全ルーティングの取得に使用 |
| `rails_lens_find_references` | パーシャル名やヘルパーメソッド名の逆引きに `GrepSearch` を共有 |
| `rails_lens_introspect_model` | モデルの属性一覧との照合でビュー内のモデル参照を正確に判定 |
| `rails_lens_impact_analysis` (A-1) | 相互補完。`impact_analysis` はモデル変更の影響を返すが、ビューレイヤーの影響は `source_to_screens` がより正確 |

#### 難易度と工数: L（大）

- 全画面走査 + 逆引きインデックス構築が必要で、処理量が大きい
- インデックスのキャッシュ管理（無効化条件の判定）が複雑
- プログレス報告の実装が必要
- `screen_to_source` の実装が前提（依存関係）

---

### H-1. モード3: full_inventory（画面台帳の自動生成）

#### 解決する課題と利用シーン

**課題**: ドキュメントが整備されていないRailsプロジェクトでは「このアプリは何画面あるの？」「どのリソースがどの画面で管理されているの？」という基本的な質問にすら即答できない。新しくプロジェクトに参加したメンバーは、コードを読み歩いて全体像を把握するしかない。

**利用シーン**:
- プロジェクト参加直後の全体把握（「このアプリは何画面あるの？」に即答）
- API化プロジェクトの棚卸し（`endpoint_inventory` と組み合わせて Web/API の対応状況を把握）
- 新メンバーへのオンボーディング資料の自動生成
- テスト計画の策定（どの画面をテストすべきか、影響度の高い共有パーシャルはどれか）
- プロジェクトマネージャーへの画面一覧の提供（非エンジニアでも理解できる形式）

#### 入力スキーマ

```json
{
  "type": "object",
  "required": ["mode"],
  "properties": {
    "mode": {
      "type": "string",
      "const": "full_inventory"
    },
    "format": {
      "type": "string",
      "description": "出力形式",
      "enum": ["json", "markdown"],
      "default": "json"
    },
    "include_api": {
      "type": "boolean",
      "description": "APIエンドポイントも含めるか",
      "default": true
    },
    "group_by": {
      "type": "string",
      "description": "グルーピング方法",
      "enum": ["namespace", "resource", "flat"],
      "default": "namespace"
    },
    "locale": {
      "type": "string",
      "description": "画面名推定の言語",
      "default": "ja"
    }
  }
}
```

#### Pydanticモデル定義（出力）

```python
class ScreenEntry(BaseModel):
    screen_name: str                  # "ユーザー一覧"
    url_pattern: str                  # "GET /users"
    http_method: str                  # "GET"
    controller_action: str            # "UsersController#index"
    template: str | None = None       # "users/index.html.erb"
    partial_count: int = 0
    models: list[str] = Field(default_factory=list)
    is_api: bool = False
    serializer: str | None = None     # APIの場合のシリアライザ名


class SharedPartialEntry(BaseModel):
    file: str                         # "shared/_navigation.html.erb"
    screen_count: int | str = 0       # 数値 or "all (layout)"
    impact_level: str = "low"


class ScreenGroup(BaseModel):
    group_name: str                   # "ユーザー管理" / "Admin" / etc.
    screens: list[ScreenEntry] = Field(default_factory=list)


class FullInventoryOutput(BaseModel):
    generated_at: str                 # ISO 8601 形式の生成日時
    total_screen_count: int = 0
    web_screen_count: int = 0
    api_endpoint_count: int = 0
    groups: list[ScreenGroup] = Field(default_factory=list)
    shared_partials: list[SharedPartialEntry] = Field(default_factory=list)
    markdown: str | None = None       # format="markdown" の場合のみ
```

#### 出力例（markdown形式）

```markdown
# 画面台帳（自動生成）

> このドキュメントは rails-lens の `full_inventory` モードにより自動生成されました。
> 生成日時: 2026-03-31T10:00:00+09:00
> 画面数: 14（Web: 10, API: 4）

## Web画面

### ユーザー管理
| 画面名 | URL | コントローラ | テンプレート | パーシャル数 | モデル |
|---|---|---|---|---|---|
| ユーザー一覧 | GET /users | UsersController#index | users/index.html.erb | 2 | User, Company |
| ユーザー詳細 | GET /users/:id | UsersController#show | users/show.html.erb | 3 | User, Order, Post, Tag |
| ユーザー新規作成 | GET /users/new | UsersController#new | users/new.html.erb | 1 | User, Company |
| ユーザー編集 | GET /users/:id/edit | UsersController#edit | users/edit.html.erb | 1 | User, Company |

### 注文管理
| 画面名 | URL | コントローラ | テンプレート | パーシャル数 | モデル |
|---|---|---|---|---|---|
| 注文一覧 | GET /orders | OrdersController#index | orders/index.html.erb | 1 | Order, User |
| 注文詳細 | GET /orders/:id | OrdersController#show | orders/show.html.erb | 4 | Order, OrderItem, Product, Payment |

### 管理画面
| 画面名 | URL | コントローラ | テンプレート | パーシャル数 | モデル |
|---|---|---|---|---|---|
| 企業一覧 | GET /admin/companies | Admin::CompaniesController#index | admin/companies/index.html.erb | 1 | Company |
| 企業詳細 | GET /admin/companies/:id | Admin::CompaniesController#show | admin/companies/show.html.erb | 2 | Company, User |

## APIエンドポイント
| エンドポイント名 | URL | コントローラ | シリアライザ | モデル |
|---|---|---|---|---|
| ユーザー一覧 API | GET /api/v1/users | Api::V1::UsersController#index | UserSerializer | User, Company |
| ユーザー詳細 API | GET /api/v1/users/:id | Api::V1::UsersController#show | UserSerializer | User, Company |
| 注文作成 API | POST /api/v1/orders | Api::V1::OrdersController#create | OrderDetailSerializer | Order, OrderItem |
| 注文詳細 API | GET /api/v1/orders/:id | Api::V1::OrdersController#show | OrderDetailSerializer | Order, OrderItem |

## 共有パーシャル使用状況
| パーシャル | 使用画面数 | 影響レベル |
|---|---|---|
| shared/_navigation.html.erb | 全画面 (layout) | critical |
| shared/_flash_messages.html.erb | 全画面 (layout) | critical |
| users/_user_card.html.erb | 3 | moderate |
| posts/_post_card.html.erb | 2 | moderate |
```

#### 出力例（json形式、抜粋）

```json
{
  "generated_at": "2026-03-31T10:00:00+09:00",
  "total_screen_count": 14,
  "web_screen_count": 10,
  "api_endpoint_count": 4,
  "groups": [
    {
      "group_name": "ユーザー管理",
      "screens": [
        {
          "screen_name": "ユーザー一覧",
          "url_pattern": "/users",
          "http_method": "GET",
          "controller_action": "UsersController#index",
          "template": "users/index.html.erb",
          "partial_count": 2,
          "models": ["User", "Company"],
          "is_api": false,
          "serializer": null
        }
      ]
    }
  ],
  "shared_partials": [
    {
      "file": "shared/_navigation.html.erb",
      "screen_count": "all (layout)",
      "impact_level": "critical"
    }
  ],
  "markdown": null
}
```

#### グルーピングロジック

| `group_by` | グルーピング方法 | 例 |
|---|---|---|
| `namespace` | コントローラの名前空間でグルーピング | `Admin::` → "管理画面"、`Api::V1::` → "API v1"、名前空間なし → リソース名で推定 |
| `resource` | リソース（モデル）単位でグルーピング | User に関する画面を全てまとめる（Web + API） |
| `flat` | グルーピングなし | 全画面をフラットに一覧 |

`namespace` モードでの名前空間がないコントローラは、リソース名から日本語グループ名を推定する:
- `UsersController` → "ユーザー管理"
- `OrdersController` → "注文管理"
- `ProductsController` → "商品管理"

#### APIエンドポイントの検出

テンプレートを持たないアクション（APIエンドポイント）は以下の方法で検出する:

1. `respond_to` ブロック内で `:json` のみに応答するアクション
2. `ActionController::API` を継承するコントローラ
3. 名前空間が `Api::` で始まるコントローラ

APIエンドポイントの場合、テンプレートの代わりにシリアライザを検出する:

| シリアライザ種別 | 検出方法 |
|---|---|
| ActiveModelSerializers | コントローラまたはモデルと同名の `*Serializer` クラスの存在 |
| Blueprinter | `app/blueprints/` ディレクトリの走査 |
| jbuilder | `app/views/**/*.json.jbuilder` ファイルの存在 |
| JSONAPI::Serializer | `app/serializers/` 内の `JSONAPI::Serializer` を include するクラス |

#### 実装方針: ハイブリッド（ランタイム + 静的解析）

`full_inventory` は `screen_to_source` の結果を全ルーティングに対して実行し、集約する。

**処理フロー**:

```
1. bridge.execute("dump_view_mapping.rb", ["all"])
   → 全ルーティング + layout 解決 + i18n ダンプ
2. 各ルーティングに対して screen_to_source の処理を実行
   → 各画面の構成ファイルを取得（キャッシュ利用）
3. グルーピングロジックを適用
4. 共有パーシャルの使用状況を集計
5. format に応じて出力を整形
   → "json": FullInventoryOutput を JSON 文字列化
   → "markdown": Markdown テンプレートに流し込み
```

**プログレス報告**: `source_to_screens` と同様に `ctx.report_progress` を使用する。

#### 既存ツールとの連携

| 既存ツール | 連携方法 |
|---|---|
| `rails_lens_get_routes` | 全ルーティングの取得に使用 |
| `rails_lens_endpoint_inventory` (G-1) | API エンドポイントの詳細情報を補完。`full_inventory` の `include_api: true` 時に統合 |
| `rails_lens_view_model_coupling` (G-3) | 相互補完。`view_model_coupling` はモデル起点、`full_inventory` は画面起点で同じ情報に異なるアングルからアクセス |
| `rails_lens_response_shape_suggest` (F-1) | `screen_to_source` の結果（画面が参照しているモデル属性）を入力として使用可能 |

#### 難易度と工数: M（中）

- `screen_to_source` の結果を集約するだけなので、解析ロジック自体の新規開発は少ない
- markdown 出力のフォーマット整形に工数がかかる（テーブル生成、グルーピング、日本語リソース名推定）
- APIエンドポイントのシリアライザ検出は新規実装が必要

---

## 4. 実装方針

### 解析手法: ハイブリッド

#### ランタイム解析（Ruby側）が必要な部分

| 解析対象 | 方法 | 理由 |
|---|---|---|
| ルーティング情報の取得 | `rails runner` で `Rails.application.routes.routes` を走査 | 規約外のルーティング（`constraints`、`mount` 等）を正確に取得するため |
| i18n の全キーと値 | `I18n.backend.translations` のダンプ | YAML ファイルの直接パースでは ERB 埋め込み型の locale に対応できないため |
| layout の解決 | コントローラごとの `_layout` メソッド呼び出し | コントローラごとに異なる layout が指定されている可能性があるため |
| render の明示指定の検出 | コントローラのアクションメソッド内の `render` 呼び出しを解析 | 動的な render（`render action: params[:type]`）はランタイムでしか解決できない |

#### 静的解析（Python側）で完結する部分

| 解析対象 | 方法 |
|---|---|
| パーシャルの再帰的解決 | テンプレート内の `render partial:` / `render "xxx"` / `render collection:` パターンの正規表現マッチ |
| ヘルパーメソッドの呼び出し検出 | `<%= method_name(...) %>` パターンの grep + ヘルパーファイル内の `def` 定義との照合 |
| モデル属性参照の抽出 | `@model.attribute` / `model.attribute` パターンの正規表現マッチ |
| 画面名推定（テンプレート内） | `<title>`、`<h1>`、`content_for :title`、`provide :title` の抽出 |
| ハードコードテキストの検出 | HTML タグ外の日本語・英語平文テキストの抽出 |
| Stimulus コントローラの検出 | `data-controller="xxx"` パターンの grep |
| Decorator/Presenter の検出 | `app/decorators/`、`app/presenters/` ディレクトリの走査 + クラス名照合 |
| シリアライザの検出 | `app/serializers/`、`app/blueprints/` ディレクトリの走査 + jbuilder ファイルの検出 |

### 必要な Ruby スクリプト

**`ruby/dump_view_mapping.rb`**（新規）:

```ruby
# 入力: mode ("single" or "all"), controller_action (single mode のみ)
# 出力: JSON
#
# single mode:
#   - 指定された controller#action のルーティング情報
#   - そのコントローラの layout
#   - アクション内の明示 render 指定
#   - i18n キー（そのコントローラ・アクションに関連するもの）
#
# all mode:
#   - 全ルーティング一覧（verb, path, controller#action, name）
#   - 各コントローラの layout 解決結果
#   - i18n の全キーと値のダンプ（画面名推定に使用）
```

出力形式:

```json
{
  "routes": [
    {
      "verb": "GET",
      "path": "/users/:id",
      "controller": "UsersController",
      "action": "show",
      "name": "user",
      "url_pattern": "/users/:id"
    }
  ],
  "layouts": {
    "UsersController": "application",
    "Admin::CompaniesController": "admin"
  },
  "explicit_renders": {
    "UsersController#profile": "users/show"
  },
  "i18n_translations": {
    "ja": {
      "users": {
        "show": {
          "title": "ユーザー詳細"
        }
      }
    }
  }
}
```

既存の `ruby/dump_routes.rb` との関係: `dump_routes.rb` はルーティング情報のみを返すが、`dump_view_mapping.rb` は layout 解決と i18n ダンプを追加する。`dump_routes.rb` のルーティング取得ロジックを内部的に再利用する（共通ヘルパーへの切り出しを検討）。

### パーシャルの再帰的解決アルゴリズム

```
resolve_partials(template_file, visited=set()):
  1. visited に template_file を追加（循環参照防止）
  2. template_file を読み込む
  3. render パターンを全て抽出:
     - render partial: "xxx"
     - render "xxx"（パーシャル省略記法）
     - render @collection（コレクションレンダリング）
     - render partial: "xxx", collection: @items
  4. 各パーシャルに対して:
     a. パーシャル名をファイルパスに解決
     b. visited に含まれていなければ再帰的に resolve_partials を呼ぶ
     c. 結果をツリー構造で返す
  5. layout ファイルも同様に処理する
```

パーシャル名の解決規則:

| パターン | 解決先 | 例 |
|---|---|---|
| `render partial: "shared/navigation"` | `app/views/shared/_navigation.html.{erb,haml,slim}` | 明示的パーシャル指定 |
| `render "users/header"` | `app/views/users/_header.html.{erb,haml,slim}` | パーシャル省略記法 |
| `render @users` | `app/views/users/_user.html.{erb,haml,slim}` | コレクションレンダリング（モデル名から推定） |
| `render partial: "posts/card", collection: @posts` | `app/views/posts/_card.html.{erb,haml,slim}` | 明示的コレクション |
| `render partial: "form"` | 同ディレクトリの `_form.html.{erb,haml,slim}` | 相対パス |

テンプレートエンジンごとの render 検出パターン:

| エンジン | パターン |
|---|---|
| ERB | `<%= render partial: "..."` / `<%= render "..."` / `<%= render @...` |
| Haml | `= render partial: "..."` / `= render "..."` / `= render @...` |
| Slim | `= render partial: "..."` / `== render "..."` / `= render @...` |

### 逆引きインデックスの構造

```json
{
  "partial_index": {
    "app/views/shared/_navigation.html.erb": [
      {
        "controller_action": "UsersController#index",
        "url_pattern": "/users",
        "screen_name": "ユーザー一覧",
        "included_via": "app/views/layouts/application.html.erb:15"
      }
    ]
  },
  "helper_index": {
    "user_status_badge": [
      {
        "controller_action": "UsersController#show",
        "url_pattern": "/users/:id",
        "screen_name": "ユーザー詳細",
        "called_from": "app/views/users/show.html.erb:8"
      }
    ]
  },
  "model_index": {
    "User": [
      {
        "controller_action": "UsersController#index",
        "url_pattern": "/users",
        "screen_name": "ユーザー一覧",
        "attributes_used": ["name", "email", "role"]
      }
    ]
  }
}
```

---

## 5. 既存ツールとの連携

### 連携マトリクス

| 既存ツール | screen_to_source | source_to_screens | full_inventory | 連携の詳細 |
|---|---|---|---|---|
| `rails_lens_get_routes` | ○ | ○ | ○ | ルーティング情報の取得。URL → controller#action の解決 |
| `rails_lens_introspect_model` | ○ | ○ | - | モデルの属性・メソッド情報をキャッシュから照合し、ビュー内のモデル参照を正確に特定 |
| `rails_lens_find_references` | ○ | ○ | - | `GrepSearch` の共有。ヘルパーメソッドやパーシャルの参照検索 |
| `rails_lens_impact_analysis` (A-1) | - | ○ | - | 相互補完。`impact_analysis` はモデル変更の影響を返すが、ビューレイヤーの影響は `source_to_screens` がより正確 |
| `rails_lens_view_model_coupling` (G-3) | - | - | ○ | 相互補完。`view_model_coupling` はモデル起点、`screen_map` は画面起点 |
| `rails_lens_response_shape_suggest` (F-1) | ○ | - | - | `screen_to_source` の結果（画面が参照しているモデル属性）を入力として使用可能（API レスポンス候補の推定） |
| `rails_lens_endpoint_inventory` (G-1) | - | - | ○ | `full_inventory` と統合して、Web画面とAPIの対応状況を一覧化 |
| `rails_lens_refresh_cache` | - | ○ | ○ | 逆引きインデックスの再構築トリガー |

### ワークフロー例

#### 画面変更ワークフロー

```
1. screen_to_source(url="/users/123")
   → 画面を構成する全ファイルを把握
2. 必要なファイルを修正
3. source_to_screens(file_path="app/views/shared/_header.html.erb")
   → 修正したパーシャルが他のどの画面に影響するか確認
4. impact_analysis(model_name="User", target="email")
   → モデル変更の波及範囲を確認
```

#### オンボーディングワークフロー

```
1. full_inventory(format="markdown", group_by="namespace", locale="ja")
   → 全画面の台帳を自動生成
2. endpoint_inventory(scope="all")
   → API エンドポイントの詳細を補完
3. 必要に応じて screen_to_source で個別画面の詳細を確認
```

---

## 6. 実装フェーズとマイルストーン

### Phase H-1: 基盤構築

| タスク | 内容 | 成果物 |
|---|---|---|
| H-1.1 | Ruby スクリプト `dump_view_mapping.rb` の実装 | `ruby/dump_view_mapping.rb` |
| H-1.2 | パーシャル再帰的解決エンジンの実装 | `analyzers/view_resolver.py` |
| H-1.3 | テンプレートエンジン別のパターンマッチャー | `analyzers/template_parser.py` |

### Phase H-2: screen_to_source の実装

| タスク | 内容 | 成果物 |
|---|---|---|
| H-2.1 | `screen_to_source` モードの実装 | `tools/screen_map.py` |
| H-2.2 | 画面名推定ロジックの実装 | `analyzers/screen_name_resolver.py` |
| H-2.3 | ヘルパー・モデル参照の抽出 | `analyzers/view_resolver.py` に追加 |
| H-2.4 | テスト（単体 + 統合） | `tests/test_screen_map.py` |

### Phase H-3: source_to_screens の実装

| タスク | 内容 | 成果物 |
|---|---|---|
| H-3.1 | 逆引きインデックスの構築ロジック | `analyzers/reverse_index_builder.py` |
| H-3.2 | `source_to_screens` モードの実装 | `tools/screen_map.py` に追加 |
| H-3.3 | キャッシュ管理（逆引きインデックス用） | `cache/manager.py` に追加 |
| H-3.4 | プログレス報告の実装 | `tools/screen_map.py` に追加 |
| H-3.5 | テスト（単体 + 統合） | `tests/test_screen_map.py` に追加 |

### Phase H-4: full_inventory の実装

| タスク | 内容 | 成果物 |
|---|---|---|
| H-4.1 | `full_inventory` モードの実装 | `tools/screen_map.py` に追加 |
| H-4.2 | markdown 出力テンプレートの実装 | `analyzers/inventory_formatter.py` |
| H-4.3 | APIエンドポイント・シリアライザ検出 | `analyzers/api_detector.py` |
| H-4.4 | テスト（単体 + 統合） | `tests/test_screen_map.py` に追加 |

### 依存関係

```
Phase H-1 → Phase H-2 → Phase H-3 → Phase H-4
                           ↑
                     Phase H-2 の結果をキャッシュとして再利用
```

### 工数見積もり

| フェーズ | 難易度 | 主な工数要因 |
|---|---|---|
| H-1（基盤構築） | M | Ruby スクリプト + パーシャル再帰解決。3テンプレートエンジン対応 |
| H-2（screen_to_source） | M | 画面名推定ロジック + ヘルパー/モデル参照抽出の精度調整 |
| H-3（source_to_screens） | L | 全画面走査 + 逆引きインデックス + キャッシュ管理 + プログレス報告 |
| H-4（full_inventory） | M | markdown 出力の整形 + グルーピングロジック + シリアライザ検出 |
