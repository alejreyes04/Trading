//+------------------------------------------------------------------+
//|                                         ExportRegimeFeatures.mq5 |
//|                                     RegimeScanner v2.0 (8 reg.)  |
//|  SPDX-License-Identifier: MIT                                    |
//|                                                                  |
//|  Script: exporta OHLC + 12 features a CSV para entrenar los dos  |
//|  modelos de eje (dirección y volatilidad) en Python.             |
//|                                                                  |
//|  Las features salen del MISMO motor (RegimeFeatures.mqh) que usa |
//|  el indicador en inferencia: paridad garantizada por diseño.     |
//|                                                                  |
//|  Salida: <DataFolder>\MQL5\Files\regime_features.csv             |
//|  Uso: arrastrar al gráfico del símbolo/timeframe objetivo.       |
//|  Requiere: RegimeFeatures.mqh en MQL5\Include\                   |
//+------------------------------------------------------------------+
#property copyright "RegimeScanner (MIT)"
#property version   "2.00"
#property script_show_inputs

#include <RegimeFeatures.mqh>

input int    InpBars       = 20000;                  // Barras a exportar
input string InpFileName   = "regime_features.csv";  // Archivo de salida
input int    InpERPeriod   = 20;                     // Periodo Efficiency Ratio
input int    InpSlopeWin   = 50;                     // Ventana regresión / AC1
input int    InpHurstWin   = 128;                    // Ventana Hurst / VR / skew
input int    InpATRRankWin = 252;                    // Ventana percentiles
input int    InpVRq        = 5;                      // Agregación Variance Ratio

//+------------------------------------------------------------------+
void OnStart()
  {
   CRegimeFeatures feat;
   if(!feat.Init(_Symbol,_Period,InpERPeriod,InpSlopeWin,
                 InpHurstWin,InpATRRankWin,InpVRq))
     {
      Print("ExportRegimeFeatures: error creando handles ATR/ADX");
      return;
     }

   const int avail=Bars(_Symbol,_Period);
   const int minb =feat.MinBars();
   const int total=MathMin(InpBars,avail-minb-2);
   if(total<500)
     {
      PrintFormat("Historia insuficiente: %d barras disponibles "
                  "(mínimo requerido ~%d).",avail,minb+502);
      feat.Deinit();
      return;
     }

   const int fh=FileOpen(InpFileName,FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(fh==INVALID_HANDLE)
     {
      PrintFormat("No se pudo abrir '%s' (err %d)",InpFileName,GetLastError());
      feat.Deinit();
      return;
     }

   // Cabecera: contrato v2 con train_regime_onnx.py (orden fijo)
   FileWrite(fh,"time","open","high","low","close",
             "er","adx_norm","slope_norm","r2","hurst","ac1","vr","atr_pct",
             "atr_ratio","sqz_pct","skew","shock");

   double f[REGIME_FEATURE_COUNT];
   int written=0;

   // Orden cronológico; shift>=1 excluye la barra en formación
   for(int shift=total;shift>=1;shift--)
     {
      if(IsStopped())
         break;
      if(!feat.Compute(shift,f))
         continue;

      FileWrite(fh,
                (long)iTime(_Symbol,_Period,shift),
                DoubleToString(iOpen(_Symbol,_Period,shift),_Digits),
                DoubleToString(iHigh(_Symbol,_Period,shift),_Digits),
                DoubleToString(iLow(_Symbol,_Period,shift),_Digits),
                DoubleToString(iClose(_Symbol,_Period,shift),_Digits),
                f[0],f[1],f[2],f[3],f[4],f[5],
                f[6],f[7],f[8],f[9],f[10],f[11]);
      written++;
      if(written%2000==0)
         PrintFormat("Exportadas %d/%d filas...",written,total);
     }

   FileClose(fh);
   PrintFormat("OK: %d filas -> MQL5\\Files\\%s  (%s %s)",
               written,InpFileName,_Symbol,EnumToString(Period()));
   feat.Deinit();
  }
//+------------------------------------------------------------------+
