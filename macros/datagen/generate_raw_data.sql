{#
  generate_raw_data
  -----------------
  源泉(raw)テーブルをマクロで生成・追記するエントリポイント。

  - 毎回: マスター(customers / products / stores / supplies)を master_data/*.csv からロード
          (= ユーザーが手で編集したCSVもそのまま反映される)
  - 毎回: トランザクション(orders / items)を n_orders 件だけ既存マスターから整合的に生成して追記
  - 適宜: 実行回数(_gen_state)に応じてマスターを更新し、変更分は CSV へ書き戻し

  呼び出し方:
    - 自動:   dbt_project.yml の on-run-start フック経由で dbt run / dbt build のたびに実行
    - 手動:   dbt run-operation generate_raw_data
              dbt run-operation generate_raw_data --args '{n_orders: 120}'
#}

{% macro generate_raw_data(n_orders=50) %}
  {# parse 時には実行しない (DDL/DML を走らせない) #}
  {% if not execute %}{{ return('') }}{% endif %}

  {% set raw = target.schema ~ '_raw' %}
  {% do run_query('create schema if not exists "' ~ raw ~ '"') %}

  {# 1a. トランザクション表を用意 (CSV管理外) #}
  {% do datagen_ensure_tx_tables(raw) %}

  {# 1b. マスターを master_data/*.csv からロード (毎回フルリフレッシュ) #}
  {% do datagen_load_masters_from_csv(raw) %}

  {# 2. 実行回数を採番し _gen_state に記録 #}
  {% set run_number = datagen_next_run_number(raw) %}

  {# 3. トランザクションを n_orders 件追記 #}
  {% do datagen_generate_transactions(raw, n_orders) %}

  {# 4. 実行回数に応じてマスターを適宜更新 #}
  {% do datagen_update_masters(raw, run_number) %}

  {# サマリ出力 #}
  {% set summary = run_query(
      "select "
      ~ "(select count(*) from \"" ~ raw ~ "\".raw_orders)    as orders, "
      ~ "(select count(*) from \"" ~ raw ~ "\".raw_items)     as items, "
      ~ "(select count(*) from \"" ~ raw ~ "\".raw_customers) as customers, "
      ~ "(select count(*) from \"" ~ raw ~ "\".raw_products)  as products, "
      ~ "(select count(*) from \"" ~ raw ~ "\".raw_stores)    as stores"
  ) %}
  {% set r = summary.rows[0] %}
  {{ log(
      "[datagen] run #" ~ run_number ~ " (+" ~ n_orders ~ " orders) | totals -> "
      ~ "orders=" ~ r[0] ~ " items=" ~ r[1]
      ~ " customers=" ~ r[2] ~ " products=" ~ r[3] ~ " stores=" ~ r[4],
      info=True
  ) }}
{% endmacro %}


{#
  _gen_state を作成・採番し、今回の実行番号を返す。
  実行履歴を1行ずつ残すので、いつ何回目が走ったか追跡できる。
#}
{% macro datagen_next_run_number(raw) %}
  {% do run_query(
      'create table if not exists "' ~ raw ~ '"._gen_state '
      ~ '(run_number integer, run_at timestamp)'
  ) %}
  {% set res = run_query(
      'select coalesce(max(run_number), 0) + 1 as n from "' ~ raw ~ '"._gen_state'
  ) %}
  {% set n = res.columns[0].values()[0] %}
  {% do run_query(
      'insert into "' ~ raw ~ '"._gen_state values (' ~ n ~ ', now())'
  ) %}
  {{ return(n) }}
{% endmacro %}
