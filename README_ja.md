[English](README.md)

# rails-lens

AIコーディングツール向けに、Railsの暗黙的な依存関係を可視化するMCPサーバー。

## 概要

rails-lensは、RubyonRailsアプリケーションの構造を抽出し、Claude CodeやCursorなどのAIコーディングツールへ提供するMCP（Model Context Protocol）サーバーです。
コールバック、アソシエーション、コンサーン、動的メソッド生成といったRailsの暗黙的な依存関係をAIツールが理解できるよう支援します。

## インストール

pip install rails-lens

## 使い方

MCPクライアントの設定ファイル（~/.claude/claude_desktop_config.json）に以下を追加してください:

{
  "mcpServers": {
    "rails-lens": {
      "command": "rails-lens",
      "env": {
        "RAILS_LENS_PROJECT_PATH": "/path/to/your/rails/project"
      }
    }
  }
}

## 設定

Railsプロジェクトのルートディレクトリに .rails-lens.toml を作成してください:

[rails]
project_path = "/path/to/rails/project"
timeout = 30

[cache]
auto_invalidate = true

[search]
command = "rg"

## ライセンス

MIT
