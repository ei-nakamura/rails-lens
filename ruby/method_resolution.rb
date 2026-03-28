# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name = ARGV[0]
  method_name = ARGV[1].to_s.empty? ? nil : ARGV[1]
  show_internal = ARGV[2] == 'true'

  unless model_name
    RailsLens::Serializer.error('Model name is required as first argument')
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

  gem_paths = (Gem.path + [Gem.paths.home]).compact.uniq.map { |p| File.expand_path(p) }

  def detect_source_file(mod)
    mod.instance_methods(false).each do |m|
      loc = mod.instance_method(m).source_location
      return loc[0] if loc
    end
    nil
  rescue StandardError
    nil
  end

  def classify_ancestor(mod, klass, gem_paths)
    return 'self' if mod == klass

    mod_name = mod.name || mod.to_s

    # Check if it's a Concern (ActiveSupport::Concern)
    if defined?(ActiveSupport::Concern) && mod.is_a?(Module) && mod.include?(ActiveSupport::Concern)
      return 'concern'
    end

    # Check if ActiveRecord internal
    return 'active_record_internal' if mod_name.start_with?('ActiveRecord::')

    # Check if it comes from a gem (source_location in gem path)
    source_file = detect_source_file(mod)
    if source_file
      expanded = File.expand_path(source_file)
      return 'gem_module' if gem_paths.any? { |gp| expanded.start_with?(gp) }
    end

    'ruby_core'
  end

  ancestors_data = klass.ancestors.map do |ancestor|
    type = classify_ancestor(ancestor, klass, gem_paths)
    next if !show_internal && type == 'active_record_internal'

    {
      name: ancestor.name || ancestor.to_s,
      type: type,
      source_file: detect_source_file(ancestor)
    }
  end.compact

  method_owner = nil
  super_chain = []
  monkey_patches = []

  if method_name
    begin
      owner = klass.instance_method(method_name.to_sym).owner
      method_owner = owner.name || owner.to_s

      # Build super_chain: all ancestors that define this method
      klass.ancestors.each do |ancestor|
        next if ancestor == klass
        if ancestor.method_defined?(method_name.to_sym)
          super_chain << (ancestor.name || ancestor.to_s)
        end
      end

      # Detect monkey_patches: definitions in gem paths
      klass.ancestors.each do |ancestor|
        next unless ancestor.method_defined?(method_name.to_sym)

        begin
          loc = ancestor.instance_method(method_name.to_sym).source_location
          next unless loc

          expanded = File.expand_path(loc[0])
          if gem_paths.any? { |gp| expanded.start_with?(gp) }
            monkey_patches << (ancestor.name || ancestor.to_s)
          end
        rescue StandardError
          # skip inaccessible methods
        end
      end
    rescue NameError
      # method not found, leave nil
    end
  end

  RailsLens::Serializer.output({
    model_name: model_name,
    ancestors: ancestors_data,
    method_owner: method_owner,
    super_chain: super_chain,
    monkey_patches: monkey_patches
  })
rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
