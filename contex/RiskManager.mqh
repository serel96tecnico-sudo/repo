//+------------------------------------------------------------------+
//| RiskManager.mqh                                                  |
//| Gestor de riesgo reutilizable para EAs de MT4 — sizing por        |
//| % de equity, tope de margen, guard de una posición a la vez      |
//| (filtrado por magic number, para no tocar operativa manual de    |
//| broker_3 en la misma cuenta) y coste estimado de swap.           |
//|                                                                   |
//| Calibrado sobre la especificación de símbolo pasada por el       |
//| operador (contrato 100, precisión 2, volumen 0.01-20.00 paso     |
//| 0.01, margen CFD-Leverage 100%, stops level 0, swap en puntos    |
//| -71.5 largo / +32.5 corto, triple swap miércoles, apalancamiento |
//| de cuenta 1:500) — pero todo se lee en vivo vía MarketInfo() /   |
//| AccountXxx(), no se asume nada a fuego, para que siga siendo     |
//| correcto si el bróker cambia la especificación.                  |
//+------------------------------------------------------------------+
#property strict

input double RiskPercentPerTrade = 1.0;    // % de equity arriesgado por operación
input double MaxMarginUsagePct   = 50.0;   // tope de margen usado sobre el free margin
input double MaxRiskPctHardCap   = 3.0;    // tope DURO: si ni el lote mínimo cabe bajo este %, se omite la operación
                                            // en vez de operar sobreexpuesto en silencio (cuentas pequeñas: XAUUSD
                                            // con lote mínimo 0.01 tiene un riesgo en $ fijo por trade que no baja
                                            // más — con equity insuficiente, RiskPercentPerTrade deja de ser un
                                            // dial real y este tope es lo único que evita reventar la cuenta)

// ---- Sizing ------------------------------------------------------------------

// Redondea lots hacia ABAJO al step del símbolo y aplica min/max — nunca nos
// pasamos del riesgo pedido por redondeo hacia arriba.
double NormalizeLots(string symbol, double lots) {
   double step   = MarketInfo(symbol, MODE_LOTSTEP);
   double minLot = MarketInfo(symbol, MODE_MINLOT);
   double maxLot = MarketInfo(symbol, MODE_MAXLOT);
   if (step <= 0) step = 0.01;

   double normalized = MathFloor(lots / step) * step;
   normalized = MathMax(normalized, minLot);
   normalized = MathMin(normalized, maxLot);
   return NormalizeDouble(normalized, 2);
}

// Recorta el lotaje si excede MaxMarginUsagePct del free margin disponible.
// MODE_MARGINREQUIRED ya devuelve el margen por 1 lote calculado por el
// bróker con el apalancamiento/margin-percentage reales de la cuenta —
// más fiable que recalcularlo a mano con el apalancamiento a fuego.
double ApplyMarginCap(string symbol, double lots) {
   double marginPerLot = MarketInfo(symbol, MODE_MARGINREQUIRED);
   if (marginPerLot <= 0) return lots; // símbolo sin datos de margen todavía (no cargado en Market Watch)

   double freeMargin     = AccountFreeMargin();
   double maxMarginUse   = freeMargin * (MaxMarginUsagePct / 100.0);
   double maxLotsByMargin = maxMarginUse / marginPerLot;

   if (lots > maxLotsByMargin) {
      double capped = NormalizeLots(symbol, maxLotsByMargin);
      if (capped < lots) {
         Print("ApplyMarginCap: lotaje recortado de ", DoubleToStr(lots, 2),
               " a ", DoubleToStr(capped, 2), " por tope de margen (",
               DoubleToStr(MaxMarginUsagePct, 0), "% del free margin)");
      }
      lots = capped;
   }
   return lots;
}

// Tamaño de posición para arriesgar riskPct % del equity dado un stop a
// stopDistPrice puntos de precio de distancia respecto a la entrada. Usa
// MODE_TICKVALUE/MODE_TICKSIZE del bróker en vez de asumir el contrato
// (100 oz, precisión 2) a mano.
double CalcLotSize(string symbol, double stopDistPrice, double riskPct = -1) {
   if (riskPct < 0) riskPct = RiskPercentPerTrade;
   if (stopDistPrice <= 0) return 0.0;

   double equity    = AccountEquity();
   double riskMoney = equity * (riskPct / 100.0);

   double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
   double tickSize  = MarketInfo(symbol, MODE_TICKSIZE);
   if (tickSize <= 0) tickSize = MarketInfo(symbol, MODE_POINT);
   if (tickValue <= 0 || tickSize <= 0) {
      Print("CalcLotSize: MODE_TICKVALUE/MODE_TICKSIZE inválidos para ", symbol,
            " — ¿símbolo no cargado en Market Watch?");
      return 0.0;
   }

   double moneyPerLot = (stopDistPrice / tickSize) * tickValue;
   if (moneyPerLot <= 0) return 0.0;

   double rawLots = riskMoney / moneyPerLot;
   double lots = NormalizeLots(symbol, rawLots);

   double minLot = MarketInfo(symbol, MODE_MINLOT);
   if (rawLots < minLot && lots > 0) {
      double actualRiskPct = (lots * moneyPerLot) / equity * 100.0;
      if (actualRiskPct > MaxRiskPctHardCap) {
         Print("CalcLotSize: operación OMITIDA — ni el lote mínimo (", DoubleToStr(minLot, 2),
               ") cabe bajo el tope duro. Arriesgaría ", DoubleToStr(actualRiskPct, 2),
               "% del equity (", DoubleToStr(equity, 2), " ", AccountCurrency(),
               ") frente al ", DoubleToStr(MaxRiskPctHardCap, 2),
               "% máximo permitido. Cuenta insuficiente para este stop en este símbolo.");
         return 0.0;
      }
      Print("CalcLotSize: el lote mínimo ya arriesga más de lo pedido — riesgo real ",
            DoubleToStr(actualRiskPct, 2), "% en vez de ", DoubleToStr(riskPct, 2), "%");
   }

   lots = ApplyMarginCap(symbol, lots);
   return lots;
}

// ---- Stop/Take Profit ajustados a las reglas del bróker ----------------------

// El "Nivel de Stops" de la especificación es 0 (sin distancia mínima forzada
// hoy), pero se deja el chequeo genérico por si el bróker lo cambia: MT4
// rechaza SL/TP más cerca del precio que MODE_STOPLEVEL+MODE_FREEZELEVEL puntos.
double AdjustStopDistance(string symbol, double desiredDistPrice) {
   double point       = MarketInfo(symbol, MODE_POINT);
   double stopLevel   = MarketInfo(symbol, MODE_STOPLEVEL) * point;
   double freezeLevel = MarketInfo(symbol, MODE_FREEZELEVEL) * point;
   double minDist     = MathMax(stopLevel, freezeLevel);
   return MathMax(desiredDistPrice, minDist);
}

// ---- Guard de una posición a la vez -------------------------------------------

// La estrategia (ruptura Donchian sobre XAUUSD H1) se backtesteó con UNA
// posición abierta a la vez. Este guard evita que el EA abra una segunda
// operación mientras la primera sigue viva, y sólo mira SUS PROPIAS órdenes
// (magic number) para no interferir con la operativa manual de broker_3 en
// la misma cuenta.
bool HasOpenPosition(string symbol, int magic) {
   for (int i = 0; i < OrdersTotal(); i++) {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (OrderSymbol() == symbol && OrderMagicNumber() == magic) return true;
   }
   return false;
}

// ---- Swap (coste de mantener la posición de un día para otro) ---------------

// Swap "en puntos": convierte swapPoints a $ usando MODE_TICKVALUE/TICKSIZE,
// igual que en el sizing, para no asumir el contrato a mano. nights=3 los
// miércoles (triple swap, según la especificación del símbolo). Es SOLO
// informativo — MT4 aplica el swap real solo; sirve para loguear/filtrar si
// mantener la posición varias noches se come el edge (el backtest en Python
// solo modeló spread+slippage, no swap).
double EstimatedSwapCost(string symbol, double lots, int orderType, int nights = 1) {
   double swapPoints = (orderType == OP_BUY)
      ? MarketInfo(symbol, MODE_SWAPLONG)
      : MarketInfo(symbol, MODE_SWAPSHORT);

   double tickValue = MarketInfo(symbol, MODE_TICKVALUE);
   double tickSize  = MarketInfo(symbol, MODE_TICKSIZE);
   double point      = MarketInfo(symbol, MODE_POINT);
   if (tickSize <= 0) tickSize = point;
   if (tickValue <= 0 || tickSize <= 0 || point <= 0) return 0.0;

   double moneyPerPoint = (point / tickSize) * tickValue;
   return swapPoints * moneyPerPoint * lots * nights;
}

//+------------------------------------------------------------------+
//| Uso real: ver XAUUSD_DonchianBreakout_H1.mq4 (mismo directorio), |
//| que ya integra este módulo (HasOpenPosition/AdjustStopDistance/  |
//| CalcLotSize) en la lógica de ruptura Donchian.                   |
//+------------------------------------------------------------------+
