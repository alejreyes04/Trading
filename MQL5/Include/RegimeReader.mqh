//+------------------------------------------------------------------+
//|                                                 RegimeReader.mqh |
//|                                     RegimeScanner v2.0 (8 reg.)  |
//|  SPDX-License-Identifier: MIT                                    |
//|                                                                  |
//|  Wrapper de consumo para EAs: encapsula iCustom() sobre          |
//|  RegimeScanner y expone una API limpia de régimen/probabilidades.|
//|                                                                  |
//|  IMPORTANTE: el orden de parámetros de iCustom debe coincidir    |
//|  EXACTAMENTE con los inputs del indicador (contrato v2.0).       |
//|                                                                  |
//|  Uso mínimo en un EA:                                            |
//|    #include <RegimeReader.mqh>                                   |
//|    CRegimeReader regime;                                         |
//|    int OnInit(){ return regime.Init(_Symbol,_Period)             |
//|                         ? INIT_SUCCEEDED : INIT_FAILED; }        |
//|    // en OnTick, sobre barra cerrada:                            |
//|    int r = regime.Regime(1);                                     |
//|    if(regime.AllowTrendFollowing(1)) { ... }                     |
//|                                                                  |
//|  Instalación: copiar a  <DataFolder>\MQL5\Include\               |
//+------------------------------------------------------------------+
#ifndef __REGIME_READER_MQH__
#define __REGIME_READER_MQH__

#include <RegimeFeatures.mqh>   // enums ENUM_REGIME8 / ejes

class CRegimeReader
  {
private:
   int               m_handle;

   double            Buf(const int buffer,const int shift) const
     {
      double b[1];
      if(CopyBuffer(m_handle,buffer,shift,1,b)!=1)
         return(-1.0);
      return(b[0]);
     }

public:
                     CRegimeReader(void) : m_handle(INVALID_HANDLE) {}
                    ~CRegimeReader(void) { Release(); }

   //+---------------------------------------------------------------+
   //| Los parámetros y su ORDEN replican los inputs del indicador.   |
   //+---------------------------------------------------------------+
   bool              Init(const string symbol,const ENUM_TIMEFRAMES tf,
                          const int    er_period    =20,
                          const int    slope_win    =50,
                          const int    hurst_win    =128,
                          const int    atr_rank_win =252,
                          const int    vr_q         =5,
                          const bool   use_onnx     =false,
                          const string dir_model    ="regime_dir.onnx",
                          const string vol_model    ="regime_vol.onnx",
                          const double min_prob     =0.30,
                          const int    confirm_bars =3,
                          const int    max_history  =3000)
     {
      m_handle=iCustom(symbol,tf,"RegimeScanner",
                       er_period,slope_win,hurst_win,atr_rank_win,vr_q,
                       use_onnx,dir_model,vol_model,
                       min_prob,confirm_bars,max_history,
                       false /*alerts: siempre off desde EA*/);
      return(m_handle!=INVALID_HANDLE);
     }

   void              Release(void)
     {
      if(m_handle!=INVALID_HANDLE)
        {
         IndicatorRelease(m_handle);
         m_handle=INVALID_HANDLE;
        }
     }

   int               Handle(void) const { return(m_handle); }

   //+---------------------------------------------------------------+
   //| Lecturas básicas (shift=1 -> última barra CERRADA, recomendado)|
   //+---------------------------------------------------------------+
   int               Regime(const int shift=1) const
     {
      const double v=Buf(1,shift);
      return(v<0.0) ? -1 : (int)v;
     }

   double            Confidence(const int shift=1) const
     {
      return(Buf(0,shift));
     }

   double            Prob(const int regime,const int shift=1) const
     {
      if(regime<0 || regime>=REGIME_COUNT)
         return(-1.0);
      return(Buf(2+regime,shift));
     }

   //+---------------------------------------------------------------+
   //| Vistas por eje (marginales de la conjunta)                     |
   //+---------------------------------------------------------------+
   double            ProbBull(const int shift=1)  const { return Prob(REG_BULL_QUIET,shift)+Prob(REG_BULL_VOL,shift); }
   double            ProbBear(const int shift=1)  const { return Prob(REG_BEAR_QUIET,shift)+Prob(REG_BEAR_VOL,shift); }
   double            ProbRange(const int shift=1) const { return Prob(REG_RANGE_QUIET,shift)+Prob(REG_RANGE_VOL,shift); }

   bool              IsBull(const int shift=1)     const { const int r=Regime(shift); return(r==REG_BULL_QUIET  || r==REG_BULL_VOL); }
   bool              IsBear(const int shift=1)     const { const int r=Regime(shift); return(r==REG_BEAR_QUIET  || r==REG_BEAR_VOL); }
   bool              IsRange(const int shift=1)    const { const int r=Regime(shift); return(r==REG_RANGE_QUIET || r==REG_RANGE_VOL); }
   bool              IsQuiet(const int shift=1)    const { const int r=Regime(shift); return(r==REG_BULL_QUIET || r==REG_BEAR_QUIET || r==REG_RANGE_QUIET); }
   bool              IsVolatile(const int shift=1) const { const int r=Regime(shift); return(r==REG_BULL_VOL || r==REG_BEAR_VOL || r==REG_RANGE_VOL); }
   bool              IsBreakout(const int shift=1) const { return(Regime(shift)==REG_BREAKOUT); }
   bool              IsCrisis(const int shift=1)   const { return(Regime(shift)==REG_CRISIS); }

   //--- +1 alcista, -1 bajista, 0 sin dirección/especial
   int               TrendDir(const int shift=1) const
     {
      if(IsBull(shift)) return(1);
      if(IsBear(shift)) return(-1);
      return(0);
     }

   //+---------------------------------------------------------------+
   //| Puertas (gates) para módulos de estrategia                     |
   //+---------------------------------------------------------------+
   bool              AllowTrendFollowing(const int shift=1,const double min_conf=0.30) const
     {
      const int r=Regime(shift);
      const bool ok=(r==REG_BULL_QUIET || r==REG_BULL_VOL ||
                     r==REG_BEAR_QUIET || r==REG_BEAR_VOL);
      return(ok && Confidence(shift)>=min_conf);
     }

   bool              AllowMeanReversion(const int shift=1,const double min_conf=0.30) const
     {
      return(Regime(shift)==REG_RANGE_QUIET && Confidence(shift)>=min_conf);
     }

   bool              AllowBreakout(const int shift=1,const double min_conf=0.30) const
     {
      return(Regime(shift)==REG_BREAKOUT && Confidence(shift)>=min_conf);
     }

   bool              RiskOff(const int shift=1) const
     {
      return(Regime(shift)==REG_CRISIS);
     }

   //+---------------------------------------------------------------+
   //| Escala de riesgo SUGERIDA por régimen [0..1].                  |
   //| ADVERTENCIA: punto de partida heurístico, NO calibrado a tu    |
   //| estrategia. Optimízala en el tester como cualquier parámetro.  |
   //+---------------------------------------------------------------+
   double            SuggestedRiskScale(const int shift=1) const
     {
      switch(Regime(shift))
        {
         case REG_BULL_QUIET:  return(1.00);
         case REG_BEAR_QUIET:  return(1.00);
         case REG_BULL_VOL:    return(0.50);
         case REG_BEAR_VOL:    return(0.50);
         case REG_RANGE_QUIET: return(0.75);
         case REG_RANGE_VOL:   return(0.25);
         case REG_BREAKOUT:    return(0.50);
         case REG_CRISIS:      return(0.00);
        }
      return(0.0);
     }

   //--- nombre legible del régimen
   string            RegimeName(const int shift=1) const
     {
      static const string names[REGIME_COUNT]=
        {
         "BULL QUIET","BULL VOLATILE","BEAR QUIET","BEAR VOLATILE",
         "RANGE QUIET","RANGE VOLATILE","BREAKOUT","CRISIS"
        };
      const int r=Regime(shift);
      return(r>=0 && r<REGIME_COUNT) ? names[r] : "N/A";
     }
  };
#endif // __REGIME_READER_MQH__
