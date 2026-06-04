# 🥪 Jaffle Shop — マクロ生成源泉版

_powered by dbt-core + dbt-duckdb_

通常の jaffle_shop サンプルは源泉が **CSV シード**なので、「データが継続的に到着する状況」を再現できません。
このプロジェクトは源泉を **datagen マクロ**に置き換え、**dbt を実行するたびに**
**注文が約50件・顧客が2人ずつ増える**源泉を持ちます。

これにより、source freshness・incremental の差分取り込み・遅延到着など、
**データ到着まわりの挙動**をローカルだけで検証できます。

生成は `on-run-start` フック(`dbt_project.yml`)で **`dbt run` / `dbt build` のたびに自動実行**されます。
全源泉は取込時刻 `last_loaded_at` を持ち、実行のたびに更新されます。

## 源泉と更新タイミング

源泉は DuckDB の `main_raw` スキーマに入り、実行のたびに次が必ず変わります。

| 源泉 | 区分 | 毎回の変化 |
| --- | --- | --- |
| `raw_orders` | トランザクション | 注文を約50件追記 |
| `raw_customers` | マスター(`master_data/customers.csv` が正) | 顧客を2人追加 → CSV へ書き戻し |

マスターは `master_data/customers.csv` を「正」とします。実行のたびに **CSV → DB へロード**するので、
**CSV を手で編集して行を足す/名前を変えると次回実行で反映**されます。マクロが顧客を追加したら **DB → CSV へ書き戻し**されます。

## モデル(最小構成)

| モデル | 種別 | 内容 |
| --- | --- | --- |
| `stg_orders` / `stg_customers` | view | 源泉の素直な整形 |
| `customer_summary` | table | 顧客ごとの注文数・売上(毎回フルリフレッシュ) |
| `orders_inc` | incremental | 新着注文だけ追記(高水位マーク `last_loaded_at`) |

## 使い方

前提:

- [uv](https://docs.astral.sh/uv/)(未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`)。dbt は `uv run dbt` で動かします(dbt-core + dbt-duckdb)。
- 外部DBは不要で、すべて `jaffle_shop.duckdb` に入ります。直接クエリ用に duckdb CLI(`brew install duckdb`)があると便利。

```bash
uv sync                                  # 初回: dbt-core / dbt-duckdb を導入
uv run dbt build --profiles-dir .        # 注文+50 / 顧客+2 → staging/marts。もう一度叩くとさらに増える
```

源泉だけを手動で増やす:

```bash
uv run dbt run-operation generate_raw_data --profiles-dir .                      # +50注文 / +2顧客
uv run dbt run-operation generate_raw_data --args '{n_orders: 120}' --profiles-dir .  # 注文件数を指定
```

到着検証(incremental):

```bash
uv run dbt build --select orders_inc --profiles-dir .                  # 2回目以降は直近バッチ分だけ追加
uv run dbt build --select orders_inc --full-refresh --profiles-dir .   # 全件作り直し
```

source freshness(`last_loaded_at` ベース):

```bash
uv run dbt source freshness --profiles-dir .
```

リセット:

```bash
rm -f jaffle_shop.duckdb          # DBを消去。次回ビルドでマスターは現在の customers.csv から作り直し
git checkout master_data          # 顧客CSVも初期状態(コミット時点)に戻したいとき
```

## データの確認(select)

duckdb CLI で `jaffle_shop.duckdb` に直接 SQL を投げます。源泉は `main_raw`、モデルは `main` スキーマに入ります。

```bash
# 読むだけなら -readonly を付ける(源泉は増えない)
duckdb -readonly jaffle_shop.duckdb "select * from main_raw._gen_state order by run_number"
duckdb -readonly jaffle_shop.duckdb "select count(*) from main.orders_inc"
duckdb -readonly jaffle_shop.duckdb "select * from main.customer_summary limit 5"

# 対話シェル
duckdb -readonly jaffle_shop.duckdb
```

> [!NOTE]
> DuckDB の単一ファイルは「書き込み接続1つ」または「読み取り接続のみ複数」のどちらか。
> `dbt` 実行中や RW シェルを開いたままだとロック競合になるので、読むだけなら `-readonly` を使い、
> dbt と同時には開かないこと。
