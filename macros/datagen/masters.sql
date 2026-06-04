{#
  masters.sql
  -----------
  マスターは master_data/*.csv を「正」として管理する。
    - datagen_load_masters_from_csv : 実行のたびに CSV → raw マスターへ create or replace
                                      (= ユーザーが手で編集したCSVもそのまま反映される)
    - datagen_dump_master           : マクロがマスターを更新したら raw マスター → CSV へ書き戻し
    - datagen_ensure_tx_tables      : トランザクション(orders / items)はCSV管理外なので別途 ensure
  CSV のパスはプロジェクトルート基準。
#}

{# ---------- トランザクション表を用意 (CSV管理外) ---------- #}
{% macro datagen_ensure_tx_tables(raw) %}
  {% set ddl %}
    create table if not exists "{{ raw }}".raw_orders (
      id varchar, customer varchar, ordered_at timestamp,
      store_id varchar, subtotal integer, tax_paid integer, order_total integer,
      _ingested_at timestamp
    );
    create table if not exists "{{ raw }}".raw_items (
      id varchar, order_id varchar, sku varchar, unit_price integer
    );
  {% endset %}
  {% do run_query(ddl) %}
{% endmacro %}


{# ---------- CSV → raw マスター (毎回フルリフレッシュ) ---------- #}
{% macro datagen_load_masters_from_csv(raw) %}
  {% set sql %}
    create or replace table "{{ raw }}".raw_customers as
      select cast(id as varchar) as id, cast(name as varchar) as name
      from read_csv_auto('master_data/customers.csv', header=true);

    create or replace table "{{ raw }}".raw_products as
      select cast(sku as varchar) as sku, cast(name as varchar) as name,
             cast(type as varchar) as type, cast(price as integer) as price,
             cast(description as varchar) as description
      from read_csv_auto('master_data/products.csv', header=true);

    create or replace table "{{ raw }}".raw_stores as
      select cast(id as varchar) as id, cast(name as varchar) as name,
             cast(opened_at as timestamp) as opened_at, cast(tax_rate as double) as tax_rate
      from read_csv_auto('master_data/stores.csv', header=true);

    create or replace table "{{ raw }}".raw_supplies as
      select cast(id as varchar) as id, cast(name as varchar) as name,
             cast(cost as integer) as cost, cast(perishable as boolean) as perishable,
             cast(sku as varchar) as sku
      from read_csv_auto('master_data/supplies.csv', header=true);
  {% endset %}
  {% do run_query(sql) %}
{% endmacro %}


{# ---------- raw マスター → CSV (1テーブルを書き戻し) ---------- #}
{% macro datagen_dump_master(raw, name) %}
  {% set spec = {
    'customers': ['master_data/customers.csv', 'id, name',                              'name'],
    'products':  ['master_data/products.csv',  'sku, name, type, price, description',   'sku'],
    'stores':    ['master_data/stores.csv',    'id, name, opened_at, tax_rate',         'opened_at'],
    'supplies':  ['master_data/supplies.csv',  'id, name, cost, perishable, sku',       'id'],
  } %}
  {% set csv = spec[name][0] %}
  {% set cols = spec[name][1] %}
  {% set order_by = spec[name][2] %}
  {% do run_query(
      "copy (select " ~ cols ~ ' from "' ~ raw ~ '".raw_' ~ name
      ~ " order by " ~ order_by ~ ") to '" ~ csv ~ "' (header, format csv)"
  ) %}
{% endmacro %}
