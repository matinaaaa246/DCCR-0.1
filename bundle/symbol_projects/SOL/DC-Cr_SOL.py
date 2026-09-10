import ccxt
import pandas as pd
import json

symbol = 'SOL/USDT'
formatted_symbol = symbol.replace('/', '')
timeframe = '5m'
limit = 150  # فقط ۱۵۰ کندل آخر بررسی می‌شود

exchange = ccxt.toobit()
candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
df['Time'] = pd.to_datetime(df['Timestamp'], unit='ms').dt.strftime('%Y.%m.%d %H:%M')

# ---------------------------------------------------------------------------
# 1) داده خام کندل‌ها (۱۵۰ کندل آخر)
# ---------------------------------------------------------------------------
candle_list = []
for _, row in df.iterrows():
    candle_list.append({
        "Time": row['Time'],
        "Open": float(row['Open']),
        "High": float(row['High']),
        "Low": float(row['Low']),
        "Close": float(row['Close']),
        "Volume": float(row['Volume'])
    })

# ---------------------------------------------------------------------------
# 2) شناسایی Fair Value Gap ها روی همین ۱۵۰ کندل
# ---------------------------------------------------------------------------
fvg_list = []

for i in range(2, len(df)):
    c1 = df.iloc[i - 2]
    c2 = df.iloc[i - 1]
    c3 = df.iloc[i]

    if c3['Low'] > c1['High']:
        fvg_top = float(c3['Low'])
        fvg_bottom = float(c1['High'])
        fvg_list.append({
            "symbol": formatted_symbol,
            "type": "Bullish",
            "middle_candle_time": c2['Time'],
            "candle_1_time": c1['Time'],
            "candle_3_time": c3['Time'],
            "fvg_top": round(fvg_top, 2),
            "fvg_bottom": round(fvg_bottom, 2),
            "fvg_ce": round((fvg_top + fvg_bottom) / 2, 2),
            "fvg_size": round(fvg_top - fvg_bottom, 2),
            "displacement_volume": float(c2['Volume'])
        })

    elif c3['High'] < c1['Low']:
        fvg_top = float(c1['Low'])
        fvg_bottom = float(c3['High'])
        fvg_list.append({
            "symbol": formatted_symbol,
            "type": "Bearish",
            "middle_candle_time": c2['Time'],
            "candle_1_time": c1['Time'],
            "candle_3_time": c3['Time'],
            "fvg_top": round(fvg_top, 2),
            "fvg_bottom": round(fvg_bottom, 2),
            "fvg_ce": round((fvg_top + fvg_bottom) / 2, 2),
            "fvg_size": round(fvg_top - fvg_bottom, 2),
            "displacement_volume": float(c2['Volume'])
        })

# ---------------------------------------------------------------------------
# 3) کندل‌ها و FVGها کنار هم در یک ساختار JSON واحد
# ---------------------------------------------------------------------------
market_data = {
    "symbol": formatted_symbol,
    "timeframe": timeframe,
    "candles_count": len(candle_list),
    "candles": candle_list,
    "fvg_list": fvg_list
}

# ---------------------------------------------------------------------------
# 4) پرامپت ترکیبی: تحلیل SMC/RTM کامل به‌صورت داخلی انجام می‌شود
#    اما هوش مصنوعی فقط سیگنال نهایی را بدون هیچ توضیحی خروجی می‌دهد
# ---------------------------------------------------------------------------
prompt_text = """Role: Elite Institutional Smart Money Concepts (SMC) Strategist & Final Execution Algorithm.

Mission: You will receive a JSON dataset containing the last 150 candles (OHLCV) of a symbol, together with an algorithmically detected Fair Value Gap (FVG) list for the same 150 candles. You must perform a complete internal SMC/RTM and Price Action analysis as a silent thinking process, then output ONLY the final validated trade signal. You are strictly forbidden from revealing your reasoning, analysis steps, or any intermediate report of any kind.

# INTERNAL ANALYSIS METHODOLOGY (perform this silently — never output it):
1. Market Structure (Mapping): Identify the dominant trend. Pinpoint the most recent Break of Structure (BOS) and any potential Change of Character (CHoCH). Differentiate between internal (minor) and external (major) structure.
2. Liquidity Engineering: Locate swept and un-swept liquidity pools (BSL/SSL). Pay special attention to session highs/lows and identify any engineered liquidity (Inducement / IDM) before key Points of Interest.
3. Supply & Demand (POI Selection): Identify high-probability, unmitigated Order Blocks (OB), Breaker Blocks, and Fair Value Gaps (FVG).
4. FVG Algorithmic Matching: Indicators are entirely removed. Your primary confirmation tool is the provided FVG JSON data. The proposed entry zone must perfectly align with a valid, unmitigated FVG within the correct structural leg.
5. Price Action Validation: Cross-verify the SMC narrative (BOS, CHoCH, Order Blocks, Liquidity Sweeps) against raw Price Action mechanics — momentum, structural shifts, and candlestick context. If Price Action contradicts the SMC setup, reject the trade.
6. Realistic Limit Orders: If issuing a BUY_LIMIT or SELL_LIMIT, the ENTRY price must be mathematically logical and proximal to current price action (e.g., the proximal line or 50% equilibrium of the confirmed FVG). Absolutely no distant, statistically improbable, or disconnected limit orders.
7. Quality Control & Win Rate: Only approve setups with a flawless confluence of SMC structure + Price Action validation + FVG alignment. If the algorithmic Win Rate is evaluated below 50% due to conflicting structural data, immediately issue a HOLD.

# ABSOLUTE ZERO-NOISE OUTPUT RULE
You are strictly forbidden from generating thought processes, reasoning blocks, introductory text, section headers, or any XML/HTML tags. Do not explain your analysis. Output ONLY the final signal block exactly as formatted below, in Persian.

Required Output Format (in Persian):
Symbol: [Symbol Name]
ACTION: [BUY_LIMIT / SELL_LIMIT / HOLD]
ENTRY: [Exact Price / N/A]
SL: [Exact Price (must include spread/risk buffer) / N/A]
TP1: [Exact Price / N/A]
TP2: [Exact Price / N/A]
TP3: [Exact Price / N/A]
RR: [Exact Decimal / N/A]
WIN RATE: [Exact Percentage / N/A]
HOLD REASON: [If HOLD, write one concise Persian sentence explaining the Price Action/FVG conflict / N/A]
RETRY CONDITION: [If HOLD, write one concise Persian sentence defining the exact price trigger needed to re-evaluate / N/A]

# JSON DATA (last 150 candles + detected FVGs):

"""

with open("SOL-Combined.txt", "w", encoding="utf-8") as f:
    f.write(prompt_text)
    json.dump(market_data, f, indent=4, ensure_ascii=False)

print("File saved successfully: SOL-Combined.txt")
