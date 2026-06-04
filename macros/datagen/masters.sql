{#
  masters.sql
  -----------
  マスター(customers)は master_data/customers.csv を「正」として管理する。
    - datagen_load_masters_from_csv : 実行のたびに CSV → raw_customers へ create or replace
                                      (= 手で編集した CSV もそのまま反映される)
    - datagen_dump_customers        : raw_customers → customers.csv へ書き戻し
    - datagen_ensure_tx_tables      : トランザクション(orders)は CSV 管理外なので別途 ensure
#}

{% macro datagen_ensure_tx_tables(raw) %}
  {% do run_query(
      'create table if not exists "' ~ raw ~ '".raw_orders ('
      ~ 'id varchar, customer varchar, ordered_at timestamp, '
      ~ 'order_total integer, last_loaded_at timestamptz)'
  ) %}
{% endmacro %}


{% macro datagen_load_masters_from_csv(raw) %}
  {% do run_query(
      'create or replace table "' ~ raw ~ '".raw_customers as '
      ~ 'select cast(id as varchar) as id, cast(name as varchar) as name, '
      ~ 'current_timestamp as last_loaded_at '
      ~ "from read_csv_auto('master_data/customers.csv', header=true)"
  ) %}
{% endmacro %}


{% macro datagen_dump_customers(raw) %}
  {% do run_query(
      'copy (select id, name from "' ~ raw ~ '".raw_customers order by name) '
      ~ "to 'master_data/customers.csv' (header, format csv)"
  ) %}
{% endmacro %}
