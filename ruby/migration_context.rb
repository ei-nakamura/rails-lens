# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  table_name = ARGV[0].to_s.strip
  operation  = ARGV[1].to_s.strip
  operation  = 'general' if operation.empty?

  unless table_name && !table_name.empty?
    RailsLens::Serializer.error('table_name is required as first argument')
    exit 0
  end

  conn = ActiveRecord::Base.connection

  # ── カラム情報 ─────────────────────────────────────────────
  columns = begin
    conn.columns(table_name).map do |col|
      {
        name: col.name,
        type: col.sql_type,
        null: col.null,
        default: col.default,
        limit: col.limit,
      }
    end
  rescue => e
    RailsLens::Serializer.error("Table '#{table_name}' not found: #{e.message}")
    exit 0
  end

  # ── インデックス情報 ────────────────────────────────────────
  indexes = begin
    conn.indexes(table_name).map do |idx|
      {
        name: idx.name,
        columns: idx.columns,
        unique: idx.unique,
      }
    end
  rescue StandardError
    []
  end

  # ── 外部キー情報 ────────────────────────────────────────────
  foreign_keys = begin
    conn.foreign_keys(table_name).map do |fk|
      {
        from_column: fk.column,
        to_table: fk.to_table,
        to_column: fk.primary_key || 'id',
      }
    end
  rescue StandardError
    []
  end

  # ── 概算行数 (PostgreSQL) ───────────────────────────────────
  estimated_row_count = begin
    result = conn.exec_query(
      "SELECT reltuples::bigint AS row_count FROM pg_class WHERE relname = $1",
      'estimated_row_count',
      [table_name]
    )
    result.first&.dig('row_count')&.to_i
  rescue StandardError
    nil
  end

  # ── マイグレーション履歴 ────────────────────────────────────
  migration_history = begin
    ActiveRecord::SchemaMigration.all.order(version: :desc).limit(20).map do |sm|
      { version: sm.version }
    end
  rescue StandardError
    []
  end

  RailsLens::Serializer.output({
    table_name: table_name,
    operation: operation,
    columns: columns,
    indexes: indexes,
    foreign_keys: foreign_keys,
    estimated_row_count: estimated_row_count,
    migration_history: migration_history,
  })

rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
