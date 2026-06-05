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

`.env` の中身（自分の値に置き換える）。**dbt と generator が共用**する:

```bash
export SF_ACCOUNT=ar29333.ap-northeast-1.aws        # Snowflake アカウント識別子
export SF_USER=you@datumstudio.jp                   # ユーザー
export SF_PRIVATE_KEY_PATH=/Users/you/.ssh/key.p8   # 秘密鍵(.p8)のパス
export SF_ROLE=accountadmin                         # ロール
export SF_WAREHOUSE=d_harato_wh                     # ウェアハウス
export SF_DATABASE=d_harato_db                      # データベース
export SF_SCHEMA=jaffle_shop                        # モデルのスキーマ(源泉は <SF_SCHEMA>_raw)
```

> `.env` は `.gitignore` 済み（コミットされない）。チームで共有するのは `.env.sample` だけ。
> 接続情報はすべて `.env` に集約（`profiles.yml` も `datagen.yml` もハードコードしない）。

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

uv run python generate.py --minutes 30       # ① 源泉を生成
uv run dbt build --profiles-dir .            # ② dbt で変換(staging → marts)
```

これで `jaffle_shop` スキーマに `customer_summary`(table) と `orders_inc`(incremental) ができる。
①②を繰り返すたびに源泉が増える（**注文=追記 / 顧客=upsert**、`orders_inc` は新着だけ取り込む）。

### generate.py は何件入れる？（`--minutes` とは）

ジェネレータは挿入件数を **「経過時間 × ペース」** で決める。`--minutes` は“経過時間”の渡し方を変えるオプション:

| コマンド | 経過時間 | 何が起きる |
| --- | --- | --- |
| `generate.py --minutes 30` | **30分とみなす**(固定) | 30分待たずに「30分ぶん」をまとめて挿入。検証・初期投入向き |
| `generate.py`（引数なし） | 前回実行からの**実経過時間** | 実時間どおり少しずつ |
| `generate.py --daemon --interval 60` | 60秒ごとに実経過ぶん | **裏で流し続ける**(常駐) |

ペースは `datagen.yml` の `tick` プロファイル（「N分に1件」）。例として `--minutes 30` を渡すと:

| 源泉 | ペース | 30分での件数 |
| --- | --- | --- |
| customers | 5分に1新規 / 30分に1更新 | `30÷5 = 6` 新規 / `30÷30 = 1` 更新 |
| orders | 30秒(0.5分)に1新規 | `30÷0.5 = 60` 新規 |

```
[datagen] raw_customers: +6 new, 1 updated  (total ...)
[datagen] raw_orders:    +60 new, 0 updated (total ...)
```

> **初回だけ**は経過時間に関係なく `seed`（各50件）を投入する。`--minutes` が効くのは2回目以降。
> 端数（例 0.6件）は `_datagen_state` に繰り越すので、短間隔で回しても平均ペースは保たれる。

### 裏で定期的に流し続ける（デーモン）

`datagen.yml` の定義どおりに、**dbt とは無関係に Snowflake へ自動で insert し続ける**:

```bash
set -a; source .env; set +a
uv run python generate.py --daemon --interval 60        # 60秒ごとに実経過ぶんを生成(Ctrl+C で停止)

# 端末を閉じても動かし続ける(ログはファイルへ)
nohup uv run python generate.py --daemon --interval 60 > datagen.log 2>&1 &
```

取り込み側は別途 `dbt build`（cron や [/loop] で回す）。**生成と変換は独立**。

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

`profiles:` に **「N分に1件」** で生成ペースを定義し、各源泉が `tick:` で参照する。
`update_every_min` を省くと更新なし（append 源泉向け）。

```yaml
profiles:
  medium: { new_every_min: 5,  update_every_min: 30 }   # 5分に1新規 / 30分に1更新
  fast:   { new_every_min: 0.5 }                         # 30秒に1新規(更新なし)
```

> 端数は `_datagen_state` に繰り越すので、`new_every_min: 5` は短間隔で回しても平均「5分に1件」になる。

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

接続情報はすべて **`.env`** に集約。`profiles.yml`(dbt) と `generate.py`(源泉) が共用する。

| env var | 用途 |
| --- | --- |
| `SF_ACCOUNT` / `SF_USER` / `SF_PRIVATE_KEY_PATH` | キーペア認証 |
| `SF_ROLE` | ロール（専用スキーマ作成に `CREATE SCHEMA` が要る。例 `accountadmin`） |
| `SF_DATABASE` / `SF_WAREHOUSE` | DB / ウェアハウス |
| `SF_SCHEMA` | モデルのスキーマ。源泉は `<SF_SCHEMA>_raw` に入る |

別の DB/WH/role/schema に変えるときは `.env` を直すだけ（両ファイルに反映される）。

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
