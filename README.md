# 🥪 Jaffle Shop — 宣言的な源泉ジェネレータ付き

_powered by Faker + dbt-core (Snowflake / DuckDB)_

「データが継続的に到着する状況」を再現するためのサンプル。
源泉は **`datagen.yml` に宣言 → `generate.py` が生成**し、**dbt は変換に専念**します。
出力先は **Snowflake**(社内 datum、既定)と **DuckDB**(ローカル検証)を `--target` で切替。

- **源泉の追加 = `datagen.yml` に数行足すだけ**（faker レシピで列を定義）
- `raw_style: append`(追記) / `upsert`(PKでmerge、**変更行だけ `last_loaded_at` が進む**)
- `tick` プロファイルで「実時間あたり何件」を制御（cron/`/loop`/手動どれでも）

これで source freshness・incremental の差分取り込み・遅延到着・CDC を検証できます。

## 使い方

前提: [uv](https://docs.astral.sh/uv/)(未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`)。

```bash
uv sync     # 初回: faker / dbt-core / dbt-snowflake / dbt-duckdb を導入
```

**Snowflake(既定)** — 社内 datum。datum 標準のキーペア認証 env var が必要:

```bash
export SF_ACCOUNT=...  SF_USER=...  SF_PRIVATE_KEY_PATH=/path/to/key.p8

uv run python generate.py --minutes 30          # 源泉を Snowflake(jaffle_shop_raw)へ生成
uv run dbt build --profiles-dir .               # Snowflake(jaffle_shop)へ変換
```

**DuckDB(ローカル検証)** — 認証不要、`jaffle_shop.duckdb` に入る:

```bash
uv run python generate.py --target duckdb --minutes 30
uv run dbt build --profiles-dir . --target duckdb
```

- `generate.py` 引数なし … 前回実行からの**実経過時間**で件数を算出
- `--minutes N` … N分経過したものとして生成（待たずに検証できる）
- `--target` … `datagen.yml` / `profiles.yml` の両方に同名ターゲット(`snowflake` / `duckdb`)を定義済み

## 源泉を追加する(`datagen.yml`)

`sources:` に1ブロック足すだけ。例(既存の定義):

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

### フィールドのレシピ(`gen`)

| gen | 説明 | 例 |
| --- | --- | --- |
| `uuid` | UUID 文字列 | `{ gen: uuid }` |
| `faker` | faker の任意メソッド | `{ gen: faker, method: name }` |
| `choice` | 重み付き選択。`mutable: true` で upsert 更新対象に | `{ gen: choice, choices: [a,b], weights: [8,2] }` |
| `int` | 整数 | `{ gen: int, min: 3, max: 50 }` |
| `now` | 現在時刻 | `{ gen: now }` |
| `recent` | 直近 N 日に散らす(遅延到着の再現) | `{ gen: recent, within_days: 14 }` |
| `ref` | 他源泉の既存値を参照(FK整合) | `{ gen: ref, source: customers, field: customer_id }` |

### tick プロファイル

`profiles:` に「1分あたりの新規/更新件数」を定義し、各源泉が `tick:` で参照します。

```yaml
profiles:
  medium: { new_per_min: 0.2, update_per_min: 0.0333 }   # 5分に1新規 / 30分に1更新
  fast:   { new_per_min: 1.7, update_per_min: 0.0 }       # 30分で約50件
```

## モデル(dbt、最小構成)

| モデル | 種別 | 内容 |
| --- | --- | --- |
| `stg_orders` / `stg_customers` | view | 源泉の素直な整形 |
| `customer_summary` | table | 顧客ごとの注文数・売上・cohort(毎回フルリフレッシュ) |
| `orders_inc` | incremental | 新着注文だけ追記(高水位マーク `last_loaded_at`) |

## last_loaded_at(取込時刻)

全源泉が `last_loaded_at` を持ちます。

- **append**(orders): 新規行に付与、既存は不変
- **upsert**(customers): **新規行と、`mutable` な列が変わった行だけ**更新 → 本来の「その行が最後に変わった時刻」になる

## 出力先の対応(profiles.yml ↔ datagen.yml)

`--target` 名は両ファイルで揃えてあります。dbt の source スキーマは `<schema>_raw` なので、
ジェネレータの書き込み先スキーマもそれに合わせています。

| target | profiles.yml (dbt) | datagen.yml (源泉) |
| --- | --- | --- |
| `snowflake` | `d_harato_db` / schema `jaffle_shop` | schema `jaffle_shop_raw` |
| `duckdb` | `jaffle_shop.duckdb` / schema `main` | schema `main_raw` |

Snowflake 認証は env-var + キーペア(`SF_ACCOUNT` / `SF_USER` / `SF_PRIVATE_KEY_PATH`)。
接続確認: `uv run dbt debug --profiles-dir .`(既定 snowflake) / `--target duckdb`。

## データの確認 / リセット(DuckDB)

```bash
# 直接クエリ(brew install duckdb)。読むだけなら -readonly
duckdb -readonly jaffle_shop.duckdb "select * from main.customer_summary limit 5"

# リセット(次回 generate.py で seed から作り直し)
rm -f jaffle_shop.duckdb
```

Snowflake 側のリセットは `drop schema jaffle_shop_raw; drop schema jaffle_shop;`(または `--full-refresh`)。
