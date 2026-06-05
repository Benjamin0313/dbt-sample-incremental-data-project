# dbt-sample-incremental-data-project

「データが継続的に到着する状況」を Snowflake 上で再現するためのサンプル。

- 源泉は **`datagen.yml` に宣言 → `generate.py` が生成**（faker でダミーデータ）
- **dbt は変換に専念**（staging → marts）
- 源泉を実行のたびに増やせるので、**incremental の差分取り込み・遅延到着・CDC・freshness** を検証できる

```
datagen.yml ──▶ generate.py ──▶ 源泉(raw_*)  ──▶ dbt(staging/marts)
 (宣言)          (faker)            Snowflake
```

源泉(raw)とモデルは Snowflake の `d_harato_db` に入る:

| 種類 | 場所 |
| --- | --- |
| 源泉 | `d_harato_db.jaffle_shop_raw.raw_customers` / `raw_orders` |
| モデル | `d_harato_db.jaffle_shop.{stg_*, customer_summary, orders_inc}` |

---

## 1. 事前準備

- **uv**（未導入なら `curl -LsSf https://astral.sh/uv/install.sh | sh`）
- **Snowflake のキーペア認証**（datum 標準）。秘密鍵 `.p8` ファイルと、Snowflake 上の対応ユーザー
  - 接続情報3つ: アカウント識別子・ユーザー・秘密鍵パス

```bash
uv sync                  # 依存(dbt-snowflake / faker / pyyaml)をバージョン固定で導入
```

---

## 2. 接続情報の設定（`.env`）

テンプレートをコピーして値を埋める:

```bash
cp .env.sample .env
```

`.env` の中身（自分の値に置き換える）:

```bash
export SF_ACCOUNT=ar29333.ap-northeast-1.aws        # Snowflake アカウント識別子
export SF_USER=you@datumstudio.jp                   # ユーザー
export SF_PRIVATE_KEY_PATH=/Users/you/.ssh/key.p8   # 秘密鍵(.p8)のパス
```

> `.env` は `.gitignore` 済み（コミットされない）。チームで共有するのは `.env.sample` だけ。

設定したら、毎回シェルで読み込んでから dbt / generate.py を使う:

```bash
set -a; source .env; set +a
```

接続確認:

```bash
uv run dbt debug --profiles-dir .
# → "Connection test: OK" / "All checks passed!" が出れば成功
```

---

## 3. 使う（源泉を生成 → dbt で変換）

基本は **2コマンド**。`generate.py` で源泉を増やし、`dbt build` で変換する。

```bash
set -a; source .env; set +a                  # ← セッションごとに1回でOK

# ① 源泉を生成(初回は seed=50件ずつ。raw_customers / raw_orders ができる)
uv run python generate.py --minutes 30
# [datagen] target = snowflake (jaffle_shop_raw)
# [datagen] raw_customers: +50 new, 0 updated  (total 50)
# [datagen] raw_orders:    +50 new, 0 updated  (total 50)

# ② dbt で変換(staging → marts)
uv run dbt build --profiles-dir .
# → Done. PASS=12 ...
```

これで `jaffle_shop` スキーマに `customer_summary`(table) と `orders_inc`(incremental) ができる。

### 「データが増え続ける」を再現する

①②を繰り返すたびに源泉が増える（**注文=追記 / 顧客=upsert**）。`orders_inc` は新着だけ取り込む。

```bash
uv run python generate.py --minutes 30 && uv run dbt build --profiles-dir .
```

- `--minutes N` … 「N分経過した」とみなして件数を決める（待たずに増やせる。検証向き）
- 引数なし `generate.py` … 前回実行からの**実際の経過時間**で件数を決める（cron や [/loop] で定期実行する用）

### incremental だけ試す

```bash
uv run dbt build --select orders_inc --profiles-dir .                 # 新着だけ追記
uv run dbt build --select orders_inc --full-refresh --profiles-dir .  # 全件作り直し
```

### freshness（最終到着からの経過）

```bash
uv run dbt source freshness --profiles-dir .
```

---

## 4. 結果を確認する

Snowflake の Web UI / snowsql 等で確認:

```sql
use database d_harato_db;

-- 源泉とモデルの件数
select count(*) from jaffle_shop_raw.raw_orders;
select count(*) from jaffle_shop.orders_inc;          -- raw_orders と同じ件数に追いつく
select * from jaffle_shop.customer_summary limit 10;  -- 顧客ごとの集計(cohort 付き)

-- CDC の確認: 直近に変わった顧客だけ last_loaded_at が新しい
select date_trunc('second', last_loaded_at) as loaded, count(*)
from jaffle_shop_raw.raw_customers
group by 1 order by 1 desc;
```

---

## 5. 源泉を追加する（`datagen.yml`）

`sources:` に1ブロック足すだけ。faker レシピで列を定義する。

```yaml
sources:
  customers:
    tick: medium                 # 増えるペース(下の profiles を参照)
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

新しい源泉を足したら、対応する dbt モデル（`models/`）を書けば変換できる。

---

## モデル（dbt）

| モデル | 種別 | 内容 |
| --- | --- | --- |
| `stg_orders` / `stg_customers` | view | 源泉の素直な整形 |
| `customer_summary` | table | 顧客ごとの注文数・売上・cohort（毎回フルリフレッシュ） |
| `orders_inc` | incremental | 新着注文だけ追記（高水位マーク `last_loaded_at`） |

全源泉が取込時刻 `last_loaded_at` を持ち、`orders_inc` の差分判定・freshness に使う。

---

## 接続先（Snowflake）

| 項目 | 値 |
| --- | --- |
| 認証 | キーペア（env-var: `SF_ACCOUNT` / `SF_USER` / `SF_PRIVATE_KEY_PATH`） |
| role | `accountadmin`（専用スキーマ作成に `CREATE SCHEMA` が必要なため） |
| database / warehouse | `d_harato_db` / `d_harato_wh` |
| schema | モデル=`jaffle_shop` / 源泉=`jaffle_shop_raw` |

接続先は `profiles.yml`（dbt）と `datagen.yml` の `target:`（源泉）で定義。別の DB/WH/role に変えるときは両方を直す。

---

## リセット

```sql
-- Snowflake 側でスキーマごと削除すると初期化される(次回 generate.py で seed から)
drop schema if exists jaffle_shop;
drop schema if exists jaffle_shop_raw;
```

---

## 困ったとき

| 症状 | 原因 / 対処 |
| --- | --- |
| `Env var required but not provided: 'SF_ACCOUNT'` | `set -a; source .env; set +a` を実行していない |
| `Insufficient privileges ... CREATE SCHEMA` | role に作成権限がない。`accountadmin` を使うか、既存スキーマに変更 |
| `250001 / JWT token is invalid` | `SF_USER` と `.p8` 鍵の対応が違う／鍵が古い。鍵と公開鍵の登録を確認 |
| `Object 'JAFFLE_SHOP_RAW.RAW_*' does not exist` | 先に `generate.py` を流していない。①→② の順で実行 |
| dbt が source を見つけない | `generate.py` の `target.schema`(`jaffle_shop_raw`) と dbt の `{{ target.schema }}_raw` がズレている |
