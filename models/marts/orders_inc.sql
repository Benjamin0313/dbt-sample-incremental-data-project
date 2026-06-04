{#
  orders_incremental
  ------------------
  「データの到着時の振る舞い」を検証するためのサンプル incremental モデル。

  源泉 raw_orders は実行のたびに約50件増える。このモデルは _ingested_at を
  ハイウォーターマークにして、前回以降に到着した注文だけを差分で取り込む。

  挙動の確認手順:
    dbt build --select orders_incremental          # 初回: フルロード
    dbt build --select orders_incremental          # 2回目以降: 直近バッチの約50件だけ追加
    dbt build --select orders_incremental --full-refresh   # 全件作り直し

  ＊ ordered_at(注文日時)は過去14日に散らばる一方、_ingested_at(取込時刻)は
     実行ごとに単調増加する。これにより「注文日時としては過去だが、今到着した
     遅延データ」も取りこぼさずに差分取り込みできる。
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
    _ingested_at

from orders

{% if is_incremental() %}
  -- 前回取り込み済みの最新 _ingested_at より後に到着した行だけ
  where _ingested_at > (select coalesce(max(_ingested_at), timestamp '1900-01-01') from {{ this }})
{% endif %}
