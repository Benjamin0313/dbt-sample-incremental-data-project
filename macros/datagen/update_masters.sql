{#
  datagen_update_masters
  ----------------------
  毎回マスター(customers)を更新する: 新規顧客を2人追加し customers.csv へ書き戻す。
  → 実行のたびに必ず「注文 +50」と「顧客 +2」の両方が変わる。
#}

{% macro datagen_update_masters(raw, run_number) %}
  {% set fnames = "['Aaron','Jessica','Carol','Daniel','Emily','Frank','Grace','Henry','Isabel','Jack','Karen','Liam','Maria','Noah','Olivia','Paul','Quinn','Rachel','Sam','Tina','Umar','Vera','Wade','Xena','Yusuf','Zoe']" %}
  {% set lnames = "['Gardner','James','Smith','Johnson','Brown','Davis','Miller','Wilson','Moore','Taylor','Anderson','Thomas','Jackson','White','Harris','Martin','Lee','Walker','Hall','Allen','Young','King','Wright','Scott','Green','Baker']" %}
  {% do run_query(
      'insert into "' ~ raw ~ '".raw_customers (id, name, last_loaded_at) '
      ~ 'select uuid()::varchar, '
      ~ fnames ~ '[1 + floor(random() * array_length(' ~ fnames ~ "))::int] || ' ' || "
      ~ lnames ~ '[1 + floor(random() * array_length(' ~ lnames ~ '))::int], '
      ~ 'current_timestamp '
      ~ 'from range(1, 3)'
  ) %}
  {% do datagen_dump_customers(raw) %}
  {{ log("[datagen]   master update: +2 customers (customers.csv 更新)", info=True) }}
{% endmacro %}
