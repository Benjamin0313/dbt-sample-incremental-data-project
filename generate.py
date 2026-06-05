#!/usr/bin/env python
"""源泉ジェネレータ。datagen.yml の宣言に従って DuckDB の源泉(main_raw)を生成・追記する。

使い方:
    uv run python generate.py                 # 前回からの実経過時間で件数を算出
    uv run python generate.py --minutes 30    # 30分経過したものとして生成(検証用)
    uv run python generate.py --config datagen.yml --db jaffle_shop.duckdb

源泉の追加 = datagen.yml の sources: に1ブロック足すだけ。
"""
from __future__ import annotations

import argparse
import random
import uuid
from datetime import datetime, timedelta

import duckdb
import yaml
from faker import Faker

fake = Faker()


# ---------- フィールド生成レシピ ----------
def gen_value(spec: dict, con, schema: str):
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
        return datetime.now()
    if kind == "recent":
        return datetime.now() - timedelta(days=random.uniform(0, spec.get("within_days", 14)))
    if kind == "ref":
        rows = con.execute(
            f'select {spec["field"]} from "{schema}"."raw_{spec["source"]}" using sample 1 rows'
        ).fetchone()
        if rows is None:
            raise SystemExit(f"ref 先 raw_{spec['source']} に行がありません。{spec['source']} を先に定義してください。")
        return rows[0]
    raise SystemExit(f"未知の gen: {kind}")


# gen → DuckDB 型
SQL_TYPE = {"uuid": "varchar", "faker": "varchar", "choice": "varchar",
            "int": "integer", "now": "timestamptz", "recent": "timestamptz", "ref": "varchar"}


def ensure_table(con, schema: str, name: str, fields: dict):
    cols = ", ".join(f'"{c}" {SQL_TYPE[s["gen"]]}' for c, s in fields.items())
    con.execute(f'create table if not exists "{schema}"."raw_{name}" ({cols}, last_loaded_at timestamptz)')


def ticks(con, schema: str, source: str, profile: dict, minutes_override, seed: int):
    """このソースの (new件数, update件数) を経過時間から算出し、state を進める。"""
    con.execute('create table if not exists "{}"._datagen_state (source varchar, last_tick_at timestamptz)'.format(schema))
    row = con.execute(f"select last_tick_at from \"{schema}\"._datagen_state where source = ?", [source]).fetchone()
    now = datetime.now()
    if row is None:
        con.execute(f"insert into \"{schema}\"._datagen_state values (?, ?)", [source, now])
        return seed, 0  # 初回は seed 件だけ投入
    elapsed_min = minutes_override if minutes_override is not None else (now - row[0]).total_seconds() / 60.0
    con.execute(f"update \"{schema}\"._datagen_state set last_tick_at = ? where source = ?", [now, source])
    n_new = round(profile["new_per_min"] * elapsed_min)
    n_upd = round(profile["update_per_min"] * elapsed_min)
    return n_new, n_upd


def run_source(con, schema: str, name: str, spec: dict, profiles: dict, minutes_override):
    fields: dict = spec["fields"]
    pk = spec["primary_key"]
    ensure_table(con, schema, name, fields)
    n_new, n_upd = ticks(con, schema, name, profiles[spec["tick"]], minutes_override, spec.get("seed", 0))

    cols = list(fields.keys()) + ["last_loaded_at"]
    placeholders = ", ".join(["?"] * len(cols))

    # --- 新規 (append/upsert 共通) ---
    new_rows = []
    for _ in range(n_new):
        vals = [gen_value(fields[c], con, schema) for c in fields]
        new_rows.append(vals + [datetime.now()])
    if new_rows:
        con.executemany(
            f'insert into "{schema}"."raw_{name}" ({", ".join(cols)}) values ({placeholders})', new_rows
        )

    # --- 更新 (upsert のみ): mutable な列だけ再生成し、その行だけ last_loaded_at を進める ---
    n_changed = 0
    if spec["raw_style"] == "upsert" and n_upd:
        mutable = [c for c, s in fields.items() if s.get("mutable")]
        if mutable:
            targets = con.execute(
                f'select {pk} from "{schema}"."raw_{name}" using sample {n_upd} rows'
            ).fetchall()
            for (key,) in targets:
                sets = ", ".join(f'"{c}" = ?' for c in mutable) + ", last_loaded_at = ?"
                params = [gen_value(fields[c], con, schema) for c in mutable] + [datetime.now(), key]
                con.execute(f'update "{schema}"."raw_{name}" set {sets} where {pk} = ?', params)
                n_changed += 1

    return n_new, n_changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="datagen.yml")
    ap.add_argument("--db")
    ap.add_argument("--minutes", type=float, default=None, help="経過分を固定(検証用)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    db = args.db or cfg.get("database", "jaffle_shop.duckdb")
    schema = cfg.get("schema", "main_raw")

    con = duckdb.connect(db)
    con.execute(f'create schema if not exists "{schema}"')
    for name, spec in cfg["sources"].items():           # 定義順に処理(ref 依存はこの順序に従う)
        n_new, n_upd = run_source(con, schema, name, spec, cfg["profiles"], args.minutes)
        total = con.execute(f'select count(*) from "{schema}"."raw_{name}"').fetchone()[0]
        print(f"[datagen] raw_{name}: +{n_new} new, {n_upd} updated  (total {total})")
    con.close()


if __name__ == "__main__":
    main()
