//+------------------------------------------------------------------+
//|                                                RegimeScanner.mq5 |
//|                                     RegimeScanner v2.0 (8 reg.)  |
//|  SPDX-License-Identifier: MIT                                    |
//|                                                                  |
//|  Detector de régimen de mercado FACTORIZADO con IA embebida:     |
//|    Eje Dirección  (3 clases) x Eje Volatilidad (4 clases)        |
//|    -> 8 regímenes compuestos.                                    |
//|                                                                  |
//|  Cada eje tiene motor dual:                                      |
//|   - Ensemble estadístico transparente (siempre disponible)       |
//|   - Modelo ONNX entrenado en Python (fallback por eje si falta)  |
//|                                                                  |
//|  CONTRATO DE BUFFERS (estable, para consumo desde EAs):          |
//|   0 = confianza (prob. del régimen candidato)                    |
//|   1 = régimen confirmado 0..7 (ENUM_REGIME8)                     |
//|   2..9 = P(régimen 0..7), suman 1                                |
//|  Ver RegimeReader.mqh para un wrapper listo para EAs.            |
//|                                                                  |
//|  Regímenes/colores:                                              |
//|   0 Lime      Bull Quiet      4 Silver    Range Quiet            |
//|   1 SeaGreen  Bull Volatile   5 Goldenrod Range Volatile         |
//|   2 Tomato    Bear Quiet      6 DeepSky   Breakout/Expansión     |
//|   3 FireBrick Bear Volatile   7 Magenta   Crisis                 |
//|                                                                  |
//|  Requiere: RegimeFeatures.mqh en MQL5\Include\                   |
//+------------------------------------------------------------------+
#property copyright   "RegimeScanner (MIT)"
#property version     "2.00"
#property description "Detector factorizado de 8 regímenes: ensemble + dual ONNX"

#property indicator_separate_window
#property indicator_buffers 10
#property indicator_plots   9
#property indicator_minimum 0.0
#property indicator_maximum 1.05

#property indicator_label1  "Regimen"
#property indicator_type1   DRAW_COLOR_HISTOGRAM
#property indicator_color1  clrLime,clrSeaGreen,clrTomato,clrFireBrick,clrSilver,clrGoldenrod,clrDeepSkyBlue,clrMagenta
#property indicator_width1  3

#property indicator_label2  "P(BullQuiet)"
#property indicator_type2   DRAW_NONE
#property indicator_label3  "P(BullVol)"
#property indicator_type3   DRAW_NONE
#property indicator_label4  "P(BearQuiet)"
#property indicator_type4   DRAW_NONE
#property indicator_label5  "P(BearVol)"
#property indicator_type5   DRAW_NONE
#property indicator_label6  "P(RangeQuiet)"
#property indicator_type6   DRAW_NONE
#property indicator_label7  "P(RangeVol)"
#property indicator_type7   DRAW_NONE
#property indicator_label8  "P(Breakout)"
#property indicator_type8   DRAW_NONE
#property indicator_label9  "P(Crisis)"
#property indicator_type9   DRAW_NONE

#include <RegimeFeatures.mqh>

//--- inputs (EL ORDEN ES CONTRATO con RegimeReader.mqh / iCustom) --
input group "Ventanas de cálculo (deben coincidir con el export)"
input int    InpERPeriod    = 20;     // 1. Periodo Efficiency Ratio
input int    InpSlopeWin    = 50;     // 2. Ventana regresión / AC1
input int    InpHurstWin    = 128;    // 3. Ventana Hurst / VR / skew / shock
input int    InpATRRankWin  = 252;    // 4. Ventana percentiles ATR y squeeze
input int    InpVRq         = 5;      // 5. Agregación Variance Ratio (q)

input group "Clasificación"
input bool   InpUseONNX     = false;              // 6. Usar modelos ONNX
input string InpDirModel    = "regime_dir.onnx";  // 7. Modelo eje dirección
input string InpVolModel    = "regime_vol.onnx";  // 8. Modelo eje volatilidad
input double InpMinProb     = 0.30;               // 9. Prob. mínima para cambiar (conjunto de 8)
input int    InpConfirmBars = 3;                  // 10. Barras de confirmación
input int    InpMaxHistory  = 3000;               // 11. Barras históricas
input bool   InpAlerts      = false;              // 12. Alertar cambios

//--- buffers ------------------------------------------------------
double BufValue[];
double BufColor[];
double BufP0[]; double BufP1[]; double BufP2[]; double BufP3[];
double BufP4[]; double BufP5[]; double BufP6[]; double BufP7[];

//--- globales -----------------------------------------------------
CRegimeFeatures g_feat;
long g_onnx_dir=INVALID_HANDLE;  bool g_dir_ok=false;  int g_dir_err=0;
long g_onnx_vol=INVALID_HANDLE;  bool g_vol_ok=false;  int g_vol_err=0;

string g_name[REGIME_COUNT]=
  {
   "BULL QUIET","BULL VOLATILE","BEAR QUIET","BEAR VOLATILE",
   "RANGE QUIET","RANGE VOLATILE","BREAKOUT / EXPANSION","CRISIS"
  };

//+------------------------------------------------------------------+
long LoadAxisModel(const string file,const int n_classes,bool &ok_flag)
  {
   const long h=OnnxCreate(file,ONNX_DEFAULT);
   if(h==INVALID_HANDLE)
     {
      PrintFormat("RegimeScanner: no se pudo cargar '%s' (err %d). "
                  "Ese eje usará el ensemble estadístico.",file,GetLastError());
      return(INVALID_HANDLE);
     }
   // Contrato con train_regime_onnx.py (Pipeline sklearn, zipmap=False):
   //   input  0: float32 [1, REGIME_FEATURE_COUNT]
   //   output 0: label   int64   [1]
   //   output 1: probs   float32 [1, n_classes]
   const long in_shape[] ={1,REGIME_FEATURE_COUNT};
   const long out_label[]={1};
   long out_probs[2]; out_probs[0]=1; out_probs[1]=n_classes;
   if(OnnxSetInputShape(h,0,in_shape) &&
      OnnxSetOutputShape(h,0,out_label) &&
      OnnxSetOutputShape(h,1,out_probs))
     {
      ok_flag=true;
      PrintFormat("RegimeScanner: '%s' cargado (%d clases).",file,n_classes);
      return(h);
     }
   PrintFormat("RegimeScanner: shapes inválidos en '%s' (err %d). Fallback.",
               file,GetLastError());
   OnnxRelease(h);
   return(INVALID_HANDLE);
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0,BufValue,INDICATOR_DATA);
   SetIndexBuffer(1,BufColor,INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2,BufP0,INDICATOR_DATA);
   SetIndexBuffer(3,BufP1,INDICATOR_DATA);
   SetIndexBuffer(4,BufP2,INDICATOR_DATA);
   SetIndexBuffer(5,BufP3,INDICATOR_DATA);
   SetIndexBuffer(6,BufP4,INDICATOR_DATA);
   SetIndexBuffer(7,BufP5,INDICATOR_DATA);
   SetIndexBuffer(8,BufP6,INDICATOR_DATA);
   SetIndexBuffer(9,BufP7,INDICATOR_DATA);
   PlotIndexSetDouble(0,PLOT_EMPTY_VALUE,0.0);
   IndicatorSetString(INDICATOR_SHORTNAME,"RegimeScanner8");
   IndicatorSetInteger(INDICATOR_DIGITS,2);

   if(!g_feat.Init(_Symbol,_Period,InpERPeriod,InpSlopeWin,
                   InpHurstWin,InpATRRankWin,InpVRq))
     {
      Print("RegimeScanner: error creando handles ATR/ADX");
      return(INIT_FAILED);
     }

   if(InpUseONNX)
     {
      g_onnx_dir=LoadAxisModel(InpDirModel,DIR_CLASSES,g_dir_ok);
      g_onnx_vol=LoadAxisModel(InpVolModel,VOL_CLASSES,g_vol_ok);
     }
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   g_feat.Deinit();
   if(g_onnx_dir!=INVALID_HANDLE) { OnnxRelease(g_onnx_dir); g_onnx_dir=INVALID_HANDLE; }
   if(g_onnx_vol!=INVALID_HANDLE) { OnnxRelease(g_onnx_vol); g_onnx_vol=INVALID_HANDLE; }
   Comment("");
  }

//+------------------------------------------------------------------+
//| Inferencia ONNX del eje dirección (3 clases)                     |
//+------------------------------------------------------------------+
bool PredictDir(const double &f[],double &pd[])
  {
   static float inp[REGIME_FEATURE_COUNT];
   static long  lbl[1];
   static float prb[DIR_CLASSES];
   for(int i=0;i<REGIME_FEATURE_COUNT;i++) inp[i]=(float)f[i];
   if(!OnnxRun(g_onnx_dir,ONNX_NO_CONVERSION,inp,lbl,prb))
     {
      if(++g_dir_err>10)
        {
         g_dir_ok=false;
         PrintFormat("RegimeScanner: ONNX dirección deshabilitado (err %d).",GetLastError());
        }
      return(false);
     }
   g_dir_err=0;
   for(int i=0;i<DIR_CLASSES;i++) pd[i]=(double)prb[i];
   return(true);
  }

//+------------------------------------------------------------------+
//| Inferencia ONNX del eje volatilidad (4 clases)                   |
//+------------------------------------------------------------------+
bool PredictVol(const double &f[],double &pv[])
  {
   static float inp[REGIME_FEATURE_COUNT];
   static long  lbl[1];
   static float prb[VOL_CLASSES];
   for(int i=0;i<REGIME_FEATURE_COUNT;i++) inp[i]=(float)f[i];
   if(!OnnxRun(g_onnx_vol,ONNX_NO_CONVERSION,inp,lbl,prb))
     {
      if(++g_vol_err>10)
        {
         g_vol_ok=false;
         PrintFormat("RegimeScanner: ONNX volatilidad deshabilitado (err %d).",GetLastError());
        }
      return(false);
     }
   g_vol_err=0;
   for(int i=0;i<VOL_CLASSES;i++) pv[i]=(double)prb[i];
   return(true);
  }

//+------------------------------------------------------------------+
double ProbAt(const int bar,const int k)
  {
   switch(k)
     {
      case 0: return BufP0[bar];
      case 1: return BufP1[bar];
      case 2: return BufP2[bar];
      case 3: return BufP3[bar];
      case 4: return BufP4[bar];
      case 5: return BufP5[bar];
      case 6: return BufP6[bar];
      case 7: return BufP7[bar];
     }
   return(0.0);
  }
//+------------------------------------------------------------------+
void SetProbs(const int bar,const double &p[])
  {
   BufP0[bar]=p[0]; BufP1[bar]=p[1]; BufP2[bar]=p[2]; BufP3[bar]=p[3];
   BufP4[bar]=p[4]; BufP5[bar]=p[5]; BufP6[bar]=p[6]; BufP7[bar]=p[7];
  }
//+------------------------------------------------------------------+
void ZeroProbs(const int bar)
  {
   BufP0[bar]=0; BufP1[bar]=0; BufP2[bar]=0; BufP3[bar]=0;
   BufP4[bar]=0; BufP5[bar]=0; BufP6[bar]=0; BufP7[bar]=0;
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,const int prev_calculated,
                const datetime &time[],const double &open[],
                const double &high[],const double &low[],
                const double &close[],const long &tick_volume[],
                const long &volume[],const int &spread[])
  {
   const int minb=g_feat.MinBars();
   if(rates_total<minb+2)
      return(0);

   int start;
   if(prev_calculated==0)
     {
      start=MathMax(minb,rates_total-InpMaxHistory);
      ArrayInitialize(BufValue,0.0);
      ArrayInitialize(BufColor,(double)REG_RANGE_QUIET);
      ArrayInitialize(BufP0,0.0); ArrayInitialize(BufP1,0.0);
      ArrayInitialize(BufP2,0.0); ArrayInitialize(BufP3,0.0);
      ArrayInitialize(BufP4,0.0); ArrayInitialize(BufP5,0.0);
      ArrayInitialize(BufP6,0.0); ArrayInitialize(BufP7,0.0);
     }
   else
      start=prev_calculated-1;

   double f[REGIME_FEATURE_COUNT];
   double pd[DIR_CLASSES];
   double pv[VOL_CLASSES];
   double p8[REGIME_COUNT];

   for(int i=start;i<rates_total;i++)
     {
      const int shift=rates_total-1-i;

      if(!g_feat.Compute(shift,f))
        {
         BufValue[i]=0.0;
         BufColor[i]=(i>0) ? BufColor[i-1] : (double)REG_RANGE_QUIET;
         ZeroProbs(i);
         continue;
        }

      //--- eje dirección: ONNX si disponible, si no ensemble
      bool ok_dir=false;
      if(g_dir_ok) ok_dir=PredictDir(f,pd);
      if(!ok_dir)  g_feat.DirectionProbs(f,pd);

      //--- eje volatilidad: ONNX si disponible, si no ensemble
      bool ok_vol=false;
      if(g_vol_ok) ok_vol=PredictVol(f,pv);
      if(!ok_vol)  g_feat.VolStateProbs(f,pv);

      //--- composición factorizada -> 8 probabilidades (suman 1)
      g_feat.ComposeJoint(pd,pv,p8);
      SetProbs(i,p8);

      //--- candidato = argmax conjunto
      int cand=0;
      double best=p8[0];
      for(int k=1;k<REGIME_COUNT;k++)
         if(p8[k]>best) { best=p8[k]; cand=k; }

      //--- histéresis (InpConfirmBars consecutivas, prob >= InpMinProb)
      const int prev=(i>0) ? (int)BufColor[i-1] : cand;
      int conf=prev;
      if(cand!=prev && best>=InpMinProb)
        {
         bool okc=true;
         for(int b=1;b<InpConfirmBars && okc;b++)
           {
            const int j=i-b;
            if(j<0) { okc=false; break; }
            int cj=0;
            double bj=ProbAt(j,0);
            for(int k=1;k<REGIME_COUNT;k++)
              {
               const double pk=ProbAt(j,k);
               if(pk>bj) { bj=pk; cj=k; }
              }
            if(cj!=cand || bj<InpMinProb)
               okc=false;
           }
         if(okc)
            conf=cand;
        }
      BufColor[i]=(double)conf;
      BufValue[i]=best;

      //--- alerta en cambio de régimen sobre barra recién cerrada
      if(InpAlerts && i==rates_total-2 && i>0 && conf!=prev)
         Alert(StringFormat("%s %s | Régimen -> %s (p=%.2f)",
               _Symbol,EnumToString(Period()),g_name[conf],best));
     }

   //--- overlay informativo
   const int last=rates_total-1;
   Comment(StringFormat(
      "RegimeScanner v2 [%s %s]\nRégimen: %s  (conf %.2f)\n"
      "Bull  Q/V: %.2f / %.2f   Bear Q/V: %.2f / %.2f\n"
      "Range Q/V: %.2f / %.2f   Brk: %.2f   Crisis: %.2f\n"
      "Motores  ->  Dirección: %s | Volatilidad: %s",
      _Symbol,EnumToString(Period()),
      g_name[(int)BufColor[last]],BufValue[last],
      BufP0[last],BufP1[last],BufP2[last],BufP3[last],
      BufP4[last],BufP5[last],BufP6[last],BufP7[last],
      g_dir_ok ? "ONNX" : "Ensemble",
      g_vol_ok ? "ONNX" : "Ensemble"));

   return(rates_total);
  }
//+------------------------------------------------------------------+
