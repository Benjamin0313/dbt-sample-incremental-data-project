with

source as (
    select * from {{ source('ecom', 'raw_orders') }}
)

select
    order_id,
    customer_id,
    cast(ordered_at as date) as order_date,
    ordered_at,
    order_total,
    last_loaded_at
from source
