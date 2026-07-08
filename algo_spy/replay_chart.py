"""Render algo_spy session chart: SPY + breadth subscores + fills."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

from .breadth_log import DEFAULT_BREADTH_LOG, load_breadth_log
from .throughput import T_ENTRY, T_FLAT, T_WARN

DEFAULT_TRADES_LOG = Path(__file__).resolve().parent / "trades.jsonl"

SYMBOL = "SPY"
TZ = ZoneInfo("America/New_York")
EMA_PERIODS = (3, 6, 10)
FIGSIZE = (16, 13)

EXIT_COLORS = {
    "breadth_medium_roll": "#e67e22",
    "breadth_short_warning": "#f39c12",
    "ema_structural": "#9b59b6",
    "trailing_stop": "#27ae60",
    "stop_loss": "#e74c3c",
    "time_stop": "#95a5a6",
    "session_end_flatten": "#7f8c8d",
}


@dataclass
class Fill:
    ts: pd.Timestamp
    action: str
    side: str
    price: float
    reason: str | None = None
    pnl: float | None = None
    score: float | None = None
    short: float | None = None
    medium: float | None = None


def load_trades(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def trades_to_fills(trades: list[dict]) -> list[Fill]:
    fills: list[Fill] = []
    for t in trades:
        qty = float(t["qty"])
        side = "LONG" if qty > 0 else "SHORT"
        entry_ts = pd.Timestamp(t["entry_ts"]).tz_convert(TZ)
        exit_ts = pd.Timestamp(t["exit_ts"]).tz_convert(TZ)
        fills.append(
            Fill(
                ts=entry_ts,
                action="OPEN",
                side=side,
                price=float(t["entry_price"]),
                score=_opt_float(t, "entry_score"),
                short=_opt_float(t, "entry_short"),
                medium=_opt_float(t, "entry_medium"),
            )
        )
        fills.append(
            Fill(
                ts=exit_ts,
                action="CLOSE",
                side=side,
                price=float(t["exit_price"]),
                reason=t.get("reason"),
                pnl=float(t.get("pnl", 0)),
                score=_opt_float(t, "exit_score"),
                short=_opt_float(t, "exit_short"),
                medium=_opt_float(t, "exit_medium"),
            )
        )
    fills.sort(key=lambda f: f.ts)
    return fills


def _opt_float(row: dict, key: str) -> float | None:
    val = row.get(key)
    return float(val) if val is not None else None


def resample_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    return (
        df_1m.resample("5min", label="left", closed="left")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Close"])
    )


def ema_algo(closes: pd.Series, period: int) -> pd.Series:
    alpha = 2.0 / (period + 1.0)
    out: list[float] = []
    ema_val: float | None = None
    for x in closes.astype(float):
        if ema_val is None:
            ema_val = x
        else:
            ema_val = alpha * x + (1.0 - alpha) * ema_val
        out.append(ema_val)
    return pd.Series(out, index=closes.index, name=f"ema{period}")


def fetch_session_bars(session_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = yf.Ticker(SYMBOL).history(period="5d", interval="1m", auto_adjust=False)
    if raw.empty:
        raise RuntimeError(f"no 1m bars returned for {SYMBOL}")
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC").tz_convert(TZ)
    else:
        raw.index = raw.index.tz_convert(TZ)

    bars_1m = raw.loc[session_date].copy()
    if bars_1m.empty:
        raise RuntimeError(
            f"no 1m bars on {session_date} — check market holiday / Yahoo retention"
        )
    return raw, bars_1m


def build_plot_data(
    raw: pd.DataFrame, bars_1m: pd.DataFrame, fills: list[Fill]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bars_5m_all = resample_5m(raw)
    ema_frame = pd.DataFrame(index=bars_5m_all.index)
    for p in EMA_PERIODS:
        ema_frame[f"ema{p}"] = ema_algo(bars_5m_all["Close"], p)

    ema_on_1m = ema_frame.reindex(bars_1m.index, method="ffill")
    session_start = fills[0].ts.floor("min") - pd.Timedelta(minutes=15)
    session_end = fills[-1].ts.ceil("min") + pd.Timedelta(minutes=15)
    plot_1m = bars_1m.loc[session_start:session_end]
    plot_ema = ema_on_1m.loc[session_start:session_end]
    return plot_1m, plot_ema


def breadth_series(breadth_rows: list[dict], window: slice) -> pd.DataFrame | None:
    if not breadth_rows:
        return None
    df = pd.DataFrame(breadth_rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(TZ)
    df = df.set_index("ts").sort_index()
    cols = ["score", "short_breadth", "medium_breadth"]
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.loc[window]
    if df.empty:
        return None
    return df


def _shade_precursor_zones(ax: plt.Axes, df: pd.DataFrame) -> None:
    """Highlight when short breadth flipped before medium (leading signal)."""
    short = df["short_breadth"]
    medium = df["medium_breadth"]
    short_sign = short.apply(lambda x: 1 if x > T_WARN else (-1 if x < -T_WARN else 0))
    medium_sign = medium.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    lead_bear = (short_sign < 0) & (medium_sign >= 0)
    lead_bull = (short_sign > 0) & (medium_sign <= 0)
    for mask, color in ((lead_bear, "#e74c3c"), (lead_bull, "#2ecc71")):
        if not mask.any():
            continue
        blocks = _contiguous_blocks(mask)
        for start, end in blocks:
            ax.axvspan(start, end, color=color, alpha=0.06, zorder=0)


def _contiguous_blocks(mask: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    blocks: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    in_block = False
    start: pd.Timestamp | None = None
    for ts, on in mask.items():
        if on and not in_block:
            start = ts
            in_block = True
        elif not on and in_block and start is not None:
            blocks.append((start, ts))
            in_block = False
    if in_block and start is not None:
        blocks.append((start, mask.index[-1]))
    return blocks


def _plot_fills_on_price(ax: plt.Axes, fills: list[Fill]) -> None:
    open_fill: Fill | None = None
    for f in fills:
        if f.action == "OPEN":
            open_fill = f
        elif f.action == "CLOSE" and open_fill is not None:
            base = "#2ecc71" if open_fill.side == "LONG" else "#e74c3c"
            ax.axvspan(open_fill.ts, f.ts, color=base, alpha=0.08, zorder=0)
            open_fill = None

    for f in fills:
        if f.action == "OPEN":
            marker = "^" if f.side == "LONG" else "v"
            color = "#27ae60" if f.side == "LONG" else "#c0392b"
            ax.scatter(
                f.ts, f.price, marker=marker, s=120, color=color,
                edgecolors="white", linewidths=0.6, zorder=5,
            )
        else:
            reason = f.reason or "close"
            color = EXIT_COLORS.get(reason, "#95a5a6")
            ax.scatter(
                f.ts, f.price, marker="X", s=90, color=color,
                edgecolors="white", linewidths=0.5, zorder=5,
            )
            pnl_txt = f" {f.pnl:+.0f}" if f.pnl is not None else ""
            ax.annotate(
                f"{reason}{pnl_txt}",
                (f.ts, f.price),
                textcoords="offset points",
                xytext=(0, -14),
                ha="center",
                fontsize=7,
                color=color,
            )


def _vlines_all(axes: list[plt.Axes], fills: list[Fill]) -> None:
    for f in fills:
        color = "#27ae60" if f.action == "OPEN" and f.side == "LONG" else (
            "#c0392b" if f.action == "OPEN" else "#7f8c8d"
        )
        ls = "-" if f.action == "OPEN" else "--"
        for ax in axes:
            ax.axvline(f.ts, color=color, alpha=0.25, linewidth=0.8, linestyle=ls, zorder=1)


def _plot_fill_breadth_points(ax: plt.Axes, fills: list[Fill], field: str) -> None:
    for f in fills:
        val = getattr(f, field, None)
        if val is None:
            continue
        color = "#27ae60" if f.action == "OPEN" and f.side == "LONG" else (
            "#c0392b" if f.action == "OPEN" and f.side == "SHORT" else "#95a5a6"
        )
        marker = "^" if f.action == "OPEN" else "X"
        ax.scatter(f.ts, val, marker=marker, s=60, color=color, zorder=6, alpha=0.9)


def build_pnl_series(trades: list[dict], price_index: pd.DatetimeIndex, prices: pd.Series) -> pd.Series:
    """Session P&L curve: realized after each close + unrealized mark while open."""
    if price_index.empty:
        return pd.Series(dtype=float)

    intervals: list[tuple[pd.Timestamp, pd.Timestamp, float, float, float]] = []
    for t in trades:
        entry_ts = pd.Timestamp(t["entry_ts"]).tz_convert(TZ)
        exit_ts = pd.Timestamp(t["exit_ts"]).tz_convert(TZ)
        qty = float(t["qty"])
        entry_px = float(t["entry_price"])
        realized = float(t.get("pnl", 0))
        intervals.append((entry_ts, exit_ts, qty, entry_px, realized))

    intervals.sort(key=lambda x: x[0])
    out: list[float] = []
    for ts in price_index:
        realized = sum(r for _, exit_ts, _, _, r in intervals if exit_ts <= ts)
        unrealized = 0.0
        for entry_ts, exit_ts, qty, entry_px, _ in intervals:
            if entry_ts <= ts <= exit_ts:
                mark = float(prices.loc[ts])
                unrealized += (mark - entry_px) * qty
                break
        out.append(realized + unrealized)

    return pd.Series(out, index=price_index, name="pnl")


def _plot_pnl_curve(ax: plt.Axes, pnl: pd.Series, fills: list[Fill]) -> None:
    ax.axhline(0, color="#7f8c8d", linewidth=0.7, alpha=0.8, zorder=1)
    ax.plot(pnl.index, pnl.values, color="#ecf0f1", linewidth=1.4, label="session P&L", zorder=2)
    ax.fill_between(
        pnl.index,
        0,
        pnl.values,
        where=pnl.values >= 0,
        color="#2ecc71",
        alpha=0.18,
        interpolate=True,
        zorder=1,
    )
    ax.fill_between(
        pnl.index,
        0,
        pnl.values,
        where=pnl.values < 0,
        color="#e74c3c",
        alpha=0.18,
        interpolate=True,
        zorder=1,
    )

    cum = 0.0
    for f in fills:
        if f.action != "CLOSE" or f.pnl is None:
            continue
        cum += f.pnl
        color = "#27ae60" if f.pnl >= 0 else "#e74c3c"
        ax.scatter(f.ts, cum, marker="o", s=45, color=color, zorder=5, edgecolors="white", linewidths=0.4)
        ax.annotate(
            f"{f.pnl:+.0f}",
            (f.ts, cum),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=7,
            color=color,
        )

    final = float(pnl.iloc[-1]) if len(pnl) else 0.0
    ax.set_ylabel(f"P&L (${final:+.0f} final)")


def render_chart(
    fills: list[Fill],
    plot_1m: pd.DataFrame,
    plot_ema: pd.DataFrame,
    session_date: str,
    out_path: Path,
    breadth_df: pd.DataFrame | None,
    trades: list[dict],
) -> None:
    plt.style.use("seaborn-v0_8-darkgrid")
    fig, axes = plt.subplots(
        4,
        1,
        figsize=FIGSIZE,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1.1, 1.1, 1.1]},
    )
    ax_price, ax_score, ax_parts, ax_pnl = axes

    pnl_series = build_pnl_series(trades, plot_1m.index, plot_1m["Close"])
    _plot_pnl_curve(ax_pnl, pnl_series, fills)

    ax_price.plot(
        plot_1m.index, plot_1m["Close"],
        color="#bdc3c7", linewidth=0.8, label="SPY 1m close", zorder=1,
    )
    ax_price.plot(plot_ema.index, plot_ema["ema3"], color="#2ecc71", linewidth=1.4, label="EMA1 (3)", zorder=2)
    ax_price.plot(plot_ema.index, plot_ema["ema6"], color="#3498db", linewidth=1.2, label="EMA2 (6)", zorder=2)
    ax_price.plot(plot_ema.index, plot_ema["ema10"], color="#9b59b6", linewidth=1.0, label="EMA3 (10)", zorder=2)
    _plot_fills_on_price(ax_price, fills)

    has_series = breadth_df is not None and not breadth_df.empty
    if has_series:
        ax_score.plot(breadth_df.index, breadth_df["score"], color="#ecf0f1", linewidth=1.2, label="total score")
        ax_score.axhline(T_ENTRY, color="#2ecc71", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_score.axhline(-T_ENTRY, color="#e74c3c", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_score.axhline(T_FLAT, color="#95a5a6", linestyle=":", linewidth=0.6, alpha=0.5)
        ax_score.axhline(-T_FLAT, color="#95a5a6", linestyle=":", linewidth=0.6, alpha=0.5)
        ax_score.fill_between(
            breadth_df.index, T_ENTRY, breadth_df["score"],
            where=breadth_df["score"] >= T_ENTRY, color="#2ecc71", alpha=0.12,
        )
        ax_score.fill_between(
            breadth_df.index, -T_ENTRY, breadth_df["score"],
            where=breadth_df["score"] <= -T_ENTRY, color="#e74c3c", alpha=0.12,
        )

        ax_parts.plot(
            breadth_df.index, breadth_df["short_breadth"],
            color="#f1c40f", linewidth=1.1, label="short (30s+1m)",
        )
        ax_parts.plot(
            breadth_df.index, breadth_df["medium_breadth"],
            color="#3498db", linewidth=1.1, label="medium (5m+20m)",
        )
        ax_parts.axhline(T_WARN, color="#f39c12", linestyle=":", linewidth=0.7, alpha=0.6)
        ax_parts.axhline(-T_WARN, color="#f39c12", linestyle=":", linewidth=0.7, alpha=0.6)
        ax_parts.axhline(0, color="#7f8c8d", linewidth=0.6, alpha=0.5)
        _shade_precursor_zones(ax_parts, breadth_df)
    else:
        ax_score.text(
            0.5, 0.5,
            "No breadth.jsonl — run next session with v2 main.py\n"
            "(or sparse fill points below if trades log has breadth fields)",
            transform=ax_score.transAxes, ha="center", va="center", fontsize=10, color="#bdc3c7",
        )
        _plot_fill_breadth_points(ax_score, fills, "score")
        _plot_fill_breadth_points(ax_parts, fills, "short")
        _plot_fill_breadth_points(ax_parts, fills, "medium")

    _vlines_all(list(axes), fills)

    total_pnl = sum(t.pnl or 0 for t in fills if t.action == "CLOSE")
    n_trades = sum(1 for t in fills if t.action == "CLOSE")
    ax_price.set_title(
        f"algo_spy v2 — {session_date}  ({n_trades} trades, P&L {total_pnl:+.2f})",
        fontsize=14, fontweight="bold",
    )
    ax_price.set_ylabel("SPY price")
    ax_score.set_ylabel("breadth score")
    ax_parts.set_ylabel("subscores")
    ax_pnl.set_xlabel("time (ET)")
    ax_price.legend(loc="upper left", fontsize=8)
    if has_series:
        ax_score.legend(loc="upper left", fontsize=8)
        ax_parts.legend(loc="upper left", fontsize=8)
    ax_pnl.legend(loc="upper left", fontsize=8)
    ax_pnl.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
    fig.autofmt_xdate()
    fig.text(
        0.01, 0.01,
        "Green/red tint on subscores = short leading medium (precursor). "
        "Entries need total score; exits often fire on medium roll.",
        fontsize=8, color="#95a5a6",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def session_date_from_trades(trades: list[dict]) -> str:
    entry_ts = pd.Timestamp(trades[0]["entry_ts"]).tz_convert(TZ)
    return entry_ts.strftime("%Y-%m-%d")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chart algo_spy session from trades.jsonl")
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES_LOG)
    parser.add_argument("--breadth", type=Path, default=DEFAULT_BREADTH_LOG)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    trades = load_trades(args.trades)
    if not trades:
        raise SystemExit(f"no trades in {args.trades}")

    session_date = args.date or session_date_from_trades(trades)
    out_path = args.out or (
        Path(__file__).resolve().parents[2]
        / "docs"
        / f"algo_spy_session_{session_date.replace('-', '')}.png"
    )

    fills = trades_to_fills(trades)
    raw, bars_1m = fetch_session_bars(session_date)
    plot_1m, plot_ema = build_plot_data(raw, bars_1m, fills)

    window = slice(plot_1m.index[0], plot_1m.index[-1])
    breadth_df = breadth_series(load_breadth_log(args.breadth), window)

    render_chart(fills, plot_1m, plot_ema, session_date, out_path, breadth_df, trades)
    print(f"saved {out_path}")
    if breadth_df is None or breadth_df.empty:
        print(f"note: no breadth time series at {args.breadth} — chart uses fill points only")


if __name__ == "__main__":
    main()
