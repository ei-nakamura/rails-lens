# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  model_name = ARGV[0]
  gem_name_filter = ARGV[1].to_s.empty? ? nil : ARGV[1]

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

  def gem_name_from_path(path, gem_paths)
    expanded = File.expand_path(path)
    gem_paths.each do |gp|
      next unless expanded.start_with?(gp)

      relative = expanded.sub("#{gp}/", '')
      parts = relative.split('/')
      # Gem paths contain gems/<name>-<version>/...
      return parts[1].sub(/-\d+(\.\d+)*$/, '') if parts[0] == 'gems' && parts[1]
    end
    nil
  end

  gem_methods = []
  gem_callbacks = []

  # Extract gem-derived ancestors and their methods
  klass.ancestors.each do |ancestor|
    source_file = detect_source_file(ancestor)
    next unless source_file

    gname = gem_name_from_path(source_file, gem_paths)
    next unless gname
    next if gem_name_filter && gname != gem_name_filter

    ancestor.instance_methods(false).each do |m|
      loc = begin
        ancestor.instance_method(m).source_location
      rescue StandardError
        nil
      end
      gem_methods << {
        gem_name: gname,
        method_name: m.to_s,
        source_file: loc ? loc[0] : nil
      }
    end
  end

  # Extract gem-derived callbacks
  callback_chains = %i[
    _commit_callbacks _create_callbacks _destroy_callbacks
    _find_callbacks _initialize_callbacks _rollback_callbacks
    _save_callbacks _touch_callbacks _update_callbacks
    _validate_callbacks _validation_callbacks
  ]

  callback_chains.each do |cb_chain|
    next unless klass.respond_to?(cb_chain)

    klass.send(cb_chain).each do |cb|
      next unless cb.respond_to?(:filter) && cb.filter.is_a?(Symbol)

      begin
        loc = klass.instance_method(cb.filter).source_location
        next unless loc

        gname = gem_name_from_path(loc[0], gem_paths)
        next unless gname
        next if gem_name_filter && gname != gem_name_filter

        event = cb_chain.to_s.sub(/\A_/, '').sub(/_callbacks\z/, '')
        gem_callbacks << {
          gem_name: gname,
          kind: cb.kind.to_s,
          event: event,
          method_name: cb.filter.to_s
        }
      rescue StandardError
        # skip inaccessible methods
      end
    end
  end

  # Deduplicate
  gem_methods.uniq! { |m| [m[:gem_name], m[:method_name]] }
  gem_callbacks.uniq! { |c| [c[:gem_name], c[:kind], c[:event], c[:method_name]] }

  RailsLens::Serializer.output({
    model_name: model_name,
    gem_methods: gem_methods,
    gem_callbacks: gem_callbacks,
    gem_routes: []
  })
rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
