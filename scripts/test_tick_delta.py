"""
Teste: verificar se o MT5 da XP entrega tick data com flags buy/sell
para calcular cumulative delta real do WIN$N.
"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
import os

os.environ["PYTHONIOENCODING"] = "utf-8"

mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe")
info = mt5.terminal_info()
print(f"Terminal: {info.company} build {info.build}")

symbol = "WIN$N"
sym = mt5.symbol_info(symbol)
print(f"\nSimbolo: {symbol}")
print(f"  trade_mode: {sym.trade_mode}")
print(f"  volume_real: {sym.volume_real}")

# Pegar ticks dos ultimos 30 min
utc_to = datetime.now(timezone.utc)
utc_from = utc_to - timedelta(minutes=30)

# Tipo COPY_TICKS_ALL = todos os ticks
ticks = mt5.copy_ticks_range(symbol, utc_from, utc_to, mt5.COPY_TICKS_ALL)

if ticks is not None and len(ticks) > 0:
    print(f"\n  Ticks recebidos: {len(ticks)}")
    print(f"  Primeiro: {datetime.fromtimestamp(ticks[0][0], tz=timezone.utc)}")
    print(f"  Ultimo:   {datetime.fromtimestamp(ticks[-1][0], tz=timezone.utc)}")

    # Analisar flags
    # TICK_FLAG_BID = 2, TICK_FLAG_ASK = 4, TICK_FLAG_LAST = 8
    # TICK_FLAG_VOLUME = 16, TICK_FLAG_BUY = 32, TICK_FLAG_SELL = 64
    n_buy = 0
    n_sell = 0
    n_other = 0
    vol_buy = 0
    vol_sell = 0
    vol_other = 0

    for t in ticks:
        # t: (time, bid, ask, last, volume, time_msc, flags, volume_real)
        flags = t[6]
        vol = t[4]  # tick_volume
        vol_real = t[7] if len(t) > 7 else 0

        if flags & 32:  # TICK_FLAG_BUY
            n_buy += 1
            vol_buy += vol_real if vol_real > 0 else vol
        elif flags & 64:  # TICK_FLAG_SELL
            n_sell += 1
            vol_sell += vol_real if vol_real > 0 else vol
        else:
            n_other += 1
            vol_other += vol_real if vol_real > 0 else vol

    total = n_buy + n_sell + n_other
    print(f"\n  Flags breakdown:")
    print(f"    BUY:   {n_buy:>6} ticks ({n_buy/total*100:.1f}%)  vol={vol_buy:.0f}")
    print(f"    SELL:  {n_sell:>6} ticks ({n_sell/total*100:.1f}%)  vol={vol_sell:.0f}")
    print(f"    OTHER: {n_other:>6} ticks ({n_other/total*100:.1f}%)  vol={vol_other:.0f}")
    print(f"    DELTA: {vol_buy - vol_sell:+.0f}")

    # Mostrar 5 ticks de exemplo
    print(f"\n  Exemplos (5 primeiros):")
    for t in ticks[:5]:
        ts = datetime.fromtimestamp(t[0], tz=timezone.utc)
        flags = t[6]
        side = "BUY" if flags & 32 else "SELL" if flags & 64 else f"flags={flags}"
        vol = t[4]
        vol_r = t[7] if len(t) > 7 else 0
        print(f"    {ts.strftime('%H:%M:%S')} last={t[3]:.0f} vol={vol} vol_real={vol_r} {side}")

    # Calcular delta cumulativo por minuto
    print(f"\n  Delta por minuto (ultimos 10 min):")
    from collections import defaultdict
    minutes = defaultdict(lambda: {"buy": 0, "sell": 0})
    for t in ticks:
        ts = datetime.fromtimestamp(t[0], tz=timezone.utc)
        minute_key = ts.strftime("%H:%M")
        flags = t[6]
        vol = t[7] if len(t) > 7 and t[7] > 0 else t[4]
        if flags & 32:
            minutes[minute_key]["buy"] += vol
        elif flags & 64:
            minutes[minute_key]["sell"] += vol

    cum_delta = 0
    for m in sorted(minutes.keys())[-10:]:
        d = minutes[m]
        bar_delta = d["buy"] - d["sell"]
        cum_delta += bar_delta
        print(f"    {m}: buy={d['buy']:>6.0f} sell={d['sell']:>6.0f} delta={bar_delta:>+6.0f} cum={cum_delta:>+8.0f}")

else:
    print(f"\n  SEM TICKS! Erro: {mt5.last_error()}")
    print("  Tentando com COPY_TICKS_TRADE...")
    ticks2 = mt5.copy_ticks_range(symbol, utc_from, utc_to, mt5.COPY_TICKS_TRADE)
    if ticks2 is not None:
        print(f"  TRADE ticks: {len(ticks2)}")
    else:
        print(f"  Tambem sem trade ticks: {mt5.last_error()}")

mt5.shutdown()
