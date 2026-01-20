from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze_demos


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="titan-hltv")
    p.add_argument(
        "demos",
        nargs="+",
        help="Path(s) to CS2 .dem file(s).",
    )
    p.add_argument(
        "--debug-schema",
        action="store_true",
        help="Include event schema samples in output (for parser debugging).",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="If >0, analyze only first N rounds (faster iteration).",
    )
    p.add_argument(
        "--progress",
        action="store_true",
        help="Print progress to stderr while parsing.",
    )
    p.add_argument(
        "--out",
        default="-",
        help="Output path. Use '-' for stdout. (default: -)",
    )
    p.add_argument(
        "--format",
        choices=["json"],
        default="json",
        help="Output format. (default: json)",
    )
    p.add_argument(
        "--print-stats",
        action="store_true",
        help="Print HLTV-style player stats to the console.",
    )
    p.add_argument(
        "--player",
        action="append",
        default=[],
        help="Filter by player name or steamid (can be repeated).",
    )
    return p.parse_args(argv)


def _format_stat(value: object, suffix: str | None = None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        out = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        out = str(value)
    if suffix:
        return f"{out}{suffix}"
    return out


def _print_player_stats(report: dict, *, players_filter: list[str], stream) -> None:
    per_demo = (report or {}).get("per_demo") or {}
    if not per_demo:
        print("No per-demo stats found.", file=stream)
        return

    filters = {f.lower() for f in players_filter if f}
    for demo_path, demo in per_demo.items():
        basic = (demo or {}).get("basic") or {}
        players = (basic or {}).get("players") or {}
        if not players:
            print(f"{demo_path}: no player stats.", file=stream)
            continue

        match_score_official = basic.get("match_score_official")
        team_score_official = basic.get("team_score_official")
        score_reset_tick = basic.get("score_reset_tick")
        print(f"\n=== {demo_path} ===", file=stream)
        if match_score_official:
            print(f"Score (official, side-based): {match_score_official}", file=stream)
        if team_score_official:
            print(f"Score (official, team-based): {team_score_official}", file=stream)
        if score_reset_tick is not None:
            print(f"Score reset tick: {score_reset_tick}", file=stream)

        for sid, ps in players.items():
            name = ps.get("name", "")
            key = f"{name} {sid}".strip()
            if filters and not any(f in key.lower() for f in filters):
                continue
            derived = ps.get("derived") or {}
            print(f"\n{name or 'Unknown'} ({sid})", file=stream)

            print("HLTV 3.0 Rating: N/A", file=stream)
            print(f"Damage Score: N/A", file=stream)
            print(f"Survival Score: N/A", file=stream)
            print(
                f"Round Swing: {_format_stat(derived.get('round_swing_per_round'), '%')} "
                f"(total {_format_stat(derived.get('round_swing_total'), '%')})",
                file=stream,
            )
            print(f"Multi-Kill Score: N/A (multi_kill_rounds_pct {_format_stat(derived.get('multi_kill_rounds_pct'), '%')})", file=stream)
            print(f"KAST 3.0: {_format_stat(derived.get('kast_pct'), '%')}", file=stream)
            print("Impact Rating: N/A", file=stream)

            print(f"Opening Duel Win %: {_format_stat(derived.get('opening_duel_win_pct'), '%')}", file=stream)
            print(f"Mid-Round Kills %: {_format_stat(derived.get('mid_round_kill_pct'), '%')}", file=stream)
            print(f"Late-Round Kills %: {_format_stat(derived.get('late_round_kill_pct'), '%')}", file=stream)
            print(f"Time To Death (TTD): {_format_stat(derived.get('ttd_avg_s'), 's')}", file=stream)

            print("Pistol King (rating): N/A", file=stream)
            print("Eco Hero (rating): N/A", file=stream)
            print("Eco Bully (rating): N/A", file=stream)
            print("Force Buy Impact (rating): N/A", file=stream)
            print(f"Investment Efficiency: {_format_stat(derived.get('investment_efficiency'))}", file=stream)

            print("Sniper Duelist Success: N/A", file=stream)
            print("Rifle Entry Success: N/A", file=stream)

            print("Baiter Rating: N/A", file=stream)
            print(f"Trade Kill Success %: {_format_stat(derived.get('trade_kill_pct'), '%')}", file=stream)
            print(f"Traded %: {_format_stat(derived.get('traded_pct'), '%')}", file=stream)
            print(f"Support Flash Kills: {_format_stat(derived.get('support_flash_kills'))}", file=stream)
            print(f"Save Rounds %: {_format_stat(derived.get('save_rounds_pct'), '%')}", file=stream)

            print(f"Clutch Success %: {_format_stat(derived.get('clutch_success_pct'), '%')}", file=stream)
            print(f"Bomb Plant Win %: {_format_stat(derived.get('bomb_plant_win_pct'), '%')}", file=stream)
            print("Converter Rounds %: N/A", file=stream)
            print(f"Exit Frags %: {_format_stat(derived.get('exit_kills_pct'), '%')}", file=stream)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    demo_paths = [Path(p).expanduser().resolve() for p in ns.demos]

    try:
        report = analyze_demos(
            demo_paths,
            debug_schema=bool(ns.debug_schema),
            max_rounds=int(ns.max_rounds or 0),
            progress=bool(ns.progress),
        )
    except ModuleNotFoundError as e:
        # Typical missing dependency is demoparser2 in Codespaces first run.
        print(
            "Missing dependency. Install with:\n"
            "  python -m pip install -e .\n\n"
            f"Original error: {e}",
            file=sys.stderr,
        )
        return 2

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if ns.out == "-":
        print(payload)
        if ns.print_stats:
            _print_player_stats(report, players_filter=ns.player, stream=sys.stderr)
        return 0

    out_path = Path(ns.out).expanduser().resolve()
    out_path.write_text(payload, encoding="utf-8")
    if ns.print_stats:
        _print_player_stats(report, players_filter=ns.player, stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

