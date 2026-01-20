from __future__ import annotations

from bisect import bisect_left, bisect_right
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
    opening_kills_sniper: int = 0
    opening_kills_rifle: int = 0
    multikill_rounds: int = 0
    opening_kill_wins: int = 0
    sniper_duel_wins: int = 0
    sniper_duel_losses: int = 0
    rifle_entry_wins: int = 0
    rifle_entry_losses: int = 0
    deaths_ttd_sum_s: float = 0.0
    deaths_ttd_n: int = 0
    mid_round_kills: int = 0
    late_round_kills: int = 0
    support_flash_kills: int = 0
    eco_hero_kills: int = 0
    eco_bully_kills: int = 0
    force_buy_kills: int = 0
    pistol_kills: int = 0
    equip_value_sum: int = 0
    bomb_plants: int = 0
    bomb_plant_wins: int = 0
    bomb_defuses: int = 0
    clutch_attempts: int = 0
    clutch_wins: int = 0
    save_rounds: int = 0
    exit_kills: int = 0


def _as_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, str):
            v = v.strip()
        return int(float(v))
    except Exception:
        return None


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


_SNIPERS = {"awp", "ssg08", "g3sg1", "scar20"}
_RIFLES = {"ak47", "m4a1", "m4a1_silencer", "m4a4", "aug", "sg556", "famas", "galilar"}
_SMGS = {"mac10", "mp9", "mp7", "mp5sd", "p90", "ump45", "bizon"}
_SHOTGUNS = {"nova", "xm1014", "mag7", "sawedoff"}
_PISTOLS_UPGRADED = {"deagle", "revolver", "tec9", "fiveseven", "cz75a", "p250"}
_PISTOLS_STARTER = {"glock", "glock_18", "hkp2000", "usp_s", "usp", "p2000", "dualberettas"}
_ECON_STRENGTH = {
    "sniper": 1.25,
    "rifle_t1": 1.15,
    "rifle_t2": 1.05,
    "smg": 0.95,
    "shotgun": 0.9,
    "pistol_upgraded": 0.8,
    "pistol_starter": 0.65,
    "other": 0.75,
}


def _weapon_cat(w: Any) -> str | None:
    if not w:
        return None
    s = _norm_weapon_name(w)
    if s in _SNIPERS:
        return "sniper"
    if s in _RIFLES:
        return "rifle"
    return None


def _econ_cat(weapon: Any, armor_value: int | None) -> str | None:
    if not weapon:
        return None
    s = _norm_weapon_name(weapon)
    if s in _SNIPERS:
        return "sniper"
    if s in _RIFLES:
        if armor_value is not None and armor_value >= 100:
            return "rifle_t1"
        return "rifle_t2"
    if s in _SMGS:
        return "smg"
    if s in _SHOTGUNS:
        return "shotgun"
    if s in _PISTOLS_UPGRADED:
        return "pistol_upgraded"
    if s in _PISTOLS_STARTER:
        return "pistol_starter"
    return "other"


def _norm_weapon_name(w: Any) -> str:
    s = str(w).strip().lower()
    s = s.replace("-", "_").replace(" ", "_")
    aliases = {
        "m4a1_s": "m4a1_silencer",
        "m4a1_silencer": "m4a1_silencer",
        "usp_s": "usp_s",
        "glock_18": "glock_18",
        "five_seven": "fiveseven",
    }
    return aliases.get(s, s)


def _team_from_num(v: Any) -> str | None:
    try:
        n = int(v)
    except Exception:
        return None
    if n == 2:
        return "T"
    if n == 3:
        return "CT"
    return None


def compute_basic_metrics(
    *,
    players: list[dict[str, Any]],
    deaths: list[dict[str, Any]],
    hurts: list[dict[str, Any]],
    round_starts: list[dict[str, Any]],
    round_ends: list[dict[str, Any]],
    freeze_ends: list[dict[str, Any]],
    tick_snapshots: list[dict[str, Any]],
    round_start_tick_snapshots: list[dict[str, Any]],
    kill_tick_snapshots: list[dict[str, Any]],
    player_teams: list[dict[str, Any]],
    half_announces: list[dict[str, Any]],
    bomb_plants: list[dict[str, Any]],
    bomb_defuses: list[dict[str, Any]],
    bomb_exploded: list[dict[str, Any]],
    match_start_tick: int | None,
    round_end_tick_snapshots: list[dict[str, Any]],
    tickrate: float | None,
    max_rounds: int = 0,
) -> dict[str, Any]:
    """
    Compute a first slice of metrics:
    K/D, KPR, ADR, HS%, Survival%, KAST%.

    Notes:
    - Uses KAST definition (kill/assist/survive/traded) with a best-effort trade detection.
    - If required columns are missing, some parts may degrade gracefully.
    """
    tickrate = float(tickrate or 64.0)

    max_rounds = int(max_rounds or 0)

    # Determine total rounds from round_end (most reliable "round finished" signal)
    rounds_total = 0
    round_winners: dict[int, str] = {}
    round_reasons: dict[int, str] = {}
    def _winner_to_team(v: Any) -> str | None:
        team = _team_from_num(v)
        if team:
            return team
        return norm_team(v)

    for r in round_ends:
        t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
        if match_start_tick is not None and t is not None and t < match_start_tick:
            continue
        rn = _as_int(pick(r, ["round", "round_num", "round_number"]))
        if rn is not None and rn > rounds_total:
            rounds_total = rn
        winner = _winner_to_team(pick(r, ["winner", "winner_team", "winnerTeam"]))
        reason = pick(r, ["reason", "end_reason", "endReason"])
        if rn is not None and reason:
            round_reasons[rn] = str(reason)
        if rn is not None and winner:
            round_winners[rn] = winner
        if rn is not None and not winner and reason:
            rstr = str(reason).lower()
            if "t_killed" in rstr:
                round_winners[rn] = "CT"
            elif "ct_killed" in rstr:
                round_winners[rn] = "T"
            elif "bomb_exploded" in rstr:
                round_winners[rn] = "T"
            elif "bomb_defused" in rstr:
                round_winners[rn] = "CT"

    for r in round_starts:
        rn = _as_int(pick(r, ["round", "round_num", "round_number"]))
        t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
        if match_start_tick is not None and t is not None and t < match_start_tick:
            continue
        if rn is not None and rn > rounds_total:
            rounds_total = rn

    if max_rounds > 0 and rounds_total:
        rounds_total = min(rounds_total, max_rounds)
    if rounds_total == 0:
        # Fallback: infer from deaths max round index
        for d in deaths:
            rn = _as_int(pick(d, ["round", "round_num", "round_number"]))
            if rn is not None and rn > rounds_total:
                rounds_total = rn

    # Build round windows using ticks so we can infer round number for events
    # that don't include a direct "round" column (common in some demoparser2 outputs).
    start_by_round: dict[int, int] = {}
    end_by_round: dict[int, int] = {}
    live_start_by_round: dict[int, int] = {}

    for r in round_starts:
        rn = _as_int(pick(r, ["round", "round_num", "round_number"]))
        t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
        if rn is not None and t is not None:
            start_by_round[rn] = min(start_by_round.get(rn, t), t) if rn in start_by_round else t

    for r in round_ends:
        rn = _as_int(pick(r, ["round", "round_num", "round_number"]))
        t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
        if rn is not None and t is not None:
            end_by_round[rn] = max(end_by_round.get(rn, t), t) if rn in end_by_round else t

    # Use max death tick per round as a safer fallback for end ticks.
    kill_end_by_round: dict[int, int] = {}
    for d in deaths:
        rn = _as_int(pick(d, ["round", "round_num", "round_number"]))
        t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
        if rn is not None and t is not None:
            kill_end_by_round[rn] = max(kill_end_by_round.get(rn, t), t) if rn in kill_end_by_round else t

    # Freeze end is a better "live round start" for timing (TTD, mid/late kills).
    for fe in freeze_ends:
        rn = _as_int(pick(fe, ["round", "round_num", "round_number"]))
        t = _as_int(pick(fe, ["tick", "game_tick", "gameTick"]))
        if rn is not None and t is not None:
            live_start_by_round[rn] = min(live_start_by_round.get(rn, t), t) if rn in live_start_by_round else t

    # If end ticks look unusable (all missing/zero), drop them to avoid mapping everything to last round.
    if end_by_round and max(end_by_round.values()) <= 0:
        end_by_round = {}

    # If rounds_total wasn't derived but we have round_start, infer it.
    if rounds_total == 0 and start_by_round:
        rounds_total = max(start_by_round.keys())

    # Hard cap rounds if requested (used for faster iteration).
    if max_rounds > 0 and rounds_total > 0:
        rounds_total = min(rounds_total, max_rounds)

    def _max_tick(rows: list[dict[str, Any]]) -> int:
        mt = 0
        for r in rows:
            t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
            if t is not None and t > mt:
                mt = t
        return mt

    max_tick = max(
        _max_tick(deaths),
        _max_tick(hurts),
        _max_tick(round_starts),
        _max_tick(round_ends),
    )

    # If we're limiting to first N rounds and we know the next round start,
    # clamp max_tick to just before that next start.
    if max_rounds > 0 and start_by_round:
        next_start = start_by_round.get(max_rounds + 1)
        if isinstance(next_start, int) and next_start > 0:
            max_tick = min(max_tick, next_start - 1)

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

    # Preferred strategy (more robust for CS2 demoparser2):
    # build round windows from round_start ticks (round_end.tick is often wrong/0 for early rounds).
    #
    # end_tick = next_round_start_tick - 1
    # last end_tick = max_tick across events
    #
    # If round_end ticks were usable, round_windows would have full coverage already; if it doesn't,
    # we fall back to this start-based windowing.
    if rounds_total and start_by_round and (not round_windows or len(round_windows) < rounds_total):
        starts: list[tuple[int, int]] = []
        for rn in range(1, rounds_total + 1):
            st = start_by_round.get(rn)
            if isinstance(st, int):
                starts.append((rn, st))
        starts.sort(key=lambda x: x[0])

        round_windows = []
        for i, (rn, st) in enumerate(starts):
            if i + 1 < len(starts):
                en = int(starts[i + 1][1]) - 1
            else:
                en = int(max_tick)
            if en < st:
                en = st
            round_windows.append((rn, st, en))

        # If we couldn't build as many windows as requested rounds_total, shrink rounds_total
        # to match what we can reliably segment.
        if round_windows:
            rounds_total = min(rounds_total, len(round_windows))

    # If we still can't build windows, we can't do tick->round inference.

    # Precompute for binary search
    _ends = [en for (_, _, en) in round_windows]

    # Live round windows (freeze_end -> round_end) for timing metrics.
    live_round_windows: list[tuple[int, int, int]] = []
    if rounds_total:
        live_starts: list[tuple[int, int]] = []
        for rn in range(1, rounds_total + 1):
            st = live_start_by_round.get(rn) or start_by_round.get(rn)
            if isinstance(st, int):
                live_starts.append((rn, st))
        live_starts.sort(key=lambda x: x[0])

        for i, (rn, st) in enumerate(live_starts):
            if i + 1 < len(live_starts):
                next_start = int(live_starts[i + 1][1])
                default_en = next_start - 1
            else:
                default_en = int(max_tick)

            en = end_by_round.get(rn)
            if not isinstance(en, int) or en < st or en > default_en:
                en = default_en

            if en < st:
                en = st
            live_round_windows.append((rn, st, en))

    live_window_by_round: dict[int, tuple[int, int]] = {
        rn: (st, en) for rn, st, en in live_round_windows
    }

    def tick_to_round(tick: int) -> tuple[int | None, int | None]:
        """
        Return (round_number, round_start_tick) for a given global tick.
        """
        if not round_windows:
            return None, None
        i = bisect_left(_ends, tick)
        if i < 0:
            rn, st, _ = round_windows[0]
            return rn, st
        if i >= len(round_windows):
            rn, st, _ = round_windows[-1]
            return rn, st
        rn, st, _ = round_windows[i]
        return rn, st

    if round_end_tick_snapshots:
        for row in round_end_tick_snapshots:
            t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
            if t is None:
                continue
            if match_start_tick is not None and t < match_start_tick:
                continue
            rn, _ = tick_to_round(t)
            if rn is None:
                continue
            if rn in round_winners:
                continue
            status = pick(row, ["round_win_status", "round_win_reason"])
            winner = _winner_to_team(status)
            if winner:
                round_winners[rn] = winner

    match_score_from_ticks = None
    match_score_official = None
    official_rounds_total = None
    score_reset_tick = None
    team_score_official = None
    team_a_ids: set[str] | None = None
    team_b_ids: set[str] | None = None
    official_timeline: list[tuple[int, int, int]] = []
    if round_end_tick_snapshots:
        score_ticks: dict[int, dict[str, int | None]] = {}
        for row in round_end_tick_snapshots:
            t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
            if t is None:
                continue
            team_num = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
            if team_num not in {"T", "CT"}:
                continue
            score_total = _as_int(pick(row, ["team_rounds_total"]))
            if score_total is None:
                first = _as_int(pick(row, ["team_score_first_half"]))
                second = _as_int(pick(row, ["team_score_second_half"]))
                if first is not None or second is not None:
                    score_total = int((first or 0) + (second or 0))
            if score_total is None:
                continue
            score_ticks.setdefault(t, {"T": None, "CT": None})[team_num] = score_total

        timeline: list[tuple[int, int, int]] = []
        for t in sorted(score_ticks.keys()):
            scores = score_ticks[t]
            if scores["T"] is None or scores["CT"] is None:
                continue
            timeline.append((t, int(scores["T"]), int(scores["CT"])))

        if timeline:
            max_scores = {"T": 0, "CT": 0}
            for _, t_score, ct_score in timeline:
                max_scores["T"] = max(max_scores["T"], t_score)
                max_scores["CT"] = max(max_scores["CT"], ct_score)
            match_score_from_ticks = {"T": max_scores["T"], "CT": max_scores["CT"]}

            prev_t = prev_ct = None
            for t, t_score, ct_score in timeline:
                if prev_t is not None and (t_score < prev_t or ct_score < prev_ct):
                    if t_score <= 1 and ct_score <= 1 and (t_score + ct_score) <= 2:
                        score_reset_tick = t
                prev_t, prev_ct = t_score, ct_score

            official_timeline = [row for row in timeline if score_reset_tick is None or row[0] >= score_reset_tick]
            if official_timeline:
                last_t, last_ct = official_timeline[-1][1], official_timeline[-1][2]
                match_score_official = {"T": last_t, "CT": last_ct}
                official_rounds_total = last_t + last_ct


    official_start_tick = match_start_tick
    if score_reset_tick is not None:
        official_start_tick = max(official_start_tick or score_reset_tick, score_reset_tick)

    rounds_in_scope: list[int] = []
    if round_windows:
        for rn, st, _ in round_windows:
            if official_start_tick is None or st >= official_start_tick:
                rounds_in_scope.append(rn)
    if not rounds_in_scope:
        rounds_in_scope = list(range(1, rounds_total + 1))
    rounds_in_scope_set = set(rounds_in_scope)
    rounds_in_scope_total = len(rounds_in_scope)

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

    # Round participation: for now, assume roster players played all rounds in scope.
    # We'll refine later once we parse per-round team rosters.
    for sid in list(aggs.keys()):
        aggs[sid].rounds = rounds_in_scope_total

    # Apply max-rounds cutoff (tick-based) if possible
    cutoff_tick: int | None = None
    if max_rounds > 0 and round_windows:
        # round_windows is ordered by round number (1..N)
        cutoff_tick = round_windows[-1][2]

    def _cut(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if cutoff_tick is None and official_start_tick is None:
            return rows
        out: list[dict[str, Any]] = []
        for r in rows:
            t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
            if t is None:
                continue
            if cutoff_tick is not None and t > cutoff_tick:
                continue
            if official_start_tick is not None and t < official_start_tick:
                continue
            out.append(r)
        return out

    deaths = _cut(deaths)
    hurts = _cut(hurts)


    # Economy reconstruction from round_freeze_end + tick snapshots
    def _classify_buy(value: int) -> str:
        # Thresholds are per-team total equipment value (5 players).
        if value < 5000:
            return "ECO"
        if value < 22000:
            return "FORCE"
        return "BUY"

    econ_by_round: dict[int, dict[str, Any]] = {}
    if freeze_ends and tick_snapshots:
        # index snapshots by tick
        snap_by_tick: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in tick_snapshots:
            t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
            if t is None:
                continue
            if cutoff_tick is not None and t > cutoff_tick:
                continue
            snap_by_tick[t].append(row)

        for fe in freeze_ends:
            t = _as_int(pick(fe, ["tick", "game_tick", "gameTick"]))
            if t is None:
                continue
            if cutoff_tick is not None and t > cutoff_tick:
                continue
            rn, _ = tick_to_round(t)
            if rn is None:
                continue
            if max_rounds > 0 and rn > rounds_total:
                continue
            rows = snap_by_tick.get(t) or []
            t_val = 0
            ct_val = 0
            for r in rows:
                team_num = _as_int(pick(r, ["team_num", "team", "teamnum", "teamNum"]))
                eq = _as_int(pick(r, ["equipment_value_this_round", "equipment_value", "equip_value"]))
                if team_num is None or eq is None:
                    continue
                if team_num == 2:
                    t_val += eq
                elif team_num == 3:
                    ct_val += eq
            if rn not in econ_by_round:
                econ_by_round[rn] = {
                    "round": rn,
                    "freeze_tick": t,
                    "t_value": t_val,
                    "ct_value": ct_val,
                    "t_buy": _classify_buy(t_val),
                    "ct_buy": _classify_buy(ct_val),
                }

    # Player investment (equipment value) from snapshots
    if tick_snapshots:
        for row in tick_snapshots:
            sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
            t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
            eq = _as_int(pick(row, ["equipment_value_this_round", "equipment_value", "equip_value"]))
            if sid is None or t is None or eq is None:
                continue
            if cutoff_tick is not None and t > cutoff_tick:
                continue
            rn, _ = tick_to_round(t)
            if rn is None or (max_rounds > 0 and rn > rounds_total):
                continue
            sid = str(sid)
            if sid not in aggs:
                aggs[sid] = PlayerAgg(name=str(pick(row, ["player_name", "name"], default="") or ""))
            aggs[sid].rounds = rounds_in_scope_total
            aggs[sid].equip_value_sum += eq

    # Build team lookup by tick for each player (from snapshots + player_team events)
    team_events_by_player: dict[str, list[tuple[int, str]]] = defaultdict(list)

    for row in tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        team = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
        if sid is None or t is None or team is None:
            continue
        team_events_by_player[str(sid)].append((t, team))

    for row in player_teams:
        sid = pick(row, ["user_steamid", "steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        team = _team_from_num(pick(row, ["team"]))
        if sid is None or t is None or team is None:
            continue
        team_events_by_player[str(sid)].append((t, team))

    for sid in team_events_by_player:
        team_events_by_player[sid].sort(key=lambda x: x[0])

    def team_at_tick(sid: str | None, tick: int | None) -> str | None:
        if sid is None or tick is None:
            return None
        events = team_events_by_player.get(str(sid))
        if not events:
            return None
        # binary search for last tick <= target
        lo, hi = 0, len(events) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            t, team = events[mid]
            if t <= tick:
                best = team
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    # Per-round roster from freeze_end snapshots (team_num + steamid)
    round_rosters: dict[int, dict[str, str]] = defaultdict(dict)
    for row in tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        team = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
        if sid is None or t is None or team is None:
            continue
        if cutoff_tick is not None and t > cutoff_tick:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        round_rosters[rn][str(sid)] = team

    # Map official scoreboard (side) into team scores using per-round rosters.
    if official_timeline and round_rosters:
        first_official_round, _ = tick_to_round(official_timeline[0][0])
        if first_official_round:
            roster = round_rosters.get(first_official_round)
            if roster:
                team_a_ids = {sid for sid, team in roster.items() if team == "T"}
                team_b_ids = {sid for sid, team in roster.items() if team == "CT"}

        if team_a_ids is not None and team_b_ids is not None:
            team_a_score = 0
            team_b_score = 0
            prev_t = prev_ct = None
            for tick, t_score, ct_score in official_timeline:
                if prev_t is None:
                    # Account for any non-zero score at first official snapshot.
                    if t_score or ct_score:
                        rn, _ = tick_to_round(tick)
                        roster = round_rosters.get(rn, {}) if rn else {}
                        roster_t = {sid for sid, team in roster.items() if team == "T"}
                        roster_ct = {sid for sid, team in roster.items() if team == "CT"}
                        a_on_t = len(team_a_ids & roster_t)
                        a_on_ct = len(team_a_ids & roster_ct)
                        team_a_is_t = a_on_t >= a_on_ct
                        if team_a_is_t:
                            team_a_score += t_score
                            team_b_score += ct_score
                        else:
                            team_a_score += ct_score
                            team_b_score += t_score
                    prev_t, prev_ct = t_score, ct_score
                    continue
                if t_score == prev_t and ct_score == prev_ct:
                    continue
                winner_side = "T" if t_score > prev_t else "CT"
                prev_t, prev_ct = t_score, ct_score

                rn, _ = tick_to_round(tick)
                roster = round_rosters.get(rn, {}) if rn else {}
                roster_t = {sid for sid, team in roster.items() if team == "T"}
                roster_ct = {sid for sid, team in roster.items() if team == "CT"}
                a_on_t = len(team_a_ids & roster_t)
                a_on_ct = len(team_a_ids & roster_ct)
                team_a_is_t = a_on_t >= a_on_ct
                if winner_side == ("T" if team_a_is_t else "CT"):
                    team_a_score += 1
                else:
                    team_b_score += 1

            team_score_official = {
                "team_a_rounds": team_a_score,
                "team_b_rounds": team_b_score,
                "team_a_start_side": "T",
                "score_reset_tick": score_reset_tick,
            }


    # Damage from player_hurt
    hurts_by_round_victim: dict[int, dict[str, list[tuple[int, str, int]]]] = defaultdict(lambda: defaultdict(list))
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
        if ateam is None or vteam is None:
            t = _as_int(pick(h, ["tick", "game_tick", "gameTick"]))
            if ateam is None:
                ateam = team_at_tick(attacker, t)
            if vteam is None:
                vteam = team_at_tick(_steamid(h, "victim"), t)
        if ateam is not None and vteam is not None and ateam == vteam:
            continue
        rn = _as_int(pick(h, ["round", "round_num", "round_number"]))
        t = _as_int(pick(h, ["tick", "game_tick", "gameTick"]))
        if t is not None:
            rn2, _ = tick_to_round(t)
            if rn2 is not None:
                rn = rn2
        victim = _steamid(h, "victim")
        if rn is not None and victim is not None:
            hurts_by_round_victim[rn][str(victim)].append(
                (int(_as_int(pick(h, ["tick", "game_tick", "gameTick"])) or 0), attacker, dmg_i)
            )
        if attacker not in aggs:
            aggs[attacker] = PlayerAgg(name=str(_pname(h, "attacker") or ""))
            aggs[attacker].rounds = rounds_in_scope_total
        aggs[attacker].damage += dmg_i

    # Prepare deaths grouped by (round, tick) for trade detection / opening duels.
    # We only do trade detection if we can identify teams (team columns present).
    deaths_by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in deaths:
        rn = _as_int(pick(d, ["round", "round_num", "round_number"]))
        t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
        if t is not None:
            rn2, _ = tick_to_round(t)
            if rn2 is not None:
                rn = rn2
        if rn is None or rn not in rounds_in_scope_set:
            continue
        deaths_by_round[rn].append(d)

    for rn in deaths_by_round:
        deaths_by_round[rn].sort(key=lambda x: int(_as_int(pick(x, ["tick", "game_tick", "gameTick"])) or 0))

    traded_rounds: set[tuple[str, int]] = set()
    traded_kill_rounds: set[tuple[str, int]] = set()
    traded_death_ids: set[int] = set()
    trade_kill_ids: set[int] = set()
    trade_kill_for: dict[int, str] = {}

    # Trade window: 5 seconds
    trade_tick_window = int(5.0 * tickrate)

    def _has_team_fields(row: dict[str, Any]) -> bool:
        return any(k in row for k in ("user_team", "victim_team", "attacker_team", "userTeam", "victimTeam", "attackerTeam"))

    enable_trade = any(_has_team_fields(d) for d in deaths) or bool(team_events_by_player)

    # Pistol rounds (round 1 + round after last round of first half)
    pistol_rounds: set[int] = {1}
    if half_announces:
        half_tick = _as_int(pick(half_announces[0], ["tick", "game_tick", "gameTick"]))
        if half_tick is not None:
            half_round, _ = tick_to_round(half_tick)
            if isinstance(half_round, int):
                pistol_rounds.add(half_round + 1)
    pistol_rounds = {r for r in pistol_rounds if isinstance(r, int) and r >= 1 and r <= rounds_total}
    pistol_rounds_in_scope = {r for r in pistol_rounds if r in rounds_in_scope_set}

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
                tick = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
                victim_team = norm_team(pick(d, ["user_team", "victim_team", "userTeam", "victimTeam"]))
                killer_team = norm_team(pick(d, ["attacker_team", "attackerTeam"]))
                if victim_team is None:
                    victim_team = team_at_tick(victim, tick)
                if killer_team is None:
                    killer_team = team_at_tick(killer, tick)
                if victim_team is None or killer_team is None or victim_team == killer_team:
                    continue
                tick = int(tick or 0)
                # scan forward in same round
                for j in range(i + 1, len(ds)):
                    d2 = ds[j]
                    tick2 = int(_as_int(pick(d2, ["tick", "game_tick", "gameTick"])) or 0)
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
                        a2_team = team_at_tick(a2, tick2)
                    if a2_team is None:
                        continue
                    if a2_team != victim_team:
                        continue
                    traded_rounds.add((victim, rn))
                    traded_kill_rounds.add((a2, rn))
                    traded_death_ids.add(id(d))
                    trade_kill_ids.add(id(d2))
                    trade_kill_for[id(d2)] = victim
                    break

    # Round outcome ticks for exit/save/clutch logic
    exploded_tick_by_round: dict[int, int] = {}
    defused_tick_by_round: dict[int, int] = {}
    round_end_tick_by_round: dict[int, int] = {}

    for b in bomb_exploded:
        t = _as_int(pick(b, ["tick", "game_tick", "gameTick"]))
        if t is None:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        exploded_tick_by_round[rn] = max(exploded_tick_by_round.get(rn, 0), t)

    for b in bomb_defuses:
        t = _as_int(pick(b, ["tick", "game_tick", "gameTick"]))
        if t is None:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        defused_tick_by_round[rn] = max(defused_tick_by_round.get(rn, 0), t)

    for r in round_ends:
        rn = _as_int(pick(r, ["round", "round_num", "round_number"]))
        t = _as_int(pick(r, ["tick", "game_tick", "gameTick"]))
        if rn is None or t is None or t <= 0:
            continue
        if max_rounds > 0 and rn > rounds_total:
            continue
        round_end_tick_by_round[rn] = max(round_end_tick_by_round.get(rn, 0), t)

    for rn in range(1, rounds_total + 1):
        if rn in round_winners:
            continue
        if rn in exploded_tick_by_round:
            round_winners[rn] = "T"
        elif rn in defused_tick_by_round:
            round_winners[rn] = "CT"

    def team_for_round(sid: str | None, rn: int | None, tick: int | None) -> str | None:
        if sid is None or rn is None:
            return None
        roster = round_rosters.get(rn)
        if roster and sid in roster:
            return roster[sid]
        return team_at_tick(sid, tick)

    clutch_attempted: set[tuple[str, int]] = set()
    kill_events: list[dict[str, Any]] = []
    equip_by_round_player: dict[tuple[int, str], int] = {}
    weapon_by_round_player: dict[tuple[int, str], str] = {}
    tick_state_by_player: dict[tuple[int, str], dict[str, Any]] = {}
    round_swing_by_player: dict[str, float] = defaultdict(float)

    for row in tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        if cutoff_tick is not None and t > cutoff_tick:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        equip = _as_int(
            pick(
                row,
                [
                    "round_start_equip_value",
                    "current_equip_value",
                    "equipment_value_this_round",
                ],
            )
        )
        if equip is not None:
            equip_by_round_player[(rn, sid)] = equip
        wname = pick(row, ["active_weapon_name", "weapon_name"])
        if wname:
            weapon_by_round_player[(rn, sid)] = str(wname)

    economy_players: list[dict[str, Any]] = []
    economy_coverage_by_round: list[dict[str, Any]] = []
    econ_players_by_round: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        if cutoff_tick is not None and t > cutoff_tick:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        team = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
        armor = _as_int(pick(row, ["armor_value", "armor"]))
        weapon = pick(row, ["active_weapon_name", "weapon_name"])
        econ_cat = _econ_cat(weapon, armor)
        entry = {
            "round": rn,
            "steamid": sid,
            "name": pick(row, ["player_name", "name"]),
            "team": team,
            "equip_value": _as_int(
                pick(
                    row,
                    [
                        "round_start_equip_value",
                        "current_equip_value",
                        "equipment_value_this_round",
                    ],
                )
            ),
            "armor_value": armor,
            "has_helmet": pick(row, ["has_helmet"]),
            "has_defuser": pick(row, ["has_defuser"]),
            "active_weapon": weapon,
            "econ_cat": econ_cat,
            "last_place_name": pick(row, ["last_place_name"]),
            "source": "freeze_end",
        }
        econ_players_by_round[rn][sid] = entry
        economy_players.append(entry)

    for row in round_start_tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        if cutoff_tick is not None and t > cutoff_tick:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        if sid in econ_players_by_round.get(rn, {}):
            continue
        team = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
        armor = _as_int(pick(row, ["armor_value", "armor"]))
        weapon = pick(row, ["active_weapon_name", "weapon_name"])
        econ_cat = _econ_cat(weapon, armor)
        entry = {
            "round": rn,
            "steamid": sid,
            "name": pick(row, ["player_name", "name"]),
            "team": team,
            "equip_value": _as_int(
                pick(
                    row,
                    [
                        "round_start_equip_value",
                        "current_equip_value",
                        "equipment_value_this_round",
                    ],
                )
            ),
            "armor_value": armor,
            "has_helmet": pick(row, ["has_helmet"]),
            "has_defuser": pick(row, ["has_defuser"]),
            "active_weapon": weapon,
            "econ_cat": econ_cat,
            "last_place_name": pick(row, ["last_place_name"]),
            "source": "round_start",
        }
        econ_players_by_round[rn][sid] = entry
        economy_players.append(entry)

    for row in kill_tick_snapshots:
        sid = pick(row, ["steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(row, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        if cutoff_tick is not None and t > cutoff_tick:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        tick_state_by_player[(t, sid)] = row
        if sid in econ_players_by_round.get(rn, {}):
            continue
        team = _team_from_num(pick(row, ["team_num", "team", "teamnum", "teamNum"]))
        armor = _as_int(pick(row, ["armor_value", "armor"]))
        weapon = pick(row, ["active_weapon_name", "weapon_name"])
        econ_cat = _econ_cat(weapon, armor)
        entry = {
            "round": rn,
            "steamid": sid,
            "name": pick(row, ["player_name", "name"]),
            "team": team,
            "equip_value": _as_int(
                pick(
                    row,
                    [
                        "round_start_equip_value",
                        "current_equip_value",
                        "equipment_value_this_round",
                    ],
                )
            ),
            "armor_value": armor,
            "has_helmet": pick(row, ["has_helmet"]),
            "has_defuser": pick(row, ["has_defuser"]),
            "active_weapon": weapon,
            "econ_cat": econ_cat,
            "last_place_name": pick(row, ["last_place_name"]),
            "source": "kill_tick",
        }
        econ_players_by_round[rn][sid] = entry
        economy_players.append(entry)

    for rn in range(1, rounds_total + 1):
        roster = round_rosters.get(rn, {})
        expected = len(roster) if roster else None
        players = econ_players_by_round.get(rn, {})
        seen = len(players)
        missing = []
        if roster:
            missing = [sid for sid in roster.keys() if sid not in players]
        if expected is None:
            expected = max(seen, 10) if seen else 10
        economy_coverage_by_round.append(
            {
                "round": rn,
                "players_expected": expected,
                "players_seen": seen,
                "missing_count": max(0, expected - seen),
                "missing_steamids": missing if missing else None,
            }
        )

    total_expected = sum(c["players_expected"] for c in economy_coverage_by_round)
    total_seen = sum(c["players_seen"] for c in economy_coverage_by_round)
    coverage_summary = {
        "rounds_total": rounds_total,
        "players_expected_total": total_expected,
        "players_seen_total": total_seen,
        "coverage_pct": (total_seen / total_expected * 100.0) if total_expected else 0.0,
    }

    econ_matchup_counts: dict[tuple[str, str], int] = defaultdict(int)
    for d in deaths:
        t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
        if t is None:
            continue
        killer = _steamid(d, "attacker")
        victim = _steamid(d, "victim")
        if killer is None or victim is None:
            continue
        k_state = tick_state_by_player.get((t, str(killer)))
        v_state = tick_state_by_player.get((t, str(victim)))
        if not k_state or not v_state:
            continue
        k_armor = _as_int(pick(k_state, ["armor_value", "armor"]))
        v_armor = _as_int(pick(v_state, ["armor_value", "armor"]))
        k_weapon = pick(k_state, ["active_weapon_name", "weapon_name"])
        v_weapon = pick(v_state, ["active_weapon_name", "weapon_name"])
        k_cat = _econ_cat(k_weapon, k_armor)
        v_cat = _econ_cat(v_weapon, v_armor)
        if k_cat and v_cat:
            econ_matchup_counts[(k_cat, v_cat)] += 1

    econ_matchup_win_rate: dict[tuple[str, str], float] = {}
    for (a, b), wins in econ_matchup_counts.items():
        losses = econ_matchup_counts.get((b, a), 0)
        total = wins + losses
        if total > 0:
            econ_matchup_win_rate[(a, b)] = wins / total

    def _win_prob(team: str | None, t_alive: int, ct_alive: int, equip_adv: float | None) -> float:
        if team not in {"T", "CT"}:
            return 0.5
        if t_alive <= 0 and ct_alive <= 0:
            return 0.5
        t_weight = float(t_alive)
        ct_weight = float(ct_alive)
        if equip_adv is not None:
            equip_adv = max(0.7, min(1.3, equip_adv))
            if team == "T":
                t_weight *= equip_adv
                ct_weight *= 1.0 / equip_adv
            else:
                ct_weight *= equip_adv
                t_weight *= 1.0 / equip_adv
        total = t_weight + ct_weight
        if total <= 0:
            return 0.5
        return (t_weight / total) if team == "T" else (ct_weight / total)

    for rn, ds in deaths_by_round.items():
        if max_rounds > 0 and rn > rounds_total:
            continue
        alive: dict[str, set[str]] = {"T": set(), "CT": set()}
        roster = round_rosters.get(rn, {})
        for sid, team in roster.items():
            alive[team].add(sid)

        if not roster:
            round_start_tick = None
            for rnr, st, _ in round_windows:
                if rnr == rn:
                    round_start_tick = st
                    break
            if round_start_tick is not None:
                for sid in team_events_by_player.keys():
                    team = team_at_tick(sid, round_start_tick)
                    if team:
                        alive[team].add(sid)

        # Ensure we include any seen players (victim/killer) as alive at start.
        for d in ds:
            t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
            for role in ("victim", "attacker"):
                sid = _steamid(d, role)
                if sid is None:
                    continue
                sid = str(sid)
                team = team_for_round(sid, rn, t)
                if team:
                    alive[team].add(sid)

        # Track clutch attempts as teams drop to 1 alive vs >=2 + build kill events
        for d in ds:
            t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
            victim = _steamid(d, "victim")
            killer = _steamid(d, "attacker")
            victim = str(victim) if victim is not None else None
            killer = str(killer) if killer is not None else None
            vteam = team_for_round(victim, rn, t) if victim else None
            kteam = team_for_round(killer, rn, t) if killer else None
            alive_t_before = len(alive["T"])
            alive_ct_before = len(alive["CT"])

            if victim is not None:
                if vteam and victim in alive[vteam]:
                    alive[vteam].remove(victim)

            alive_t_after = len(alive["T"])
            alive_ct_after = len(alive["CT"])
            is_opening = bool(ds and ds[0] is d)
            time_into_round_s = None
            if t is not None:
                win = live_window_by_round.get(rn)
                if win is None:
                    win = next((w for w in round_windows if w[0] == rn), None)
                    if win is not None:
                        win = (win[1], win[2])
                if win is not None:
                    st, en = win
                    if isinstance(st, int) and t >= st:
                        ttd_ticks = t - st
                        max_round_ticks = int(115 * tickrate)
                        if max_round_ticks > 0:
                            ttd_ticks = min(ttd_ticks, max_round_ticks)
                        time_into_round_s = ttd_ticks / tickrate

            k_state = tick_state_by_player.get((t, killer)) if (t is not None and killer) else None
            v_state = tick_state_by_player.get((t, victim)) if (t is not None and victim) else None
            k_equip = None
            v_equip = None
            if k_state:
                k_equip = _as_int(
                    pick(
                        k_state,
                        ["current_equip_value", "round_start_equip_value", "equipment_value_this_round"],
                    )
                )
            if v_state:
                v_equip = _as_int(
                    pick(
                        v_state,
                        ["current_equip_value", "round_start_equip_value", "equipment_value_this_round"],
                    )
                )
            k_weapon = pick(k_state, ["active_weapon_name", "weapon_name"]) if k_state else None
            v_weapon = pick(v_state, ["active_weapon_name", "weapon_name"]) if v_state else None
            k_place = pick(k_state, ["last_place_name"]) if k_state else None
            v_place = pick(v_state, ["last_place_name"]) if v_state else None
            k_armor = _as_int(pick(k_state, ["armor_value"])) if k_state else None
            v_armor = _as_int(pick(v_state, ["armor_value"])) if v_state else None
            k_econ_cat = _econ_cat(k_weapon, k_armor)
            v_econ_cat = _econ_cat(v_weapon, v_armor)
            k_pos = None
            v_pos = None
            if k_state is not None and all(k in k_state for k in ("X", "Y", "Z")):
                k_pos = {"x": k_state.get("X"), "y": k_state.get("Y"), "z": k_state.get("Z")}
            if v_state is not None and all(k in v_state for k in ("X", "Y", "Z")):
                v_pos = {"x": v_state.get("X"), "y": v_state.get("Y"), "z": v_state.get("Z")}

            equip_adv = None
            if k_equip is not None and v_equip is not None:
                equip_adv = (float(k_equip) + 1.0) / (float(v_equip) + 1.0)
            if equip_adv is None and k_econ_cat and v_econ_cat:
                win_rate = econ_matchup_win_rate.get((k_econ_cat, v_econ_cat))
                if win_rate is None:
                    k_strength = _ECON_STRENGTH.get(k_econ_cat, _ECON_STRENGTH["other"])
                    v_strength = _ECON_STRENGTH.get(v_econ_cat, _ECON_STRENGTH["other"])
                    win_rate = k_strength / (k_strength + v_strength)
                equip_adv = 1.0 + (win_rate - 0.5) * 1.2

            damage_breakdown: list[dict[str, Any]] = []
            damage_total = 0
            damage_share_killer = None
            if victim is not None and rn is not None:
                entries = hurts_by_round_victim.get(rn, {}).get(str(victim), [])
                if entries:
                    round_start_tick = next((st for (rnr, st, _) in round_windows if rnr == rn), None)
                    valid_entries = []
                    for ht, hattacker, hdmg in entries:
                        if t is not None and ht > t:
                            continue
                        if isinstance(round_start_tick, int) and ht < round_start_tick:
                            continue
                        valid_entries.append((ht, hattacker, hdmg))
                    if valid_entries:
                        dmg_by_attacker: dict[str, int] = defaultdict(int)
                        for _, hattacker, hdmg in valid_entries:
                            dmg_by_attacker[hattacker] += int(hdmg)
                        damage_total = sum(dmg_by_attacker.values())
                        if damage_total > 0:
                            for hattacker, hdmg in sorted(dmg_by_attacker.items(), key=lambda x: x[1], reverse=True):
                                share = hdmg / damage_total
                                damage_breakdown.append(
                                    {"attacker_steamid": hattacker, "damage": hdmg, "share": share}
                                )
                            if killer and killer in dmg_by_attacker:
                                damage_share_killer = dmg_by_attacker[killer] / damage_total

            wp_before = _win_prob(kteam, alive_t_before, alive_ct_before, equip_adv)
            wp_after = _win_prob(kteam, alive_t_after, alive_ct_after, equip_adv)
            swing = wp_after - wp_before

            assister = _steamid(d, "assister")
            assisted_flash = pick(d, ["assistedflash", "assistedFlash"], default=False)
            trade_for = trade_kill_for.get(id(d))
            credits: dict[str, float] = defaultdict(float)
            if damage_breakdown:
                for item in damage_breakdown:
                    credits[str(item["attacker_steamid"])] += float(item["share"])
            elif killer:
                credits[killer] = 1.0

            credit_assister = 0.0
            credit_trade_for = 0.0
            credit_killer = credits.get(killer, 0.0) if killer else 0.0
            if assisted_flash and assister and killer:
                flash_credit = min(0.2, credit_killer * 0.5)
                credits[killer] = max(0.0, credits.get(killer, 0.0) - flash_credit)
                credits[str(assister)] += flash_credit
                credit_assister = flash_credit
                credit_killer = credits.get(killer, 0.0)
            if id(d) in trade_kill_ids and trade_for and killer:
                trade_credit = min(0.15, credit_killer * 0.5)
                credits[killer] = max(0.0, credits.get(killer, 0.0) - trade_credit)
                credits[str(trade_for)] += trade_credit
                credit_trade_for = trade_credit
                credit_killer = credits.get(killer, 0.0)

            if killer and credits.get(killer, 0.0) < 0.1:
                deficit = 0.1 - credits.get(killer, 0.0)
                credits[killer] = 0.1
                total_others = sum(v for k, v in credits.items() if k != killer)
                if total_others > 0:
                    for k in list(credits.keys()):
                        if k == killer:
                            continue
                        credits[k] *= max(0.0, (total_others - deficit) / total_others)

            total_credit = sum(credits.values())
            if total_credit > 0:
                for k in credits:
                    credits[k] /= total_credit

            for sid_credit, share in credits.items():
                round_swing_by_player[sid_credit] += swing * share

            credit_killer = credits.get(killer, 0.0) if killer else 0.0
            credit_assister = credits.get(str(assister), 0.0) if assisted_flash and assister else 0.0
            credit_trade_for = credits.get(str(trade_for), 0.0) if trade_for else 0.0

            kill_events.append(
                {
                    "round": rn,
                    "tick": t,
                    "time_into_round_s": time_into_round_s,
                    "killer_steamid": killer,
                    "victim_steamid": victim,
                    "killer_team": kteam,
                    "victim_team": vteam,
                    "weapon": pick(d, ["weapon", "weapon_name", "weaponName"]),
                    "killer_equip_value": k_equip if k_equip is not None else equip_by_round_player.get((rn, killer)) if killer else None,
                    "victim_equip_value": v_equip if v_equip is not None else equip_by_round_player.get((rn, victim)) if victim else None,
                    "killer_round_weapon": weapon_by_round_player.get((rn, killer)) if killer else None,
                    "killer_active_weapon": k_weapon,
                    "victim_active_weapon": v_weapon,
                    "killer_place": k_place,
                    "victim_place": v_place,
                    "killer_pos": k_pos,
                    "victim_pos": v_pos,
                    "killer_armor_value": k_armor,
                    "victim_armor_value": v_armor,
                    "killer_econ_cat": k_econ_cat,
                    "victim_econ_cat": v_econ_cat,
                    "econ_strength_killer": _ECON_STRENGTH.get(k_econ_cat, _ECON_STRENGTH["other"]) if k_econ_cat else None,
                    "econ_strength_victim": _ECON_STRENGTH.get(v_econ_cat, _ECON_STRENGTH["other"]) if v_econ_cat else None,
                    "equip_adv": equip_adv,
                    "damage_total_to_victim": damage_total,
                    "damage_share_killer": damage_share_killer,
                    "damage_breakdown": damage_breakdown if damage_breakdown else None,
                    "win_prob_before": wp_before,
                    "win_prob_after": wp_after,
                    "round_swing": swing,
                    "swing_credit_killer": credit_killer,
                    "swing_credit_assister": credit_assister,
                    "swing_credit_trade_for": credit_trade_for,
                    "assister_steamid": str(assister) if assister else None,
                    "assisted_flash": bool(assisted_flash),
                    "trade_for_steamid": trade_for,
                    "is_opening_kill": is_opening,
                    "is_trade_kill": id(d) in trade_kill_ids,
                    "is_traded_death": id(d) in traded_death_ids,
                    "alive_t_before": alive_t_before,
                    "alive_ct_before": alive_ct_before,
                    "alive_t_after": alive_t_after,
                    "alive_ct_after": alive_ct_after,
                }
            )

            t_alive = len(alive["T"])
            ct_alive = len(alive["CT"])
            if t_alive == 1 and ct_alive >= 2:
                sid = next(iter(alive["T"]))
                if (sid, rn) not in clutch_attempted:
                    clutch_attempted.add((sid, rn))
                    if sid not in aggs:
                        aggs[sid] = PlayerAgg()
                        aggs[sid].rounds = rounds_in_scope_total
                    aggs[sid].clutch_attempts += 1
            if ct_alive == 1 and t_alive >= 2:
                sid = next(iter(alive["CT"]))
                if (sid, rn) not in clutch_attempted:
                    clutch_attempted.add((sid, rn))
                    if sid not in aggs:
                        aggs[sid] = PlayerAgg()
                        aggs[sid].rounds = rounds_in_scope_total
                    aggs[sid].clutch_attempts += 1

        # Round-end outcomes: clutch wins + saves
        winner = round_winners.get(rn)
        if winner not in {"T", "CT"}:
            if len(alive["T"]) != len(alive["CT"]):
                winner = "T" if len(alive["T"]) > len(alive["CT"]) else "CT"
                round_winners[rn] = winner
        if winner in {"T", "CT"}:
            losing = "CT" if winner == "T" else "T"
            for sid in list(alive[winner]):
                if (sid, rn) in clutch_attempted:
                    aggs[sid].clutch_wins += 1
            for sid in alive.get(losing, set()):
                if sid not in aggs:
                    aggs[sid] = PlayerAgg()
                    aggs[sid].rounds = rounds_in_scope_total
                aggs[sid].save_rounds += 1

        # Exit frags: kills after bomb explodes/defuses, on losing side
        obj_tick = None
        if rn in exploded_tick_by_round:
            obj_tick = exploded_tick_by_round[rn]
        elif rn in defused_tick_by_round:
            obj_tick = defused_tick_by_round[rn]
        elif rn in round_end_tick_by_round:
            obj_tick = round_end_tick_by_round[rn]

        if obj_tick is not None and winner in {"T", "CT"}:
            losing = "CT" if winner == "T" else "T"
            for d in ds:
                t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
                if t is None or t <= obj_tick:
                    continue
                victim = _steamid(d, "victim")
                killer = _steamid(d, "attacker")
                if victim is None or killer is None:
                    continue
                victim = str(victim)
                killer = str(killer)
                vteam = team_for_round(victim, rn, t)
                if vteam != losing:
                    continue
                if killer not in aggs:
                    aggs[killer] = PlayerAgg()
                    aggs[killer].rounds = rounds_in_scope_total
                aggs[killer].exit_kills += 1

    trade_kills_by_player: dict[str, int] = defaultdict(int)
    traded_deaths_by_player: dict[str, int] = defaultdict(int)
    for ev in kill_events:
        killer = ev.get("killer_steamid")
        victim = ev.get("victim_steamid")
        if ev.get("is_trade_kill") and killer:
            trade_kills_by_player[str(killer)] += 1
        if ev.get("is_traded_death") and victim:
            traded_deaths_by_player[str(victim)] += 1

    # Kills/deaths/assists/HS from player_death
    kills_in_round: set[tuple[str, int]] = set()
    assists_in_round: set[tuple[str, int]] = set()
    deaths_in_round: set[tuple[str, int]] = set()
    kills_per_round: dict[tuple[str, int], int] = defaultdict(int)

    for d in deaths:
        rn = _as_int(pick(d, ["round", "round_num", "round_number"]))
        t = _as_int(pick(d, ["tick", "game_tick", "gameTick"]))
        round_start_tick = None
        if t is not None:
            rn2, st2 = tick_to_round(t)
            if rn2 is not None:
                rn = rn2
                round_start_tick = st2
        if round_start_tick is None and isinstance(rn, int):
            live_win = live_window_by_round.get(rn)
            if live_win:
                round_start_tick = live_win[0]
        if rn is None:
            # Still count aggregate kills/deaths (for KD) even if we can't map to rounds.
            rn = None
        in_scope = rn is not None and rn in rounds_in_scope_set

        victim = _steamid(d, "victim")
        if victim is not None:
            victim = str(victim)
            if victim not in aggs:
                aggs[victim] = PlayerAgg(name=str(_pname(d, "victim") or ""))
                aggs[victim].rounds = rounds_in_scope_total
            aggs[victim].deaths += 1
            if in_scope:
                deaths_in_round.add((victim, rn))
            # Time To Death (TTD) if we have live round start tick
            if t is not None and isinstance(round_start_tick, int) and t >= round_start_tick:
                ttd_ticks = t - round_start_tick
                max_round_ticks = int(115 * tickrate)
                if max_round_ticks > 0:
                    ttd_ticks = min(ttd_ticks, max_round_ticks)
                aggs[victim].deaths_ttd_sum_s += ttd_ticks / tickrate
                aggs[victim].deaths_ttd_n += 1

        killer = _steamid(d, "attacker")
        if killer is not None:
            killer = str(killer)
            # ignore world/self kills if we can detect (steamid "0" etc.)
            if killer not in {"0", "None", ""}:
                if killer not in aggs:
                    aggs[killer] = PlayerAgg(name=str(_pname(d, "attacker") or ""))
                    aggs[killer].rounds = rounds_in_scope_total
                aggs[killer].kills += 1
                if in_scope:
                    kills_in_round.add((killer, rn))
                    kills_per_round[(killer, rn)] += 1
                hs = pick(d, ["headshot", "is_headshot", "isHeadshot"], default=False)
                if bool(hs):
                    aggs[killer].hs_kills += 1

                if in_scope and isinstance(rn, int) and rn in pistol_rounds_in_scope:
                    aggs[killer].pistol_kills += 1

                # Opening duel: first death event in a round
                if in_scope:
                    if deaths_by_round.get(rn) and deaths_by_round[rn][0] is d:
                        aggs[killer].opening_kills += 1
                        wcat = _weapon_cat(pick(d, ["weapon", "weapon_name", "weaponName"]))
                        if wcat == "sniper":
                            aggs[killer].opening_kills_sniper += 1
                        elif wcat == "rifle":
                            aggs[killer].opening_kills_rifle += 1

        assister = _steamid(d, "assister")
        if assister is not None:
            assister = str(assister)
            if assister not in {"0", "None", ""}:
                if assister not in aggs:
                    aggs[assister] = PlayerAgg(name=str(_pname(d, "assister") or ""))
                    aggs[assister].rounds = rounds_in_scope_total
                aggs[assister].assists += 1
                if in_scope:
                    assists_in_round.add((assister, rn))

                # Support flash kills
                assisted_flash = pick(d, ["assistedflash", "assistedFlash"], default=False)
                if bool(assisted_flash):
                    aggs[assister].support_flash_kills += 1

        # Opening deaths
        if in_scope and deaths_by_round.get(rn) and deaths_by_round[rn][0] is d and victim is not None:
            aggs[str(victim)].opening_deaths += 1

        # Mid/late kills buckets (simple time-based split using round progress)
        if in_scope and killer is not None and t is not None and (live_window_by_round or round_windows):
            # Find current round window
            win = live_window_by_round.get(rn)
            if win is None:
                win = next((w for w in round_windows if w[0] == rn), None)
                if win is not None:
                    win = (win[1], win[2])
            if win is not None:
                st, en = win
                if t >= st:
                    dur = max(1, en - st)
                    max_round_ticks = int(115 * tickrate)
                    if max_round_ticks > 0:
                        dur = min(dur, max_round_ticks)
                    prog = (t - st) / dur
                    if prog >= 0.66:
                        aggs[str(killer)].late_round_kills += 1
                    elif prog >= 0.33:
                        aggs[str(killer)].mid_round_kills += 1

        # Economy-based kills (eco/force contexts)
        if in_scope and killer is not None and victim is not None and econ_by_round:
            econ = econ_by_round.get(rn)
            if econ:
                kt = team_at_tick(str(killer), t)
                vt = team_at_tick(str(victim), t)
                if kt and vt and kt != vt:
                    k_buy = econ["t_buy"] if kt == "T" else econ["ct_buy"]
                    v_buy = econ["t_buy"] if vt == "T" else econ["ct_buy"]
                    if k_buy == "ECO" and v_buy == "BUY":
                        aggs[str(killer)].eco_hero_kills += 1
                    if k_buy == "BUY" and v_buy == "ECO":
                        aggs[str(killer)].eco_bully_kills += 1
                    if k_buy == "FORCE" and v_buy == "BUY":
                        aggs[str(killer)].force_buy_kills += 1

    # Bomb events (plant win % and defuse counts)
    plant_team_by_round: dict[int, str] = {}
    plant_tick_by_round: dict[int, int] = {}
    defuse_team_by_round: dict[int, str] = {}
    for b in bomb_plants:
        sid = pick(b, ["user_steamid", "steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(b, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or rn not in rounds_in_scope_set or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        if sid not in aggs:
            aggs[sid] = PlayerAgg(name=str(pick(b, ["user_name", "player_name", "name"], default="") or ""))
            aggs[sid].rounds = rounds_in_scope_total
        aggs[sid].bomb_plants += 1
        team = team_at_tick(sid, t)
        winner = round_winners.get(rn)
        if team and winner and team == winner:
            aggs[sid].bomb_plant_wins += 1
        if team and rn not in plant_team_by_round:
            plant_team_by_round[rn] = team
            plant_tick_by_round[rn] = int(t)

    for b in bomb_defuses:
        sid = pick(b, ["user_steamid", "steamid", "steam_id", "steamId", "xuid"])
        t = _as_int(pick(b, ["tick", "game_tick", "gameTick"]))
        if sid is None or t is None:
            continue
        rn, _ = tick_to_round(t)
        if rn is None or rn not in rounds_in_scope_set or (max_rounds > 0 and rn > rounds_total):
            continue
        sid = str(sid)
        if sid not in aggs:
            aggs[sid] = PlayerAgg(name=str(pick(b, ["user_name", "player_name", "name"], default="") or ""))
            aggs[sid].rounds = rounds_in_scope_total
        aggs[sid].bomb_defuses += 1
        team = team_at_tick(sid, t)
        if team and rn not in defuse_team_by_round:
            defuse_team_by_round[rn] = team

    # Survival: if player did not die in a round, count as survived.
    if rounds_in_scope_total > 0:
        for sid, agg in aggs.items():
            if agg.rounds <= 0:
                continue
            died = {rn for (s, rn) in deaths_in_round if s == sid and rn in rounds_in_scope_set}
            agg.survived_rounds = rounds_in_scope_total - len(died)

            # KAST: kill or assist or survive or traded
            kast = 0
            for rn in rounds_in_scope:
                if (
                    (sid, rn) in kills_in_round
                    or (sid, rn) in assists_in_round
                    or rn not in died
                    or (sid, rn) in traded_rounds
                ):
                    kast += 1
            agg.kast_rounds = kast

    # Multi-kill rounds
    if rounds_in_scope_total > 0:
        for (sid, rn), cnt in kills_per_round.items():
            if rn not in rounds_in_scope_set:
                continue
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
                "opening_kills_sniper": agg.opening_kills_sniper,
                "opening_kills_rifle": agg.opening_kills_rifle,
                "multi_kill_rounds": agg.multikill_rounds,
                "multi_kill_rounds_pct": multikill_pct,
                "mid_round_kill_pct": mid_kill_pct,
                "late_round_kill_pct": late_kill_pct,
                "ttd_avg_s": ttd,
                "support_flash_kills": agg.support_flash_kills,
                "traded_pct": (len({rn for (s, rn) in traded_rounds if s == sid}) / r * 100.0)
                if r
                else 0.0,
                "trade_kill_pct": (len({rn for (s, rn) in traded_kill_rounds if s == sid}) / r * 100.0)
                if r
                else 0.0,
                "eco_hero_kills": agg.eco_hero_kills,
                "eco_bully_kills": agg.eco_bully_kills,
                "force_buy_kills": agg.force_buy_kills,
                "pistol_kills": agg.pistol_kills,
                "equip_value_sum": agg.equip_value_sum,
                "investment_efficiency": (agg.damage * 1000.0 / agg.equip_value_sum)
                if agg.equip_value_sum
                else None,
                "bomb_plants": agg.bomb_plants,
                "bomb_plant_win_pct": (agg.bomb_plant_wins / agg.bomb_plants * 100.0)
                if agg.bomb_plants
                else 0.0,
                "bomb_defuses": agg.bomb_defuses,
                "clutch_attempts": agg.clutch_attempts,
                "clutch_wins": agg.clutch_wins,
                "clutch_success_pct": (agg.clutch_wins / agg.clutch_attempts * 100.0)
                if agg.clutch_attempts
                else 0.0,
                "save_rounds": agg.save_rounds,
                "save_rounds_pct": (agg.save_rounds / r * 100.0) if r else 0.0,
                "exit_kills": agg.exit_kills,
                "exit_kills_pct": (agg.exit_kills / k * 100.0) if k else 0.0,
                "round_swing_total": round_swing_by_player.get(sid, 0.0) * 100.0,
                "round_swing_per_round": (round_swing_by_player.get(sid, 0.0) / r * 100.0) if r else 0.0,
            },
        }

    side_summary = {
        "T": {"rounds_won": 0, "pistol_wins": 0, "pistol_conversions": 0},
        "CT": {"rounds_won": 0, "pistol_wins": 0, "pistol_conversions": 0},
    }
    for rn in range(1, rounds_total + 1):
        winner = round_winners.get(rn)
        if winner in side_summary:
            side_summary[winner]["rounds_won"] += 1
            if rn in pistol_rounds:
                side_summary[winner]["pistol_wins"] += 1
                if round_winners.get(rn + 1) == winner:
                    side_summary[winner]["pistol_conversions"] += 1

    for side in side_summary:
        wins = side_summary[side]["pistol_wins"]
        conv = side_summary[side]["pistol_conversions"]
        side_summary[side]["pistol_conversion_pct"] = (conv / wins * 100.0) if wins else 0.0

    match_score = {
        "T": side_summary["T"]["rounds_won"],
        "CT": side_summary["CT"]["rounds_won"],
        "unknown": max(0, rounds_total - side_summary["T"]["rounds_won"] - side_summary["CT"]["rounds_won"]),
    }

    team_score = None
    if round_rosters:
        first_round = min(round_rosters.keys())
        team_a_ids = {sid for sid, team in round_rosters[first_round].items() if team == "T"}
        team_b_ids = {sid for sid, team in round_rosters[first_round].items() if team == "CT"}
        if team_a_ids or team_b_ids:
            half_round = None
            if half_announces:
                half_tick = _as_int(pick(half_announces[0], ["tick", "game_tick", "gameTick"]))
                if half_tick is not None:
                    half_round, _ = tick_to_round(half_tick)
            if half_round is None:
                half_round = rounds_total // 2
            if abs(half_round - (rounds_total // 2)) > 1:
                half_round = rounds_total // 2
            team_a_score = 0
            team_b_score = 0
            for rn in range(1, rounds_total + 1):
                winner_side = round_winners.get(rn)
                if winner_side not in {"T", "CT"}:
                    continue
                swapped = rn > half_round
                # Team A started as T on first_round
                team_a_wins = winner_side == ("CT" if swapped else "T")
                if team_a_wins:
                    team_a_score += 1
                else:
                    team_b_score += 1
            team_score = {
                "team_a_rounds": team_a_score,
                "team_b_rounds": team_b_score,
                "team_a_start_side": "T",
                "half_round": half_round,
            }

    round_details: list[dict[str, Any]] = []
    if round_windows:
        for rn, st, en in round_windows:
            if max_rounds > 0 and rn > rounds_total:
                continue
            detail = {
                "round": rn,
                "start_tick": st,
                "end_tick": en,
                "winner": round_winners.get(rn),
                "reason": round_reasons.get(rn),
                "plant_team": plant_team_by_round.get(rn),
                "plant_tick": plant_tick_by_round.get(rn),
                "defuse_team": defuse_team_by_round.get(rn),
                "bomb_exploded_tick": exploded_tick_by_round.get(rn),
                "bomb_defused_tick": defused_tick_by_round.get(rn),
            }
            round_details.append(detail)

    return {
        "rounds_total": rounds_total,
        "round_windows": [
            {"round": rn, "start_tick": st, "end_tick": en} for (rn, st, en) in round_windows
        ]
        if round_windows
        else None,
        "live_round_windows": [
            {"round": rn, "start_tick": st, "end_tick": en} for (rn, st, en) in live_round_windows
        ]
        if live_round_windows
        else None,
        "trade_detection": {"enabled": enable_trade},
        "side_summary": side_summary,
        "match_score": match_score,
        "match_score_from_ticks": match_score_from_ticks,
        "match_score_official": match_score_official,
        "official_rounds_total": official_rounds_total,
        "score_reset_tick": score_reset_tick,
        "team_score_official": team_score_official,
        "team_score": team_score,
        "round_details": round_details if round_details else None,
        "kill_events": kill_events if kill_events else None,
        "round_swing_model": {
            "type": "heuristic",
            "econ_strengths": _ECON_STRENGTH,
            "econ_matchup_win_rate": {
                f"{a}__vs__{b}": rate for (a, b), rate in sorted(econ_matchup_win_rate.items())
            },
            "notes": "Heuristic model; needs calibration to real CS2 win rates.",
        },
        "economy": {
            "rounds": [econ_by_round[rn] for rn in sorted(econ_by_round.keys())],
            "players": economy_players if economy_players else None,
            "coverage": economy_coverage_by_round if economy_coverage_by_round else None,
            "coverage_summary": coverage_summary if economy_coverage_by_round else None,
        }
        if econ_by_round
        else None,
        "players": out_players,
    }

