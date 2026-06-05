-- table: 顧客ごとの注文サマリ。毎回フルリフレッシュされる。
with

customers as (
    select * from {{ ref('stg_customers') }}
),

orders as (
    select * from {{ ref('stg_orders') }}
),

order_summary as (
    select
        customer_id,
        count(*) as count_orders,
        sum(order_total) as total_spend,
        min(order_date) as first_order_date,
        max(order_date) as last_order_date
    from orders
    group by 1
)

select
    customers.customer_id,
    customers.customer_name,
    customers.cohort,
    coalesce(order_summary.count_orders, 0) as count_orders,
    coalesce(order_summary.total_spend, 0) as total_spend,
    order_summary.first_order_date,
    order_summary.last_order_date
from customers
left join order_summary using (customer_id)
