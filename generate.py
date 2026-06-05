#!/usr/bin/env python
"""datagen.yml の宣言に従って Snowflake の源泉(raw_*)を生成・追記する。

    uv run python generate.py --minutes 30                 # 30分ぶんを生成
    uv run python generate.py --daemon --interval 60       # 60秒ごとに常駐生成

接続情報は .env から: set -a; source .env; set +a
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


class SnowflakeTarget:
    placeholder = "%s"
    ts_type = "timestamp_tz"

    def __init__(self):
        import snowflake.connector
        self.schema = os.environ["SF_SCHEMA"] + "_raw"   # dbt の <SF_SCHEMA>_raw
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


def gen_value(spec, ref_cache, col):
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


def col_type(tgt, spec):
    return tgt.ts_type if spec["gen"] in ("now", "recent") else (
        "integer" if spec["gen"] == "int" else "varchar")


def ensure_table(tgt, name, fields):
    cols = ", ".join(f"{c} {col_type(tgt, s)}" for c, s in fields.items())
    tgt.execute(f"create table if not exists {tgt.schema}.raw_{name} ({cols}, last_loaded_at {tgt.ts_type})")


def per_min(every_min):
    """「N分に1件」→ 1分あたりの件数。None/0 はその種別なし。"""
    return (1.0 / every_min) if every_min else 0.0


def ensure_state(tgt):
    s = tgt.schema
    tgt.execute(f"create table if not exists {s}._datagen_state "
                f"(source varchar, last_tick_at {tgt.ts_type}, acc_new double, acc_upd double)")
    tgt.execute(f"alter table {s}._datagen_state add column if not exists acc_new double")
    tgt.execute(f"alter table {s}._datagen_state add column if not exists acc_upd double")


def ticks(tgt, source, profile, minutes_override, seed):
    """経過時間×レートから (new, update) 件数を算出。端数は acc_* に繰り越す。"""
    ph = tgt.placeholder
    rows = tgt.fetchall(
        f"select last_tick_at, acc_new, acc_upd from {tgt.schema}._datagen_state where source = {ph}", [source])
    now = now_tz()
    if not rows:                                   # 初回 = seed
        tgt.execute(f"insert into {tgt.schema}._datagen_state values ({ph}, {ph}, 0, 0)", [source, now])
        return seed, 0
    last_tick_at, acc_new, acc_upd = rows[0]
    elapsed_min = minutes_override if minutes_override is not None else (now - last_tick_at).total_seconds() / 60.0
    acc_new = (acc_new or 0) + per_min(profile.get("new_every_min")) * elapsed_min
    acc_upd = (acc_upd or 0) + per_min(profile.get("update_every_min")) * elapsed_min
    n_new, n_upd = int(acc_new), int(acc_upd)
    tgt.execute(
        f"update {tgt.schema}._datagen_state set last_tick_at={ph}, acc_new={ph}, acc_upd={ph} where source={ph}",
        [now, acc_new - n_new, acc_upd - n_upd, source])
    return n_new, n_upd


def run_source(tgt, name, spec, profiles, minutes_override):
    fields, pk, ph = spec["fields"], spec["primary_key"], tgt.placeholder
    ensure_table(tgt, name, fields)

    # ref 先の候補値を一度だけ取得してキャッシュ
    ref_cache = {}
    for col, s in fields.items():
        if s["gen"] == "ref":
            vals = [r[0] for r in tgt.fetchall(f"select {s['field']} from {tgt.schema}.raw_{s['source']}")]
            if not vals:
                raise SystemExit(f"ref 先 raw_{s['source']} が空です。{s['source']} を先に定義してください。")
            ref_cache[col] = vals

    n_new, n_upd = ticks(tgt, name, profiles[spec["tick"]], minutes_override, spec.get("seed", 0))

    cols = list(fields.keys()) + ["last_loaded_at"]
    if n_new:
        rows = [[gen_value(fields[c], ref_cache, c) for c in fields] + [now_tz()] for _ in range(n_new)]
        tgt.executemany(
            f"insert into {tgt.schema}.raw_{name} ({', '.join(cols)}) values ({', '.join([ph] * len(cols))})", rows)

    # upsert: mutable 列だけ振り直し、その行だけ last_loaded_at を進める
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
    tgt = SnowflakeTarget()
    try:
        tgt.execute(f"create schema if not exists {tgt.schema}")
        ensure_state(tgt)
        for name, spec in cfg["sources"].items():    # 定義順(ref 依存はこの順序)
            run_source(tgt, name, spec, cfg["profiles"], minutes_override)
    finally:
        tgt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="datagen.yml")
    ap.add_argument("--minutes", type=float, default=None,
                    help="経過分を固定。--daemon と併用すると 1 tick ごとにこの分数ぶん(早送り)")
    ap.add_argument("--daemon", action="store_true", help="一定間隔で生成し続ける")
    ap.add_argument("--interval", type=float, default=60.0, help="--daemon の生成間隔(秒)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))

    if not args.daemon:
        run_once(cfg, args.minutes)
        return

    pace = f"{args.minutes:.0f}分ぶん/tick" if args.minutes is not None else "実経過時間ぶん"
    print(f"[datagen] daemon 開始: {args.interval:.0f}秒ごとに{pace} (Ctrl+C で停止)")
    while True:
        try:
            run_once(cfg, args.minutes)
        except KeyboardInterrupt:
            print("\n[datagen] 停止しました")
            break
        except Exception as e:
            print(f"[datagen] エラー(継続します): {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
