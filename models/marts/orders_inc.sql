{#
  到着検証用の incremental モデル。last_loaded_at(取込時刻)をハイウォーターマークに、
  前回以降に到着した注文だけを差分取り込みする。ordered_at は過去14日に散らばるが
  last_loaded_at は実行ごとに単調増加するので、遅延データも取りこぼさない。
#}

{{
  config(
    materialized='incremental',
    unique_key='order_id',
    on_schema_change='append_new_columns'
  )
}}

with orders as (
    select * from {{ ref('stg_orders') }}
)

select
    order_id,
    customer_id,
    location_id,
    order_date,
    ordered_at,
    subtotal,
    tax_paid,
    order_total,
    last_loaded_at
from orders

{% if is_incremental() %}
where last_loaded_at > (select coalesce(max(last_loaded_at), timestamp '1900-01-01') from {{ this }})
{% endif %}
