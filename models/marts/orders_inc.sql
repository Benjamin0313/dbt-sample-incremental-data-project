-- incremental: 取込時刻 last_loaded_at を高水位マークに、新しく到着した注文だけ追記する。
{{
  config(
    materialized='incremental',
    unique_key='order_id'
  )
}}

with orders as (
    select * from {{ ref('stg_orders') }}
)

select
    order_id,
    customer_id,
    order_date,
    ordered_at,
    order_total,
    last_loaded_at
from orders

{% if is_incremental() %}
where last_loaded_at > (select coalesce(max(last_loaded_at), '1900-01-01'::timestamptz) from {{ this }})
{% endif %}
