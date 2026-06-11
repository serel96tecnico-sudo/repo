#!/usr/bin/env python
"""
TradingView terminal access for the trading-agent project.

Commands
--------
  status
      Check library availability and connection.

  ohlcv <SYMBOL> [--exchange X] [--interval 1D] [--bars N] [--tail N]
      Historical OHLCV via Alpaca/yfinance.
      Intervals: 1h  4h  1D  (use Alpaca free-tier intervals)

  analysis <SYMBOL> [--exchange X] [--screener S] [--interval I] [--full]
      TradingView technical analysis: recommendation, buy/sell/neutral votes,
      key indicator values. Use --full to print every indicator.
      Intervals: 1m 5m 15m 30m 1h 2h 4h 1D 1W 1M
      Screeners: america  crypto  forex  europe  cfd

  screener <SYM1> <SYM2> ... [--screener S] [--interval I]
      Batch analysis table for a list of tickers.

Examples
--------
  python scripts/tv_query.py status
  python scripts/tv_query.py ohlcv AAPL --interval 4h --bars 200
  python scripts/tv_query.py analysis NVDA --interval 1D --full
  python scripts/tv_query.py analysis BTCUSD --exchange BINANCE --screener crypto --interval 4h
  python scripts/tv_query.py screener AAPL MSFT NVDA AMD --interval 1D
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
from data.tradingview import TradingViewFetcher

INTERVALS_TA  = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D", "1W", "1M"]
SCREENERS     = ["america", "crypto", "forex", "europe", "cfd"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _fmt(v, dec: int = 2) -> str:
    if v is None or v == "":
        return "-"
    return f"{v:.{dec}f}" if isinstance(v, float) else str(v)


def _rec_label(rec: str) -> str:
    icons = {
        "STRONG_BUY": "++ STRONG BUY",
        "BUY":        "+  BUY",
        "NEUTRAL":    "   NEUTRAL",
        "SELL":       "-  SELL",
        "STRONG_SELL": "-- STRONG SELL",
    }
    return icons.get(rec, rec)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_status(args, tv: TradingViewFetcher):
    print()
    print("=== TradingView Status ===")
    print(f"  tradingview-ta   : {'OK' if tv.is_available() else 'NOT INSTALLED  ->  pip install tradingview-ta'}")
    print(f"  tvdatafeed       : discontinued (replaced by Alpaca/yfinance for OHLCV)")
    print(f"  OHLCV provider   : Alpaca Markets (primary) + yfinance (fallback)")
    print()
    if tv.is_available():
        print("  Testing live connection to scanner.tradingview.com ...")
        try:
            r = tv.get_analysis("AAPL", exchange="NASDAQ", screener="america", interval="1D")
            print(f"  Connection OK — AAPL 1D: {r['recommendation']} (B{r['buy']}/S{r['sell']}/N{r['neutral']})")
        except Exception as e:
            print(f"  Connection FAILED: {e}")
    print()


def cmd_ohlcv(args, tv: TradingViewFetcher):
    from data.market_data import MarketDataFetcher

    ctx = Path(__file__).parent.parent / "contex"
    fetcher = MarketDataFetcher(ctx)

    tf_map = {"1D": "day", "4h": "4hour", "1h": "hour"}
    tf = tf_map.get(args.interval, "day")

    bars = args.bars
    period = f"{int(bars * 1.5)}d" if tf == "day" else f"{max(bars * 2, 90)}d"

    print(f"\nFetching {bars} bars {args.interval} for {args.symbol}...")
    df = fetcher.fetch_ohlcv(args.symbol, period=period, timeframe=tf)
    if df is None or df.empty:
        print("  No data returned.")
        return

    df = df.tail(bars)
    tail = min(args.tail, len(df))
    print(f"\n{args.symbol} — last {tail} of {len(df)} bars ({args.interval}):")
    print(df.tail(tail).to_string())
    print(f"\n  Range : {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  Close : {_fmt(float(df['Close'].iloc[-1]), 4)}")
    print()


def cmd_analysis(args, tv: TradingViewFetcher):
    if not tv.is_available():
        print("ERROR: tradingview-ta not installed. Run: pip install tradingview-ta")
        return

    print(f"\nFetching TradingView analysis: {args.symbol} ({args.exchange}) [{args.interval}] ...")
    result = tv.get_analysis(
        symbol=args.symbol,
        exchange=args.exchange,
        screener=args.screener,
        interval=args.interval,
    )

    bar = "=" * 55
    print(f"\n{bar}")
    print(f"  {result['symbol']} / {result['exchange']}   [{result['interval']}]")
    print(f"  Recommendation : {_rec_label(result['recommendation'])}")
    print(f"  Votes          : BUY {result['buy']}  |  SELL {result['sell']}  |  NEUTRAL {result['neutral']}")
    print(bar)

    print("\n  Key Indicators:")
    kw = max((len(k) for k in result["key_indicators"]), default=20) + 2
    for k, v in result["key_indicators"].items():
        print(f"    {k:<{kw}} {_fmt(v, 4)}")

    osc_rec = result["oscillators"].get("RECOMMENDATION", "?")
    ma_rec  = result["moving_averages"].get("RECOMMENDATION", "?")
    print(f"\n  Oscillators    : {_rec_label(osc_rec)}")
    print(f"  Moving Avgs    : {_rec_label(ma_rec)}")

    if args.full:
        print("\n  All Indicators:")
        all_ind = result["all_indicators"]
        kw2 = max((len(k) for k in all_ind), default=30) + 2
        for k, v in sorted(all_ind.items()):
            if v is not None:
                print(f"    {k:<{kw2}} {_fmt(v, 4)}")
    print()


def cmd_screener(args, tv: TradingViewFetcher):
    if not tv.is_available():
        print("ERROR: tradingview-ta not installed. Run: pip install tradingview-ta")
        return

    syms = args.symbols
    print(f"\nFetching TradingView screener: {len(syms)} symbols [{args.interval}] ...")
    results = tv.get_screener_batch(symbols=syms, screener=args.screener, interval=args.interval)

    if not results:
        print("  No results returned.")
        return

    cols = f"\n  {'Symbol':<12} {'Recommendation':<16} {'B':>3} {'S':>3} {'N':>3} {'Price':>9} {'RSI':>6} {'EMA20':>9} {'ADX':>6}"
    print(cols)
    print("  " + "-" * (len(cols) - 3))
    for sym, d in sorted(results.items(), key=lambda x: x[1]["buy"], reverse=True):
        rec = _rec_label(d["recommendation"])
        print(
            f"  {sym:<12} {rec:<16} {d['buy']:>3} {d['sell']:>3} {d['neutral']:>3}"
            f" {_fmt(d.get('close', ''), 2):>9}"
            f" {_fmt(d.get('RSI', ''), 1):>6}"
            f" {_fmt(d.get('EMA20', ''), 2):>9}"
            f" {_fmt(d.get('ADX', ''), 1):>6}"
        )
    print()


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="tv_query",
        description="TradingView terminal access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Check library and connection status")

    p = sub.add_parser("ohlcv", help="Historical OHLCV (Alpaca/yfinance)")
    p.add_argument("symbol")
    p.add_argument("--exchange", default="NASDAQ")
    p.add_argument("--interval", default="1D", choices=["1h", "4h", "1D"])
    p.add_argument("--bars", type=int, default=100)
    p.add_argument("--tail", type=int, default=15)

    p = sub.add_parser("analysis", help="TradingView TA for one symbol")
    p.add_argument("symbol")
    p.add_argument("--exchange", default="NASDAQ")
    p.add_argument("--screener", default="america", choices=SCREENERS)
    p.add_argument("--interval", default="1D", choices=INTERVALS_TA)
    p.add_argument("--full", action="store_true", help="Print all indicators")

    p = sub.add_parser("screener", help="Batch TradingView analysis")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--screener", default="america", choices=SCREENERS)
    p.add_argument("--interval", default="1D", choices=INTERVALS_TA)

    args = parser.parse_args()
    tv = TradingViewFetcher()

    {"status": cmd_status, "ohlcv": cmd_ohlcv, "analysis": cmd_analysis, "screener": cmd_screener}[args.cmd](args, tv)


if __name__ == "__main__":
    main()
