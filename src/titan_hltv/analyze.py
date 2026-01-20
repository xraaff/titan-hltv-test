from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .demoparser_adapter import parse_event_rows, parse_players, parse_ticks_rows
from .metrics_basic import compute_basic_metrics


@dataclass(frozen=True)
class DemoInput:
    path: str
    exists: bool
    size_bytes: int | None


def analyze_demos(
    demo_paths: list[Path],
    *,
    debug_schema: bool = False,
    max_rounds: int = 0,
    progress: bool = False,
) -> dict[str, Any]:
    """
    Entry point for demo analysis.

    For now this is a scaffold that:
    - validates inputs
    - returns a stable report shape

    Next step: plug in more events and compute advanced metrics.
    """
    demos: list[DemoInput] = []
    for p in demo_paths:
        exists = p.exists()
        size = p.stat().st_size if exists else None
        demos.append(DemoInput(path=str(p), exists=exists, size_bytes=size))

    missing = [d.path for d in demos if not d.exists]
    if missing:
        raise FileNotFoundError(f"Demo file(s) not found: {missing}")

    demo_meta: dict[str, Any] = {}
    per_demo_reports: dict[str, Any] = {}
    # Best-effort metadata extraction (doesn't fail the whole run if parser API differs)
    try:
        # demoparser2 API may evolve; we keep this isolated and defensive.
        from demoparser2 import DemoParser  # type: ignore

        for d in demos:
            try:
                parser = DemoParser(d.path)
                meta: dict[str, Any] = {}
                for key in ("tickrate", "map_name", "map", "server_name"):
                    if hasattr(parser, key):
                        meta[key] = getattr(parser, key)
                # Some versions expose header dict-like
                if hasattr(parser, "header"):
                    try:
                        meta["header"] = dict(parser.header)  # type: ignore[arg-type]
                    except Exception:
                        meta["header"] = parser.header  # type: ignore[assignment]
                demo_meta[d.path] = meta

                # Try basic events for a first batch of stats
                players = []
                deaths = []
                hurts = []
                round_starts = []
                round_ends = []
                freeze_ends = []
                player_teams = []
                half_announces = []
                bomb_plants = []
                bomb_defuses = []
                bomb_exploded = []
                tick_snapshots = []
                round_start_tick_snapshots = []
                round_end_tick_snapshots = []
                kill_tick_snapshots = []
                match_start_ticks: list[int] = []
                errors: dict[str, str] = {}
                schema: dict[str, Any] = {}

                def _schema_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
                    if not rows:
                        return {"count": 0, "keys": [], "sample": None, "tick_min": None, "tick_max": None}
                    sample = rows[0]
                    ticks = []
                    for r in rows:
                        t = r.get("tick")
                        if isinstance(t, int):
                            ticks.append(t)
                    return {
                        "count": len(rows),
                        "keys": sorted(list(sample.keys())) if isinstance(sample, dict) else [],
                        "sample": sample,
                        "tick_min": min(ticks) if ticks else None,
                        "tick_max": max(ticks) if ticks else None,
                    }

                for event, target in (
                    ("player_death", "deaths"),
                    ("player_hurt", "hurts"),
                    ("round_start", "round_starts"),
                    ("round_end", "round_ends"),
                    ("round_freeze_end", "freeze_ends"),
                    ("player_team", "player_teams"),
                    ("round_announce_last_round_half", "half_announces"),
                    ("bomb_planted", "bomb_plants"),
                    ("bomb_defused", "bomb_defuses"),
                    ("bomb_exploded", "bomb_exploded"),
                    ("round_announce_match_start", "match_start"),
                    ("begin_new_match", "match_start"),
                    ("cs_pre_restart", "match_start"),
                    ("round_officially_ended", "round_officially_ended"),
                ):
                    try:
                        if progress:
                            print(
                                f"[titan-hltv] parse {event} ({Path(d.path).name})...",
                                file=sys.stderr,
                                flush=True,
                            )
                        rows = parse_event_rows(parser, event)
                        if debug_schema:
                            schema[event] = _schema_for_rows(rows)
                        if target == "deaths":
                            deaths = rows
                        elif target == "hurts":
                            hurts = rows
                        elif target == "round_starts":
                            round_starts = rows
                        elif target == "round_ends":
                            round_ends = rows
                        elif target == "freeze_ends":
                            freeze_ends = rows
                        elif target == "player_teams":
                            player_teams = rows
                        elif target == "half_announces":
                            half_announces = rows
                        elif target == "bomb_plants":
                            bomb_plants = rows
                        elif target == "bomb_defuses":
                            bomb_defuses = rows
                        elif target == "bomb_exploded":
                            bomb_exploded = rows
                        elif target == "match_start":
                            for r in rows:
                                t = r.get("tick")
                                if isinstance(t, int):
                                    match_start_ticks.append(t)
                        elif target == "round_officially_ended":
                            pass
                    except Exception as e:  # noqa: BLE001
                        errors[event] = f"{type(e).__name__}: {e}"

                tick_fields = [
                    "tick",
                    "steamid",
                    "player_name",
                    "name",
                    "team_num",
                    "X",
                    "Y",
                    "Z",
                    "round_win_status",
                    "round_win_reason",
                    "round_start_count",
                    "round_end_count",
                    "team_rounds_total",
                    "team_score_first_half",
                    "team_score_second_half",
                    "health",
                    "armor_value",
                    "has_helmet",
                    "has_defuser",
                    "is_alive",
                    "is_scoped",
                    "is_defusing",
                    "is_airborne",
                    "is_walking",
                    "in_bomb_zone",
                    "in_buy_zone",
                    "in_no_defuse_area",
                    "last_place_name",
                    "flash_duration",
                    "flash_max_alpha",
                    "aim_punch_angle",
                    "aim_punch_angle_vel",
                    "duck_amount",
                    "duck_speed",
                    "ducking",
                    "in_duck_jump",
                    "move_type",
                    "move_state",
                    "max_speed",
                    "fall_velo",
                    "velo_modifier",
                    "game_time",
                    "round_start_time",
                    "time_until_next_phase_start",
                    "round_in_progress",
                    "is_freeze_period",
                    "equipment_value_this_round",
                    "current_equip_value",
                    "round_start_equip_value",
                    "active_weapon",
                    "active_weapon_name",
                    "active_weapon_ammo",
                    "weapon_name",
                    "shots_fired",
                    "damage_this_round",
                    "kills_this_round",
                    "assists_this_round",
                    "headshot_kills_this_round",
                    "enemies_flashed_this_round",
                    "utility_damage_this_round",
                    "usercmd_viewangle_x",
                    "usercmd_viewangle_y",
                    "usercmd_viewangle_z",
                    "usercmd_mouse_dx",
                    "usercmd_mouse_dy",
                ]

                # Tick snapshots at round_freeze_end (for economy reconstruction)
                if freeze_ends:
                    try:
                        ticks = []
                        for r in freeze_ends:
                            t = r.get("tick")
                            if isinstance(t, int):
                                ticks.append(t)
                        if ticks:
                            if progress:
                                print(
                                    f"[titan-hltv] parse ticks at freeze_end ({Path(d.path).name})...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            tick_snapshots = parse_ticks_rows(
                                parser,
                                tick_fields,
                                ticks=ticks,
                            )
                            if debug_schema:
                                schema["tick_snapshot"] = _schema_for_rows(tick_snapshots)
                    except Exception as e:  # noqa: BLE001
                        errors["tick_snapshot"] = f"{type(e).__name__}: {e}"

                # Tick snapshots at round_start (fallback economy)
                if round_starts:
                    try:
                        ticks = []
                        for r in round_starts:
                            t = r.get("tick")
                            if isinstance(t, int):
                                ticks.append(t)
                        if ticks:
                            if progress:
                                print(
                                    f"[titan-hltv] parse ticks at round_start ({Path(d.path).name})...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            round_start_tick_snapshots = parse_ticks_rows(
                                parser,
                                tick_fields,
                                ticks=sorted(set(ticks)),
                            )
                            if debug_schema:
                                schema["tick_snapshot_round_start"] = _schema_for_rows(round_start_tick_snapshots)
                    except Exception as e:  # noqa: BLE001
                        errors["tick_snapshot_round_start"] = f"{type(e).__name__}: {e}"

                if round_ends:
                    try:
                        ticks = []
                        for r in round_ends:
                            t = r.get("tick")
                            if isinstance(t, int):
                                ticks.append(t)
                        if ticks:
                            if progress:
                                print(
                                    f"[titan-hltv] parse ticks at round_end ({Path(d.path).name})...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            round_end_tick_snapshots = parse_ticks_rows(
                                parser,
                                tick_fields,
                                ticks=sorted(set(ticks)),
                            )
                            if debug_schema:
                                schema["tick_snapshot_round_end"] = _schema_for_rows(round_end_tick_snapshots)
                    except Exception as e:  # noqa: BLE001
                        errors["tick_snapshot_round_end"] = f"{type(e).__name__}: {e}"

                # Tick snapshots at kill ticks (per-kill context)
                if deaths:
                    try:
                        kill_ticks = []
                        for drow in deaths:
                            t = drow.get("tick")
                            if isinstance(t, int):
                                kill_ticks.append(t)
                        if kill_ticks:
                            if progress:
                                print(
                                    f"[titan-hltv] parse ticks at kill events ({Path(d.path).name})...",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            kill_tick_snapshots = parse_ticks_rows(
                                parser,
                                tick_fields,
                                ticks=sorted(set(kill_ticks)),
                            )
                            if debug_schema:
                                schema["tick_snapshot_kills"] = _schema_for_rows(kill_tick_snapshots)
                    except Exception as e:  # noqa: BLE001
                        errors["tick_snapshot_kills"] = f"{type(e).__name__}: {e}"

                try:
                    if progress:
                        print(f"[titan-hltv] parse players ({Path(d.path).name})...", file=sys.stderr, flush=True)
                    players = parse_players(parser)
                    if debug_schema:
                        schema["players"] = _schema_for_rows(players)
                except Exception as e:  # noqa: BLE001
                    errors["players"] = f"{type(e).__name__}: {e}"

                tickrate = meta.get("tickrate")
                try:
                    if progress:
                        print(f"[titan-hltv] compute metrics ({Path(d.path).name})...", file=sys.stderr, flush=True)
                    per_demo_reports[d.path] = {
                        "errors": errors,
                        "schema": schema if debug_schema else None,
                        "basic": compute_basic_metrics(
                            players=players,
                            deaths=deaths,
                            hurts=hurts,
                            round_starts=round_starts,
                            round_ends=round_ends,
                            freeze_ends=freeze_ends,
                            tick_snapshots=tick_snapshots,
                            round_start_tick_snapshots=round_start_tick_snapshots,
                            kill_tick_snapshots=kill_tick_snapshots,
                            player_teams=player_teams,
                            half_announces=half_announces,
                            bomb_plants=bomb_plants,
                            bomb_defuses=bomb_defuses,
                            bomb_exploded=bomb_exploded,
                            match_start_tick=min(match_start_ticks) if match_start_ticks else None,
                            round_end_tick_snapshots=round_end_tick_snapshots,
                            tickrate=float(tickrate) if tickrate is not None else None,
                            max_rounds=max_rounds,
                        ),
                    }
                except Exception as e:  # noqa: BLE001
                    per_demo_reports[d.path] = {
                        "errors": {**errors, "compute_basic_metrics": f"{type(e).__name__}: {e}"},
                        "schema": schema if debug_schema else None,
                        "basic": None,
                    }
            except Exception as e:
                demo_meta[d.path] = {"error": f"{type(e).__name__}: {e}"}
    except Exception as e:
        demo_meta = {"error": f"{type(e).__name__}: {e}"}

    # Aggregate basic metrics across demos (sum counters, recompute derived)
    agg: dict[str, dict[str, Any]] = {}
    for demo_path, rep in per_demo_reports.items():
        basic = (rep or {}).get("basic") or {}
        players = (basic or {}).get("players") or {}
        for sid, ps in players.items():
            if sid not in agg:
                agg[sid] = {
                    "name": ps.get("name", ""),
                    "rounds": 0,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "damage": 0,
                    "hs_kills": 0,
                    "survived_rounds": 0,
                    "kast_rounds": 0,
                    "_demos": set(),
                }
            a = agg[sid]
            a["_demos"].add(demo_path)
            for k in (
                "rounds",
                "kills",
                "deaths",
                "assists",
                "damage",
                "hs_kills",
                "survived_rounds",
                "kast_rounds",
            ):
                try:
                    a[k] += int(ps.get(k, 0) or 0)
                except Exception:
                    pass
            if not a.get("name") and ps.get("name"):
                a["name"] = ps["name"]

    # recompute derived
    out_players: dict[str, Any] = {}
    for sid, a in agg.items():
        r = int(a["rounds"])
        k = int(a["kills"])
        d = int(a["deaths"])
        adr = (int(a["damage"]) / r) if r else 0.0
        kpr = (k / r) if r else 0.0
        hs_pct = (int(a["hs_kills"]) / k * 100.0) if k else 0.0
        surv_pct = (int(a["survived_rounds"]) / r * 100.0) if r else 0.0
        kast_pct = (int(a["kast_rounds"]) / r * 100.0) if r else 0.0
        kd = (k / d) if d else float(k)

        demos_list = sorted(list(a["_demos"]))
        out_players[sid] = {
            "name": a.get("name", ""),
            "demos": demos_list,
            "rounds": r,
            "kills": k,
            "deaths": d,
            "assists": int(a["assists"]),
            "damage": int(a["damage"]),
            "hs_kills": int(a["hs_kills"]),
            "survived_rounds": int(a["survived_rounds"]),
            "kast_rounds": int(a["kast_rounds"]),
            "derived": {
                "kd": kd,
                "kpr": kpr,
                "adr": adr,
                "hs_pct": hs_pct,
                "survival_pct": surv_pct,
                "kast_pct": kast_pct,
            },
        }

    # Placeholder report schema (we'll keep it stable for downstream tooling)
    return {
        "schema_version": "0.0.1",
        "demos": [asdict(d) for d in demos],
        "demo_meta": demo_meta,
        "per_demo": per_demo_reports,
        "players": out_players,
        "team_summary": {},
        "notes": [
            "Basic metrics implemented (best-effort): KD/KPR/ADR/HS%/Survival%/KAST%.",
            "Next: aggregate across demos + add advanced HLTV 3.0 metrics.",
        ],
    }

