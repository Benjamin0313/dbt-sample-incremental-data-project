{#
  datagen_update_masters
  ----------------------
  実行回数(run_number)に応じてマスターを「適宜」更新する。決定的なので再現性がある。
  更新したマスターは datagen_dump_master で master_data/*.csv へ書き戻す。

    - 5回ごと  : 新規顧客を3人追加
    - 7回ごと  : 新規店舗を1つ追加 (最大8店舗まで)
    - 10回ごと : ランダムな1商品の価格を10%値上げ
    - 4回ごと(初回除く) : 新商品を1つ追加 (sku = NEW-<run_number>)
#}

{% macro datagen_update_masters(raw, run_number) %}

  {# ----- 5回ごと: 新規顧客を3人追加 ----- #}
  {% if run_number % 5 == 0 %}
    {% set fnames = "['Aaron','Jessica','Carol','Daniel','Emily','Frank','Grace','Henry','Isabel','Jack','Karen','Liam','Maria','Noah','Olivia','Paul','Quinn','Rachel','Sam','Tina','Umar','Vera','Wade','Xena','Yusuf','Zoe']" %}
    {% set lnames = "['Gardner','James','Smith','Johnson','Brown','Davis','Miller','Wilson','Moore','Taylor','Anderson','Thomas','Jackson','White','Harris','Martin','Lee','Walker','Hall','Allen','Young','King','Wright','Scott','Green','Baker']" %}
    {% do run_query(
        'insert into "' ~ raw ~ '".raw_customers '
        ~ 'select uuid()::varchar, '
        ~ fnames ~ '[1 + floor(random() * array_length(' ~ fnames ~ "))::int] || ' ' || "
        ~ lnames ~ '[1 + floor(random() * array_length(' ~ lnames ~ '))::int] '
        ~ 'from range(1, 4)'
    ) %}
    {% do datagen_dump_master(raw, 'customers') %}
    {{ log("[datagen]   master update: +3 customers (run #" ~ run_number ~ ", customers.csv 更新)", info=True) }}
  {% endif %}

  {# ----- 7回ごと: 新規店舗を追加 (最大8店舗) ----- #}
  {% if run_number % 7 == 0 %}
    {% set store_names = "['Boston','Austin','Denver','Seattle','Miami']" %}
    {% set added = run_query(
        'insert into "' ~ raw ~ '".raw_stores '
        ~ 'select uuid()::varchar, '
        ~ store_names ~ '[1 + ((select count(*) from "' ~ raw ~ '".raw_stores) - 3)], '
        ~ "now(), round((0.03 + random() * 0.05)::numeric, 4)::double "
        ~ 'where (select count(*) from "' ~ raw ~ '".raw_stores) < 8'
    ) %}
    {% do datagen_dump_master(raw, 'stores') %}
    {{ log("[datagen]   master update: +1 store (run #" ~ run_number ~ ", 最大8, stores.csv 更新)", info=True) }}
  {% endif %}

  {# ----- 10回ごと: ランダムな1商品を10%値上げ ----- #}
  {% if run_number % 10 == 0 %}
    {% do run_query(
        'update "' ~ raw ~ '".raw_products set price = round(price * 1.10)::int '
        ~ 'where sku = (select sku from "' ~ raw ~ '".raw_products order by random() limit 1)'
    ) %}
    {% do datagen_dump_master(raw, 'products') %}
    {{ log("[datagen]   master update: price +10% on a random product (run #" ~ run_number ~ ", products.csv 更新)", info=True) }}
  {% endif %}

  {# ----- 4回ごと(初回除く): 新商品を追加 ----- #}
  {% if run_number % 4 == 0 and run_number > 1 %}
    {% do run_query(
        'insert into "' ~ raw ~ '".raw_products values ('
        ~ "'NEW-" ~ run_number ~ "', "
        ~ "'limited edition #" ~ run_number ~ "', "
        ~ "case when random() < 0.5 then 'jaffle' else 'beverage' end, "
        ~ "(400 + floor(random() * 1200)::int), "
        ~ "'seasonal special introduced on run #" ~ run_number ~ "')"
    ) %}
    {% do datagen_dump_master(raw, 'products') %}
    {{ log("[datagen]   master update: +1 product NEW-" ~ run_number ~ " (run #" ~ run_number ~ ", products.csv 更新)", info=True) }}
  {% endif %}

{% endmacro %}
