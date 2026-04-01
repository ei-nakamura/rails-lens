# frozen_string_literal: true

require_relative 'helpers/serializer'

# Usage:
#   bundle exec rails runner ruby/dump_view_mapping.rb [mode] [controller_action]
#
# mode: "single" | "all"  (default: "all")
# controller_action: e.g. "UsersController#show"  (required for mode=single)

begin
  Rails.application.eager_load!

  mode = ARGV[0] || 'all'
  controller_action_arg = ARGV[1]

  # Build routing table: array of { verb, path, controller, action, name }
  all_routes = Rails.application.routes.routes.filter_map do |route|
    verb = route.verb.to_s
    next if verb.empty?

    path = route.path.spec.to_s.sub(/\(\.:format\)$/, '')
    defaults = route.defaults
    controller = defaults[:controller]
    action = defaults[:action]
    next if controller.nil? || action.nil?

    {
      verb: verb,
      path: path,
      controller: controller,  # snake_case, e.g. "users"
      action: action,
      name: route.name,
    }
  end

  # Helper: resolve layout for a controller class
  def resolve_layout(controller_class)
    instance = controller_class.new
    # Rails stores layout in _layout method or via layout declaration
    layout_name = if controller_class.instance_methods(false).include?(:_layout)
      begin
        instance._layout(instance, [:html])
      rescue StandardError
        nil
      end
    end

    # Fallback: check layout class method
    if layout_name.nil?
      layout_value = controller_class._layout rescue nil
      if layout_value.is_a?(String)
        layout_name = layout_value
      elsif layout_value.respond_to?(:call)
        begin
          layout_name = controller_class.new.instance_exec(&layout_value)
        rescue StandardError
          layout_name = nil
        end
      end
    end

    layout_name
  rescue StandardError
    nil
  end

  # Helper: convert controller string "users" or "admin/users" to class
  def controller_class_for(controller_str)
    class_name = "#{controller_str.camelize}Controller"
    class_name.constantize
  rescue NameError
    nil
  end

  # Helper: dump i18n keys relevant to view titles
  def dump_i18n_title_keys(controller_str, action_str)
    return {} unless defined?(I18n)

    resource = controller_str.split('/').last
    patterns = [
      "#{resource}.#{action_str}.title",
      "#{resource}.#{action_str}.page_title",
      "titles.#{resource}.#{action_str}",
      "views.#{resource}.#{action_str}.title",
    ]

    result = {}
    patterns.each do |key|
      begin
        val = I18n.t(key, locale: :ja, raise: true)
        result[key] = val.to_s
      rescue I18n::MissingTranslationData, I18n::InvalidLocale
        begin
          val = I18n.t(key, locale: :en, raise: true)
          result[key] = val.to_s
        rescue I18n::MissingTranslationData, I18n::InvalidLocale
          # not found
        end
      rescue StandardError
        # ignore
      end
    end
    result
  rescue StandardError
    {}
  end

  # Process a single route entry and return enriched mapping
  def process_route(route_entry)
    controller_str = route_entry[:controller]  # e.g. "users" or "admin/users"
    action_str = route_entry[:action]

    ctrl_class = controller_class_for(controller_str)
    layout_name = ctrl_class ? resolve_layout(ctrl_class) : nil

    # Detect explicit render in action (source-based; runtime detection is limited)
    # We record the conventional template path
    conventional_template = "#{controller_str}/#{action_str}"

    i18n_keys = dump_i18n_title_keys(controller_str, action_str)

    {
      verb: route_entry[:verb],
      path: route_entry[:path],
      controller: controller_str,
      action: action_str,
      route_name: route_entry[:name],
      layout: layout_name,
      conventional_template: conventional_template,
      explicit_render: nil,  # populated by static analysis
      i18n_title_keys: i18n_keys,
    }
  end

  if mode == 'single'
    if controller_action_arg.nil?
      RailsLens::Serializer.error("controller_action argument required for mode=single")
    else
      # Parse "UsersController#show" or "users#show"
      raw_ctrl, raw_action = controller_action_arg.split('#')
      # Normalize to snake_case controller path
      ctrl_normalized = raw_ctrl
        .sub(/Controller$/, '')
        .gsub('::', '/')
        .gsub(/([A-Z])/) { "_#{$1}" }
        .sub(/^_/, '')
        .downcase

      matching = all_routes.select do |r|
        r[:controller] == ctrl_normalized && r[:action] == raw_action
      end

      if matching.empty?
        RailsLens::Serializer.error(
          "No route found for #{controller_action_arg}",
          details: { normalized_controller: ctrl_normalized, action: raw_action }
        )
      else
        mappings = matching.map { |r| process_route(r) }
        RailsLens::Serializer.output({ mode: 'single', mappings: mappings })
      end
    end
  else
    # mode == 'all'
    mappings = all_routes.map { |r| process_route(r) }
    RailsLens::Serializer.output({
      mode: 'all',
      total: mappings.size,
      mappings: mappings,
    })
  end

rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
