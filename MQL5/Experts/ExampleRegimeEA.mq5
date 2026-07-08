//+------------------------------------------------------------------+
//|                                              ExampleRegimeEA.mq5 |
//|                                     RegimeScanner v2.0 (8 reg.)  |
//|  SPDX-License-Identifier: MIT                                    |
//|                                                                  |
//|  ESQUELETO de EA que enruta módulos de estrategia por régimen.   |
//|                                                                  |
//|  DELIBERADAMENTE no contiene lógica de entrada real: enviarte    |
//|  una "estrategia" genérica sin validar sería deshonesto. Lo que  |
//|  sí contiene es la arquitectura correcta de gating:              |
//|                                                                  |
//|    régimen (barra cerrada) -> módulo permitido -> escala riesgo  |
//|                                                                  |
//|  Sustituye los TODO por tus señales (p. ej. tu breakout de rango |
//|  asiático sólo en REG_BREAKOUT/tendencia, tu scalper de reversión|
//|  sólo en REG_RANGE_QUIET) y valida en el Strategy Tester el      |
//|  delta REAL: misma estrategia con gating vs sin gating.          |
//|                                                                  |
//|  Requiere: RegimeScanner.ex5 compilado en MQL5\Indicators\       |
//|            RegimeFeatures.mqh y RegimeReader.mqh en MQL5\Include\|
//+------------------------------------------------------------------+
#property copyright "RegimeScanner (MIT)"
#property version   "2.00"

#include <RegimeReader.mqh>

//--- inputs de gating (optimizables en el tester)
input bool   InpUseONNX      = false;   // Usar modelos ONNX en el indicador
input double InpMinConfTrend = 0.35;    // Confianza mínima para módulo tendencia
input double InpMinConfMR    = 0.35;    // Confianza mínima para módulo reversión
input double InpMinConfBrk   = 0.40;    // Confianza mínima para módulo breakout
input double InpBaseRiskPct  = 1.0;     // Riesgo base por operación (%)

CRegimeReader g_regime;
datetime      g_last_bar = 0;
int           g_last_reg = -1;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(!g_regime.Init(_Symbol,_Period,
                     20,50,128,252,5,          // ventanas (= defaults indicador)
                     InpUseONNX))
     {
      Print("ExampleRegimeEA: no se pudo crear el handle de RegimeScanner");
      return(INIT_FAILED);
     }
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   g_regime.Release();
  }

//+------------------------------------------------------------------+
//| Nueva barra: toda la lógica opera sobre la barra CERRADA (shift=1)|
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   const datetime t=iTime(_Symbol,_Period,0);
   if(t==g_last_bar)
      return(false);
   g_last_bar=t;
   return(true);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   if(!IsNewBar())
      return;

   const int    reg =g_regime.Regime(1);
   const double conf=g_regime.Confidence(1);
   if(reg<0)
      return; // indicador aún sin datos

   //--- log de transiciones (útil para auditar el gating en el tester)
   if(reg!=g_last_reg)
     {
      PrintFormat("Régimen: %s (conf %.2f)",g_regime.RegimeName(1),conf);
      g_last_reg=reg;
     }

   //--- 1) risk-off absoluto
   if(g_regime.RiskOff(1))
     {
      ManageCrisis();
      return;
     }

   //--- 2) escala de riesgo por régimen (optimizable; ver advertencia
   //       en RegimeReader::SuggestedRiskScale)
   const double risk_pct=InpBaseRiskPct*g_regime.SuggestedRiskScale(1);

   //--- 3) enrutado de módulos por régimen
   if(g_regime.AllowTrendFollowing(1,InpMinConfTrend))
      ModuleTrendFollowing(g_regime.TrendDir(1),risk_pct,conf);
   else if(g_regime.AllowMeanReversion(1,InpMinConfMR))
      ModuleMeanReversion(risk_pct,conf);
   else if(g_regime.AllowBreakout(1,InpMinConfBrk))
      ModuleBreakout(risk_pct,conf);
   // REG_RANGE_VOL cae aquí sin módulo: whipsaw -> mejor no operar,
   // o gestiona sólo posiciones abiertas.
   ManageOpenPositions(reg,risk_pct);
  }

//+------------------------------------------------------------------+
//| Módulos de estrategia (TODO: tu lógica real)                     |
//+------------------------------------------------------------------+
void ModuleTrendFollowing(const int dir,const double risk_pct,const double conf)
  {
   // TODO: señal de continuación en dirección 'dir' (+1/-1).
   // Ejemplos de encaje: pullback a media, ruptura de swing, Donchian.
   // 'conf' puede escalar tamaño adicionalmente o filtrar entradas.
  }

void ModuleMeanReversion(const double risk_pct,const double conf)
  {
   // TODO: señal de reversión SÓLO en RANGE_QUIET.
   // Ejemplos: bandas + rechazo, RSI extremo con filtro de rango.
  }

void ModuleBreakout(const double risk_pct,const double conf)
  {
   // TODO: señal de ruptura durante EXPANSION.
   // Nota: BREAKOUT es transitorio por definición; usa el eje de
   // dirección (ProbBull/ProbBear) para orientar el lado.
  }

void ManageCrisis()
  {
   // TODO: política de crisis: cerrar/reducir, ampliar stops NO,
   // suspender nuevas entradas. Decisión de diseño tuya.
  }

void ManageOpenPositions(const int reg,const double risk_pct)
  {
   // TODO: trailing/salidas condicionadas al régimen. Ejemplo típico:
   // en tendencia quiet, trailing amplio; si el régimen degrada a
   // RANGE_VOL o CRISIS, endurecer salidas.
  }
//+------------------------------------------------------------------+
