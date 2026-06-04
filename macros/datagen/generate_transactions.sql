{#
  datagen_generate_transactions
  ------------------------------
  既存マスターから整合的に n_orders 件の注文と、その明細(1注文あたり1〜3件)を生成して追記する。
  - customer / store は既存マスターからランダムに参照
  - sku は既存商品からランダムに参照
  - ordered_at は now() を基準に直近14日へ散らす (=データが継続的に到着する状況を再現)
  - subtotal / tax_paid / order_total は明細(商品価格)と店舗の税率から算出
#}

{% macro datagen_generate_transactions(raw, n_orders) %}

  {# 1. 今回バッチの注文ヘッダをスクラッチテーブルに作る
     (run_query ごとに接続が分かれ temp は共有されないため raw スキーマの実テーブルを使う) #}
  {% set batch %}
    create or replace table "{{ raw }}"._datagen_batch as
    with c as (
      select id, row_number() over (order by id) as rn from "{{ raw }}".raw_customers
    ),
    s as (
      select id, tax_rate, row_number() over (order by id) as rn from "{{ raw }}".raw_stores
    ),
    n as (
      select (select count(*) from c) as nc, (select count(*) from s) as ns
    ),
    picks as (
      select
        uuid()::varchar as order_id,
        1 + floor(random() * (select nc from n))::int as cpick,
        1 + floor(random() * (select ns from n))::int as spick,
        now() - (random() * interval 14 day) as ordered_at,
        1 + floor(random() * 3)::int as item_count   -- 1〜3 明細
      from range(1, {{ n_orders }} + 1)
    )
    select
      picks.order_id,
      c.id        as customer_id,
      s.id        as store_id,
      s.tax_rate  as tax_rate,
      picks.ordered_at,
      picks.item_count
    from picks
    join c on c.rn = picks.cpick
    join s on s.rn = picks.spick;
  {% endset %}
  {% do run_query(batch) %}

  {# 2. 注文ヘッダを raw_orders へ (金額は後で確定するので一旦0、last_loaded_at は今回の取込時刻) #}
  {% do run_query(
      'insert into "' ~ raw ~ '".raw_orders '
      ~ 'select order_id, customer_id, ordered_at, store_id, 0, 0, 0, now() from "' ~ raw ~ '"._datagen_batch'
  ) %}

  {# 3. 明細を raw_items へ (注文ごとに item_count 件、sku はランダム) #}
  {% set items %}
    insert into "{{ raw }}".raw_items
    with p as (
      select sku, row_number() over (order by sku) as rn from "{{ raw }}".raw_products
    ),
    np as (
      select count(*) as n from "{{ raw }}".raw_products
    ),
    exploded as (
      select
        b.order_id,
        1 + floor(random() * (select n from np))::int as ppick
      from "{{ raw }}"._datagen_batch b
      join range(1, 4) g(k) on g.k <= b.item_count
    )
    select uuid()::varchar as id, e.order_id, p.sku, now()
    from exploded e
    join p on p.rn = e.ppick;
  {% endset %}
  {% do run_query(items) %}

  {# 4. 注文金額を明細×商品マスタの現在価格と店舗税率から確定 (全注文を再計算するので値上げも反映) #}
  {% set settle %}
    update "{{ raw }}".raw_orders o
    set subtotal    = sub.s,
        tax_paid    = round(sub.s * st.tax_rate)::int,
        order_total = sub.s + round(sub.s * st.tax_rate)::int
    from (
      select i.order_id, sum(p.price) as s
      from "{{ raw }}".raw_items i
      join "{{ raw }}".raw_products p on p.sku = i.sku
      group by i.order_id
    ) sub,
    "{{ raw }}".raw_stores st
    where o.id = sub.order_id
      and st.id = o.store_id;
  {% endset %}
  {% do run_query(settle) %}

  {# 5. スクラッチテーブルを片付ける #}
  {% do run_query('drop table if exists "' ~ raw ~ '"._datagen_batch') %}

{% endmacro %}
