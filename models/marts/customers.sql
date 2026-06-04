{{
  config(
    materialized='incremental',
    unique_key='customer_id'
  )
}}

{#
  最終購入日時(last_purchased_at, μs)を高水位マークにした upsert。
  新規顧客は挿入、最終購入が進んだ顧客は行ごと再計算してマージ、変化なしは据え置き。
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
        max(orders.ordered_at) as last_purchased_at,
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
where current_rows.customer_id is null
    or joined.last_purchased_at > current_rows.last_purchased_at
    or (joined.last_purchased_at is not null and current_rows.last_purchased_at is null)

{% else %}

select * from joined

{% endif %}
