//+------------------------------------------------------------------+
//| Broker3_Export.mq4                                               |
//| Exporta posiciones, balance e historial a JSON cada 5 segundos   |
//+------------------------------------------------------------------+
#property copyright "Trading Agent"
#property version   "1.0"
#property strict

extern int    UpdateSeconds = 5;
extern string OutputFile    = "broker3_positions.json";

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

void ExportData() {
   int handle = FileOpen(OutputFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if (handle == INVALID_HANDLE) return;

   double balance  = AccountBalance();
   double equity   = AccountEquity();
   double margin   = AccountMargin();
   double freeMargin = AccountFreeMargin();
   double profit   = AccountProfit();
   string currency = AccountCurrency();
   string server   = AccountServer();
   int    leverage = AccountLeverage();

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

   // Posiciones abiertas
   FileWrite(handle, "  \"positions\": [");
   int total = OrdersTotal();
   for (int i = 0; i < total; i++) {
      if (!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if (OrderType() > OP_SELL) continue; // solo market orders

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

   // Historial reciente (ultimas 20 operaciones cerradas)
   FileWrite(handle, "  \"recent_history\": [");
   int histTotal = OrdersHistoryTotal();
   int count = 0;
   int maxHist = 20;
   for (int j = histTotal - 1; j >= 0 && count < maxHist; j--) {
      if (!OrderSelect(j, SELECT_BY_POS, MODE_HISTORY)) continue;
      if (OrderType() > OP_SELL) continue;

      string tipoH = (OrderType() == OP_BUY) ? "long" : "short";
      string commaH = (count < maxHist - 1 && j > 0) ? "," : "";
      count++;

      FileWrite(handle, "    {");
      FileWrite(handle, "      \"ticket\": " + IntegerToString(OrderTicket()) + ",");
      FileWrite(handle, "      \"symbol\": \"" + OrderSymbol() + "\",");
      FileWrite(handle, "      \"type\": \"" + tipoH + "\",");
      FileWrite(handle, "      \"lots\": " + DoubleToStr(OrderLots(), 2) + ",");
      FileWrite(handle, "      \"open_price\": " + DoubleToStr(OrderOpenPrice(), 5) + ",");
      FileWrite(handle, "      \"close_price\": " + DoubleToStr(OrderClosePrice(), 5) + ",");
      FileWrite(handle, "      \"sl\": " + DoubleToStr(OrderStopLoss(), 5) + ",");
      FileWrite(handle, "      \"tp\": " + DoubleToStr(OrderTakeProfit(), 5) + ",");
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
