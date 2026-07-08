//+------------------------------------------------------------------+
//|                                               RegimeFeatures.mqh |
//|                                     RegimeScanner v2.0 (8 reg.)  |
//|  SPDX-License-Identifier: MIT                                    |
//|                                                                  |
//|  Motor de features COMPARTIDO entre el indicador (inferencia) y  |
//|  el script de exportación (entrenamiento). Garantiza paridad     |
//|  exacta de features Python <-> MQL5.                             |
//|                                                                  |
//|  ARQUITECTURA FACTORIZADA (clave para la fiabilidad):            |
//|    Eje Dirección  (3): UP / DOWN / NONE                          |
//|    Eje Volatilidad(4): QUIET / VOLATILE / EXPANSION / CRISIS     |
//|    Composición -> 8 regímenes. Las probabilidades conjuntas      |
//|    suman 1 por construcción (producto de simplex).               |
//|                                                                  |
//|  CONTRATO v2: 12 features en orden fijo. No reordenar ni añadir  |
//|  sin re-exportar dataset y re-entrenar los modelos ONNX.         |
//|                                                                  |
//|  Instalación: copiar a  <DataFolder>\MQL5\Include\               |
//+------------------------------------------------------------------+
#ifndef __REGIME_FEATURES_MQH__
#define __REGIME_FEATURES_MQH__

#define REGIME_FEATURE_COUNT 12
#define REGIME_COUNT          8
#define DIR_CLASSES           3
#define VOL_CLASSES           4
#define SQZ_STD_PERIOD       20   // periodo del stdev para el detector de squeeze
#define ATR_FAST_MEAN        63   // ventana del ATR "lento" para atr_ratio
#define SHOCK_LOOKBACK       10   // barras para el detector de saltos

//--- Regímenes compuestos (salida final del indicador)
enum ENUM_REGIME8
  {
   REG_BULL_QUIET  = 0,  // Tendencia alcista ordenada
   REG_BULL_VOL    = 1,  // Tendencia alcista volátil
   REG_BEAR_QUIET  = 2,  // Tendencia bajista ordenada
   REG_BEAR_VOL    = 3,  // Tendencia bajista volátil
   REG_RANGE_QUIET = 4,  // Rango tranquilo / mean reversion
   REG_RANGE_VOL   = 5,  // Rango volátil / whipsaw
   REG_BREAKOUT    = 6,  // Expansión de volatilidad / ruptura
   REG_CRISIS      = 7   // Volatilidad extrema + saltos
  };

//--- Eje de dirección
enum ENUM_DIR_AXIS  { DIR_UP=0, DIR_DOWN=1, DIR_NONE=2 };

//--- Eje de estado de volatilidad
enum ENUM_VOL_AXIS  { VOLS_QUIET=0, VOLS_VOLATILE=1, VOLS_EXPANSION=2, VOLS_CRISIS=3 };

//+------------------------------------------------------------------+
//| Vector de features v2 (contrato con los modelos ONNX):           |
//|  f[0]  er         Efficiency Ratio de Kaufman        [0..1]      |
//|  f[1]  adx_norm   ADX(14)/50 recortado               [0..1]      |
//|  f[2]  slope_norm pendiente LR en unidades ATR/0.20  [-1..1]     |
//|  f[3]  r2         R² de la regresión lineal          [0..1]      |
//|  f[4]  hurst      exponente de Hurst (R/S simple)    [0..1]      |
//|  f[5]  ac1        autocorrelación lag-1 de retornos  [-1..1]     |
//|  f[6]  vr         Variance Ratio(q) recortado        [0..2]      |
//|  f[7]  atr_pct    percentil de ATR(14), ventana W    [0..1]      |
//|  f[8]  atr_ratio  ATR / media(ATR, 63): expansión    [0..3]      |
//|  f[9]  sqz_pct    percentil de stdev(20): squeeze    [0..1]      |
//|  f[10] skew       asimetría de log-retornos          [-2..2]     |
//|  f[11] shock      máx |z| de retorno, últimas 10 b.  [0..6]      |
//+------------------------------------------------------------------+
class CRegimeFeatures
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   int               m_hATR;
   int               m_hADX;
   int               m_erP;
   int               m_slopeW;
   int               m_hurstW;
   int               m_atrRankW;
   int               m_vrQ;

   double            Clip(const double x,const double lo,const double hi) const
     {
      return MathMax(lo,MathMin(hi,x));
     }

public:
                     CRegimeFeatures(void) : m_hATR(INVALID_HANDLE),
                                             m_hADX(INVALID_HANDLE) {}

   //+---------------------------------------------------------------+
   bool              Init(const string symbol,const ENUM_TIMEFRAMES tf,
                          const int er_period=20,const int slope_win=50,
                          const int hurst_win=128,const int atr_rank_win=252,
                          const int vr_q=5)
     {
      m_symbol   = symbol;
      m_tf       = tf;
      m_erP      = MathMax(5,er_period);
      m_slopeW   = MathMax(10,slope_win);
      m_hurstW   = MathMax(32,hurst_win);
      m_atrRankW = MathMax(50,atr_rank_win);
      m_vrQ      = MathMax(2,vr_q);
      m_hATR     = iATR(symbol,tf,14);
      m_hADX     = iADX(symbol,tf,14);
      return(m_hATR!=INVALID_HANDLE && m_hADX!=INVALID_HANDLE);
     }

   //+---------------------------------------------------------------+
   void              Deinit(void)
     {
      if(m_hATR!=INVALID_HANDLE) { IndicatorRelease(m_hATR); m_hATR=INVALID_HANDLE; }
      if(m_hADX!=INVALID_HANDLE) { IndicatorRelease(m_hADX); m_hADX=INVALID_HANDLE; }
     }

   //+---------------------------------------------------------------+
   int               MinBars(void) const
     {
      int w=MathMax(m_hurstW+m_vrQ,
            MathMax(m_slopeW,
            MathMax(m_erP,m_atrRankW+SQZ_STD_PERIOD)));
      return(w+10);
     }

   //+---------------------------------------------------------------+
   //| Vector de features para la barra 'shift' (0 = en formación).   |
   //+---------------------------------------------------------------+
   bool              Compute(const int shift,double &f[])
     {
      if(ArraySize(f)<REGIME_FEATURE_COUNT)
         ArrayResize(f,REGIME_FEATURE_COUNT);

      const int need=MinBars();
      double close[];
      ArraySetAsSeries(close,true);
      if(CopyClose(m_symbol,m_tf,shift,need,close)!=need)
         return(false);

      //--- ATR: valor actual, percentil largo, ratio de expansión
      double atr[];
      ArraySetAsSeries(atr,true);
      if(CopyBuffer(m_hATR,0,shift,m_atrRankW,atr)!=m_atrRankW)
         return(false);
      const double atr_now=atr[0];
      if(atr_now<=0.0)
         return(false);

      int cnt=0;
      for(int i=0;i<m_atrRankW;i++)
         if(atr[i]<=atr_now) cnt++;
      const double atr_pct=(double)cnt/(double)m_atrRankW;

      double atr_slow=0.0;
      const int nslow=MathMin(ATR_FAST_MEAN,m_atrRankW);
      for(int i=0;i<nslow;i++)
         atr_slow+=atr[i];
      atr_slow/=nslow;
      const double atr_ratio=(atr_slow>0.0) ? Clip(atr_now/atr_slow,0.0,3.0) : 1.0;

      //--- ADX(14)
      double adx[];
      ArraySetAsSeries(adx,true);
      if(CopyBuffer(m_hADX,MAIN_LINE,shift,1,adx)!=1)
         return(false);
      const double adx_norm=Clip(adx[0]/50.0,0.0,1.0);

      //--- Efficiency Ratio (Kaufman)
      double path=0.0;
      for(int i=0;i<m_erP;i++)
         path+=MathAbs(close[i]-close[i+1]);
      const double er=(path>0.0) ? MathAbs(close[0]-close[m_erP])/path : 0.0;

      //--- Regresión lineal (cronológica): pendiente normalizada + R²
      double sx=0,sy=0,sxx=0,sxy=0,syy=0;
      for(int t=0;t<m_slopeW;t++)
        {
         const double x=(double)t;
         const double y=close[m_slopeW-1-t];
         sx+=x; sy+=y; sxx+=x*x; sxy+=x*y; syy+=y*y;
        }
      const double n  =(double)m_slopeW;
      const double den=n*sxx-sx*sx;
      double slope=0.0,r2=0.0;
      if(den>0.0)
        {
         slope=(n*sxy-sx*sy)/den;
         const double vy=n*syy-sy*sy;
         if(vy>0.0)
           {
            const double r=(n*sxy-sx*sy)/MathSqrt(den*vy);
            r2=r*r;
           }
        }
      const double slope_norm=Clip((slope/atr_now)/0.20,-1.0,1.0);

      //--- Autocorrelación lag-1 de retornos (ventana de la LR)
      double ac1=0.0;
        {
         const int m=m_slopeW-1;
         double r_[];
         ArrayResize(r_,m);
         double mu=0.0;
         for(int i=0;i<m;i++)
           {
            r_[i]=close[i]-close[i+1];
            mu+=r_[i];
           }
         mu/=m;
         double c0=0.0,c1=0.0;
         for(int i=0;i<m;i++)   c0+=(r_[i]-mu)*(r_[i]-mu);
         for(int i=0;i<m-1;i++) c1+=(r_[i]-mu)*(r_[i+1]-mu);
         if(c0>0.0)
            ac1=Clip(c1/c0,-1.0,1.0);
        }

      //--- Log-retornos de la ventana Hurst (reutilizados por
      //    Hurst, VR, skew y shock)
      const int nH=m_hurstW;
      double lr[];
      ArrayResize(lr,nH);
      double muH=0.0;
      for(int i=0;i<nH;i++)
        {
         const double a=close[i], b=close[i+1];
         lr[i]=(a>0.0 && b>0.0) ? MathLog(a/b) : 0.0;
         muH+=lr[i];
        }
      muH/=nH;

      //--- Hurst (R/S) + Variance Ratio(q)
      double hurst=0.5, vr=1.0, sigmaH=0.0;
        {
         double cum=0.0,mx=-DBL_MAX,mn=DBL_MAX,s2=0.0;
         for(int i=0;i<nH;i++)
           {
            cum+=(lr[i]-muH);
            if(cum>mx) mx=cum;
            if(cum<mn) mn=cum;
            s2+=(lr[i]-muH)*(lr[i]-muH);
           }
         const double S=MathSqrt(s2/nH);
         const double R=mx-mn;
         if(S>0.0 && R>0.0)
            hurst=Clip(MathLog(R/S)/MathLog((double)nH),0.0,1.0);
         sigmaH=S;

         const double var1=s2/nH;
         if(var1>0.0)
           {
            const int q=m_vrQ, mq=nH-q+1;
            const double muq=muH*q;
            double vq=0.0;
            for(int i=0;i<mq;i++)
              {
               double rq=0.0;
               for(int k=0;k<q;k++) rq+=lr[i+k];
               vq+=(rq-muq)*(rq-muq);
              }
            vq/=mq;
            vr=Clip(vq/(q*var1),0.0,2.0);
           }
        }

      //--- Skew de retornos (asimetría; discrimina crisis)
      double skew=0.0;
      if(sigmaH>0.0)
        {
         double m3=0.0;
         for(int i=0;i<nH;i++)
           {
            const double d=lr[i]-muH;
            m3+=d*d*d;
           }
         m3/=nH;
         skew=Clip(m3/(sigmaH*sigmaH*sigmaH),-2.0,2.0);
        }

      //--- Shock: máximo |z| de retorno en las últimas barras
      double shock=0.0;
      if(sigmaH>0.0)
        {
         const int nsk=MathMin(SHOCK_LOOKBACK,nH);
         for(int i=0;i<nsk;i++)
           {
            const double z=MathAbs(lr[i]-muH)/sigmaH;
            if(z>shock) shock=z;
           }
         shock=Clip(shock,0.0,6.0);
        }

      //--- Squeeze: percentil del stdev(20) de cierres en la ventana
      //    de ranking. Bajo = compresión (pre-breakout típico).
      double sqz_pct=0.5;
        {
         double sd[];
         ArrayResize(sd,m_atrRankW);
         for(int j=0;j<m_atrRankW;j++)
           {
            double s=0.0,ss=0.0;
            for(int k=0;k<SQZ_STD_PERIOD;k++)
              {
               const double c=close[j+k];
               s+=c; ss+=c*c;
              }
            const double mn2=s/SQZ_STD_PERIOD;
            const double v2=ss/SQZ_STD_PERIOD-mn2*mn2;
            sd[j]=(v2>0.0) ? MathSqrt(v2) : 0.0;
           }
         int c2=0;
         for(int j=0;j<m_atrRankW;j++)
            if(sd[j]<=sd[0]) c2++;
         sqz_pct=(double)c2/(double)m_atrRankW;
        }

      f[0]=er;        f[1]=adx_norm;  f[2]=slope_norm; f[3]=r2;
      f[4]=hurst;     f[5]=ac1;       f[6]=vr;         f[7]=atr_pct;
      f[8]=atr_ratio; f[9]=sqz_pct;   f[10]=skew;      f[11]=shock;
      return(true);
     }

   //+---------------------------------------------------------------+
   //| EJE DIRECCIÓN — heurística transparente (baseline sin ML).     |
   //| pd[3] = { P(UP), P(DOWN), P(NONE) }                            |
   //+---------------------------------------------------------------+
   void              DirectionProbs(const double &f[],double &pd[])
     {
      if(ArraySize(pd)<DIR_CLASSES)
         ArrayResize(pd,DIR_CLASSES);
      const double er=f[0], adxn=f[1], slope=f[2], r2=f[3];
      const double hurst=f[4], ac1=f[5], vr=f[6];

      const double T =0.35*er+0.35*adxn+0.30*r2;      // fuerza de tendencia
      const double mr=0.40*Clip((0.55-hurst)/0.25,0.0,1.0)
                     +0.30*Clip(-ac1/0.30,0.0,1.0)
                     +0.30*Clip((1.0-vr)/0.50,0.0,1.0); // evidencia reversión

      double s[DIR_CLASSES];
      s[DIR_UP]  =T*Clip(slope,0.0,1.0);
      s[DIR_DOWN]=T*Clip(-slope,0.0,1.0);
      s[DIR_NONE]=Clip(1.0-T*MathAbs(slope),0.0,1.0)*(0.5+0.5*mr);

      const double sum=s[0]+s[1]+s[2];
      if(sum<1e-9) { pd[0]=pd[1]=pd[2]=1.0/3.0; return; }
      for(int i=0;i<DIR_CLASSES;i++)
         pd[i]=s[i]/sum;
     }

   //+---------------------------------------------------------------+
   //| EJE VOLATILIDAD — heurística transparente (baseline sin ML).   |
   //| pv[4] = { P(QUIET), P(VOLATILE), P(EXPANSION), P(CRISIS) }     |
   //| Nota: f[10] (skew) se reserva al modelo ML; aquí no se usa.    |
   //+---------------------------------------------------------------+
   void              VolStateProbs(const double &f[],double &pv[])
     {
      if(ArraySize(pv)<VOL_CLASSES)
         ArrayResize(pv,VOL_CLASSES);
      const double atrp=f[7], ratio=f[8], sqz=f[9], shock=f[11];

      // Crisis: percentil de vol extremo Y saltos (ambos, multiplicativo)
      const double c=Clip((atrp-0.85)/0.15,0.0,1.0)
                    *Clip((shock-2.0)/2.0,0.0,1.0);

      // Expansión: ratio ATR corto/largo disparado, percentil largo aún
      // no extremo (energía liberándose), con salto que lo confirme
      const double e=Clip((ratio-1.20)/0.60,0.0,1.0)
                    *Clip((0.85-atrp)/0.45,0.0,1.0)
                    *Clip(shock/2.5,0.0,1.0);

      // Quiet: percentil bajo, reforzado por compresión (squeeze)
      const double q=Clip((0.50-atrp)/0.50,0.0,1.0)
                    *(0.6+0.4*Clip((0.5-sqz)/0.5,0.0,1.0));

      // Volatile: percentil alto sostenido
      const double v=Clip((atrp-0.45)/0.45,0.0,1.0);

      double s[VOL_CLASSES];
      s[VOLS_QUIET]    =q*(1.0-e)*(1.0-c);
      s[VOLS_VOLATILE] =v*(1.0-e)*(1.0-c);
      s[VOLS_EXPANSION]=e*(1.0-c);
      s[VOLS_CRISIS]   =c;

      const double sum=s[0]+s[1]+s[2]+s[3];
      if(sum<1e-9) { for(int i=0;i<VOL_CLASSES;i++) pv[i]=0.25; return; }
      for(int i=0;i<VOL_CLASSES;i++)
         pv[i]=s[i]/sum;
     }

   //+---------------------------------------------------------------+
   //| COMPOSICIÓN pd(3) ⊗ pv(4) -> p8(8).                            |
   //| Propiedad: si Σpd=1 y Σpv=1, entonces Σp8=1 exactamente.       |
   //+---------------------------------------------------------------+
   void              ComposeJoint(const double &pd[],const double &pv[],double &p8[])
     {
      if(ArraySize(p8)<REGIME_COUNT)
         ArrayResize(p8,REGIME_COUNT);
      p8[REG_BULL_QUIET] =pd[DIR_UP]  *pv[VOLS_QUIET];
      p8[REG_BULL_VOL]   =pd[DIR_UP]  *pv[VOLS_VOLATILE];
      p8[REG_BEAR_QUIET] =pd[DIR_DOWN]*pv[VOLS_QUIET];
      p8[REG_BEAR_VOL]   =pd[DIR_DOWN]*pv[VOLS_VOLATILE];
      p8[REG_RANGE_QUIET]=pd[DIR_NONE]*pv[VOLS_QUIET];
      p8[REG_RANGE_VOL]  =pd[DIR_NONE]*pv[VOLS_VOLATILE];
      p8[REG_BREAKOUT]   =pv[VOLS_EXPANSION]; // marginal sobre dirección
      p8[REG_CRISIS]     =pv[VOLS_CRISIS];    // marginal sobre dirección
     }
  };
#endif // __REGIME_FEATURES_MQH__
