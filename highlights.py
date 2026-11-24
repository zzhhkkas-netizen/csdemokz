"""Поиск хайлайтов в событиях матча CS2.

Модуль работает только с pandas DataFrame и не зависит от demoparser2,
поэтому его легко тестировать и переиспользовать.

Ожидаемые колонки в `kills` (событие player_death):
    tick, attacker_name, attacker_steamid, attacker_team_num,
    user_name, user_steamid, user_team_num   (user_* = жертва)
    weapon, headshot, thrusmoke, noscope, attackerblind, penetrated, distance
    (последние необязательны)

`round_ends`: колонки tick, winner (событие round_end).
`rosters` (необязательно): колонки round, steamid, name, team_num —
составы команд на начало каждого раунда. Без них используется
предположение «5 на 5» (для клатчей).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TICKRATE = 64
PRE_ROLL_SECONDS = 5
SIDE = {2: "T", 3: "CT"}

TAG_LABELS = {
    "noscope": "Ноускоуп",
    "smoke": "Через смок",
    "wallbang": "Через стену",
    "blind": "Вслепую",
}

OPTIONAL_DEFAULTS = {
    "weapon": "",
    "headshot": False,
    "thrusmoke": False,
    "noscope": False,
    "attackerblind": False,
    "penetrated": 0,
    "distance": 0.0,
}


# ---------------------------------------------------------------- helpers
def _s(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return str(v)


def _truthy(v) -> bool:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return False
    return bool(v)


def normalize_team(v):
    """Приводит команду к 2 (T) / 3 (CT) или None."""
    if isinstance(v, str):
        u = v.strip().upper()
        if u in ("T", "TERRORIST", "TERRORISTS"):
            return 2
        if u in ("CT", "COUNTERTERRORIST", "COUNTER-TERRORIST", "COUNTERTERRORISTS"):
            return 3
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n in (2, 3) else None


def _tags(row) -> list[str]:
    tags = []
    if _truthy(row["noscope"]):
        tags.append("noscope")
    if _truthy(row["thrusmoke"]):
        tags.append("smoke")
    try:
        if int(row["penetrated"]) > 0:
            tags.append("wallbang")
    except (TypeError, ValueError):
        pass
    if _truthy(row["attackerblind"]):
        tags.append("blind")
    return tags


def _kill_entry(row) -> dict:
    return {
        "tick": int(row["tick"]),
        "victim": _s(row["user_name"]),
        "weapon": _s(row["weapon"]),
        "headshot": _truthy(row["headshot"]),
        "tags": _tags(row),
        "distance": round(float(row["distance"]), 1) if _truthy(row["distance"]) else 0.0,
    }


def _make(kind, label, rnd, steamid, name, team, rows, score, start_tick=None, **extra) -> dict:
    ticks = [int(r["tick"]) for r in rows]
    start = int(start_tick) if start_tick is not None else min(ticks)
    start = min(start, min(ticks))
    end = max(ticks)
    goto = max(0, start - PRE_ROLL_SECONDS * TICKRATE)
    h = {
        "type": kind,
        "label": label,
        "round": int(rnd),
        "player": name,
        "steamid": steamid,
        "side": SIDE.get(team, "?"),
        "start_tick": start,
        "end_tick": end,
        "goto_tick": goto,
        "command": f"demo_gototick {goto}",
        "duration_s": round((end - start) / TICKRATE, 1),
        "kills": [_kill_entry(r) for r in rows],
        "score": int(score),
    }
    h.update(extra)
    return h


# ------------------------------------------------------------ preparation
def prepare(kills: pd.DataFrame, round_ends: pd.DataFrame):
    """Добавляет номер раунда, нормализует команды. Возвращает (все убийства, победители)."""
    k = kills.copy()
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in k.columns:
            k[col] = default
    for col in ("attacker_name", "user_name"):
        if col not in k.columns:
            k[col] = ""
    for col in ("attacker_team_num", "user_team_num"):
        if col not in k.columns:
            k[col] = None

    k["attacker_steamid"] = k["attacker_steamid"].map(_s)
    k["user_steamid"] = k["user_steamid"].map(_s)

    ends = round_ends.sort_values("tick").reset_index(drop=True)
    end_ticks = ends["tick"].to_numpy()
    idx = np.searchsorted(end_ticks, k["tick"].to_numpy(), side="left")
    k["round"] = idx + 1
    k = k[idx < len(end_ticks)].copy()

    k["attacker_team"] = k["attacker_team_num"].map(normalize_team)
    k["victim_team"] = k["user_team_num"].map(normalize_team)
    k = k.sort_values("tick").reset_index(drop=True)

    winners = {i + 1: normalize_team(w) for i, w in enumerate(ends.get("winner", []))}
    return k, winners


def _valid_kills(k: pd.DataFrame) -> pd.DataFrame:
    """Убийства игроков: без мира/суицидов/тимкиллов."""
    m = (k["attacker_steamid"] != "") & (k["attacker_steamid"] != "0")
    m &= k["attacker_steamid"] != k["user_steamid"]
    same_team = (
        k["attacker_team"].notna()
        & k["victim_team"].notna()
        & (k["attacker_team"] == k["victim_team"])
    )
    return k[m & ~same_team].copy()


# --------------------------------------------------------------- detectors
def find_multikills(valid: pd.DataFrame) -> list[dict]:
    out = []
    for (rnd, sid), g in valid.groupby(["round", "attacker_steamid"]):
        n = len(g)
        if n < 3:
            continue
        rows = [r for _, r in g.iterrows()]
        label = "ACE" if n >= 5 else f"{n}K"
        kind = "ace" if n >= 5 else f"{n}k"
        hs = sum(_truthy(r["headshot"]) for r in rows)
        base = {3: 30, 4: 60}.get(n, 100)
        out.append(
            _make(kind, label, rnd, sid, _s(rows[0]["attacker_name"]),
                  rows[0]["attacker_team"], rows, base + 5 * hs)
        )
    return out


def _round_roster(rnd, g: pd.DataFrame, rosters):
    """{team: {steamid: name}} для раунда."""
    roster = {2: {}, 3: {}}
    if rosters is not None:
        r = rosters[rosters["round"] == rnd]
        for _, row in r.iterrows():
            t = normalize_team(row["team_num"])
            if t:
                roster[t][_s(row["steamid"])] = _s(row["name"])
        return roster, True
    # без составов — только игроки, замеченные в убийствах этого раунда
    for _, row in g.iterrows():
        if row["attacker_team"] and row["attacker_steamid"] not in ("", "0"):
            roster[row["attacker_team"]][row["attacker_steamid"]] = _s(row["attacker_name"])
        if row["victim_team"]:
            roster[row["victim_team"]][row["user_steamid"]] = _s(row["user_name"])
    return roster, False


def find_clutches(k: pd.DataFrame, valid: pd.DataFrame, winners: dict,
                  rosters=None, min_vs: int = 2) -> list[dict]:
    out = []
    for rnd, g in k.groupby("round", sort=True):
        roster, have_roster = _round_roster(rnd, g, rosters)
        size = {t: (len(roster[t]) if have_roster else 5) for t in (2, 3)}
        if min(size.values()) == 0:
            continue
        dead = {2: set(), 3: set()}
        started = {}
        for _, d in g.iterrows():
            vt = d["victim_team"]
            if vt not in dead:
                continue
            dead[vt].add(d["user_steamid"])
            for team in (2, 3):
                if team in started:
                    continue
                opp = 5 - team
                alive_cnt = size[team] - len(dead[team])
                known = [s for s in roster[team] if s not in dead[team]]
                opp_alive = size[opp] - len(dead[opp])
                if alive_cnt == 1 and len(known) == 1 and opp_alive >= 1:
                    started[team] = (known[0], opp_alive, int(d["tick"]))

        for team, (sid, vs, tick0) in started.items():
            if vs < min_vs:
                continue
            ks = valid[(valid["round"] == rnd)
                       & (valid["attacker_steamid"] == sid)
                       & (valid["tick"] >= tick0)]
            if ks.empty:
                continue
            rows = [r for _, r in ks.iterrows()]
            won = winners.get(rnd) == team
            score = 20 * vs + (20 if won else 0) + 5 * len(rows)
            label = f"Клатч 1v{vs}" + (" · выигран" if won else " · проигран")
            name = roster[team].get(sid) or _s(rows[0]["attacker_name"])
            out.append(_make("clutch", label, rnd, sid, name, team, rows, score,
                             start_tick=tick0, vs=vs, won=won))
    return out


def find_special_kills(valid: pd.DataFrame) -> list[dict]:
    out = []
    for _, r in valid.iterrows():
        tags = [t for t in _tags(r) if t in TAG_LABELS]
        if not tags:
            continue
        label = " + ".join(TAG_LABELS[t] for t in tags)
        score = 10 * len(tags) + (5 if _truthy(r["headshot"]) else 0)
        out.append(_make("special", label, r["round"], r["attacker_steamid"],
                         _s(r["attacker_name"]), r["attacker_team"], [r], score))
    return out


def player_stats(k: pd.DataFrame, valid: pd.DataFrame) -> list[dict]:
    stats: dict[str, dict] = {}

    def get(sid, name):
        return stats.setdefault(sid, {"steamid": sid, "name": name, "kills": 0,
                                      "deaths": 0, "headshots": 0})

    for _, r in valid.iterrows():
        s = get(r["attacker_steamid"], _s(r["attacker_name"]))
        s["kills"] += 1
        s["headshots"] += int(_truthy(r["headshot"]))
    for _, r in k.iterrows():
        if r["user_steamid"]:
            get(r["user_steamid"], _s(r["user_name"]))["deaths"] += 1
    res = list(stats.values())
    for s in res:
        s["hs_pct"] = round(100 * s["headshots"] / s["kills"]) if s["kills"] else 0
    return sorted(res, key=lambda s: (-s["kills"], s["deaths"]))


# -------------------------------------------------------------- entrypoint
def analyze(kills: pd.DataFrame, round_ends: pd.DataFrame,
            rosters: pd.DataFrame | None = None, min_clutch_vs: int = 2) -> dict:
    k, winners = prepare(kills, round_ends)
    valid = _valid_kills(k)

    highlights = (
        find_multikills(valid)
        + find_clutches(k, valid, winners, rosters, min_clutch_vs)
        + find_special_kills(valid)
    )
    highlights.sort(key=lambda h: (-h["score"], h["round"]))
    for i, h in enumerate(highlights):
        h["id"] = i

    return {
        "highlights": highlights,
        "players": player_stats(k, valid),
        "rounds": len(winners),
    }
