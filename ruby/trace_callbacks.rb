# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name = ARGV[0]
  lifecycle_event = ARGV[1]

  unless model_name && lifecycle_event
    RailsLens::Serializer.error(
      'Usage: trace_callbacks.rb <ModelName> <lifecycle_event>'
    )
    exit 0
  end

  klass = model_name.constantize

  # イベントに関連するコールバック種別のマッピング
  event_to_callbacks = {
    'save'     => %w[before_save around_save after_save],
    'create'   => %w[before_validation after_validation before_save before_create around_create after_create after_save after_commit],
    'update'   => %w[before_validation after_validation before_save before_update around_update after_update after_save after_commit],
    'destroy'  => %w[before_destroy around_destroy after_destroy after_commit],
    'validate' => %w[before_validation after_validation],
    'commit'   => %w[after_commit after_rollback],
  }

  target_callbacks = event_to_callbacks[lifecycle_event]
  unless target_callbacks
    RailsLens::Serializer.error(
      "Unknown lifecycle event: '#{lifecycle_event}'. " \
      "Valid events: #{event_to_callbacks.keys.join(', ')}"
    )
    exit 0
  end

  execution_order = []
  order_counter = 0

  target_callbacks.each do |cb_kind|
    parts = cb_kind.split('_', 2)
    kind = parts[0]
    event = parts[1]

    callbacks = klass.__callbacks[event.to_sym]
    next unless callbacks

    callbacks.each do |callback|
      next unless callback.kind.to_s == kind

      order_counter += 1
      filter = callback.filter
      method_name = filter.is_a?(Symbol) ? filter.to_s : filter.to_s

      # ソースロケーション取得
      source_file = nil
      source_line = nil
      defined_in_concern = nil

      if filter.is_a?(Symbol) && klass.method_defined?(filter)
        method_obj = klass.instance_method(filter)
        loc = method_obj.source_location
        if loc
          source_file = loc[0]
          source_line = loc[1]
          owner = method_obj.owner
          defined_in_concern = owner.name if owner != klass && owner.is_a?(Module)
        end
      end

      # conditions の取得
      conditions = {}
      if_cond = callback.instance_variable_get(:@if)
      unless_cond = callback.instance_variable_get(:@unless)
      conditions[:if] = if_cond.first.to_s if if_cond&.any?
      conditions[:unless] = unless_cond.first.to_s if unless_cond&.any?

      execution_order << {
        order: order_counter,
        kind: kind,
        method_name: method_name,
        source_file: source_file,
        source_line: source_line,
        defined_in_concern: defined_in_concern,
        conditions: conditions,
        note: nil,
      }
    end
  end

  RailsLens::Serializer.output({
    model_name: klass.name,
    lifecycle_event: lifecycle_event,
    execution_order: execution_order,
  })

rescue NameError
  all_models = ActiveRecord::Base.descendants.map(&:name).compact.sort
  RailsLens::Serializer.error(
    "Model '#{model_name}' not found.",
    details: { all_models: all_models }
  )
rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
