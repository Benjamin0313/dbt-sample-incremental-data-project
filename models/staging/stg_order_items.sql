with

source as (
    select * from {{ source('ecom', 'raw_items') }}
),

renamed as (
    select
        ---------- ids
        id as order_item_id,
        order_id,
        sku as product_id,
        ---------- numerics
        -- 購入時点の単価(スナップショット)。値上げ後も過去注文は当時の価格のまま。
        {{ cents_to_dollars('unit_price') }} as product_price
    from source
)

select * from renamed
