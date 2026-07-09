//+------------------------------------------------------------------+
//| US100_ORB_TrendFilter_MT5.mq5                                    |
//| Opening Range Breakout (ORB) con FILTRO DE TENDENCIA sobre        |
//| US100.cash (NASDAQ 100 spot CFD). Intradía: abre en la ruptura    |
//| del rango de apertura de Nueva York y cierra antes del fin de     |
//| sesión (el swap largo del activo es -626 pts, nada de overnight). |
//|                                                                   |
//| Config VALIDADA (backtest_orb.py + backtest_orb_sweep.py, QQQ 5m  |
//| como proxy, walk-forward IS/OOS 2 años):                          |
//|   - Rango de apertura 15 min, ruptura por CIERRE de vela M5.      |
//|   - Filtro de tendencia EMA200 diaria: solo LONGS en alcista,     |
//|     solo SHORTS en bajista. ESTE es el edge real (+0.223 IS /     |
//|     +0.149 OOS). Quitarlo deja el OOS en ~+0.01 (break-even).     |
//|   - SL = mitad del rango, TP = 2R.                                |
//|   - RVOL y VWAP (los 'filtros estrella' del vídeo origen) NO      |
//|     sobrevivieron OOS -> incluidos pero OFF por defecto.          |
//|                                                                   |
//| Guardián calibrado para FTMO 2-step: pérdida diaria 5% (buffer 4%)|
//| y pérdida total 10% ESTÁTICA desde el capital inicial (buffer 8%).|
//| Intradía-flat (sin overnight) → cumple la regla diaria de forma   |
//| exacta y evita el swap. Mín. 4 días de trading: se cumple solo.   |
//|                                                                   |
//| CAUTELAS: validado sobre QQQ (ETF), NO sobre este CFD; edge fino  |
//| (~+0.15R) que el spread puede recortar. OBLIGATORIO revalidar en  |
//| el Strategy Tester de MT5 con spreads reales antes de fondeo.     |
//|                                                                   |
//| Adjuntar a un gráfico US100.cash en M5.                           |
//+------------------------------------------------------------------+
#property copyright "Trading Agent"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

//--- Sesión / horario (TODO en hora del SERVIDOR del bróker) --------
input int    InpORStartHour     = 16;      // hora (servidor) del inicio del rango = apertura NY 09:30 ET. ¡VERIFICAR!
input int    InpORStartMinute   = 30;      // minuto (servidor) del inicio del rango
input int    InpORMinutes       = 15;      // duración del rango de apertura (min) — validado 15
input int    InpEntryWindowMin  = 120;     // ventana para abrir tras el rango (min)
input int    InpForceCloseHour  = 22;      // hora (servidor) para cerrar todo (evitar overnight/swap)
input int    InpForceCloseMin   = 45;

//--- Estrategia -----------------------------------------------------
input double InpRR              = 2.0;     // ratio riesgo:beneficio (validado 2:1)
input bool   InpUseTrendFilter  = true;    // EMA200 diaria — EL edge, no desactivar sin motivo
input int    InpTrendEMAPeriod  = 200;
input bool   InpOneTradePerDay  = true;    // 1 operación por día (ORB clásico)

//--- Filtros opcionales (no validados OOS — OFF) --------------------
input bool   InpUseRVOL         = false;   // volumen relativo (tick volume, proxy)
input double InpRVOLThreshold    = 1.5;
input int    InpRVOLDays         = 14;
input bool   InpUseVWAP         = false;   // VWAP de sesión (resultó redundante)

//--- Riesgo y guardián de fondeo (calibrado para FTMO 2-step) -------
// FTMO: pérdida diaria máx 5% (equity, reset 00:00 CET), pérdida total máx 10%
// ESTÁTICA desde el capital inicial. Aquí usamos BUFFER por debajo de esos topes
// para no rozar el límite duro. El EA es intradía-flat, así que el equity de
// inicio de día = balance a las 00:00 (anclaje exacto de la regla diaria).
input double InpRiskPercent      = 0.5;    // % de equity arriesgado por trade
input double InpAccountStartBalance = 0;   // capital inicial FTMO (para el 10% estático). 0 = auto (balance al iniciar)
input double InpMaxDailyLossPct  = 4.0;    // corta el día aquí (buffer bajo el 5% de FTMO)
input double InpMaxTotalDDPct    = 8.0;    // detiene el EA aquí (buffer bajo el 10% estático de FTMO)
input double InpMaxSpreadPoints  = 60;     // bloquea entradas si el spread se dispara; 0 = sin filtro

//--- Técnicos -------------------------------------------------------
input int    InpMagic           = 20260709;
input int    InpSlippage        = 10;      // desviación máx en puntos

//--- Estado ---------------------------------------------------------
CTrade   trade;
int      g_emaHandle   = INVALID_HANDLE;
datetime g_lastBar     = 0;
double   g_orHigh      = 0.0;
double   g_orLow       = 0.0;
bool     g_orReady     = false;
int      g_orDay       = -1;      // día (yyyymmdd) del rango calculado
int      g_lastTradeDay = -1;     // día en que ya se operó
double   g_initialEquity = 0.0;   // para el DD total
double   g_dayStartEquity = 0.0;  // para el DD diario
int      g_equityDay   = -1;
bool     g_haltAll     = false;   // DD total superado -> parada dura

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFillingBySymbol(_Symbol);

   if(Period() != PERIOD_M5)
      Print("AVISO: la estrategia se validó en M5. Gráfico actual en otro timeframe.");

   if(InpUseTrendFilter)
   {
      g_emaHandle = iMA(_Symbol, PERIOD_D1, InpTrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);
      if(g_emaHandle == INVALID_HANDLE)
      {
         Print("ERROR: no se pudo crear el handle EMA200 diaria.");
         return(INIT_FAILED);
      }
   }

   // 10% estático de FTMO se ancla al CAPITAL INICIAL, no al equity del momento
   // de arranque (que puede llevar P&L). Input explícito o balance actual.
   g_initialEquity  = (InpAccountStartBalance > 0) ? InpAccountStartBalance
                                                   : AccountInfoDouble(ACCOUNT_BALANCE);
   g_dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   Print("US100_ORB_TrendFilter iniciado. Magic=", InpMagic,
         " Riesgo/trade=", DoubleToString(InpRiskPercent,2), "%",
         " Trend=", InpUseTrendFilter, " RR=", DoubleToString(InpRR,1),
         " | Rango ", InpORStartHour, ":", InpORStartMinute, " +", InpORMinutes, "min (servidor)");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   if(g_emaHandle != INVALID_HANDLE) IndicatorRelease(g_emaHandle);
}

//+------------------------------------------------------------------+
//| Utilidades de tiempo                                             |
//+------------------------------------------------------------------+
int DayKey(datetime t){ MqlDateTime d; TimeToStruct(t,d); return d.year*10000+d.mon*100+d.day; }
int MinuteOfDay(datetime t){ MqlDateTime d; TimeToStruct(t,d); return d.hour*60+d.min; }

//+------------------------------------------------------------------+
//| Guardián de fondeo. Devuelve true si se puede operar.            |
//+------------------------------------------------------------------+
bool RiskGuardOK()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   // reinicio del ancla diaria al cambiar de día
   int today = DayKey(TimeCurrent());
   if(today != g_equityDay)
   {
      g_equityDay = today;
      g_dayStartEquity = equity;
   }

   // DD total (desde el equity inicial) — parada dura
   if(g_initialEquity > 0 && equity <= g_initialEquity * (1.0 - InpMaxTotalDDPct/100.0))
   {
      if(!g_haltAll)
         Print("GUARDIÁN: DD total >= ", DoubleToString(InpMaxTotalDDPct,1), "% — EA detenido.");
      g_haltAll = true;
      return false;
   }
   if(g_haltAll) return false;

   // DD diario (regla FTMO 5%) — cierra lo abierto y bloquea el resto del día.
   // El cierre protege si se sube el riesgo por trade; con 0.5% y 1 trade/día
   // este límite es un backstop que en la práctica no se toca.
   if(g_dayStartEquity > 0 && equity <= g_dayStartEquity * (1.0 - InpMaxDailyLossPct/100.0))
   {
      if(HasOpenPosition())
      {
         Print("GUARDIÁN: pérdida diaria >= ", DoubleToString(InpMaxDailyLossPct,1),
               "% — cerrando posiciones y sin más trades hoy.");
         CloseOurPositions();
      }
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| ¿Hay ya una posición nuestra abierta?                            |
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
//| Rango de apertura del día: escanea las velas M5 de la ventana.   |
//+------------------------------------------------------------------+
void UpdateOpeningRange()
{
   int today = DayKey(TimeCurrent());
   if(g_orDay == today && g_orReady) return;      // ya calculado hoy
   if(g_orDay != today){ g_orReady = false; g_orHigh = 0; g_orLow = 0; g_orDay = today; }

   int startMin = InpORStartHour*60 + InpORStartMinute;
   int endMin   = startMin + InpORMinutes;
   if(MinuteOfDay(TimeCurrent()) < endMin) return; // el rango aún no ha terminado

   MqlRates rates[];
   int copied = CopyRates(_Symbol, PERIOD_M5, 0, 300, rates);
   if(copied <= 0) return;

   double hi = -DBL_MAX, lo = DBL_MAX; bool found = false;
   for(int i = 0; i < copied; i++)
   {
      if(DayKey(rates[i].time) != today) continue;
      int m = MinuteOfDay(rates[i].time);
      if(m >= startMin && m < endMin)          // velas dentro del rango de apertura
      {
         hi = MathMax(hi, rates[i].high);
         lo = MathMin(lo, rates[i].low);
         found = true;
      }
   }
   if(found && hi > lo){ g_orHigh = hi; g_orLow = lo; g_orReady = true; }
}

//+------------------------------------------------------------------+
//| Filtro de tendencia: +1 alcista, -1 bajista, 0 sin dato.         |
//+------------------------------------------------------------------+
int TrendDirection()
{
   if(!InpUseTrendFilter) return 0;              // 0 = sin restricción
   double ema[];
   if(CopyBuffer(g_emaHandle, 0, 1, 1, ema) <= 0) return 0; // EMA de la vela diaria cerrada
   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ema[0] <= 0) return 0;
   return (price > ema[0]) ? 1 : -1;
}

//+------------------------------------------------------------------+
//| VWAP de sesión (desde el inicio del rango). Proxy con tick vol.  |
//+------------------------------------------------------------------+
double SessionVWAP()
{
   int today = DayKey(TimeCurrent());
   int startMin = InpORStartHour*60 + InpORStartMinute;
   MqlRates r[]; int copied = CopyRates(_Symbol, PERIOD_M5, 0, 300, r);
   if(copied <= 0) return 0.0;
   double pv = 0, vv = 0;
   for(int i = 0; i < copied; i++)
   {
      if(DayKey(r[i].time) != today) continue;
      if(MinuteOfDay(r[i].time) < startMin) continue;
      double tp = (r[i].high + r[i].low + r[i].close)/3.0;
      pv += tp * (double)r[i].tick_volume;
      vv += (double)r[i].tick_volume;
   }
   return (vv > 0) ? pv/vv : 0.0;
}

//+------------------------------------------------------------------+
//| RVOL (proxy): tick vol de la vela actual vs media de esa misma   |
//| franja horaria en los últimos N días.                            |
//+------------------------------------------------------------------+
double CurrentRVOL()
{
   MqlRates r[]; int copied = CopyRates(_Symbol, PERIOD_M5, 0, InpRVOLDays*300+50, r);
   if(copied <= 2) return 1.0;
   int curMin = MinuteOfDay(r[copied-1].time);
   int curDay = DayKey(r[copied-1].time);
   double cur = (double)r[copied-1].tick_volume;
   double sum = 0; int cnt = 0;
   for(int i = copied-2; i >= 0 && cnt < InpRVOLDays; i--)
   {
      if(DayKey(r[i].time) == curDay) continue;
      if(MinuteOfDay(r[i].time) == curMin){ sum += (double)r[i].tick_volume; cnt++; }
   }
   if(cnt == 0 || sum <= 0) return 1.0;
   return cur / (sum/cnt);
}

//+------------------------------------------------------------------+
//| Sizing por % de riesgo. Lee la spec en vivo.                     |
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
   lots = MathFloor(lots/step) * step;             // redondeo a la baja: nunca más riesgo del pedido
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return lots;
}

//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, PERIOD_M5, 0);
   if(t != g_lastBar){ g_lastBar = t; return true; }
   return false;
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar()) return;

   int nowMin  = MinuteOfDay(TimeCurrent());
   int closeMin = InpForceCloseHour*60 + InpForceCloseMin;

   // 1) Cierre forzado por fin de sesión (evita overnight/swap)
   if(nowMin >= closeMin)
   {
      if(HasOpenPosition()) CloseOurPositions();
      return;
   }

   // 2) Guardián de fondeo
   if(!RiskGuardOK()) return;

   // 3) Rango de apertura
   UpdateOpeningRange();
   if(!g_orReady) return;

   // 4) Una posición / un trade por día
   if(HasOpenPosition()) return;
   int today = DayKey(TimeCurrent());
   if(InpOneTradePerDay && g_lastTradeDay == today) return;

   // 5) Ventana de entrada
   int startMin = InpORStartHour*60 + InpORStartMinute;
   int entryStart = startMin + InpORMinutes;
   int entryEnd   = entryStart + InpEntryWindowMin;
   if(nowMin < entryStart || nowMin > entryEnd) return;

   // 6) Filtro de spread
   if(InpMaxSpreadPoints > 0)
   {
      long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spread > InpMaxSpreadPoints) return;
   }

   // 7) Ruptura por CIERRE de la última vela M5 cerrada
   double closePrev = iClose(_Symbol, PERIOD_M5, 1);
   int direction = 0;
   if(closePrev > g_orHigh) direction = 1;
   else if(closePrev < g_orLow) direction = -1;
   if(direction == 0) return;

   // 8) Filtro de tendencia (el edge)
   int tdir = TrendDirection();
   if(InpUseTrendFilter && tdir != 0 && direction != tdir) return;

   // 9) Filtros opcionales
   if(InpUseRVOL && CurrentRVOL() < InpRVOLThreshold) return;
   if(InpUseVWAP)
   {
      double vwap = SessionVWAP();
      if(vwap > 0)
      {
         if(direction == 1 && closePrev <= vwap) return;
         if(direction == -1 && closePrev >= vwap) return;
      }
   }

   // 10) Niveles y ejecución
   double mid = (g_orHigh + g_orLow)/2.0;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = (direction == 1) ? ask : bid;
   double risk  = MathAbs(entry - mid);
   if(risk <= 0) return;

   double sl = mid;
   double tp = (direction == 1) ? entry + InpRR*risk : entry - InpRR*risk;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   double lots = CalcLots(risk);
   if(lots <= 0){ Print("Lotaje 0 — riesgo insuficiente o spec no disponible."); return; }

   bool ok = (direction == 1)
             ? trade.Buy(lots, _Symbol, 0.0, sl, tp, "ORB long")
             : trade.Sell(lots, _Symbol, 0.0, sl, tp, "ORB short");

   if(ok)
   {
      g_lastTradeDay = today;
      Print("ORB ", (direction==1?"LONG":"SHORT"), " ", DoubleToString(lots,2),
            " lots @", DoubleToString(entry,digits), " SL=", DoubleToString(sl,digits),
            " TP=", DoubleToString(tp,digits), " (rango ", DoubleToString(g_orLow,digits),
            "-", DoubleToString(g_orHigh,digits), ")");
   }
   else
      Print("Fallo al abrir: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
}
//+------------------------------------------------------------------+
