# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name = ARGV[0]

  unless model_name
    RailsLens::Serializer.error('Model name is required as first argument')
    exit 0
  end

  # モデルクラスの取得
  begin
    klass = model_name.constantize
  rescue NameError
    # 全モデルリストを取得してサジェスト用に返す
    all_models = ActiveRecord::Base.descendants.map(&:name).compact.sort
    RailsLens::Serializer.error(
      "Model '#{model_name}' not found.",
      details: { all_models: all_models }
    )
    exit 0
  end

  unless klass < ActiveRecord::Base
    RailsLens::Serializer.error("'#{model_name}' is not an ActiveRecord model.")
    exit 0
  end

  data = {}

  # --- 基本情報 ---
  data[:model_name] = klass.name
  data[:table_name] = klass.table_name
  data[:file_path] = begin
    source_location = klass.instance_method(:initialize).source_location&.first
    # ApplicationRecordを指す場合はモデルファイルを推定
    source_location || "app/models/#{model_name.underscore}.rb"
  rescue
    "app/models/#{model_name.underscore}.rb"
  end

  # --- Associations ---
  data[:associations] = klass.reflect_on_all_associations.map do |ref|
    {
      name: ref.name.to_s,
      type: ref.macro.to_s,
      class_name: ref.class_name,
      foreign_key: ref.foreign_key.to_s,
      through: ref.is_a?(ActiveRecord::Reflection::ThroughReflection) ? ref.through_reflection.name.to_s : nil,
      polymorphic: ref.respond_to?(:polymorphic?) && ref.polymorphic?,
      dependent: ref.options[:dependent]&.to_s,
      has_scope: ref.scope ? true : false,
    }
  end

  # --- Callbacks ---
  callback_kinds = %i[
    before_validation after_validation
    before_save after_save around_save
    before_create after_create around_create
    before_update after_update around_update
    before_destroy after_destroy around_destroy
    after_commit after_rollback
    after_initialize after_find after_touch
  ]

  data[:callbacks] = []
  callback_kinds.each do |cb_kind|
    # ActiveSupport::Callbacks の内部構造にアクセス
    # cb_kind = :before_save → event = "save", kind = "before"
    kind_str = cb_kind.to_s
    parts = kind_str.split('_', 2)
    next unless parts.length == 2

    kind = parts[0]      # "before", "after", "around"
    event = parts[1]     # "save", "create", etc.

    begin
      callbacks = klass.__callbacks[event.to_sym]
      next unless callbacks

      callbacks.each do |callback|
        next unless callback.kind.to_s == kind

        filter = callback.filter
        method_name = case filter
                      when Symbol then filter.to_s
                      when String then filter
                      else filter.class.name || 'anonymous'
                      end

        # ソースロケーションの取得
        source_file = nil
        source_line = nil
        defined_in_concern = nil

        if filter.is_a?(Symbol) && klass.method_defined?(filter)
          method_obj = klass.instance_method(filter)
          loc = method_obj.source_location
          if loc
            source_file = loc[0]
            source_line = loc[1]

            # Concern判定: メソッドの定義元がklass自身でなければConcern
            owner = method_obj.owner
            if owner != klass && owner.is_a?(Module)
              defined_in_concern = owner.name
            end
          end
        end

        # conditions の取得
        conditions = {}
        if callback.instance_variable_defined?(:@if)
          if_conds = callback.instance_variable_get(:@if)
          conditions[:if] = if_conds.first.to_s unless if_conds.empty?
        end
        if callback.instance_variable_defined?(:@unless)
          unless_conds = callback.instance_variable_get(:@unless)
          conditions[:unless] = unless_conds.first.to_s unless unless_conds.empty?
        end

        data[:callbacks] << {
          kind: kind,
          event: event,
          method_name: method_name,
          source_file: source_file,
          source_line: source_line,
          conditions: conditions,
          defined_in_concern: defined_in_concern,
        }
      end
    rescue => e
      $stderr.puts "Warning: Failed to inspect callbacks for #{cb_kind}: #{e.message}"
    end
  end

  # --- Validations ---
  data[:validations] = klass.validators.map do |v|
    source_file = nil
    source_line = nil

    if v.respond_to?(:source_location)
      loc = v.source_location
      source_file = loc&.first
      source_line = loc&.last
    end

    {
      type: v.class.name.demodulize.underscore,
      attributes: v.attributes.map(&:to_s),
      options: v.options.transform_keys(&:to_s),
      custom_validator: v.is_a?(ActiveModel::Validations::WithValidator) ? v.options[:with]&.name : nil,
      source_file: source_file,
      source_line: source_line,
    }
  end

  # --- Scopes ---
  data[:scopes] = []
  if klass.respond_to?(:scope_names)
    # Rails 内部: scope_names は公式APIではないため、
    # defined_scopes or singleton_methods からフィルタ
  end
  # フォールバック: クラスのシングルトンメソッドでActiveRecord::Relationを返すものを検出
  scope_methods = klass.methods(false).select do |m|
    begin
      klass.method(m).source_location&.first&.include?('app/models')
    rescue
      false
    end
  end
  scope_methods.each do |m|
    loc = klass.method(m).source_location
    data[:scopes] << {
      name: m.to_s,
      source_file: loc&.first,
      source_line: loc&.last,
    }
  end

  # --- Concerns ---
  data[:concerns] = []
  klass.ancestors.each do |ancestor|
    next if ancestor == klass
    next unless ancestor.is_a?(Module) && !ancestor.is_a?(Class)
    next if ancestor.name.nil?
    next if ancestor.name.start_with?('ActiveRecord', 'ActiveModel', 'ActiveSupport')
    next if ancestor.name.start_with?('Kernel', 'JSON', 'PP', 'Object', 'BasicObject')

    provided_methods = ancestor.instance_methods(false).map(&:to_s)
    source_file = begin
      first_method = ancestor.instance_methods(false).first
      first_method ? ancestor.instance_method(first_method).source_location&.first : nil
    rescue
      nil
    end

    data[:concerns] << {
      name: ancestor.name,
      provided_methods: provided_methods,
      source_file: source_file,
    }
  end

  # --- Enums ---
  data[:enums] = if klass.respond_to?(:defined_enums)
    klass.defined_enums.map do |name, values|
      { name: name, values: values }
    end
  else
    []
  end

  # --- Schema ---
  columns = klass.columns_hash.map do |name, col|
    {
      name: name,
      type: col.type.to_s,
      null: col.null,
      default: col.default,
      limit: col.limit,
    }
  end

  indexes = begin
    klass.connection.indexes(klass.table_name).map do |idx|
      {
        name: idx.name,
        columns: idx.columns,
        unique: idx.unique,
      }
    end
  rescue
    []
  end

  foreign_keys = begin
    klass.connection.foreign_keys(klass.table_name).map do |fk|
      {
        from_column: fk.column,
        to_table: fk.to_table,
        to_column: fk.primary_key,
      }
    end
  rescue
    []
  end

  data[:schema] = {
    columns: columns,
    indexes: indexes,
    foreign_keys: foreign_keys,
  }

  # --- STI ---
  if klass.column_names.include?(klass.inheritance_column)
    data[:sti] = {
      base_class: klass.base_class.name,
      descendants: klass.descendants.map(&:name).compact,
      type_column: klass.inheritance_column,
    }
  else
    data[:sti] = nil
  end

  # --- Delegations ---
  # delegate はメタ情報を保持しないため、ソースコードから正規表現で抽出
  data[:delegations] = []
  model_file = Rails.root.join("app/models/#{model_name.underscore}.rb")
  if File.exist?(model_file)
    content = File.read(model_file)
    content.scan(/delegate\s+(.+?)(?:,\s*to:\s*[:\"]?(\w+)[:\"]?)(?:,\s*prefix:\s*(\w+|true|false))?/m) do |methods_str, to, prefix|
      methods = methods_str.scan(/:(\w+[?!]?)/).flatten
      data[:delegations] << {
        methods: methods,
        to: to,
        prefix: prefix == 'true' ? true : (prefix == 'false' ? false : prefix),
      }
    end
  end

  # --- Methods (model-specific only) ---
  base_methods = ActiveRecord::Base.instance_methods + ApplicationRecord.instance_methods
  data[:instance_methods] = (klass.instance_methods(false) - base_methods).map do |m|
    loc = begin
            klass.instance_method(m).source_location
          rescue
            nil
          end
    {
      name: m.to_s,
      source_file: loc&.first,
      source_line: loc&.last,
    }
  end

  base_class_methods = ActiveRecord::Base.methods + ApplicationRecord.methods
  data[:class_methods] = (klass.methods(false) - base_class_methods).map do |m|
    loc = begin
            klass.method(m).source_location
          rescue
            nil
          end
    {
      name: m.to_s,
      source_file: loc&.first,
      source_line: loc&.last,
    }
  end

  RailsLens::Serializer.output(data)

rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
