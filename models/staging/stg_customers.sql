with

source as (
    select * from {{ source('ecom', 'raw_customers') }}
)

select
    id as customer_id,
    name as customer_name
from source
