# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name = ARGV[0]
  target     = ARGV[1]
  change_type = (ARGV[2].to_s.empty? ? 'modify' : ARGV[2])

  unless model_name && target
    RailsLens::Serializer.error('model_name and target are required as first two arguments')
    exit 0
  end

  begin
    klass = model_name.constantize
  rescue NameError
    RailsLens::Serializer.error("Model '#{model_name}' not found.")
    exit 0
  end

  unless klass < ActiveRecord::Base
    RailsLens::Serializer.error("'#{model_name}' is not an ActiveRecord model.")
    exit 0
  end

  # カラムかメソッドかを判定
  column_names = klass.column_names rescue []
  target_type = column_names.include?(target) ? 'column' : 'method'

  impacts = []
  cascade_effects = []

  # ── コールバック解析 ──────────────────────────────────────────
  callback_chains = %i[
    _commit_callbacks _create_callbacks _destroy_callbacks
    _find_callbacks _initialize_callbacks _rollback_callbacks
    _save_callbacks _touch_callbacks _update_callbacks
    _validate_callbacks _validation_callbacks
  ]

  callback_chains.each do |chain_name|
    next unless klass.respond_to?(chain_name)

    klass.send(chain_name).each do |cb|
      filter = cb.respond_to?(:filter) ? cb.filter : nil
      next unless filter

      # targetをif/unless条件またはフィルタ名に含むか判定
      filter_str = filter.to_s
      next unless filter_str.include?(target) ||
                  cb.options[:if].to_s.include?(target) ||
                  cb.options[:unless].to_s.include?(target)

      loc = begin
        klass.instance_method(filter).source_location if filter.is_a?(Symbol)
      rescue StandardError
        nil
      end

      event = chain_name.to_s.sub(/\A_/, '').sub(/_callbacks\z/, '')
      impacts << {
        category: 'callback',
        file: loc ? loc[0] : klass.instance_method(:initialize).source_location&.first || '',
        line: loc ? loc[1] : 0,
        description: "#{cb.kind} #{event} callback '#{filter}' references '#{target}'",
        severity: change_type == 'remove' ? 'breaking' : 'warning',
        code_snippet: "#{cb.kind}_#{event} :#{filter}"
      }
    end
  end

  # ── バリデーション解析 ────────────────────────────────────────
  klass.validators.each do |v|
    next unless v.attributes.map(&:to_s).include?(target)

    loc = begin
      v.class.instance_method(:validate).source_location
    rescue StandardError
      nil
    end

    impacts << {
      category: 'validation',
      file: loc ? loc[0] : '',
      line: loc ? loc[1] : 0,
      description: "#{v.class.name} validates :#{target}",
      severity: change_type == 'remove' ? 'breaking' : 'warning',
      code_snippet: "validates :#{target} (#{v.class.name})"
    }
  end

  # ── スコープ解析 ──────────────────────────────────────────────
  klass.methods(false).each do |m|
    next unless m.to_s.start_with?('scope_') || klass.respond_to?(m)

    begin
      loc = klass.method(m).source_location
      next unless loc

      source = File.read(loc[0]) rescue nil
      next unless source

      lines = source.lines
      line_content = lines[loc[1] - 1].to_s
      next unless line_content.include?(target) && line_content.match?(/\bscope\b/)

      impacts << {
        category: 'scope',
        file: loc[0],
        line: loc[1],
        description: "Scope '#{m}' references '#{target}'",
        severity: change_type == 'remove' ? 'breaking' : 'info',
        code_snippet: line_content.strip
      }
    rescue StandardError
      next
    end
  end

  # スコープはclass methodとして定義されている場合も検索
  klass.singleton_class.instance_methods(false).each do |m|
    begin
      loc = klass.method(m).source_location
      next unless loc

      source = File.read(loc[0]) rescue nil
      next unless source

      line_content = source.lines[loc[1] - 1].to_s
      next unless line_content.include?(target)

      # scopeキーワードを含む行周辺を確認
      surrounding = source.lines[[loc[1] - 3, 0].max..loc[1]].join
      next unless surrounding.match?(/\bscope\s+:#{Regexp.escape(m)}/)

      impacts << {
        category: 'scope',
        file: loc[0],
        line: loc[1],
        description: "Scope '#{m}' references '#{target}'",
        severity: change_type == 'remove' ? 'breaking' : 'info',
        code_snippet: line_content.strip
      }
    rescue StandardError
      next
    end
  end

  # ── アソシエーション・カスケード解析 ─────────────────────────
  klass.reflect_on_all_associations.each do |assoc|
    dep = assoc.options[:dependent]
    next unless dep

    relation = case dep
               when :destroy, :destroy_all then 'dependent_destroy'
               when :nullify               then 'dependent_nullify'
               when :delete, :delete_all   then 'dependent_delete'
               when :restrict_with_error,
                    :restrict_with_exception then 'dependent_restrict'
               else dep.to_s
               end

    cascade_effects << {
      source_model: model_name,
      target_model: assoc.class_name,
      relation: relation,
      description: "#{model_name} has_#{assoc.macro} :#{assoc.name} with dependent: :#{dep}"
    }
  end

  # ── accepts_nested_attributes_for 検出 ───────────────────────
  if klass.respond_to?(:nested_attributes_options)
    klass.nested_attributes_options.each_key do |nested_name|
      assoc = klass.reflect_on_association(nested_name)
      next unless assoc

      begin
        nested_klass = assoc.klass
        if nested_klass.column_names.include?(target)
          impacts << {
            category: 'association_cascade',
            file: '',
            line: 0,
            description: "accepts_nested_attributes_for :#{nested_name} — nested model '#{nested_klass.name}' has column '#{target}'",
            severity: 'warning',
            code_snippet: "accepts_nested_attributes_for :#{nested_name}"
          }
        end
      rescue StandardError
        next
      end
    end
  end

  RailsLens::Serializer.output({
    model_name: model_name,
    target: target,
    change_type: change_type,
    target_type: target_type,
    direct_impacts: impacts,
    cascade_effects: cascade_effects,
    affected_files: impacts.map { |i| i[:file] }.reject(&:empty?).uniq,
    summary: "#{impacts.size} direct impact(s) and #{cascade_effects.size} cascade effect(s) found for '#{target}' (#{change_type})"
  })
rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
