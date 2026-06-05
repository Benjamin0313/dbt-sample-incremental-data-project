# dbt-sample-incremental-data-project

「データが継続的に到着する状況」を再現するためのサンプル。

- 源泉は **`datagen.yml` に宣言 → `generate.py` が生成**（faker でダミーデータ）
- **dbt は変換に専念**（staging → marts）
- 出力先は **Snowflake**(既定) / **DuckDB**(ローカル) を `--target` で切替
- 源泉を実行のたびに増やせるので、**incremental の差分取り込み・遅延到着・CDC・freshness** を検証できる

```
datagen.yml ──▶ generate.py ──▶ 源泉(raw_*)  ──▶ dbt(staging/marts)
 (宣言)          (faker)        Snowflake / DuckDB
```

---

## セットアップ

[uv](https://docs.astral.sh/uv/) が必要（未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`）。

```bash
uv sync     # faker / dbt-core / dbt-snowflake / dbt-duckdb を導入
```

---

## 使い方

源泉を生成 → dbt で変換、の2ステップ。

### A. DuckDB（ローカル・認証不要）

```bash
uv run python generate.py --target duckdb --minutes 30   # 源泉を生成(30分経過ぶん)
uv run dbt build --profiles-dir . --target duckdb        # 変換
```

### B. Snowflake（社内 datum・既定ターゲット）

datum 標準のキーペア認証の環境変数が必要。`.env` に書いて `source` する:

```bash
# .env (gitignore 済み)
#   export SF_ACCOUNT=...
#   export SF_USER=...
#   export SF_PRIVATE_KEY_PATH=/path/to/key.p8
set -a; source .env; set +a

uv run python generate.py --minutes 30        # 源泉を Snowflake(jaffle_shop_raw)へ
uv run dbt build --profiles-dir .             # Snowflake(jaffle_shop)へ変換
```

接続確認: `uv run dbt debug --profiles-dir .`

### よく使う操作

```bash
# 実行を繰り返すたびに源泉が増える(orders=追記 / customers=upsert)
uv run python generate.py --minutes 30 && uv run dbt build --profiles-dir .

# incremental だけ動かす
uv run dbt build --select orders_inc --profiles-dir .
uv run dbt build --select orders_inc --full-refresh --profiles-dir .

# source freshness
uv run dbt source freshness --profiles-dir .
```

- `generate.py` 引数なし … 前回からの**実経過時間**で件数を算出
- `--minutes N` … N分経過したものとして生成（待たずに検証できる）
- `--target snowflake|duckdb` … 出力先（既定は `datagen.yml` の `default_target`）

---

## 源泉を追加する（`datagen.yml`）

`sources:` に1ブロック足すだけ。

```yaml
sources:
  customers:
    tick: medium
    raw_style: upsert            # PK で merge。変更行だけ last_loaded_at が進む
    primary_key: customer_id
    seed: 50                     # 初回に投入する件数
    fields:
      customer_id: { gen: uuid }
      name:        { gen: faker, method: name }
      email:       { gen: faker, method: email }
      cohort:      { gen: choice, choices: [bronze, silver, gold], weights: [70, 25, 5], mutable: true }
      created_at:  { gen: now }

  orders:
    tick: fast
    raw_style: append            # 追記のみ。既存行は不変
    primary_key: order_id
    seed: 50
    fields:
      order_id:    { gen: uuid }
      customer_id: { gen: ref, source: customers, field: customer_id }  # 既存顧客を参照
      order_total: { gen: int, min: 3, max: 50 }
      ordered_at:  { gen: recent, within_days: 14 }
```

### フィールドのレシピ（`gen`）

| gen | 説明 | 例 |
| --- | --- | --- |
| `uuid` | UUID 文字列 | `{ gen: uuid }` |
| `faker` | faker の任意メソッド | `{ gen: faker, method: name }` |
| `choice` | 重み付き選択。`mutable: true` で upsert 更新対象に | `{ gen: choice, choices: [a,b], weights: [8,2] }` |
| `int` | 整数 | `{ gen: int, min: 3, max: 50 }` |
| `now` | 現在時刻 | `{ gen: now }` |
| `recent` | 直近 N 日に散らす（遅延到着の再現） | `{ gen: recent, within_days: 14 }` |
| `ref` | 他源泉の既存値を参照（FK整合） | `{ gen: ref, source: customers, field: customer_id }` |

### tick プロファイル

`profiles:` に「1分あたりの新規/更新件数」を定義し、各源泉が `tick:` で参照する。

```yaml
profiles:
  medium: { new_per_min: 0.2, update_per_min: 0.0333 }   # 5分に1新規 / 30分に1更新
  fast:   { new_per_min: 1.7, update_per_min: 0.0 }       # 30分で約50件
```

### raw_style

- **append** … 追記のみ。既存行は不変（注文など）
- **upsert** … PK で merge。`mutable: true` の列が変わった行**だけ** `last_loaded_at` を更新（CDC）

---

## モデル（dbt）

| モデル | 種別 | 内容 |
| --- | --- | --- |
| `stg_orders` / `stg_customers` | view | 源泉の素直な整形 |
| `customer_summary` | table | 顧客ごとの注文数・売上・cohort（毎回フルリフレッシュ） |
| `orders_inc` | incremental | 新着注文だけ追記（高水位マーク `last_loaded_at`） |

全源泉が取込時刻 `last_loaded_at` を持ち、`orders_inc` の差分判定・freshness に使う。

---

## 出力先の対応（`profiles.yml` ↔ `datagen.yml`）

`--target` 名は両ファイルで揃えてある。dbt の source は `{{ target.schema }}_raw`。

| target | dbt(モデル) | 源泉(raw_*) | 認証 |
| --- | --- | --- | --- |
| `snowflake` | `d_harato_db.jaffle_shop` | `d_harato_db.jaffle_shop_raw` | キーペア(env-var)。role=accountadmin |
| `duckdb` | `jaffle_shop.duckdb` / `main` | `main_raw` | 不要 |

> [!NOTE]
> Snowflake は専用スキーマ作成に `CREATE SCHEMA` 権限が要るため role=accountadmin にしている。
> 最小権限で運用するなら既存 `public` に置く構成も可。

---

## データ確認 / リセット

```bash
# DuckDB を直接クエリ(brew install duckdb)。読むだけなら -readonly
duckdb -readonly jaffle_shop.duckdb "select * from main.customer_summary limit 5"

# リセット
rm -f jaffle_shop.duckdb                                   # DuckDB
# Snowflake: drop schema jaffle_shop; drop schema jaffle_shop_raw;
```
