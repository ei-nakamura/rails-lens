# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name    = ARGV[0]
  include_priv  = ARGV[1].to_s.downcase == 'true'

  unless model_name && !model_name.strip.empty?
    RailsLens::Serializer.error('model_name is required as first argument')
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

  # ── 除外リスト構築 ────────────────────────────────────────────
  excluded = Set.new

  # スコープ名（ActiveRecord named scopes）
  scopes = []
  if klass.respond_to?(:scope_names, true)
    klass.scope_names.each { |s| excluded << s.to_s; scopes << s.to_s }
  end
  # singleton_class のメソッドからscopeを推定
  klass.singleton_class.instance_methods(false).each do |m|
    next unless klass.respond_to?(m)
    begin
      loc = klass.method(m).source_location
      next unless loc
      line = File.readlines(loc[0])[loc[1] - 1].to_s
      if line.match?(/\bscope\s+:#{Regexp.escape(m.to_s)}/)
        excluded << m.to_s
        scopes << m.to_s unless scopes.include?(m.to_s)
      end
    rescue StandardError
      next
    end
  end

  # コールバック名
  callbacks = []
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
      next unless filter.is_a?(Symbol)
      excluded << filter.to_s
      callbacks << filter.to_s
    end
  end

  # ActiveRecord 基底メソッド（除外）
  base_methods = ActiveRecord::Base.instance_methods.map(&:to_s).to_set
  base_methods |= ActiveRecord::Base.methods.map(&:to_s).to_set

  # include_privateがfalseなら_で始まるメソッドを除外
  excluded_methods = excluded.to_a

  RailsLens::Serializer.output({
    model_name: model_name,
    scopes: scopes.uniq,
    callbacks: callbacks.uniq,
    excluded_methods: excluded_methods.uniq,
    base_method_count: base_methods.size
  })
rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
