"""Сборка данных для 2D-реплея: позиции игроков по кадрам + убийства.

Работает на pandas, не зависит от demoparser2 (его вызывает parse_demo.py).

Вход build_replay():
  pos      DataFrame: tick, steamid, name, X, Y, [yaw], health, team_num
  windows  список (round, start_tick, end_tick, winner_team) — см. round_windows()
  kills    DataFrame убийств (tick, attacker_steamid, user_steamid, weapon, headshot) или None

Выход (JSON-совместимый dict):
  players: [{id, steamid, name}]
  frames:  [{t: tick, p: [[x, y, yaw, hp, team] | None, ...по игрокам]}]
  rounds:  [{round, start, end, f0, f1, winner}]  (f0..f1 — индексы кадров раунда)
  kills:   [{t, a, v, w, hs}]  (a/v — индексы игроков, a может быть None)
  bounds:  {minx, maxx, miny, maxy}
  step:    шаг между кадрами в тиках
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STEP = 16  # 4 кадра в секунду при 64 тиках


def _s(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def round_windows(round_ends: pd.DataFrame, freeze_ticks=None):
    """Окна раундов: от конца заморозки (или конца прошлого раунда) до round_end."""
    ends = round_ends.sort_values("tick").reset_index(drop=True)
    freeze = sorted(int(t) for t in (freeze_ticks or []))
    out, prev = [], 0
    for i, row in ends.iterrows():
        end = int(row["tick"])
        start = next((f for f in freeze if prev < f < end), prev)
        winner = row["winner"] if "winner" in ends.columns else None
        try:
            winner = int(winner)
        except (TypeError, ValueError):
            winner = None
        out.append((i + 1, start, end, winner))
        prev = end
    return out


def sample_ticks(windows, step: int = STEP):
    ticks = set()
    for _, start, end, _ in windows:
        ticks.update(range(int(start), int(end), step))
        ticks.add(int(end))
    return sorted(ticks)


def build_replay(pos: pd.DataFrame, windows, kills: pd.DataFrame | None = None,
                 step: int = STEP) -> dict | None:
    if pos is None or len(pos) == 0 or not windows:
        return None
    df = pos.copy()
    if "yaw" not in df.columns:
        df["yaw"] = 0.0
    df = df.dropna(subset=["X", "Y"])
    df["steamid"] = df["steamid"].map(_s)
    df = df[(df["steamid"] != "") & (df["steamid"] != "0")]
    df = df.drop_duplicates(subset=["tick", "steamid"])
    if df.empty:
        return None

    first = df.sort_values("tick").drop_duplicates("steamid")
    sids = list(first["steamid"])
    sid_idx = {s: i for i, s in enumerate(sids)}
    names = df.sort_values("tick").groupby("steamid")["name"].last()
    players = [{"id": i, "steamid": s, "name": _s(names.get(s, s))} for s, i in sid_idx.items()]

    ticks = np.array(sorted(df["tick"].unique()), dtype=np.int64)
    arr = np.full((len(ticks), len(sids), 5), np.nan)
    ti = np.searchsorted(ticks, df["tick"].to_numpy(dtype=np.int64))
    pi = df["steamid"].map(sid_idx).to_numpy()
    arr[ti, pi, 0] = df["X"].to_numpy(dtype=float)
    arr[ti, pi, 1] = df["Y"].to_numpy(dtype=float)
    arr[ti, pi, 2] = df["yaw"].to_numpy(dtype=float)
    arr[ti, pi, 3] = df["health"].to_numpy(dtype=float) if "health" in df.columns else 100
    arr[ti, pi, 4] = df["team_num"].to_numpy(dtype=float) if "team_num" in df.columns else 0

    frames = []
    for k, t in enumerate(ticks):
        row = []
        for j in range(len(sids)):
            v = arr[k, j]
            if np.isnan(v[0]) or np.isnan(v[1]):
                row.append(None)
            else:
                hp = 0 if np.isnan(v[3]) else int(v[3])
                tm = 0 if np.isnan(v[4]) else int(v[4])
                yaw = 0 if np.isnan(v[2]) else int(round(v[2]))
                row.append([int(round(v[0])), int(round(v[1])), yaw, hp, tm])
        frames.append({"t": int(t), "p": row})

    rounds = []
    for rnd, start, end, winner in windows:
        idx = np.nonzero((ticks >= start) & (ticks <= end))[0]
        if len(idx) == 0:
            continue
        rounds.append({"round": int(rnd), "start": int(start), "end": int(end),
                       "f0": int(idx[0]), "f1": int(idx[-1]), "winner": winner})
    if not rounds:
        return None

    kill_events = []
    if kills is not None and len(kills):
        for _, r in kills.iterrows():
            v = sid_idx.get(_s(r.get("user_steamid")))
            if v is None:
                continue
            a = sid_idx.get(_s(r.get("attacker_steamid")))
            hs = r.get("headshot", False)
            kill_events.append({"t": int(r["tick"]), "a": a, "v": v,
                                "w": _s(r.get("weapon", "")),
                                "hs": bool(hs) if hs is not None and not (isinstance(hs, float) and np.isnan(hs)) else False})
        kill_events.sort(key=lambda e: e["t"])

    xs, ys = df["X"].to_numpy(dtype=float), df["Y"].to_numpy(dtype=float)
    lox, hix = np.percentile(xs, [0.5, 99.5])
    loy, hiy = np.percentile(ys, [0.5, 99.5])
    padx, pady = (hix - lox) * 0.06 + 50, (hiy - loy) * 0.06 + 50
    bounds = {"minx": int(lox - padx), "maxx": int(hix + padx),
              "miny": int(loy - pady), "maxy": int(hiy + pady)}

    return {"players": players, "frames": frames, "rounds": rounds,
            "kills": kill_events, "bounds": bounds, "step": step}
