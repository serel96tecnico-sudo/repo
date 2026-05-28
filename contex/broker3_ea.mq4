//+------------------------------------------------------------------+
//| Broker3_Export.mq4                                               |
//| Exporta posiciones, balance, historial e indicadores a JSON      |
//+------------------------------------------------------------------+
#property copyright "Trading Agent"
#property version   "1.10"
#property strict

extern int    UpdateSeconds = 5;
extern string OutputFile    = "broker3_positions.json";

// Nombre exacto del indicador Supply & Demand (sin extensión .ex4)
extern string SD_Indicator  = "Supply and Demand Order Blocks";

datetime lastUpdate = 0;

int OnInit() {
   EventSetTimer(UpdateSeconds);
   ExportData();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
   EventKillTimer();
}

void OnTimer() {
   ExportData();
}

string ExportIndicators(string symbol, int tf) {
   string tfStr = IntegerToString(tf);

   // Parabolic SAR (parámetros estándar: step=0.02, max=0.2)
   double sar0 = iSAR(symbol, tf, 0.02, 0.2, 0);
   double sar1 = iSAR(symbol, tf, 0.02, 0.2, 1);
   double price = MarketInfo(symbol, MODE_BID);

   // MACD (12, 26, 9, Close)
   double macd0  = iMACD(symbol, tf, 12, 26, 9, PRICE_CLOSE, MODE_MAIN,   0);
   double macd1  = iMACD(symbol, tf, 12, 26, 9, PRICE_CLOSE, MODE_MAIN,   1);
   double sig0   = iMACD(symbol, tf, 12, 26, 9, PRICE_CLOSE, MODE_SIGNAL, 0);
   double sig1   = iMACD(symbol, tf, 12, 26, 9, PRICE_CLOSE, MODE_SIGNAL, 1);
   double hist0  = macd0 - sig0;
   double hist1  = macd1 - sig1;

   // Supply & Demand Order Blocks (buffer 0 = demand/soporte, buffer 1 = supply/resistencia)
   double sd_demand  = iCustom(symbol, tf, SD_Indicator, 0, 0);
   double sd_supply  = iCustom(symbol, tf, SD_Indicator, 1, 0);

   // Señales derivadas
   string sar_signal  = (price > sar0) ? "bullish" : "bearish";
   string macd_signal = "neutral";
   if (hist0 > 0 && hist1 <= 0) macd_signal = "bullish_cross";
   else if (hist0 < 0 && hist1 >= 0) macd_signal = "bearish_cross";
   else if (hist0 > 0) macd_signal = "bullish";
   else if (hist0 < 0) macd_signal = "bearish";

   string result = "";
   result += "    \"price\": " + DoubleToStr(price, 5) + ",\n";
   result += "    \"timeframe\": \"M" + tfStr + "\",\n";
   result += "    \"parabolic_sar\": {\n";
   result += "      \"value\": " + DoubleToStr(sar0, 5) + ",\n";
   result += "      \"prev\": " + DoubleToStr(sar1, 5) + ",\n";
   result += "      \"signal\": \"" + sar_signal + "\"\n";
   result += "    },\n";
   result += "    \"macd\": {\n";
   result += "      \"macd\": " + DoubleToStr(macd0, 5) + ",\n";
   result += "      \"signal\": " + DoubleToStr(sig0, 5) + ",\n";
   result += "      \"histogram\": " + DoubleToStr(hist0, 5) + ",\n";
   result += "      \"macd_prev\": " + DoubleToStr(macd1, 5) + ",\n";
   result += "      \"hist_prev\": " + DoubleToStr(hist1, 5) + ",\n";
   result += "      \"cross\": \"" + macd_signal + "\"\n";
   result += "    },\n";
   result += "    \"supply_demand\": {\n";
   result += "      \"demand_zone\": " + DoubleToStr(sd_demand, 5) + ",\n";
   result += "      \"supply_zone\": " + DoubleToStr(sd_supply, 5) + "\n";
   result += "    }";
   return result;
}

void ExportData() {
   int handle = FileOpen(OutputFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if (handle == INVALID_HANDLE) return;

   double balance    = AccountBalance();
   double equity     = AccountEquity();
   double margin     = AccountMargin();
   double freeMargin = AccountFreeMargin();
   double profit     = AccountProfit();
   string currency   = AccountCurrency();
   string server     = AccountServer();
   int    leverage   = AccountLeverage();

   FileWrite(handle, "{");
   FileWrite(handle, "  \"timestamp\": \"" + TimeToStr(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",");
   FileWrite(handle, "  \"account\": {");
   FileWrite(handle, "    \"balance\": " + DoubleToStr(balance, 2) + ",");
   FileWrite(handle, "    \"equity\": " + DoubleToStr(equity, 2) + ",");
   FileWrite(handle, "    \"margin\": " + DoubleToStr(margin, 2) + ",");
   FileWrite(handle, "    \"free_margin\": " + DoubleToStr(freeMargin, 2) + ",");
   FileWrite(handle, "    \"profit\": " + DoubleToStr(profit, 2) + ",");
   FileWrite(handle, "    \"currency\": \"" + currency + "\",");
   FileWrite(handle, "    \"leverage\": " + IntegerToString(leverage) + ",");
   FileWrite(handle, "    \"server\": \"" + server + "\"");
   FileWrite(handle, "  },");

   // Indicadores XAUUSD M15
   FileWrite(handle, "  \"indicators\": {");
   FileWrite(handle, "    \"XAUUSD\": {");
   FileWriteString(handle, ExportIndicators("XAUUSD", PERIOD_M15));
   FileWrite(handle, "");
   FileWrite(handle, "    },");
   FileWrite(handle, "    \"BTCUSD\": {");
   FileWriteString(handle, ExportIndicators("BTCUSD", PERIOD_M15));
   FileWrite(handle, "");
   FileWrite(handle, "    }");
   FileWrite(handle, "  },");

   // Posiciones abiertas
   FileWrite(handle, "  \"positions\": [");
   int total = OrdersTotal();
   for (int i = 0; i < total; i++) {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (OrderType() > OP_SELL) continue;

      string comma = (i < total - 1) ? "," : "";
      string tipo  = (OrderType() == OP_BUY) ? "long" : "short";

      FileWrite(handle, "    {");
      FileWrite(handle, "      \"ticket\": " + IntegerToString(OrderTicket()) + ",");
      FileWrite(handle, "      \"symbol\": \"" + OrderSymbol() + "\",");
      FileWrite(handle, "      \"type\": \"" + tipo + "\",");
      FileWrite(handle, "      \"lots\": " + DoubleToStr(OrderLots(), 2) + ",");
      FileWrite(handle, "      \"open_price\": " + DoubleToStr(OrderOpenPrice(), 5) + ",");
      FileWrite(handle, "      \"current_price\": " + DoubleToStr(OrderClosePrice(), 5) + ",");
      FileWrite(handle, "      \"sl\": " + DoubleToStr(OrderStopLoss(), 5) + ",");
      FileWrite(handle, "      \"tp\": " + DoubleToStr(OrderTakeProfit(), 5) + ",");
      FileWrite(handle, "      \"profit\": " + DoubleToStr(OrderProfit(), 2) + ",");
      FileWrite(handle, "      \"swap\": " + DoubleToStr(OrderSwap(), 2) + ",");
      FileWrite(handle, "      \"commission\": " + DoubleToStr(OrderCommission(), 2) + ",");
      FileWrite(handle, "      \"net_profit\": " + DoubleToStr(OrderProfit() + OrderSwap() + OrderCommission(), 2) + ",");
      FileWrite(handle, "      \"open_time\": \"" + TimeToStr(OrderOpenTime(), TIME_DATE|TIME_SECONDS) + "\",");
      FileWrite(handle, "      \"comment\": \"" + OrderComment() + "\"");
      FileWrite(handle, "    }" + comma);
   }
   FileWrite(handle, "  ],");

   // Historial reciente (ultimas 20 operaciones)
   FileWrite(handle, "  \"recent_history\": [");
   int histTotal = OrdersHistoryTotal();
   int count = 0;
   int maxHist = 20;
   for (int j = histTotal - 1; j >= 0 && count < maxHist; j--) {
      if (!OrderSelect(j, SELECT_BY_POS, MODE_HISTORY)) continue;
      if (OrderType() > OP_SELL) continue;

      string tipoH  = (OrderType() == OP_BUY) ? "long" : "short";
      string commaH = (count < maxHist - 1 && j > 0) ? "," : "";
      count++;

      FileWrite(handle, "    {");
      FileWrite(handle, "      \"ticket\": " + IntegerToString(OrderTicket()) + ",");
      FileWrite(handle, "      \"symbol\": \"" + OrderSymbol() + "\",");
      FileWrite(handle, "      \"type\": \"" + tipoH + "\",");
      FileWrite(handle, "      \"lots\": " + DoubleToStr(OrderLots(), 2) + ",");
      FileWrite(handle, "      \"open_price\": " + DoubleToStr(OrderOpenPrice(), 5) + ",");
      FileWrite(handle, "      \"close_price\": " + DoubleToStr(OrderClosePrice(), 5) + ",");
      FileWrite(handle, "      \"profit\": " + DoubleToStr(OrderProfit(), 2) + ",");
      FileWrite(handle, "      \"swap\": " + DoubleToStr(OrderSwap(), 2) + ",");
      FileWrite(handle, "      \"commission\": " + DoubleToStr(OrderCommission(), 2) + ",");
      FileWrite(handle, "      \"net_profit\": " + DoubleToStr(OrderProfit() + OrderSwap() + OrderCommission(), 2) + ",");
      FileWrite(handle, "      \"open_time\": \"" + TimeToStr(OrderOpenTime(), TIME_DATE|TIME_SECONDS) + "\",");
      FileWrite(handle, "      \"close_time\": \"" + TimeToStr(OrderCloseTime(), TIME_DATE|TIME_SECONDS) + "\"");
      FileWrite(handle, "    }" + commaH);
   }
   FileWrite(handle, "  ]");
   FileWrite(handle, "}");

   FileClose(handle);
}
