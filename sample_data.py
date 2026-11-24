"""Синтетический мини-матч (3 раунда) для демонстрации и тестов."""
import pandas as pd

T, CT = 2, 3
PLAYERS = {
    1: ("Alpha", T), 2: ("Bravo", T), 3: ("Charlie", T), 4: ("Delta", T), 5: ("Echo", T),
    6: ("Foxtrot", CT), 7: ("Golf", CT), 8: ("Hotel", CT), 9: ("India", CT), 10: ("Juliet", CT),
}


def _k(tick, a, v, weapon="ak47", hs=False, **flags):
    an, at = PLAYERS[a]
    vn, vt = PLAYERS[v]
    row = {
        "tick": tick,
        "attacker_name": an, "attacker_steamid": 76561190000 + a, "attacker_team_num": at,
        "user_name": vn, "user_steamid": 76561190000 + v, "user_team_num": vt,
        "weapon": weapon, "headshot": hs,
        "thrusmoke": False, "noscope": False, "attackerblind": False,
        "penetrated": 0, "distance": 12.0,
    }
    row.update(flags)
    return row


def make_sample():
    rows = [
        # Раунд 1: ACE у Alpha (T)
        _k(1000, 1, 6, hs=True), _k(1200, 1, 7), _k(1300, 1, 8, hs=True),
        _k(1500, 1, 9), _k(1700, 1, 10, hs=True),
        # Раунд 2: Juliet (CT) вытаскивает 1v5
        _k(7000, 1, 6), _k(7100, 2, 7), _k(7200, 3, 8), _k(7300, 1, 9),
        _k(7600, 10, 1, "m4a1_silencer", hs=True),
        _k(8000, 10, 2, "m4a1_silencer", thrusmoke=True),
        _k(8400, 10, 3, "m4a1_silencer", penetrated=1),
        _k(9000, 10, 4, "awp", noscope=True, distance=18.5),
        _k(9400, 10, 5, "m4a1_silencer", hs=True),
        # Раунд 3: 3K у Bravo (T)
        _k(13000, 2, 6, hs=True), _k(13100, 9, 3), _k(13200, 2, 7), _k(13500, 2, 8),
    ]
    kills = pd.DataFrame(rows)
    round_ends = pd.DataFrame({"tick": [6000, 12000, 18000], "winner": [T, CT, T]})
    rosters = pd.DataFrame(
        [{"round": r, "steamid": 76561190000 + pid, "name": n, "team_num": t}
         for r in (1, 2, 3) for pid, (n, t) in PLAYERS.items()]
    )
    return kills, round_ends, rosters


def make_sample_replay(kills=None, round_ends=None):
    """Синтетические позиции игроков для демонстрации 2D-реплея."""
    import math

    import numpy as np

    from replay import STEP, build_replay, round_windows, sample_ticks

    if kills is None or round_ends is None:
        kills, round_ends, _ = make_sample()
    windows = round_windows(round_ends)
    # где кто умер: {(round, steamid): tick}
    ends = [w[2] for w in windows]
    death = {}
    for _, r in kills.iterrows():
        rnd = int(np.searchsorted(ends, r["tick"], side="left")) + 1
        death[(rnd, int(r["user_steamid"]))] = int(r["tick"])

    rows = []
    for rnd, start, end, _ in windows:
        dur = end - start
        for t in sample_ticks([(rnd, start, end, None)], STEP):
            for pid, (name, team) in PLAYERS.items():
                sid = 76561190000 + pid
                lane = ((pid - 1) % 5 - 2) * 420
                if team == T:
                    x0, y0, x1, y1 = -1900, -1500 + lane, 300, 200 + lane * 0.6
                else:
                    x0, y0, x1, y1 = 1900, 1500 + lane, -200, -100 + lane * 0.6
                dt = death.get((rnd, sid))
                tt = min(t, dt) if dt is not None else t
                f = min(1.0, max(0.0, (tt - start) / (dur * 0.7)))
                x = x0 + (x1 - x0) * f + 60 * math.sin(tt / 140 + pid)
                y = y0 + (y1 - y0) * f + 60 * math.cos(tt / 170 + pid)
                yaw = math.degrees(math.atan2(y1 - y0, x1 - x0))
                alive = dt is None or t < dt
                rows.append({"tick": t, "steamid": sid, "name": name, "X": x, "Y": y,
                             "yaw": yaw, "health": 100 if alive else 0, "team_num": team})
    return build_replay(pd.DataFrame(rows), windows, kills)
