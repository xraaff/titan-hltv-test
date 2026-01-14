from __future__ import annotations

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

    # Prepare deaths grouped by (round, tick) for trade detection
    deaths_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in deaths:
        rn = pick(d, ["round", "round_num", "round_number"])
        if not isinstance(rn, int):
            continue
        deaths_by_round[rn].append(d)
    for rn in deaths_by_round:
        deaths_by_round[rn].sort(key=lambda x: int(pick(x, ["tick", "game_tick", "gameTick"], default=0) or 0))

    traded_rounds: set[tuple[str, int]] = set()
    traded_kill_rounds: set[tuple[str, int]] = set()

    # Trade window: 5 seconds
    trade_tick_window = int(5.0 * tickrate)

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

    for d in deaths:
        rn = pick(d, ["round", "round_num", "round_number"])
        if not isinstance(rn, int):
            continue

        victim = _steamid(d, "victim")
        if victim is not None:
            victim = str(victim)
            if victim not in aggs:
                aggs[victim] = PlayerAgg(name=str(_pname(d, "victim") or ""))
                aggs[victim].rounds = rounds_total
            aggs[victim].deaths += 1
            deaths_in_round.add((victim, rn))

        killer = _steamid(d, "attacker")
        if killer is not None:
            killer = str(killer)
            # ignore world/self kills if we can detect (steamid "0" etc.)
            if killer not in {"0", "None", ""}:
                if killer not in aggs:
                    aggs[killer] = PlayerAgg(name=str(_pname(d, "attacker") or ""))
                    aggs[killer].rounds = rounds_total
                aggs[killer].kills += 1
                kills_in_round.add((killer, rn))
                hs = pick(d, ["headshot", "is_headshot", "isHeadshot"], default=False)
                if bool(hs):
                    aggs[killer].hs_kills += 1

        assister = _steamid(d, "assister")
        if assister is not None:
            assister = str(assister)
            if assister not in {"0", "None", ""}:
                if assister not in aggs:
                    aggs[assister] = PlayerAgg(name=str(_pname(d, "assister") or ""))
                    aggs[assister].rounds = rounds_total
                aggs[assister].assists += 1
                assists_in_round.add((assister, rn))

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
        "players": out_players,
    }

