with

source as (
    select * from {{ source('ecom', 'raw_orders') }}
)

select
    id as order_id,
    customer as customer_id,
    cast(ordered_at as date) as order_date,
    ordered_at,
    order_total / 100.0 as order_total,
    last_loaded_at
from source
