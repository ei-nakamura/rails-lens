# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  identifier = ARGV[0].to_s.strip
  action     = ARGV[1].to_s.strip

  if identifier.empty?
    RailsLens::Serializer.error('controller_or_model is required as first argument')
    exit 0
  end

  Rails.application.eager_load!

  # Determine if identifier is a controller or model name
  # Controllers contain "#" separator when passed as "UsersController#create"
  # or can be detected by ending with "Controller"
  controller_name = nil
  model_name      = nil

  if identifier.include?('#')
    parts = identifier.split('#', 2)
    controller_name = parts[0]
    action = parts[1] if action.empty?
  elsif identifier.end_with?('Controller')
    controller_name = identifier
  else
    model_name = identifier
  end

  routes_info = []
  callbacks_info = []
  nested_attributes_info = []

  # ── ルーティング情報取得 ─────────────────────────────────────
  begin
    all_routes = Rails.application.routes.routes
    target_controller = if controller_name
                          # Rails routing uses lowercase with slashes: "users" or "admin/users"
                          controller_name.gsub(/Controller$/, '').gsub('::', '/').downcase
                        elsif model_name
                          # Try to infer controller from model name
                          model_name.gsub('::', '/').downcase.pluralize rescue model_name.downcase
                        end

    all_routes.each do |route|
      verb = route.verb.to_s
      next if verb.empty?

      defaults = route.defaults
      rc = defaults[:controller].to_s
      ra = defaults[:action].to_s

      next unless rc == target_controller
      next if !action.empty? && ra != action

      routes_info << {
        verb: verb,
        path: route.path.spec.to_s.sub(/\(\.:format\)$/, ''),
        controller: rc,
        action: ra,
      }
    end
  rescue => e
    # routing info is best-effort
  end

  # ── コールバック情報取得（モデル経由）────────────────────────
  begin
    klass_name = if model_name
                   model_name
                 elsif controller_name
                   # Infer model from controller name
                   controller_name.gsub(/Controller$/, '').gsub('::', '/').classify rescue nil
                 end

    if klass_name
      begin
        klass = klass_name.constantize
        if klass < ActiveRecord::Base
          callback_chains = %i[
            _commit_callbacks _create_callbacks _destroy_callbacks
            _save_callbacks _touch_callbacks _update_callbacks
            _validate_callbacks _validation_callbacks
          ]

          callback_chains.each do |chain_name|
            next unless klass.respond_to?(chain_name)

            klass.send(chain_name).each do |cb|
              filter = cb.respond_to?(:filter) ? cb.filter : nil
              next unless filter

              loc = begin
                klass.instance_method(filter).source_location if filter.is_a?(Symbol)
              rescue StandardError
                nil
              end

              event = chain_name.to_s.sub(/\A_/, '').sub(/_callbacks\z/, '')
              callbacks_info << {
                kind: "#{cb.kind}_#{event}",
                method_name: filter.to_s,
                file: loc ? loc[0] : '',
                line: loc ? loc[1] : 0,
                description: "#{cb.kind} #{event} :#{filter}",
              }
            end
          end

          # ── accepts_nested_attributes_for ─────────────────────
          if klass.respond_to?(:nested_attributes_options)
            klass.nested_attributes_options.each_key do |nested_name|
              assoc = klass.reflect_on_association(nested_name)
              next unless assoc

              begin
                nested_klass = assoc.klass
                nested_attributes_info << {
                  association: nested_name.to_s,
                  nested_model: nested_klass.name,
                  columns: nested_klass.column_names,
                }
              rescue StandardError
                next
              end
            end
          end
        end
      rescue NameError
        # model not found — best effort
      end
    end
  rescue => e
    # callbacks are best-effort
  end

  RailsLens::Serializer.output({
    identifier: identifier,
    action: action,
    routes: routes_info,
    callbacks: callbacks_info,
    nested_attributes: nested_attributes_info,
  })

rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
