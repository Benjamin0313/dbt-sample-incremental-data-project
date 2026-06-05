#!/usr/bin/env python
"""源泉ジェネレータ。datagen.yml の宣言に従って Snowflake の源泉(raw_*)を生成・追記する。

    uv run python generate.py                  # 前回からの実経過時間で件数を算出
    uv run python generate.py --minutes 30     # 30分経過したものとして生成(検証用)

事前に Snowflake 認証の env var が必要: set -a; source .env; set +a
源泉の追加 = datagen.yml の sources: に1ブロック足すだけ。
"""
from __future__ import annotations

import argparse
import os
import random
import time
import uuid
from datetime import datetime, timedelta

import yaml
from faker import Faker

fake = Faker()


def now_tz() -> datetime:
    return datetime.now().astimezone()


# ---------- 出力先 (Snowflake) ----------
class SnowflakeTarget:
    placeholder = "%s"
    ts_type = "timestamp_tz"

    def __init__(self):
        import snowflake.connector
        # 接続情報はすべて env var(.env)から。源泉は dbt の <SF_SCHEMA>_raw に書く。
        self.schema = os.environ["SF_SCHEMA"] + "_raw"
        self.con = snowflake.connector.connect(
            account=os.environ["SF_ACCOUNT"],
            user=os.environ["SF_USER"],
            private_key_file=os.environ["SF_PRIVATE_KEY_PATH"],
            role=os.environ["SF_ROLE"],
            warehouse=os.environ["SF_WAREHOUSE"],
            database=os.environ["SF_DATABASE"],
            autocommit=True,
        )

    def execute(self, sql, params=None):
        with self.con.cursor() as cur:
            cur.execute(sql, params)

    def executemany(self, sql, rows):
        with self.con.cursor() as cur:
            cur.executemany(sql, [tuple(r) for r in rows])

    def fetchall(self, sql, params=None):
        with self.con.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def sample_sql(self, table, col, n):
        return f"select {col} from {table} sample ({n} rows)"

    def close(self):
        self.con.close()


# ---------- フィールド生成レシピ ----------
def gen_value(spec: dict, ref_cache: dict, col: str):
    kind = spec["gen"]
    if kind == "uuid":
        return str(uuid.uuid4())
    if kind == "faker":
        return getattr(fake, spec["method"])()
    if kind == "choice":
        return random.choices(spec["choices"], weights=spec.get("weights"))[0]
    if kind == "int":
        return random.randint(spec["min"], spec["max"])
    if kind == "now":
        return now_tz()
    if kind == "recent":
        return now_tz() - timedelta(days=random.uniform(0, spec.get("within_days", 14)))
    if kind == "ref":
        return random.choice(ref_cache[col])
    raise SystemExit(f"未知の gen: {kind}")


# gen → 列型カテゴリ
def col_type(tgt, spec):
    return tgt.ts_type if spec["gen"] in ("now", "recent") else (
        "integer" if spec["gen"] == "int" else "varchar")


def ensure_table(tgt, name, fields):
    cols = ", ".join(f"{c} {col_type(tgt, s)}" for c, s in fields.items())
    tgt.execute(f"create table if not exists {tgt.schema}.raw_{name} ({cols}, last_loaded_at {tgt.ts_type})")


def _per_min(every_min):
    """「N分に1件」→ 1分あたりの件数。None/0 は 0(その種別なし)。"""
    return (1.0 / every_min) if every_min else 0.0


def ensure_state(tgt):
    """実行状態テーブル(_datagen_state)を用意。経過時間と端数(acc_*)を源泉ごとに持つ。"""
    s = tgt.schema
    tgt.execute(f"create table if not exists {s}._datagen_state "
                f"(source varchar, last_tick_at {tgt.ts_type}, acc_new double, acc_upd double)")
    tgt.execute(f"alter table {s}._datagen_state add column if not exists acc_new double")
    tgt.execute(f"alter table {s}._datagen_state add column if not exists acc_upd double")


def ticks(tgt, source, profile, minutes_override, seed):
    """このソースの (new件数, update件数) を経過時間×レートから算出し、端数を繰り越す。

    短間隔のデーモンでも「5分に1人」等を正しく実現するため、レート×経過分を acc_* に
    積み増し、整数部だけを今回の件数にして残りを次回へ繰り越す(round で 0 に消えない)。
    """
    ph = tgt.placeholder
    rows = tgt.fetchall(
        f"select last_tick_at, acc_new, acc_upd from {tgt.schema}._datagen_state where source = {ph}", [source])
    now = now_tz()
    if not rows:                                   # 初回 = seed 投入
        tgt.execute(f"insert into {tgt.schema}._datagen_state values ({ph}, {ph}, 0, 0)", [source, now])
        return seed, 0
    last_tick_at, acc_new, acc_upd = rows[0]
    elapsed_min = minutes_override if minutes_override is not None else (now - last_tick_at).total_seconds() / 60.0
    # profile は「N分に1件」(new_every_min / update_every_min)。省略/0 はその種別なし。
    acc_new = (acc_new or 0) + _per_min(profile.get("new_every_min")) * elapsed_min
    acc_upd = (acc_upd or 0) + _per_min(profile.get("update_every_min")) * elapsed_min
    n_new, n_upd = int(acc_new), int(acc_upd)      # 整数部だけ生成
    tgt.execute(
        f"update {tgt.schema}._datagen_state set last_tick_at={ph}, acc_new={ph}, acc_upd={ph} where source={ph}",
        [now, acc_new - n_new, acc_upd - n_upd, source])   # 端数は繰り越し
    return n_new, n_upd


def run_source(tgt, name, spec, profiles, minutes_override):
    fields, pk = spec["fields"], spec["primary_key"]
    ph = tgt.placeholder
    ensure_table(tgt, name, fields)

    # ref 先の候補値を一度だけ取得してキャッシュ(行ごとの往復を避ける)
    ref_cache = {}
    for col, s in fields.items():
        if s["gen"] == "ref":
            vals = [r[0] for r in tgt.fetchall(f"select {s['field']} from {tgt.schema}.raw_{s['source']}")]
            if not vals:
                raise SystemExit(f"ref 先 raw_{s['source']} が空です。{s['source']} を先に定義してください。")
            ref_cache[col] = vals

    n_new, n_upd = ticks(tgt, name, profiles[spec["tick"]], minutes_override, spec.get("seed", 0))

    # --- 新規 ---
    cols = list(fields.keys()) + ["last_loaded_at"]
    if n_new:
        rows = [[gen_value(fields[c], ref_cache, c) for c in fields] + [now_tz()] for _ in range(n_new)]
        tgt.executemany(
            f"insert into {tgt.schema}.raw_{name} ({', '.join(cols)}) values ({', '.join([ph] * len(cols))})", rows
        )

    # --- 更新 (upsert のみ): mutable 列だけ再生成し、その行だけ last_loaded_at を進める ---
    n_changed = 0
    if spec["raw_style"] == "upsert" and n_upd:
        mutable = [c for c, s in fields.items() if s.get("mutable")]
        if mutable:
            for (key,) in tgt.fetchall(tgt.sample_sql(f"{tgt.schema}.raw_{name}", pk, n_upd)):
                sets = ", ".join(f"{c} = {ph}" for c in mutable) + f", last_loaded_at = {ph}"
                params = [gen_value(fields[c], ref_cache, c) for c in mutable] + [now_tz(), key]
                tgt.execute(f"update {tgt.schema}.raw_{name} set {sets} where {pk} = {ph}", params)
                n_changed += 1

    total = tgt.fetchall(f"select count(*) from {tgt.schema}.raw_{name}")[0][0]
    print(f"[datagen] raw_{name}: +{n_new} new, {n_changed} updated  (total {total})")


def run_once(cfg, minutes_override):
    """1 tick ぶん生成する。Snowflake 接続はその都度開いて閉じる(セッション切れに強い)。"""
    tgt = SnowflakeTarget()
    try:
        tgt.execute(f"create schema if not exists {tgt.schema}")
        ensure_state(tgt)
        for name, spec in cfg["sources"].items():    # 定義順に処理(ref 依存はこの順序に従う)
            run_source(tgt, name, spec, cfg["profiles"], minutes_override)
    finally:
        tgt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="datagen.yml")
    ap.add_argument("--minutes", type=float, default=None,
                    help="経過分を固定。--daemon と併用すると 1 tick ごとにこの分数ぶん生成(早送り)")
    ap.add_argument("--daemon", action="store_true", help="一定間隔で生成し続ける(裏で常駐)")
    ap.add_argument("--interval", type=float, default=60.0, help="--daemon の生成間隔(秒、既定60)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))

    if not args.daemon:                              # 単発
        run_once(cfg, args.minutes)
        return

    # --minutes 指定時は各 tick でその分数ぶん(早送り)、未指定なら実経過時間ぶん
    pace = f"{args.minutes:.0f}分ぶん/tick" if args.minutes is not None else "実経過時間ぶん"
    print(f"[datagen] daemon 開始: {args.interval:.0f}秒ごとに{pace}を生成 (Ctrl+C で停止)")
    while True:
        try:
            run_once(cfg, args.minutes)
        except KeyboardInterrupt:
            print("\n[datagen] 停止しました")
            break
        except Exception as e:                       # 一時的な接続エラー等は握りつぶして継続
            print(f"[datagen] エラー(継続します): {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
