# datagen マクロ

源泉(raw)を CSV シードではなくマクロで生成・追記する仕組み。`dbt run` / `dbt build` のたびに
`on-run-start` フック(`dbt_project.yml`)経由で `generate_raw_data` が呼ばれ、トランザクションが
約50件増え、実行回数に応じてマスターが更新される。

**マスターは `master_data/*.csv` を「正」として管理する。** 実行ごとに CSV → DB へロードし(手で
編集したCSVもそのまま反映)、マクロがマスターを更新したら DB → CSV へ書き戻す。

## マクロ

ユーザーが直接呼ぶのは **`generate_raw_data` だけ**。残りはそれが内部で呼ぶヘルパー。

| マクロ | ファイル | 役割 |
| --- | --- | --- |
| **`generate_raw_data(n_orders=50)`** | `generate_raw_data.sql` | **エントリポイント**。下記ヘルパーを順に呼ぶ。 |
| `datagen_ensure_tx_tables(raw)` | `masters.sql` | トランザクション表(orders / items)を `create if not exists`。 |
| `datagen_load_masters_from_csv(raw)` | `masters.sql` | `master_data/*.csv` を読んで raw マスターを create or replace。 |
| `datagen_next_run_number(raw)` | `generate_raw_data.sql` | 実行回数を採番し `_gen_state` に記録、番号を返す。 |
| `datagen_generate_transactions(raw, n_orders)` | `generate_transactions.sql` | 注文＋明細を `n_orders` 件、既存マスターから整合的に生成・追記。 |
| `datagen_update_masters(raw, run_number)` | `update_masters.sql` | 実行回数に応じてマスターを更新し、変更分を `datagen_dump_master` で CSV へ書き戻す。 |
| `datagen_dump_master(raw, name)` | `masters.sql` | 指定マスター(raw)を `master_data/<name>.csv` へ書き戻す。 |

`raw` は `{{ target.schema }}_raw`(既定では `main_raw`)。

## generate_raw_data の流れ

```
generate_raw_data(n_orders)
  1. datagen_ensure_tx_tables(raw)            … orders / items を用意
  2. datagen_load_masters_from_csv(raw)       … CSV → raw マスター (手動編集を反映)
  3. datagen_next_run_number(raw)             … _gen_state を採番・記録 → run_number
  4. datagen_generate_transactions(raw, n)    … 注文+明細を +n 件
  5. datagen_update_masters(raw, run_number)  … 必要なら更新 → datagen_dump_master で CSV 書き戻し
```

## マスターCSV(`master_data/`)

CSV が源泉マスターの源。手で行を足す/値を変えると次回実行で DB に反映される。マクロ更新時は
ここへ書き戻されるので、変更が CSV に残る。

| CSV | 列 |
| --- | --- |
| `customers.csv` | `id, name` |
| `products.csv` | `sku, name, type, price, description` |
| `stores.csv` | `id, name, opened_at, tax_rate` |
| `supplies.csv` | `id, name, cost, perishable, sku` |

## 生成・管理されるテーブル(`main_raw` スキーマ)

| テーブル | 区分 | 元 | 更新タイミング |
| --- | --- | --- | --- |
| `raw_orders` | トランザクション | マクロ生成 | 毎回 +50件(`_ingested_at` で incremental の差分判定) |
| `raw_items` | トランザクション | マクロ生成 | 毎回(注文に連動、1注文1〜3件)。`unit_price` に購入時単価をスナップショット |
| `raw_customers` | マスター | `customers.csv` | 5回ごとに +3 → CSV 書き戻し |
| `raw_products` | マスター | `products.csv` | 4回ごとに新商品+1、10回ごとに1件10%値上げ → CSV 書き戻し |
| `raw_stores` | マスター | `stores.csv` | 7回ごとに +1(最大8) → CSV 書き戻し |
| `raw_supplies` | マスター | `supplies.csv` | マクロ更新なし(CSV編集のみ反映) |
| `_gen_state` | 状態 | マクロ生成 | 毎回 +1行(実行回数の履歴) |
| `_datagen_batch` | 一時 | マクロ生成 | 毎回 作成→drop(注文組み立て用スクラッチ) |

## 実装メモ

- マスターは毎回 CSV から `create or replace` するため、手動編集が常に反映される。マクロ更新は DB に適用後すぐ `datagen_dump_master` で CSV に書き戻す。
- 注文明細(`raw_items`)は生成時の商品価格を `unit_price` にスナップショットし、注文金額もこれで確定する。これにより「10回ごとの値上げ」後も**過去注文は当時の価格のまま**(新注文だけ新価格)になり、`orders` マートの `order_items_subtotal = subtotal` 不変条件が壊れない。`order_items` マートの `product_price` は商品マスターの現在価格ではなく**購入時単価**を指す。
- `run_query` は呼び出しごとに接続が分かれ **temp table が共有されない**ため、バッチ組み立ては raw スキーマの実テーブル `_datagen_batch` を使い最後に drop している。
- DuckDB の `::int` キャストは**四捨五入**。ランダムインデックスが範囲外に出て join で行落ちするため、`floor(random() * N)::int` で 0..N-1 に揃えている。
- DB(`jaffle_shop.duckdb`)を消すとマスターは現在の CSV から作り直される。マスターも完全初期化したいときは CSV を git で戻す。
