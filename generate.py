#!/usr/bin/env python
"""源泉ジェネレータ。datagen.yml の宣言に従って源泉(raw_*)を生成・追記する。

出力先は datagen.yml の targets で定義(snowflake / duckdb)。

    uv run python generate.py                      # 既定ターゲット(snowflake)
    uv run python generate.py --target duckdb      # ローカル DuckDB に出力
    uv run python generate.py --minutes 30         # 30分経過したものとして生成(検証用)

源泉の追加 = datagen.yml の sources: に1ブロック足すだけ。
"""
from __future__ import annotations

import argparse
import os
import random
import uuid
from datetime import datetime, timedelta

import yaml
from faker import Faker

fake = Faker()


def now_tz() -> datetime:
    return datetime.now().astimezone()


# ---------- 出力先アダプタ (DuckDB / Snowflake) ----------
class DuckDBTarget:
    placeholder = "?"
    ts_type = "timestamptz"

    def __init__(self, cfg):
        import duckdb
        self.con = duckdb.connect(cfg["database"])
        self.schema = cfg["schema"]

    def execute(self, sql, params=None):
        self.con.execute(sql, params or [])

    def executemany(self, sql, rows):
        self.con.executemany(sql, rows)

    def fetchall(self, sql, params=None):
        return self.con.execute(sql, params or []).fetchall()

    def sample_sql(self, table, col, n):
        return f"select {col} from {table} using sample {n} rows"

    def close(self):
        self.con.close()


class SnowflakeTarget:
    placeholder = "%s"
    ts_type = "timestamp_tz"

    def __init__(self, cfg):
        import snowflake.connector
        self.con = snowflake.connector.connect(
            account=os.environ[cfg["account_env"]],
            user=os.environ[cfg["user_env"]],
            private_key_file=os.environ[cfg["private_key_env"]],
            role=cfg["role"],
            warehouse=cfg["warehouse"],
            database=cfg["database"],
            schema=cfg["schema"],
            autocommit=True,
        )
        self.schema = cfg["schema"]

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


def make_target(target_cfg):
    return {"duckdb": DuckDBTarget, "snowflake": SnowflakeTarget}[target_cfg["type"]](target_cfg)


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


def ticks(tgt, source, profile, minutes_override, seed):
    """このソースの (new件数, update件数) を経過時間から算出し、state を進める。"""
    tgt.execute(f"create table if not exists {tgt.schema}._datagen_state (source varchar, last_tick_at {tgt.ts_type})")
    rows = tgt.fetchall(f"select last_tick_at from {tgt.schema}._datagen_state where source = {tgt.placeholder}", [source])
    now = now_tz()
    if not rows:
        tgt.execute(f"insert into {tgt.schema}._datagen_state values ({tgt.placeholder}, {tgt.placeholder})", [source, now])
        return seed, 0
    elapsed_min = minutes_override if minutes_override is not None else (now - rows[0][0]).total_seconds() / 60.0
    tgt.execute(f"update {tgt.schema}._datagen_state set last_tick_at = {tgt.placeholder} where source = {tgt.placeholder}", [now, source])
    return round(profile["new_per_min"] * elapsed_min), round(profile["update_per_min"] * elapsed_min)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="datagen.yml")
    ap.add_argument("--target", help="datagen.yml の targets から選択(既定: default_target)")
    ap.add_argument("--minutes", type=float, default=None, help="経過分を固定(検証用)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    target_name = args.target or cfg["default_target"]
    tgt = make_target(cfg["targets"][target_name])
    print(f"[datagen] target = {target_name} ({tgt.schema})")
    tgt.execute(f"create schema if not exists {tgt.schema}")
    for name, spec in cfg["sources"].items():        # 定義順に処理(ref 依存はこの順序に従う)
        run_source(tgt, name, spec, cfg["profiles"], args.minutes)
    tgt.close()


if __name__ == "__main__":
    main()
