{{
  config(
    materialized='incremental',
    unique_key='customer_id'
  )
}}

{#
  incremental 戦略: 最終購入日時(last_purchased_at, μs)をハイウォーターマークにした upsert。
    - 新規顧客              … 初回挿入(注文がまだ無ければ last_purchased_at は NULL)
    - 既存顧客で最終購入が進んだ … 行ごと再計算して unique_key=customer_id でマージ(上書き)
    - 最終購入が変わらない顧客   … 何もしない(再計算しない)
  → 購入があった顧客の集計だけが最新化される。
#}

with

customers as (

    select * from {{ ref('stg_customers') }}

),

orders as (

    select * from {{ ref('orders') }}

),

customer_orders_summary as (

    select
        orders.customer_id,

        count(distinct orders.order_id) as count_lifetime_orders,
        count(distinct orders.order_id) > 1 as is_repeat_buyer,
        min(orders.order_date) as first_order_date,
        max(orders.order_date) as last_order_date,
        max(orders.ordered_at) as last_purchased_at,   -- μs 精度の最終購入日時
        sum(orders.subtotal) as lifetime_spend_pretax,
        sum(orders.tax_paid) as lifetime_tax_paid,
        sum(orders.order_total) as lifetime_spend

    from orders

    group by 1

),

joined as (

    select
        customers.*,

        customer_orders_summary.count_lifetime_orders,
        customer_orders_summary.first_order_date,
        customer_orders_summary.last_order_date,
        customer_orders_summary.last_purchased_at,
        customer_orders_summary.lifetime_spend_pretax,
        customer_orders_summary.lifetime_tax_paid,
        customer_orders_summary.lifetime_spend,

        case
            when customer_orders_summary.is_repeat_buyer then 'returning'
            else 'new'
        end as customer_type,

        -- この行がいつ作られたか (incremental の確認用: 再計算された行だけ新しくなる)
        current_timestamp as _built_at

    from customers

    left join customer_orders_summary
        on customers.customer_id = customer_orders_summary.customer_id

)

{% if is_incremental() %}

select joined.*
from joined
left join {{ this }} as current_rows
    on current_rows.customer_id = joined.customer_id
where
    -- 新規顧客 (まだテーブルに無い)
    current_rows.customer_id is null
    -- 最終購入日時(μs)が前回より進んだ顧客
    or joined.last_purchased_at > current_rows.last_purchased_at
    -- 既存だが初めて購入した顧客 (前回 NULL → 今回値あり)
    or (joined.last_purchased_at is not null and current_rows.last_purchased_at is null)

{% else %}

select * from joined

{% endif %}
