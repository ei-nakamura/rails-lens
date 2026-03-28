# frozen_string_literal: true

require 'json'

module RailsLens
  module Serializer
    def self.output(data)
      $stdout.puts JSON.generate({
        status: 'success',
        data: data,
        metadata: {
          rails_version: Rails.version,
          ruby_version: RUBY_VERSION,
          timestamp: Time.now.iso8601
        }
      })
    end

    def self.error(message, details: nil)
      $stdout.puts JSON.generate({
        status: 'error',
        error: {
          message: message,
          details: details
        }
      })
    end
  end
end
