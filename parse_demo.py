"""Чтение .dem файла CS2 через demoparser2 и приведение к формату highlights.analyze()."""
from __future__ import annotations

import numpy as np
import pandas as pd


def parse_demo(path: str):
    """Возвращает (kills, round_ends, rosters, meta, positions, windows).

    positions/windows могут быть None, если позиции прочитать не удалось —
    тогда список хайлайтов всё равно работает, просто без 2D-реплея.
    """
    from demoparser2 import DemoParser  # импорт здесь, чтобы остальное работало без пакета

    parser = DemoParser(path)

    try:
        header = parser.parse_header()
    except Exception:
        header = {}
    meta = {"map": header.get("map_name", "unknown")}

    kills = parser.parse_event("player_death", player=["team_num"])
    round_ends = parser.parse_event("round_end")

    # Отрезаем разминку и рестарты: всё, что до последнего начала матча.
    try:
        begin = parser.parse_event("begin_new_match")
        if len(begin):
            cut = int(begin["tick"].max())
            kills = kills[kills["tick"] > cut]
            round_ends = round_ends[round_ends["tick"] > cut]
    except Exception:
        pass

    kills = kills.reset_index(drop=True)
    round_ends = round_ends.sort_values("tick").reset_index(drop=True)

    rosters = _rosters(parser, round_ends)
    positions, windows = _positions(parser, round_ends)
    return kills, round_ends, rosters, meta, positions, windows


def _rosters(parser, round_ends: pd.DataFrame):
    """Составы команд на начало каждого раунда (для точного определения клатчей)."""
    try:
        freeze = parser.parse_event("round_freeze_end")
        ticks = sorted(set(int(t) for t in freeze["tick"]))
        if not ticks:
            return None
        df = parser.parse_ticks(["team_num"], ticks=ticks)
        end_ticks = round_ends["tick"].to_numpy()
        df["round"] = np.searchsorted(end_ticks, df["tick"].to_numpy(), side="left") + 1
        df = df[df["team_num"].isin([2, 3])]
        return df[["round", "steamid", "name", "team_num"]].drop_duplicates()
    except Exception:
        return None  # fallback: «5 на 5»


def _positions(parser, round_ends: pd.DataFrame):
    """Позиции игроков по кадрам (4 в секунду) для 2D-реплея."""
    from replay import STEP, round_windows, sample_ticks

    try:
        try:
            freeze = [int(t) for t in parser.parse_event("round_freeze_end")["tick"]]
        except Exception:
            freeze = []
        windows = round_windows(round_ends, freeze)
        ticks = sample_ticks(windows, STEP)
        if not ticks:
            return None, None
        try:
            df = parser.parse_ticks(["X", "Y", "yaw", "health", "team_num"], ticks=ticks)
        except Exception:
            df = parser.parse_ticks(["X", "Y", "health", "team_num"], ticks=ticks)
        return df, windows
    except Exception:
        return None, None
