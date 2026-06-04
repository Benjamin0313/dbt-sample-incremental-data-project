# datagen マクロ

源泉(raw)を CSV シードではなくマクロで生成・追記する仕組み。`dbt run` / `dbt build` のたびに
`on-run-start` フック(`dbt_project.yml`)経由で `generate_raw_data` が呼ばれ、**毎回 注文+50 / 顧客+2** が変わる。

**顧客マスターは `master_data/customers.csv` を「正」として管理する。** 実行ごとに CSV → DB へロードし
(手で編集した CSV もそのまま反映)、顧客を追加したら DB → CSV へ書き戻す。

## マクロ

ユーザーが直接呼ぶのは **`generate_raw_data` だけ**。残りはそれが内部で呼ぶヘルパー。

| マクロ | ファイル | 役割 |
| --- | --- | --- |
| **`generate_raw_data(n_orders=50)`** | `generate_raw_data.sql` | **エントリポイント**。下記ヘルパーを順に呼ぶ。 |
| `datagen_ensure_tx_tables(raw)` | `masters.sql` | トランザクション表(`raw_orders`)を `create if not exists`。 |
| `datagen_load_masters_from_csv(raw)` | `masters.sql` | `customers.csv` を読んで `raw_customers` を create or replace。 |
| `datagen_dump_customers(raw)` | `masters.sql` | `raw_customers` を `customers.csv` へ書き戻し。 |
| `datagen_next_run_number(raw)` | `generate_raw_data.sql` | 実行回数を採番し `_gen_state` に記録、番号を返す。 |
| `datagen_update_masters(raw, run_number)` | `update_masters.sql` | 顧客を2人追加し `datagen_dump_customers` で CSV 書き戻し。 |
| `datagen_generate_transactions(raw, n_orders)` | `generate_transactions.sql` | 注文を `n_orders` 件、既存顧客から整合的に生成・追記。 |

`raw` は `{{ target.schema }}_raw`(既定では `main_raw`)。

## generate_raw_data の流れ

```
generate_raw_data(n_orders)
  1. datagen_ensure_tx_tables(raw)            … raw_orders を用意
  2. datagen_load_masters_from_csv(raw)       … customers.csv → raw_customers (手動編集を反映)
  3. datagen_next_run_number(raw)             … _gen_state を採番・記録 → run_number
  4. datagen_update_masters(raw, run_number)  … 顧客 +2 → customers.csv 書き戻し
  5. datagen_generate_transactions(raw, n)    … 注文 +n
```

## 生成・管理されるテーブル(`main_raw` スキーマ)

全テーブルに取込時刻 `last_loaded_at` を持つ(マスターは毎回フルリロードで全行更新、トランザクションは新規行に付与)。

| テーブル | 区分 | 元 | 毎回の変化 |
| --- | --- | --- | --- |
| `raw_orders` | トランザクション | マクロ生成 | +50件(`last_loaded_at` で incremental の差分判定) |
| `raw_customers` | マスター | `customers.csv` | +2件 → CSV 書き戻し |
| `_gen_state` | 状態 | マクロ生成 | +1行(実行回数の履歴) |

## 実装メモ

- 顧客マスターは毎回 CSV から `create or replace` するため、手動編集が常に反映される。顧客追加は DB に適用後すぐ `datagen_dump_customers` で CSV に書き戻す。
- 注文の `customer` は乱数で ID を作らず、`raw_customers` に連番を振って乱数インデックスで join するので、**必ず実在の顧客**を参照する。
- DuckDB の `::int` キャストは**四捨五入**。ランダムインデックスが範囲外に出て join で行落ちするため、`floor(random() * N)::int` で 0..N-1 に揃えている。
- DB(`jaffle_shop.duckdb`)を消すと顧客は現在の `customers.csv` から作り直される。完全初期化は `git checkout master_data`。
