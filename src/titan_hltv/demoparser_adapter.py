from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _rows_from_table(table: Any) -> list[dict[str, Any]]:
    """
    Convert a demoparser2 table (pandas/polars/list-of-dicts) to list[dict].
    """
    if table is None:
        return []
    if isinstance(table, list):
        # list[dict] already
        return [r for r in table if isinstance(r, dict)]
    if hasattr(table, "to_dicts"):
        # polars.DataFrame
        try:
            return list(table.to_dicts())
        except Exception:
            pass
    if hasattr(table, "to_dict"):
        # pandas.DataFrame
        try:
            return list(table.to_dict("records"))  # type: ignore[arg-type]
        except TypeError:
            try:
                return list(table.to_dict(orient="records"))  # type: ignore[arg-type]
            except Exception:
                pass
        except Exception:
            pass
    if hasattr(table, "to_pandas"):
        try:
            pdf = table.to_pandas()
            return _rows_from_table(pdf)
        except Exception:
            pass
    raise TypeError(f"Unsupported table type: {type(table)}")


def _try_call(obj: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(obj, method_name, None)
    if fn is None:
        raise AttributeError(method_name)
    return fn(*args, **kwargs)


def parse_event_rows(parser: Any, event_name: str) -> list[dict[str, Any]]:
    """
    Best-effort event extraction across demoparser2 versions.
    """
    last_err: Exception | None = None
    for attempt in (
        lambda: _try_call(parser, "parse_event", event_name),
        lambda: _try_call(parser, "parse_events", [event_name]),
        lambda: _try_call(parser, "parse_event", event_name, {}),
    ):
        try:
            table = attempt()
            # Some parse_events variants return dict[event]->table
            if isinstance(table, dict) and event_name in table:
                table = table[event_name]
            return _rows_from_table(table)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Failed to parse event '{event_name}': {last_err}")


def parse_players(parser: Any) -> list[dict[str, Any]]:
    """
    Try to get a list of players with identifiers (steamid/name).
    """
    last_err: Exception | None = None
    for attempt in (
        lambda: _try_call(parser, "parse_players"),
        lambda: _try_call(parser, "get_players"),
        lambda: _try_call(parser, "players"),
    ):
        try:
            data = attempt()
            if isinstance(data, dict):
                # some APIs return dict keyed by steamid
                rows: list[dict[str, Any]] = []
                for k, v in data.items():
                    if isinstance(v, dict):
                        rows.append({"steamid": str(k), **v})
                return rows
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            # tables
            return _rows_from_table(data)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Failed to parse players: {last_err}")


def pick(row: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def norm_team(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in {"CT", "COUNTERTERRORIST", "COUNTER-TERRORIST"}:
        return "CT"
    if s in {"T", "TERRORIST"}:
        return "T"
    return s or None

