{#
  generate_raw_data
  -----------------
  源泉(raw)をマクロで生成・追記するエントリポイント。実行のたびに:
    - マスター(customers)を master_data/customers.csv からロード (手編集も反映)
    - 顧客を2人追加し customers.csv へ書き戻し
    - 注文を n_orders 件追記

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

  {# 1. トランザクション表を用意 + マスターを CSV からロード #}
  {% do datagen_ensure_tx_tables(raw) %}
  {% do datagen_load_masters_from_csv(raw) %}

  {# 2. 実行回数を採番し _gen_state に記録 #}
  {% set run_number = datagen_next_run_number(raw) %}

  {# 3. マスターを更新 (顧客 +2) してから注文を生成 #}
  {% do datagen_update_masters(raw, run_number) %}
  {% do datagen_generate_transactions(raw, n_orders) %}

  {# サマリ出力 #}
  {% set summary = run_query(
      'select '
      ~ '(select count(*) from "' ~ raw ~ '".raw_orders) as orders, '
      ~ '(select count(*) from "' ~ raw ~ '".raw_customers) as customers'
  ) %}
  {% set r = summary.rows[0] %}
  {{ log(
      "[datagen] run #" ~ run_number ~ " (+" ~ n_orders ~ " orders, +2 customers) | "
      ~ "totals -> orders=" ~ r[0] ~ " customers=" ~ r[1],
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
