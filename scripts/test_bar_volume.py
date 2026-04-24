"""Testar campos de volume nas barras M5 do WIN$N."""
import MetaTrader5 as mt5
from datetime import datetime, timezone
import os
os.environ["PYTHONIOENCODING"] = "utf-8"

mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe")

rates = mt5.copy_rates_from_pos("WIN$N", mt5.TIMEFRAME_M5, 0, 20)
if rates is not None and len(rates) > 0:
    print(f"Campos: {rates.dtype.names}")
    print(f"\nUltimas 10 barras M5 WIN$N:")
    cum_delta = 0
    for r in rates[-10:]:
        ts = datetime.fromtimestamp(r["time"], tz=timezone.utc)
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        tv = r["tick_volume"]
        rv = r["real_volume"]
        bar_range = h - l if (h - l) > 0 else 1
        body = c - o
        # Aproximacao de delta: volume * posicao relativa do close na barra
        # close_pct = (close - low) / (high - low) => 0..1
        # delta_approx = real_vol * (2 * close_pct - 1)
        close_pct = (c - l) / bar_range if bar_range > 0 else 0.5
        delta_approx = rv * (2 * close_pct - 1)
        cum_delta += delta_approx
        tag = "UP" if c > o else "DN" if c < o else "=="
        print(f"  {ts.strftime('%H:%M')} {tag} O={o:.0f} C={c:.0f} tv={tv:>5} rv={rv:>8.0f} delta~{delta_approx:>+8.0f} cum={cum_delta:>+10.0f}")
else:
    print(f"Sem dados: {mt5.last_error()}")

mt5.shutdown()
