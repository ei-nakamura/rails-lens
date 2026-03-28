# frozen_string_literal: true

require_relative 'helpers/serializer'

begin
  entry_point = ARGV[0].to_s.strip
  entry_point = nil if entry_point.empty?

  Rails.application.eager_load!

  # ── 有向グラフ構築 ────────────────────────────────────────────
  # edges: [{from:, to:, relation:, label:}, ...]
  edges = []

  ar_models = ActiveRecord::Base.descendants.select do |klass|
    klass.name && !klass.abstract_class?
  rescue StandardError
    false
  end

  ar_models.each do |klass|
    model = klass.name
    next unless model

    # アソシエーション辺
    begin
      klass.reflect_on_all_associations.each do |assoc|
        target = begin
          assoc.klass.name
        rescue StandardError
          assoc.class_name
        end
        next unless target

        edges << {
          from: model,
          to: target,
          relation: 'association',
          label: "#{assoc.macro}(:#{assoc.name})"
        }
      end
    rescue StandardError
      # skip
    end

    # コールバック辺（他モデルのインスタンスを更新するafter_saveなど）
    callback_chains = %i[
      _save_callbacks _update_callbacks _create_callbacks _destroy_callbacks
    ]
    callback_chains.each do |chain_name|
      next unless klass.respond_to?(chain_name)
      klass.send(chain_name).each do |cb|
        filter = cb.respond_to?(:filter) ? cb.filter : nil
        next unless filter.is_a?(Symbol)
        begin
          loc = klass.instance_method(filter).source_location
          next unless loc
          source_lines = File.readlines(loc[0]) rescue next
          # メソッド本体を最大20行読んで他モデルへの参照を探す
          body = source_lines[loc[1]...[loc[1] + 20]].join
          ar_models.each do |other|
            next if other.name == model
            if body.include?(other.name) || body.include?(other.name.underscore)
              edges << {
                from: model,
                to: other.name,
                relation: 'callback',
                label: "#{cb.kind}_#{chain_name.to_s.sub(/\A_/, '').sub(/_callbacks\z/, '')}(:#{filter})"
              }
            end
          end
        rescue StandardError
          next
        end
      end
    end
  end

  # ── DFS で循環検出 ────────────────────────────────────────────
  # 隣接リスト構築
  adj = Hash.new { |h, k| h[k] = [] }
  edge_map = Hash.new { |h, k| h[k] = {} }  # adj[from][to] = edge

  edges.each do |e|
    adj[e[:from]] << e[:to]
    edge_map[e[:from]][e[:to]] ||= e
  end

  cycles = []
  visited = {}
  rec_stack = {}

  find_cycles = lambda do |node, path|
    visited[node] = true
    rec_stack[node] = true
    path.push(node)

    (adj[node] || []).each do |neighbor|
      if !visited[neighbor]
        find_cycles.call(neighbor, path)
      elsif rec_stack[neighbor]
        # 循環発見
        cycle_start = path.index(neighbor)
        next unless cycle_start
        cycle_models = path[cycle_start..].dup + [neighbor]

        # 辺タイプ収集
        cycle_edges = []
        cycle_models.each_cons(2) do |a, b|
          e = edge_map[a][b]
          cycle_edges << e if e
        end

        # cycle_type判定
        relations = cycle_edges.map { |e| e[:relation] }.uniq
        cycle_type = if relations == ['association']
                       'association_bidirectional'
                     elsif relations == ['callback']
                       'callback_mutual'
                     else
                       'validation_cross_reference'
                     end

        # severity判定: 直接2ホップ循環=critical、それ以上=warning
        severity = cycle_models.uniq.size <= 2 ? 'critical' : 'warning'

        # entry_pointフィルタ
        if !entry_point || cycle_models.include?(entry_point)
          cycles << {
            models: cycle_models,
            edges: cycle_edges,
            cycle_type: cycle_type,
            severity: severity
          }
        end
      end
    end

    path.pop
    rec_stack[node] = false
  end

  adj.keys.each do |node|
    next if visited[node]
    find_cycles.call(node, [])
  end

  # 重複削除（同じモデルセットの循環を統合）
  seen_cycles = Set.new
  unique_cycles = cycles.select do |c|
    key = c[:models].sort.join(',')
    next false if seen_cycles.include?(key)
    seen_cycles << key
    true
  end

  affected_models = unique_cycles.flat_map { |c| c[:models] }.uniq

  RailsLens::Serializer.output({
    total_cycles: unique_cycles.size,
    cycles: unique_cycles,
    affected_models: affected_models,
    summary: "#{unique_cycles.size} cycle(s) detected among #{affected_models.size} model(s)"
  })
rescue => e
  RailsLens::Serializer.error(e.message, details: { backtrace: e.backtrace&.first(5) })
end
