//+------------------------------------------------------------------+
//| XAUUSD_Donchian_MT5.mq5                                          |
//| Ruptura de canal Donchian (100 velas H1) sobre XAUUSD. Port a    |
//| MT5 del EA MT4 validado, con guardián de fondeo FTMO integrado.  |
//|                                                                   |
//| Config VALIDADA (backtest_mt4_gold_silver_h1.py, walk-forward,   |
//| re-ejecutado 2026-07-09 con datos frescos):                      |
//|   XAUUSD OOS agregado: 199 trades, +0.276R/trade, 6/6 folds OOS  |
//|   positivos. Config: Donchian 100, SIN filtro de tendencia,      |
//|   stop 1.5·ATR, target 3.0·ATR (R:R 1:2). (Plata y forex NO      |
//|   sobrevivieron — este EA es solo para XAUUSD.)                  |
//|                                                                   |
//| Guardián FTMO 2-step: pérdida diaria 5% (buffer 4%), pérdida     |
//| total 10% ESTÁTICA desde el capital inicial (buffer 8%). A       |
//| diferencia del ORB, esto es SWING (mantiene overnight): opcional |
//| cierre antes del finde para evitar el gap. El swap del oro NO    |
//| estaba en el backtest — coste real algo peor en holds largos.    |
//|                                                                   |
//| Adjuntar a un gráfico XAUUSD en H1.                              |
//+------------------------------------------------------------------+
#property copyright "Trading Agent"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Estrategia (validada) -----------------------------------------
input int    InpDonchianPeriod  = 100;     // velas del canal (validado)
input int    InpATRPeriod       = 14;
input double InpStopATRMult     = 1.5;     // stop = entrada -/+ 1.5·ATR
input double InpTargetATRMult   = 3.0;     // target = entrada +/- 3.0·ATR (R:R 1:2)
input bool   InpUseTrendFilter  = false;   // en el walk-forward ganó SIN filtro
input int    InpTrendEMAPeriod  = 200;

//--- Gestión de fin de semana --------------------------------------
input bool   InpCloseBeforeWeekend = true; // cerrar el viernes (evita gap del finde)
input int    InpFridayCloseHour = 22;      // hora servidor del cierre del viernes
input int    InpFridayCloseMin  = 45;

//--- Riesgo y guardián FTMO 2-step ---------------------------------
input double InpRiskPercent      = 0.5;    // % de equity arriesgado por trade
input double InpAccountStartBalance = 0;   // capital inicial FTMO (10% estático). 0 = auto (balance al iniciar)
input double InpMaxDailyLossPct  = 4.0;    // corta el día aquí (buffer bajo el 5% de FTMO)
input double InpMaxTotalDDPct    = 8.0;    // detiene el EA aquí (buffer bajo el 10% estático)
input double InpMaxSpreadPoints  = 60;     // bloquea entradas si el spread se dispara; 0 = sin filtro

//--- Técnicos -------------------------------------------------------
input int    InpMagic            = 20260710;
input int    InpSlippage         = 10;

//--- Estado ---------------------------------------------------------
CTrade   trade;
int      g_atrHandle   = INVALID_HANDLE;
int      g_emaHandle   = INVALID_HANDLE;
datetime g_lastBar     = 0;
double   g_initialEquity  = 0.0;
double   g_dayStartEquity = 0.0;
int      g_equityDay   = -1;
bool     g_haltAll     = false;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(Period() != PERIOD_H1)
      Print("AVISO: la estrategia se validó en H1. Gráfico actual en otro timeframe — el backtest no aplica.");

   g_atrHandle = iATR(_Symbol, PERIOD_H1, InpATRPeriod);
   if(g_atrHandle == INVALID_HANDLE){ Print("ERROR: handle ATR."); return(INIT_FAILED); }
   if(InpUseTrendFilter)
   {
      g_emaHandle = iMA(_Symbol, PERIOD_H1, InpTrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_emaHandle == INVALID_HANDLE){ Print("ERROR: handle EMA."); return(INIT_FAILED); }
   }

   g_initialEquity  = (InpAccountStartBalance > 0) ? InpAccountStartBalance
                                                   : AccountInfoDouble(ACCOUNT_BALANCE);
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   Print("XAUUSD_Donchian_MT5 iniciado. Magic=", InpMagic,
         " Riesgo/trade=", DoubleToString(InpRiskPercent,2), "%",
         " Donchian=", InpDonchianPeriod, " Stop=", DoubleToString(InpStopATRMult,1),
         "·ATR Target=", DoubleToString(InpTargetATRMult,1), "·ATR TrendFilter=", InpUseTrendFilter);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(g_atrHandle != INVALID_HANDLE) IndicatorRelease(g_atrHandle);
   if(g_emaHandle != INVALID_HANDLE) IndicatorRelease(g_emaHandle);
}

//+------------------------------------------------------------------+
int DayKey(datetime t){ MqlDateTime d; TimeToStruct(t,d); return d.year*10000+d.mon*100+d.day; }
int MinuteOfDay(datetime t){ MqlDateTime d; TimeToStruct(t,d); return d.hour*60+d.min; }
int DayOfWeek(datetime t){ MqlDateTime d; TimeToStruct(t,d); return d.day_of_week; } // 0=Dom..5=Vie,6=Sab

//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
   }
   return false;
}

void CloseOurPositions()
{
   for(int i = PositionsTotal()-1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
//| Guardián FTMO. true = se puede operar.                           |
//+------------------------------------------------------------------+
bool RiskGuardOK()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   int today = DayKey(TimeCurrent());
   if(today != g_equityDay){ g_equityDay = today; g_dayStartEquity = equity; }

   if(g_initialEquity > 0 && equity <= g_initialEquity * (1.0 - InpMaxTotalDDPct/100.0))
   {
      if(!g_haltAll) Print("GUARDIÁN: DD total >= ", DoubleToString(InpMaxTotalDDPct,1), "% — EA detenido.");
      g_haltAll = true;
      return false;
   }
   if(g_haltAll) return false;

   if(g_dayStartEquity > 0 && equity <= g_dayStartEquity * (1.0 - InpMaxDailyLossPct/100.0))
   {
      if(HasOpenPosition())
      {
         Print("GUARDIÁN: pérdida diaria >= ", DoubleToString(InpMaxDailyLossPct,1),
               "% — cerrando y sin más trades hoy.");
         CloseOurPositions();
      }
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Sizing por % de riesgo (lee la spec en vivo).                    |
//+------------------------------------------------------------------+
double CalcLots(double slDistancePrice)
{
   if(slDistancePrice <= 0) return 0.0;
   double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskMoney = equity * (InpRiskPercent/100.0);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickValue <= 0) return 0.0;

   double lossPerLot = (slDistancePrice / tickSize) * tickValue;
   if(lossPerLot <= 0) return 0.0;
   double lots = riskMoney / lossPerLot;

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   lots = MathFloor(lots/step) * step;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return lots;
}

//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, PERIOD_H1, 0);
   if(t != g_lastBar){ g_lastBar = t; return true; }
   return false;
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar()) return;

   // Cierre antes del fin de semana (evita gap del domingo)
   if(InpCloseBeforeWeekend && DayOfWeek(TimeCurrent()) == 5 &&
      MinuteOfDay(TimeCurrent()) >= InpFridayCloseHour*60 + InpFridayCloseMin)
   {
      if(HasOpenPosition()) CloseOurPositions();
      return;
   }

   if(!RiskGuardOK()) return;
   if(HasOpenPosition()) return;

   int need = MathMax(InpDonchianPeriod, InpUseTrendFilter ? InpTrendEMAPeriod : 0) + InpATRPeriod + 10;
   if(Bars(_Symbol, PERIOD_H1) < need) return;

   if(InpMaxSpreadPoints > 0)
   {
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > InpMaxSpreadPoints) return;
   }

   // Canal Donchian de las DonchianPeriod velas PREVIAS a la última cerrada
   // (shift 2..N+1, sin incluir la vela de señal shift 1). Igual que
   // rolling(n).shift(1) del backtest.
   MqlRates r[];
   int copied = CopyRates(_Symbol, PERIOD_H1, 2, InpDonchianPeriod, r);
   if(copied < InpDonchianPeriod) return;
   double dHigh = -DBL_MAX, dLow = DBL_MAX;
   for(int i = 0; i < copied; i++){ dHigh = MathMax(dHigh, r[i].high); dLow = MathMin(dLow, r[i].low); }

   double closePrev = iClose(_Symbol, PERIOD_H1, 1);   // vela de señal (última cerrada)
   bool longSig  = closePrev > dHigh;
   bool shortSig = closePrev < dLow;

   if(InpUseTrendFilter)
   {
      double ema[];
      if(CopyBuffer(g_emaHandle, 0, 1, 1, ema) > 0)
      {
         longSig  = longSig  && (closePrev > ema[0]);
         shortSig = shortSig && (closePrev < ema[0]);
      }
   }
   if(!longSig && !shortSig) return;

   double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 1, 1, atrBuf) <= 0) return;
   double atr = atrBuf[0];
   if(atr <= 0) return;

   double stopDist   = InpStopATRMult   * atr;
   double targetDist = InpTargetATRMult * atr;
   double lots = CalcLots(stopDist);
   if(lots <= 0){ Print("Lotaje 0 — riesgo insuficiente o spec no disponible."); return; }

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   bool ok;
   if(longSig)
   {
      double sl = NormalizeDouble(ask - stopDist, digits);
      double tp = NormalizeDouble(ask + targetDist, digits);
      ok = trade.Buy(lots, _Symbol, 0.0, sl, tp, "donchian100");
   }
   else
   {
      double sl = NormalizeDouble(bid + stopDist, digits);
      double tp = NormalizeDouble(bid - targetDist, digits);
      ok = trade.Sell(lots, _Symbol, 0.0, sl, tp, "donchian100");
   }

   if(ok)
      Print("Donchian ", (longSig?"LONG":"SHORT"), " ", DoubleToString(lots,2),
            " lots | canal ", DoubleToString(dLow,digits), "-", DoubleToString(dHigh,digits),
            " ATR=", DoubleToString(atr,digits));
   else
      Print("Fallo al abrir: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
}
//+------------------------------------------------------------------+
