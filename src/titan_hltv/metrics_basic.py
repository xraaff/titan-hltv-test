from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .demoparser_adapter import norm_team, pick


@dataclass
class PlayerAgg:
    name: str = ""
    rounds: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    hs_kills: int = 0
    damage: int = 0
    survived_rounds: int = 0
    kast_rounds: int = 0
    opening_kills: int = 0
    opening_deaths: int = 0
    multikill_rounds: int = 0
    deaths_ttd_sum_s: float = 0.0
    deaths_ttd_n: int = 0
    mid_round_kills: int = 0
    late_round_kills: int = 0


def _steamid(row: dict[str, Any], role: str) -> str | None:
    if role == "attacker":
        return pick(
            row,
            [
                "attacker_steamid",
                "attackerSteamId",
                "attacker_xuid",
                "attackerXuid",
            ],
        )
    if role == "victim":
        return pick(
            row,
            [
                "user_steamid",
                "victim_steamid",
                "userid_steamid",
                "user_xuid",
                "victim_xuid",
            ],
        )
    if role == "assister":
        return pick(row, ["assister_steamid", "assister_xuid", "assisterXuid"])
    raise ValueError(role)


def _pname(row: dict[str, Any], role: str) -> str | None:
    if role == "attacker":
        return pick(row, ["attacker_name", "attackerName"])
    if role == "victim":
        return pick(row, ["user_name", "victim_name", "userid_name", "userName"])
    if role == "assister":
        return pick(row, ["assister_name", "assisterName"])
    raise ValueError(role)


def compute_basic_metrics(
    *,
    players: list[dict[str, Any]],
    deaths: list[dict[str, Any]],
    hurts: list[dict[str, Any]],
    round_starts: list[dict[str, Any]],
    round_ends: list[dict[str, Any]],
    tickrate: float | None,
) -> dict[str, Any]:
    """
    Compute a first slice of metrics:
    K/D, KPR, ADR, HS%, Survival%, KAST%.

    Notes:
    - Uses KAST definition (kill/assist/survive/traded) with a best-effort trade detection.
    - If required columns are missing, some parts may degrade gracefully.
    """
    tickrate = float(tickrate or 64.0)

    # Determine total rounds from round_end (most reliable "round finished" signal)
    rounds_total = 0
    for r in round_ends:
        rn = pick(r, ["round", "round_num", "round_number"])
        if isinstance(rn, int) and rn > rounds_total:
            rounds_total = rn
    if rounds_total == 0:
        # Fallback: infer from deaths max round index
        for d in deaths:
            rn = pick(d, ["round", "round_num", "round_number"])
            if isinstance(rn, int) and rn > rounds_total:
                rounds_total = rn

    # Build round windows using ticks so we can infer round number for events
    # that don't include a direct "round" column (common in some demoparser2 outputs).
    start_by_round: dict[int, int] = {}
    end_by_round: dict[int, int] = {}

    for r in round_starts:
        rn = pick(r, ["round", "round_num", "round_number"])
        t = pick(r, ["tick", "game_tick", "gameTick"])
        if isinstance(rn, int) and isinstance(t, int):
            start_by_round[rn] = min(start_by_round.get(rn, t), t) if rn in start_by_round else t

    for r in round_ends:
        rn = pick(r, ["round", "round_num", "round_number"])
        t = pick(r, ["tick", "game_tick", "gameTick"])
        if isinstance(rn, int) and isinstance(t, int):
            end_by_round[rn] = max(end_by_round.get(rn, t), t) if rn in end_by_round else t

    # If end ticks look unusable (all missing/zero), drop them to avoid mapping everything to last round.
    if end_by_round and max(end_by_round.values()) <= 0:
        end_by_round = {}

    # Fallback: if we have ends but no starts, start tick is previous end + 1
    if rounds_total and not start_by_round and end_by_round:
        prev_end = 0
        for rn in range(1, rounds_total + 1):
            start_by_round[rn] = prev_end
            prev_end = int(end_by_round.get(rn, prev_end))

    round_windows: list[tuple[int, int, int]] = []
    if rounds_total and start_by_round and end_by_round:
        for rn in range(1, rounds_total + 1):
            st = start_by_round.get(rn)
            en = end_by_round.get(rn)
            if isinstance(st, int) and isinstance(en, int) and en >= st:
                round_windows.append((rn, st, en))

        # Ensure round windows are actually increasing (otherwise mapping is garbage)
        if round_windows:
            ends = [en for (_, _, en) in round_windows]
            if any(ends[i] < ends[i - 1] for i in range(1, len(ends))):
                round_windows = []

    # Precompute for binary search
    _ends = [en for (_, _, en) in round_windows]

    def tick_to_round(tick: int) -> tuple[int | None, int | None]:
        """
        Return (round_number, round_start_tick) for a given global tick.
        """
        if not round_windows:
            return None, None
        i = bisect_right(_ends, tick)
        if i <= 0:
            rn, st, _ = round_windows[0]
            return rn, st
        if i > len(round_windows):
            rn, st, _ = round_windows[-1]
            return rn, st
        rn, st, _ = round_windows[i - 1]
        return rn, st

    aggs: dict[str, PlayerAgg] = defaultdict(PlayerAgg)

    # Seed names from roster
    for p in players:
        sid = pick(p, ["steamid", "steam_id", "steamId", "xuid"])
        if sid is None:
            continue
        sid = str(sid)
        name = pick(p, ["name", "player_name", "playerName"], default="") or ""
        if name and not aggs[sid].name:
            aggs[sid].name = str(name)

    # Round participation: for now, assume roster players played all rounds (typical 5v5).
    # We'll refine later once we parse per-round team rosters.
    for sid in list(aggs.keys()):
        aggs[sid].rounds = rounds_total

    # Damage from player_hurt
    for h in hurts:
        attacker = _steamid(h, "attacker")
        if attacker is None:
            continue
        attacker = str(attacker)
        dmg = pick(h, ["dmg_health", "dmgHealth", "health_damage", "damage"], default=0)
        try:
            dmg_i = int(dmg)
        except Exception:
            dmg_i = 0
        if dmg_i <= 0:
            continue
        # Ignore friendly fire if we can detect it
        ateam = norm_team(pick(h, ["attacker_team", "attackerTeam"]))
        vteam = norm_team(pick(h, ["user_team", "victim_team", "userTeam", "victimTeam"]))
        if ateam is not None and vteam is not None and ateam == vteam:
            continue
        if attacker not in aggs:
            aggs[attacker] = PlayerAgg(name=str(_pname(h, "attacker") or ""))
            aggs[attacker].rounds = rounds_total
        aggs[attacker].damage += dmg_i

    # Prepare deaths grouped by (round, tick) for trade detection / opening duels.
    # We only do trade detection if we can identify teams (team columns present).
    deaths_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in deaths:
        rn = pick(d, ["round", "round_num", "round_number"])
        if not isinstance(rn, int):
            t = pick(d, ["tick", "game_tick", "gameTick"])
            if isinstance(t, int):
                rn, _ = tick_to_round(t)
        if not isinstance(rn, int):
            continue
        deaths_by_round[rn].append(d)

    for rn in deaths_by_round:
        deaths_by_round[rn].sort(key=lambda x: int(pick(x, ["tick", "game_tick", "gameTick"], default=0) or 0))

    traded_rounds: set[tuple[str, int]] = set()
    traded_kill_rounds: set[tuple[str, int]] = set()

    # Trade window: 5 seconds
    trade_tick_window = int(5.0 * tickrate)

    def _has_team_fields(row: dict[str, Any]) -> bool:
        return any(k in row for k in ("user_team", "victim_team", "attacker_team", "userTeam", "victimTeam", "attackerTeam"))

    enable_trade = any(_has_team_fields(d) for d in deaths)

    if enable_trade:
        for rn, ds in deaths_by_round.items():
            # For each death, see if killer is killed within window by victim's teammate
            for i, d in enumerate(ds):
                victim = _steamid(d, "victim")
                killer = _steamid(d, "attacker")
                if victim is None or killer is None:
                    continue
                victim = str(victim)
                killer = str(killer)
                victim_team = norm_team(pick(d, ["user_team", "victim_team", "userTeam", "victimTeam"]))
                killer_team = norm_team(pick(d, ["attacker_team", "attackerTeam"]))
                if victim_team is None or killer_team is None or victim_team == killer_team:
                    continue
                tick = int(pick(d, ["tick", "game_tick", "gameTick"], default=0) or 0)
                # scan forward in same round
                for j in range(i + 1, len(ds)):
                    d2 = ds[j]
                    tick2 = int(pick(d2, ["tick", "game_tick", "gameTick"], default=0) or 0)
                    if tick2 - tick > trade_tick_window:
                        break
                    a2 = _steamid(d2, "attacker")
                    v2 = _steamid(d2, "victim")
                    if a2 is None or v2 is None:
                        continue
                    a2 = str(a2)
                    v2 = str(v2)
                    if v2 != killer:
                        continue
                    a2_team = norm_team(pick(d2, ["attacker_team", "attackerTeam"]))
                    if a2_team is None:
                        continue
                    if a2_team != victim_team:
                        continue
                    traded_rounds.add((victim, rn))
                    traded_kill_rounds.add((a2, rn))
                    break

    # Kills/deaths/assists/HS from player_death
    kills_in_round: set[tuple[str, int]] = set()
    assists_in_round: set[tuple[str, int]] = set()
    deaths_in_round: set[tuple[str, int]] = set()
    kills_per_round: dict[tuple[str, int], int] = defaultdict(int)

    for d in deaths:
        rn = pick(d, ["round", "round_num", "round_number"])
        t = pick(d, ["tick", "game_tick", "gameTick"])
        if not isinstance(rn, int) and isinstance(t, int):
            rn, round_start_tick = tick_to_round(t)
        else:
            round_start_tick = None
        if not isinstance(rn, int):
            # Still count aggregate kills/deaths (for KD) even if we can't map to rounds.
            rn = None

        victim = _steamid(d, "victim")
        if victim is not None:
            victim = str(victim)
            if victim not in aggs:
                aggs[victim] = PlayerAgg(name=str(_pname(d, "victim") or ""))
                aggs[victim].rounds = rounds_total
            aggs[victim].deaths += 1
            if isinstance(rn, int):
                deaths_in_round.add((victim, rn))
            # Time To Death (TTD) if we have round start tick
            if isinstance(t, int) and isinstance(round_start_tick, int):
                aggs[victim].deaths_ttd_sum_s += (t - round_start_tick) / tickrate
                aggs[victim].deaths_ttd_n += 1

        killer = _steamid(d, "attacker")
        if killer is not None:
            killer = str(killer)
            # ignore world/self kills if we can detect (steamid "0" etc.)
            if killer not in {"0", "None", ""}:
                if killer not in aggs:
                    aggs[killer] = PlayerAgg(name=str(_pname(d, "attacker") or ""))
                    aggs[killer].rounds = rounds_total
                aggs[killer].kills += 1
                if isinstance(rn, int):
                    kills_in_round.add((killer, rn))
                    kills_per_round[(killer, rn)] += 1
                hs = pick(d, ["headshot", "is_headshot", "isHeadshot"], default=False)
                if bool(hs):
                    aggs[killer].hs_kills += 1

                # Opening duel: first death event in a round
                if isinstance(rn, int):
                    if deaths_by_round.get(rn) and deaths_by_round[rn][0] is d:
                        aggs[killer].opening_kills += 1

        assister = _steamid(d, "assister")
        if assister is not None:
            assister = str(assister)
            if assister not in {"0", "None", ""}:
                if assister not in aggs:
                    aggs[assister] = PlayerAgg(name=str(_pname(d, "assister") or ""))
                    aggs[assister].rounds = rounds_total
                aggs[assister].assists += 1
                if isinstance(rn, int):
                    assists_in_round.add((assister, rn))

        # Opening deaths
        if isinstance(rn, int) and deaths_by_round.get(rn) and deaths_by_round[rn][0] is d and victim is not None:
            aggs[str(victim)].opening_deaths += 1

        # Mid/late kills buckets (simple time-based split using round progress)
        if isinstance(rn, int) and killer is not None and isinstance(t, int) and round_windows:
            # Find current round window
            win = next((w for w in round_windows if w[0] == rn), None)
            if win is not None:
                _, st, en = win
                dur = max(1, en - st)
                prog = (t - st) / dur
                if prog >= 0.75:
                    aggs[str(killer)].late_round_kills += 1
                elif prog >= 0.25:
                    aggs[str(killer)].mid_round_kills += 1

    # Survival: if player did not die in a round, count as survived.
    if rounds_total > 0:
        for sid, agg in aggs.items():
            if agg.rounds <= 0:
                continue
            died = {rn for (s, rn) in deaths_in_round if s == sid}
            agg.survived_rounds = agg.rounds - len(died)

            # KAST: kill or assist or survive or traded
            kast = 0
            for rn in range(1, agg.rounds + 1):
                if (
                    (sid, rn) in kills_in_round
                    or (sid, rn) in assists_in_round
                    or rn not in died
                    or (sid, rn) in traded_rounds
                ):
                    kast += 1
            agg.kast_rounds = kast

    # Multi-kill rounds
    if rounds_total > 0:
        for (sid, rn), cnt in kills_per_round.items():
            if cnt >= 2:
                aggs[sid].multikill_rounds += 1

    # Build report
    out_players: dict[str, Any] = {}
    for sid, agg in aggs.items():
        r = max(agg.rounds, 0)
        k = agg.kills
        d = agg.deaths
        adr = (agg.damage / r) if r else 0.0
        kpr = (k / r) if r else 0.0
        hs_pct = (agg.hs_kills / k * 100.0) if k else 0.0
        surv_pct = (agg.survived_rounds / r * 100.0) if r else 0.0
        kast_pct = (agg.kast_rounds / r * 100.0) if r else 0.0
        kd = (k / d) if d else float(k)
        opening_win_pct = (
            (agg.opening_kills / (agg.opening_kills + agg.opening_deaths) * 100.0)
            if (agg.opening_kills + agg.opening_deaths) > 0
            else 0.0
        )
        ttd = (agg.deaths_ttd_sum_s / agg.deaths_ttd_n) if agg.deaths_ttd_n else None
        mid_kill_pct = (agg.mid_round_kills / k * 100.0) if k else 0.0
        late_kill_pct = (agg.late_round_kills / k * 100.0) if k else 0.0
        multikill_pct = (agg.multikill_rounds / r * 100.0) if r else 0.0

        out_players[sid] = {
            "name": agg.name,
            "rounds": r,
            "kills": k,
            "deaths": d,
            "assists": agg.assists,
            "damage": agg.damage,
            "hs_kills": agg.hs_kills,
            "survived_rounds": agg.survived_rounds,
            "kast_rounds": agg.kast_rounds,
            "derived": {
                "kd": kd,
                "kpr": kpr,
                "adr": adr,
                "hs_pct": hs_pct,
                "survival_pct": surv_pct,
                "kast_pct": kast_pct,
                "opening_duel_win_pct": opening_win_pct,
                "opening_kills": agg.opening_kills,
                "opening_deaths": agg.opening_deaths,
                "multi_kill_rounds": agg.multikill_rounds,
                "multi_kill_rounds_pct": multikill_pct,
                "mid_round_kill_pct": mid_kill_pct,
                "late_round_kill_pct": late_kill_pct,
                "ttd_avg_s": ttd,
                "traded_pct": (len({rn for (s, rn) in traded_rounds if s == sid}) / r * 100.0)
                if r
                else 0.0,
                "trade_kill_pct": (len({rn for (s, rn) in traded_kill_rounds if s == sid}) / r * 100.0)
                if r
                else 0.0,
            },
        }

    return {
        "rounds_total": rounds_total,
        "round_windows": [
            {"round": rn, "start_tick": st, "end_tick": en} for (rn, st, en) in round_windows
        ]
        if round_windows
        else None,
        "trade_detection": {"enabled": enable_trade},
        "players": out_players,
    }

