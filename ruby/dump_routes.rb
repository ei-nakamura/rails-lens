# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  Rails.application.eager_load!

  routes = Rails.application.routes.routes.map do |route|
    # verb: GET/POST/... or empty string for non-HTTP routes
    verb = route.verb.to_s
    next nil if verb.empty?

    path = route.path.spec.to_s
    # Remove the trailing format segment "(.:format)"
    path = path.sub(/\(\.:format\)$/, '')

    defaults = route.defaults
    controller = defaults[:controller]
    action = defaults[:action]
    name = route.name

    {
      verb: verb,
      path: path,
      controller: controller,
      action: action,
      name: name,
    }
  end.compact

  RailsLens::Serializer.output({ routes: routes })

rescue => e
  RailsLens::Serializer.error(
    "Unexpected error: #{e.message}",
    details: { backtrace: e.backtrace&.first(10) }
  )
end
