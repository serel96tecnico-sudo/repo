//+------------------------------------------------------------------+
//| XAUUSD_DonchianBreakout_H1.mq4                                   |
//| Ruptura de canal Donchian (100 velas H1) sobre XAUUSD, gestión de |
//| riesgo por % de equity (RiskManager.mqh). Reglas validadas en    |
//| backtest_mt4_gold_silver_h1.py (walk-forward, 6/6 folds OOS      |
//| positivos en 5 de 6, +0.283R/trade agregado sobre 192 trades).   |
//|                                                                   |
//| Adjuntar a un gráfico XAUUSD H1. Requiere RiskManager.mqh en la  |
//| misma carpeta (Experts) o en MQL4/Include (cambiar el #include a |
//| <RiskManager.mqh> en ese caso).                                  |
//+------------------------------------------------------------------+
#property copyright "Trading Agent"
#property version   "1.00"
#property strict

#include "RiskManager.mqh"

input int    DonchianPeriod  = 100;    // nº de velas del canal (validado en backtest)
input int    ATRPeriod       = 14;
input double StopATRMult     = 1.5;    // stop = entrada -/+ StopATRMult * ATR
input double TargetATRMult   = 3.0;    // target = entrada +/- TargetATRMult * ATR (R:R 1:2)
input bool   UseTrendFilter  = false;  // en el backtest ganó "sin filtro" en 5/6 folds
input int    TrendEMAPeriod  = 200;    // solo se usa si UseTrendFilter = true
input int    MagicNumber     = 20260702;
input int    Slippage        = 5;      // puntos
input int    MaxSpreadPoints = 50;     // bloquea entradas si el spread flotante se dispara; 0 = sin filtro

datetime lastBarTime = 0;

int OnInit() {
   if (Period() != PERIOD_H1) {
      Print("AVISO: esta estrategia se validó en H1 — el gráfico actual está en otro timeframe (",
            Period(), "). Los resultados del backtest no aplican aquí.");
   }
   Print("XAUUSD_DonchianBreakout_H1 iniciado. Magic=", MagicNumber,
         " Riesgo/trade=", DoubleToStr(RiskPercentPerTrade, 2), "%",
         " Donchian=", DonchianPeriod, " StopATR=", StopATRMult, " TargetATR=", TargetATRMult,
         " TrendFilter=", UseTrendFilter);
   return (INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
}

bool IsNewBar() {
   if (Time[0] != lastBarTime) {
      lastBarTime = Time[0];
      return true;
   }
   return false;
}

void OnTick() {
   if (!IsNewBar()) return;

   string symbol = Symbol();
   int minBarsNeeded = MathMax(DonchianPeriod, UseTrendFilter ? TrendEMAPeriod : 0) + ATRPeriod + 10;
   if (Bars < minBarsNeeded) return;

   if (HasOpenPosition(symbol, MagicNumber)) return; // una posición a la vez (así se backtesteó)

   if (MaxSpreadPoints > 0) {
      double spread = MarketInfo(symbol, MODE_SPREAD);
      if (spread > MaxSpreadPoints) {
         Print("Entrada bloqueada: spread ", DoubleToStr(spread, 0),
               " pts > máximo permitido ", MaxSpreadPoints, " pts");
         return;
      }
   }

   // Canal Donchian de las DonchianPeriod velas PREVIAS a la última vela cerrada
   // (shift 1 = última vela cerrada; el canal mira shift 2..101, sin incluirla,
   // igual que rolling(n).shift(1) en el backtest de Python).
   int highIdx = iHighest(symbol, PERIOD_H1, MODE_HIGH, DonchianPeriod, 2);
   int lowIdx  = iLowest(symbol, PERIOD_H1, MODE_LOW, DonchianPeriod, 2);
   if (highIdx < 0 || lowIdx < 0) return;
   double donchianHigh = High[highIdx];
   double donchianLow  = Low[lowIdx];

   double closeSignalBar = Close[1];
   bool longSignal  = closeSignalBar > donchianHigh;
   bool shortSignal = closeSignalBar < donchianLow;

   if (UseTrendFilter) {
      double emaTrend = iMA(symbol, PERIOD_H1, TrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE, 1);
      longSignal  = longSignal  && (closeSignalBar > emaTrend);
      shortSignal = shortSignal && (closeSignalBar < emaTrend);
   }

   if (!longSignal && !shortSignal) return;

   double atr = iATR(symbol, PERIOD_H1, ATRPeriod, 1);
   if (atr <= 0) return;

   double stopDist   = AdjustStopDistance(symbol, StopATRMult * atr);
   double targetDist = TargetATRMult * atr;

   double lots = CalcLotSize(symbol, stopDist);
   if (lots <= 0) {
      Print("Entrada omitida: CalcLotSize devolvió 0 (revisa margen/tick value/símbolo cargado)");
      return;
   }

   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   int ticket;
   if (longSignal) {
      double ask = MarketInfo(symbol, MODE_ASK);
      double sl  = NormalizeDouble(ask - stopDist, digits);
      double tp  = NormalizeDouble(ask + targetDist, digits);
      ticket = OrderSend(symbol, OP_BUY, lots, ask, Slippage, sl, tp,
                          "donchian100", MagicNumber, 0, clrGreen);
   } else {
      double bid = MarketInfo(symbol, MODE_BID);
      double sl  = NormalizeDouble(bid + stopDist, digits);
      double tp  = NormalizeDouble(bid - targetDist, digits);
      ticket = OrderSend(symbol, OP_SELL, lots, bid, Slippage, sl, tp,
                          "donchian100", MagicNumber, 0, clrRed);
   }

   if (ticket < 0) {
      Print("OrderSend falló, error ", GetLastError());
   }
}
