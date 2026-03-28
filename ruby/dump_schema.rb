# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  conn = ActiveRecord::Base.connection

  tables = conn.tables.sort.map do |table_name|
    columns = conn.columns(table_name).map do |col|
      {
        name: col.name,
        type: col.type.to_s,
        null: col.null,
        default: col.default,
        limit: col.limit,
      }
    end

    indexes = begin
      conn.indexes(table_name).map do |idx|
        {
          name: idx.name,
          columns: idx.columns,
          unique: idx.unique,
        }
      end
    rescue => e
      $stderr.puts "Warning: Failed to get indexes for #{table_name}: #{e.message}"
      []
    end

    {
      name: table_name,
      columns: columns,
      indexes: indexes,
    }
  end

  RailsLens::Serializer.output({ tables: tables })

rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
