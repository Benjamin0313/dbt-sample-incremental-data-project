{#
  datagen_generate_transactions
  ------------------------------
  既存顧客から整合的に n_orders 件の注文を生成して追記する。
  - customer は既存マスターからランダムに参照 (連番に乱数インデックスで join)
  - ordered_at は now() を基準に直近14日へ散らす
  - order_total はランダムな金額(cents)
  - last_loaded_at は今回の取込時刻
#}

{% macro datagen_generate_transactions(raw, n_orders) %}
  {% set sql %}
    insert into "{{ raw }}".raw_orders
    with c as (
      select id, row_number() over (order by id) as rn from "{{ raw }}".raw_customers
    ),
    n as (
      select count(*) as nc from c
    ),
    picks as (
      select
        uuid()::varchar as order_id,
        1 + floor(random() * (select nc from n))::int as cpick,
        now() - (random() * interval 14 day) as ordered_at,
        300 + floor(random() * 4700)::int as order_total
      from range(1, {{ n_orders }} + 1)
    )
    select picks.order_id, c.id, picks.ordered_at, picks.order_total, now()
    from picks
    join c on c.rn = picks.cpick;
  {% endset %}
  {% do run_query(sql) %}
{% endmacro %}
