# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  # 全てのモデルを確実にロードする
  Rails.application.eager_load!

  models = ActiveRecord::Base.descendants
    .reject(&:abstract_class?)
    .select { |k| k.name.present? }
    .sort_by(&:name)
    .map do |klass|
      {
        name: klass.name,
        table_name: begin; klass.table_name; rescue; nil; end,
        file_path: "app/models/#{klass.name.underscore}.rb",
      }
    end

  RailsLens::Serializer.output({ models: models })

rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
