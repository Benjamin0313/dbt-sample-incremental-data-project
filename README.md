# 🥪 Jaffle Shop — マクロ生成源泉版

_powered by the dbt Fusion engine + DuckDB_

通常の jaffle_shop サンプルは源泉が **CSV シード**なので、「データが継続的に到着する状況」を再現できません。
このプロジェクトは源泉を **datagen マクロ**に置き換え、**dbt を実行するたびにトランザクションが約50件ずつ増える**源泉を持ちます。
マスター(顧客・商品・店舗・仕入)も**実行回数に応じて適宜更新**されます。

これにより、source freshness・incremental の差分取り込み・遅延到着など、
**データ到着まわりの挙動**をローカルだけで検証できます。

生成は `on-run-start` フック(`dbt_project.yml`)で **`dbt run` / `dbt build` のたびに自動実行**されます。
全源泉は取込時刻 `last_loaded_at` を持ち、実行のたびに更新されます。

## マスターは CSV(`master_data/`)で管理

マスターは `master_data/*.csv`(`customers` / `products` / `stores` / `supplies`)を「正」とします。

- 実行のたびに **CSV → DB へロード**するので、**CSV を手で編集して行を足す/値を変えると次回実行で反映**されます。
- マクロがマスターを更新した場合(値上げ・新商品・顧客や店舗の追加)は、**DB → CSV へ書き戻し**されて変更が CSV に残ります。

## マスター更新タイミング

| 実行回数 | 更新内容 | 書き戻し先CSV |
| --- | --- | --- |
| 毎回 | トランザクション(注文・明細)を約50件追記 | (なし) |
| 4回ごと | 新商品を1つ追加 | `products.csv` |
| 5回ごと | 顧客を3人追加 | `customers.csv` |
| 7回ごと | 店舗を1つ追加(最大8店舗) | `stores.csv` |
| 10回ごと | ランダムな1商品を10%値上げ | `products.csv` |

## 使い方

前提:

- `dbt-fusion`(DuckDB アダプタ内蔵)がインストール済み。外部DBは不要で、すべて `jaffle_shop.duckdb` に入ります。
- 直接クエリ用に duckdb CLI(`brew install duckdb`)。Python から触る場合は [uv](https://docs.astral.sh/uv/)(未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`)。

```bash
dbt deps                          # パッケージ取得 (dbt_utils)
dbt build --profiles-dir .        # 源泉+50 → staging/marts。もう一度叩くとさらに+50
```

源泉だけを手動で増やす:

```bash
dbt run-operation generate_raw_data --profiles-dir .                      # +50
dbt run-operation generate_raw_data --args '{n_orders: 120}' --profiles-dir .  # 件数指定
```

到着検証(incremental)。2つの戦略を試せます:

```bash
# orders_inc: last_loaded_at(取込時刻)を高水位マークに、新しく到着した注文だけ追記
dbt build --select orders_inc --profiles-dir .
dbt build --select orders_inc --full-refresh --profiles-dir .   # 全件作り直し

# customers: last_purchased_at(最終購入日時, μs)を高水位マークにした upsert。
#   購入が進んだ顧客だけ行を再計算してマージ、変化なしの顧客は据え置き。
#   ※ 上流 orders を含めて再構築する +customers で実行する
dbt build --select +customers --profiles-dir .
dbt build --select customers --full-refresh --profiles-dir .    # 集計を最新で作り直し
```

source freshness(`last_loaded_at` ベース):

```bash
dbt source freshness --profiles-dir .
```

リセット:

```bash
rm -f jaffle_shop.duckdb          # DBを消去。次回ビルドでマスターは現在の master_data/*.csv から作り直し
git checkout master_data          # マスターCSVも初期状態(コミット時点)に戻したいとき
```

## データの確認(select)

duckdb CLI で `jaffle_shop.duckdb` に直接 SQL を投げます。源泉は `main_raw`、モデルは `main` スキーマに入ります。

```bash
# 読むだけなら -readonly を付ける(源泉は増えない)
duckdb -readonly jaffle_shop.duckdb "select * from main_raw._gen_state order by run_number"
duckdb -readonly jaffle_shop.duckdb "select count(*) from main_raw.raw_orders"
duckdb -readonly jaffle_shop.duckdb "select * from main.orders limit 5"

# 対話シェル(.tables / .schema なども使える)
duckdb -readonly jaffle_shop.duckdb
```

> [!NOTE]
> DuckDB の単一ファイルは「書き込み接続1つ」または「読み取り接続のみ複数」のどちらか。
> `dbt` 実行中や RW シェルを開いたままだとロック競合になるので、読むだけなら `-readonly` を使い、
> dbt と同時には開かないこと。

Python から触りたい場合は **uv** 管理(`pyproject.toml` / `uv.lock` で `duckdb==1.5.3` を固定):

```bash
uv sync
uv run python -c "import duckdb; duckdb.connect('jaffle_shop.duckdb', read_only=True).sql('select * from main_raw._gen_state').show()"
```
