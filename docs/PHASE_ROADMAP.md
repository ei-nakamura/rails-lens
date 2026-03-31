# Rails-Lens Implementation Roadmap

Phase 1-8: 実装済み (18 tools)
Phase 9-20: 未実装 (20 tools + 3 modes)

---

## 実装順の方針

| 優先度 | 理由 |
|--------|------|
| 画面マッピングを最優先 | 依存は Phase 2, 4 のみ（実装済み）。独立性が高く先行実装に最適 |
| SQL診断を次に | 既存の get_schema / introspect_model 上に構築。即座に価値を出せる |
| セキュリティ・性能をその次に | SQL診断と同じ「ルールエンジン+静的解析」パターン。アーキテクチャを再利用 |
| API化支援を最後に | 最大規模（12ツール）。Phase 1-8の全ツールに依存。集大成 |

---

## Phase 9: 画面マッピング 基盤 + screen_to_source

**設計書**: `SCREEN_MAP_FEATURE.md` Phase H-1, H-2

| ツール | モード | 概要 |
|--------|--------|------|
| `screen_map` | `screen_to_source` | 画面名 -> 関連ソースファイル一覧 |

**成果物**: `ruby/dump_view_mapping.rb`, `analyzers/view_resolver.py`, `analyzers/template_parser.py`, `analyzers/screen_name_resolver.py`, `tools/screen_map.py`
**依存**: get_routes (Phase 4), find_references (Phase 2)
**対応テンプレートエンジン**: ERB, Haml, Slim
**難易度**: M

---

## Phase 10: 画面マッピング source_to_screens + full_inventory

**設計書**: `SCREEN_MAP_FEATURE.md` Phase H-3, H-4

| モード | 概要 |
|--------|------|
| `source_to_screens` | ソースファイル -> 影響を受ける画面一覧 |
| `full_inventory` | 画面台帳の自動生成 (Markdown出力) |

**成果物**: `analyzers/reverse_index_builder.py`, `analyzers/inventory_formatter.py`, `analyzers/api_detector.py`
**依存**: Phase 9 (screen_map 基盤)
**難易度**: M

---

## Phase 11: SQL診断 基本実装

**設計書**: `SQL_DIAGNOSTICS_FEATURE.md` Phase 1-2

| ツール | 概要 | ルール数 |
|--------|------|---------|
| `schema_audit` | スキーマ設計問題の検出 | IDX001-002, COL001, TBL001 (4 rules) |
| `query_audit` | クエリの安全性・効率性検査 | SEC001-002, PERF001, PERF003 (4 rules) |

**成果物**: `analyzers/schema_audit.py`, `analyzers/query_audit.py`, `tools/schema_audit.py`, `tools/query_audit.py`
**依存**: get_schema (Phase 4), introspect_model (Phase 1)
**難易度**: M

---

## Phase 12: SQL診断 ルール拡充 + 誤検知抑制

**設計書**: `SQL_DIAGNOSTICS_FEATURE.md` Phase 3-5

| 内容 | 詳細 |
|------|------|
| 残りルール追加 | IDX003-006, COL002-004, TBL002-003, SEC003, PERF002-008, DATA001-003 (22 rules) |
| introspect_model連携 | IDX004, COL004をキャッシュ連携で強化 |
| 誤検知抑制 | `# rails-lens:disable` コメント対応 |
| ダッシュボード | `web/templates/sql.html` + マイグレーションヒント生成 |

**難易度**: M-H

---

## Phase 13: SQL query_preview

**設計書**: `SQL_DIAGNOSTICS_FEATURE.md` Phase 6-7

| ツール | 概要 |
|--------|------|
| `query_preview` | ActiveRecord チェーン -> SQL 変換プレビュー |
| `query_preview_snippet` | コードスニペット入力対応版 |

**成果物**: `analyzers/query_preview.py`, `tools/query_preview.py` (snippet含む)
**依存**: Phase 11-12 (schema_audit, query_audit の基盤)
**難易度**: L (最も複雑なSQL診断ツール)

---

## Phase 14: セキュリティ診断

**設計書**: `SECURITY_PERFORMANCE_AUDIT.md` Phase 1-2

| ツール | 概要 | ルール数 |
|--------|------|---------|
| `security_audit` | セキュリティ問題の静的検出 | CRED001-007, LOG001-005, AUTH001-005, EXP001-005, DEP001-002 (24 rules) |

**成果物**: `analyzers/security_audit.py`, `tools/security_audit.py`
**依存**: find_references (Phase 2), get_routes (Phase 4)
**難易度**: M (工数 ~14h)

---

## Phase 15: パフォーマンス診断 + ボトルネックランキング

**設計書**: `SECURITY_PERFORMANCE_AUDIT.md` Phase 3-4

| ツール | 概要 | ルール数 |
|--------|------|---------|
| `performance_audit` | パフォーマンス問題検出 | MEM001-003, TXN001-004, SYNC001-004, QEXT001-003 (14 rules) |
| `bottleneck_ranking` | エンドポイントのスコアリング・ランキング | - |

**成果物**: `analyzers/performance_audit.py`, `analyzers/bottleneck_ranking.py`, `tools/performance_audit.py`, `tools/bottleneck_ranking.py`
**依存**: Phase 14 (security_audit のルールエンジン再利用), query_audit (Phase 11)
**難易度**: H (工数 ~16h)

---

## Phase 16: セキュリティ・パフォーマンス ダッシュボード + テスト

**設計書**: `SECURITY_PERFORMANCE_AUDIT.md` Phase 5-6

| 内容 | 詳細 |
|------|------|
| ダッシュボード統合 | `web/templates/audit.html`, `audit_endpoint.html` |
| TOML設定対応 | ルール有効化/無効化の設定ファイル |
| テスト・ドキュメント | 全ルールのテストケース + README更新 |

**難易度**: M (工数 ~10h)

---

## Phase 17: API化支援 基盤ツール

**設計書**: `API_MIGRATION_FEATURES.md` Phase 9

| ツール | ID | 概要 | 難易度 |
|--------|----|------|--------|
| `endpoint_inventory` | G-1 | 全エンドポイント棚卸し + 複雑度算出 | M |
| `before_action_chain` | G-2 | 継承チェーン上の全フィルタ取得 | M |
| `exposure_check` | F-4 | 機密属性の漏洩リスク検出 | S |

**成果物**: `ruby/before_action_chain.rb`, `tools/endpoint_inventory.py`, `tools/before_action_chain.py`, `tools/exposure_check.py`
**依存**: get_routes (Phase 4), introspect_model (Phase 1)
**難易度**: M

---

## Phase 18: API レスポンス設計・監査ツール

**設計書**: `API_MIGRATION_FEATURES.md` Phase 10

| ツール | ID | 概要 | 難易度 |
|--------|----|------|--------|
| `response_shape_suggest` | F-1 | シリアライザ/jbuilder/ビューからレスポンス構造を提案 | M |
| `api_audit` | F-2 | API規約検出・違反レポート (6カテゴリ) | M |
| `n_plus_one_detector` | F-3 | N+1クエリの可能性を静的検出 | M |
| `redundancy_detector` | F-5 | 冗長な呼び出しを検出 | L |

**成果物**: `ruby/response_shape.rb`, `tools/response_shape_suggest.py`, `tools/api_audit.py`, `tools/n_plus_one_detector.py`, `tools/redundancy_detector.py`
**依存**: Phase 17 (endpoint_inventory, before_action_chain)
**難易度**: H

---

## Phase 19: API 仕様生成・ビュー解析

**設計書**: `API_MIGRATION_FEATURES.md` Phase 11

| ツール | ID | 概要 | 難易度 |
|--------|----|------|--------|
| `generate_openapi` | E-1 | OpenAPI 3.1 spec 自動生成 (Phase 17-18の集大成) | L |
| `view_model_coupling` | G-3 | ビュー-モデル依存関係の可視化 | M |
| `service_extraction_map` | G-4 | Fat Controller -> Service Object 抽出候補提案 | M |

**依存**: Phase 17, 18 (全ツールの出力を統合)
**難易度**: H

---

## Phase 20: API コード生成

**設計書**: `API_MIGRATION_FEATURES.md` Phase 12

| ツール | ID | 概要 | 難易度 |
|--------|----|------|--------|
| `generate_pydantic_models` | E-2 | Rails モデル -> Pydantic モデル生成 (api/orm 2スタイル) | M |
| `logic_extract` | E-3 | ビジネスロジック構造化抽出 (shallow/deep 2モード) | L |

**成果物**: `ruby/logic_extract.rb`, `tools/generate_pydantic_models.py`, `tools/logic_extract.py`
**依存**: Phase 19 (generate_openapi, service_extraction_map)
**難易度**: H (Ruby parser gem 導入が必要)

---

## 全体サマリー

| Phase | 機能群 | ツール数 | 状態 |
|-------|--------|---------|------|
| 1-4 | 基盤 MVP + ユーティリティ | 9 | ✅ 実装済み |
| 5-8 | メソッド解決 + データフロー | 9 | ✅ 実装済み |
| **9** | **画面マッピング 基盤** | **1 (1 mode)** | 未実装 |
| **10** | **画面マッピング 拡張** | **- (2 modes)** | 未実装 |
| **11** | **SQL診断 基本** | **2** | 未実装 |
| **12** | **SQL診断 ルール拡充** | **-** | 未実装 |
| **13** | **SQL query_preview** | **2** | 未実装 |
| **14** | **セキュリティ診断** | **1** | 未実装 |
| **15** | **パフォーマンス診断 + ランキング** | **2** | 未実装 |
| **16** | **セキュリティ・パフォーマンス ダッシュボード** | **-** | 未実装 |
| **17** | **API化支援 基盤** | **3** | 未実装 |
| **18** | **API レスポンス・監査** | **4** | 未実装 |
| **19** | **API 仕様生成** | **3** | 未実装 |
| **20** | **API コード生成** | **2** | 未実装 |
| **合計** | | **38 tools + 3 modes** | **18 done / 20 remaining** |

## 依存関係図

```
Phase 1-8 (実装済み)
  │
  ├── Phase 9 (Screen基盤) → Phase 10 (Screen拡張)
  │
  ├── Phase 11 (SQL基本) → Phase 12 (SQL拡充) → Phase 13 (query_preview)
  │
  ├── Phase 14 (Security) → Phase 15 (Performance) → Phase 16 (Dashboard+Test)
  │
  └── Phase 17 (API基盤) → Phase 18 (API監査) → Phase 19 (API仕様生成) → Phase 20 (APIコード生成)
```

4系統は互いに独立しており、並行実装が可能。
推奨実装順: Screen Map (9-10) → SQL (11-13) → Security/Performance (14-16) → API Migration (17-20)
