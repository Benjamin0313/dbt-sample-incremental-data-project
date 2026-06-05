with

source as (
    select * from {{ source('ecom', 'raw_customers') }}
)

select
    customer_id,
    name as customer_name,
    email,
    cohort,
    created_at
from source
