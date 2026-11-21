#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
BÖLÜM 8: PİYASA REJİM DEDEKTÖRÜ & OTONOM ÖĞRENME MOTORU (autonomous_brain.py)
================================================================================
GÖREVLER:
  1. 4H & 1D Piyasa Rejim Dedektörü (MarketRegimeDetector):
     BTC'nin 4 saatlik ve günlük yapısından EMA20/EMA50 ve ATR volatilitesini
     hesaplar. Piyasayı 4 net rejime ayırır:
     - BULL_TREND     : Boğa trendi (Geniş trailing, tam kâr sürüşü)
     - SIDEWAYS       : Yatay bant (Standart Sweep & Reclaim ayarları)
     - BEAR_DOWNTREND : Ayı/Düşüş (Sıkı L2 filtresi, erken başabaş +%0.8)
     - HIGH_VOL_CHOP  : Yüksek volatilite testeresi (Uzun cooldown, sıkı giriş)
  2. Otonom Geri Bildirim & Öğrenme Döngüsü (AutonomousLearningEngine):
     orchestrator_state.json içindeki trade_history ve signal_snapshot
     kayıtlarını analiz eder. Kazanan/kaybeden işlemlerin eşik farklarını
     ölçerek aşırı öğrenmeyi (overfitting) engelleyen güvenli sınırlarla
     autonomous_weights.json dosyasını günceller.
Sıfır Dış Bağımlılık: Saf Python standart kütüphanesi.
================================================================================
"""

import os
import time
import json
import socket
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request

socket.setdefaulttimeout(10)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [BÖLÜM 8 - BEYİN] %(message)s"
)

BRAIN_CONFIG = {
    "BASE_URL": "https://api.binance.com/api/v3",
    "STATE_FILE": "orchestrator_state.json",
    "WEIGHTS_FILE": "autonomous_weights.json",
    "REGIME_CACHE_SEC": 300,           # 5 dakikada bir 4H/1D rejim güncellemesi
    "MIN_TRADES_FOR_LEARNING": 10,     # Otonom eşik güncellemesi için asgari işlem sayısı
    "HIGH_VOL_ATR_PCT": 0.022,         # 4H ATR %2.2 üzerindeyse yüksek volatilite
}

# Rejimlere göre temel güvenli parametre profilleri
REGIME_PROFILES: Dict[str, Dict[str, float]] = {
    "BULL_TREND": {
        "break_even_trigger_pct": 0.015,
        "trailing_activation_pct": 0.022,
        "min_l2_bid_ratio": 0.52,
        "stop_cooldown_minutes": 20,
        "risk_multiplier": 1.0,
    },
    "SIDEWAYS": {
        "break_even_trigger_pct": 0.012,
        "trailing_activation_pct": 0.018,
        "min_l2_bid_ratio": 0.55,
        "stop_cooldown_minutes": 30,
        "risk_multiplier": 1.0,
    },
    "BEAR_DOWNTREND": {
        "break_even_trigger_pct": 0.008,   # Düşüş trendinde +%0.8'de riski sıfırla
        "trailing_activation_pct": 0.014,  # Kârı daha erken kilitle
        "min_l2_bid_ratio": 0.60,          # Daha güçlü alıcı tahtası şartı
        "stop_cooldown_minutes": 45,       # Düşen bıçakta daha uzun soğuma
        "risk_multiplier": 0.75,
    },
    "HIGH_VOL_CHOP": {
        "break_even_trigger_pct": 0.010,
        "trailing_activation_pct": 0.016,
        "min_l2_bid_ratio": 0.62,
        "stop_cooldown_minutes": 60,
        "risk_multiplier": 0.65,
    }
}


@dataclass
class MarketRegimeReport:
    regime: str                  # BULL_TREND, SIDEWAYS, BEAR_DOWNTREND, HIGH_VOL_CHOP
    btc_price: float
    ema20_4h: float
    ema50_4h: float
    atr_pct_4h: float
    daily_trend: str             # UP, DOWN, FLAT
    confidence: float
    reason: str
    recommended_params: Dict[str, float]
    timestamp: float


class _BrainDataClient:
    @staticmethod
    def fetch_json(url: str, timeout: int = 6):
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    @staticmethod
    def get_klines(symbol: str, interval: str, limit: int) -> List[list]:
        url = f"{BRAIN_CONFIG['BASE_URL']}/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = _BrainDataClient.fetch_json(url)
        return data if isinstance(data, list) else []


def _calc_ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))
    return ema


def _calc_atr_pct(klines: List[list], period: int = 14) -> float:
    if len(klines) < period + 1:
        return 0.01
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i - 1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    recent_tr = trs[-period:]
    atr = sum(recent_tr) / len(recent_tr)
    last_close = float(klines[-1][4])
    return (atr / last_close) if last_close > 0 else 0.01


class MarketRegimeDetector:
    _cached_report: Optional[MarketRegimeReport] = None
    _last_update: float = 0.0

    @classmethod
    def detect(cls, force_refresh: bool = False) -> MarketRegimeReport:
        now = time.time()
        if (not force_refresh) and cls._cached_report and (now - cls._last_update < BRAIN_CONFIG["REGIME_CACHE_SEC"]):
            return cls._cached_report

        klines_4h = _BrainDataClient.get_klines("BTCUSDT", "4h", 60)
        klines_1d = _BrainDataClient.get_klines("BTCUSDT", "1d", 25)

        if len(klines_4h) < 50 or len(klines_1d) < 20:
            fallback = MarketRegimeReport(
                regime="SIDEWAYS", btc_price=0.0, ema20_4h=0.0, ema50_4h=0.0,
                atr_pct_4h=0.0, daily_trend="FLAT", confidence=0.0,
                reason="4H/1D veri alınamadı, güvenli varsayılan (SIDEWAYS) devrede.",
                recommended_params=dict(REGIME_PROFILES["SIDEWAYS"]),
                timestamp=now
            )
            return fallback

        closes_4h = [float(k[4]) for k in klines_4h]
        closes_1d = [float(k[4]) for k in klines_1d]

        btc_price = closes_4h[-1]
        ema20_4h = _calc_ema(closes_4h, 20)
        ema50_4h = _calc_ema(closes_4h, 50)
        atr_pct_4h = _calc_atr_pct(klines_4h, 14)

        ema10_1d = _calc_ema(closes_1d, 10)
        ema20_1d = _calc_ema(closes_1d, 20)
        if btc_price > ema10_1d > ema20_1d:
            daily_trend = "UP"
        elif btc_price < ema10_1d < ema20_1d:
            daily_trend = "DOWN"
        else:
            daily_trend = "FLAT"

        # Rejim Sınıflandırması
        ema_gap_pct = (ema20_4h - ema50_4h) / ema50_4h if ema50_4h > 0 else 0.0

        if btc_price < ema20_4h and ema20_4h < ema50_4h:
            regime = "BEAR_DOWNTREND"
            confidence = 0.85 if daily_trend == "DOWN" else 0.65
            reason = (
                f"4H Düşüş Trendi (Fiyat {btc_price:.0f} < EMA20 {ema20_4h:.0f} < EMA50 {ema50_4h:.0f}) | "
                f"Günlük: {daily_trend} | ATR: %{atr_pct_4h*100:.2f}"
            )
        elif btc_price > ema20_4h and ema20_4h > ema50_4h and ema_gap_pct > 0.005:
            regime = "BULL_TREND"
            confidence = 0.85 if daily_trend == "UP" else 0.65
            reason = (
                f"4H Yükseliş Trendi (Fiyat {btc_price:.0f} > EMA20 {ema20_4h:.0f} > EMA50 {ema50_4h:.0f}) | "
                f"Günlük: {daily_trend} | ATR: %{atr_pct_4h*100:.2f}"
            )
        elif atr_pct_4h >= BRAIN_CONFIG["HIGH_VOL_ATR_PCT"]:
            regime = "HIGH_VOL_CHOP"
            confidence = 0.75
            reason = f"Yüksek Volatilite Testeresi (4H ATR: %{atr_pct_4h*100:.2f}, Yön Belirsiz)"
        else:
            regime = "SIDEWAYS"
            confidence = 0.70
            reason = f"Yatay / Konsolidasyon Bandı (4H EMA Farkı: %{ema_gap_pct*100:+.2f} | ATR: %{atr_pct_4h*100:.2f})"

        report = MarketRegimeReport(
            regime=regime,
            btc_price=round(btc_price, 2),
            ema20_4h=round(ema20_4h, 2),
            ema50_4h=round(ema50_4h, 2),
            atr_pct_4h=round(atr_pct_4h, 5),
            daily_trend=daily_trend,
            confidence=confidence,
            reason=reason,
            recommended_params=dict(REGIME_PROFILES[regime]),
            timestamp=now
        )
        cls._cached_report = report
        cls._last_update = now
        return report


class AutonomousLearningEngine:
    @staticmethod
    def load_trade_history(state_file: str = BRAIN_CONFIG["STATE_FILE"]) -> List[dict]:
        if not os.path.exists(state_file):
            return []
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("trade_history", [])
        except Exception:
            return []

    @staticmethod
    def analyze_and_update_weights(regime_report: MarketRegimeReport) -> dict:
        history = AutonomousLearningEngine.load_trade_history()
        params = dict(regime_report.recommended_params)

        total = len(history)
        wins = [t for t in history if t.get("net_pnl_usdt", 0) > 0]
        losses = [t for t in history if t.get("net_pnl_usdt", 0) <= 0]
        win_rate = (len(wins) / total * 100.0) if total > 0 else 0.0

        learning_notes = []
        learning_active = False

        if total >= BRAIN_CONFIG["MIN_TRADES_FOR_LEARNING"]:
            learning_active = True

            # 1. Kazanan vs Kaybeden L2 Alıcı Oranı Karşılaştırması
            win_l2 = [t["signal_snapshot"]["l2_bid_ratio"] for t in wins if "l2_bid_ratio" in t.get("signal_snapshot", {})]
            loss_l2 = [t["signal_snapshot"]["l2_bid_ratio"] for t in losses if "l2_bid_ratio" in t.get("signal_snapshot", {})]

            if win_l2 and loss_l2:
                avg_win_l2 = sum(win_l2) / len(win_l2)
                avg_loss_l2 = sum(loss_l2) / len(loss_l2)
                if avg_win_l2 > avg_loss_l2 + 0.03:
                    # Güvenli sınırlar içinde (0.50 - 0.68) eşiği yukarı çek
                    suggested_l2 = round(min(0.68, max(0.50, (avg_win_l2 + avg_loss_l2) / 2.0)), 3)
                    params["min_l2_bid_ratio"] = max(params["min_l2_bid_ratio"], suggested_l2)
                    learning_notes.append(f"L2 Alıcı eşiği otonom sıkılaştırıldı -> %{params['min_l2_bid_ratio']*100:.1f}")

            # 2. Stop / Phantom Abort Yoğunluğu Kontrolü
            recent_10 = history[-10:]
            recent_stops = sum(1 for t in recent_10 if t.get("exit_type") in ("FAILSAFE_STOP", "PHANTOM_ABORT"))
            if recent_stops >= 6:
                params["stop_cooldown_minutes"] = min(90, int(params["stop_cooldown_minutes"] * 1.5))
                params["break_even_trigger_pct"] = max(0.007, round(params["break_even_trigger_pct"] - 0.002, 4))
                learning_notes.append("Son 10 işlemde yüksek stop oranı: Cooldown artırıldı, başabaş erkene çekildi.")
        else:
            remaining = BRAIN_CONFIG["MIN_TRADES_FOR_LEARNING"] - total
            learning_notes.append(f"Öğrenme için veri toplanıyor ({total}/{BRAIN_CONFIG['MIN_TRADES_FOR_LEARNING']} işlem - {remaining} işlem kaldı).")

        payload = {
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "regime": asdict(regime_report),
            "performance_summary": {
                "total_trades": total,
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "win_rate_pct": round(win_rate, 2),
                "learning_active": learning_active
            },
            "active_parameters": params,
            "learning_notes": learning_notes
        }

        try:
            with open(BRAIN_CONFIG["WEIGHTS_FILE"], "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"[AĞIRLIK KAYIT HATASI] {e}")

        return payload


if __name__ == "__main__":
    print("=" * 75)
    print("  BÖLÜM 8: PİYASA REJİM DEDEKTÖRÜ & OTONOM ÖĞRENME MOTORU TESTİ")
    print("=" * 75)
    regime = MarketRegimeDetector.detect(force_refresh=True)
    result = AutonomousLearningEngine.analyze_and_update_weights(regime)

    print(f"  Aktif Piyasa Rejimi : {regime.regime} (Güven: %{regime.confidence*100:.0f})")
    print(f"  BTC Fiyatı          : ${regime.btc_price:,.2f}")
    print(f"  4H EMA20 / EMA50    : ${regime.ema20_4h:,.2f} / ${regime.ema50_4h:,.2f}")
    print(f"  4H ATR Volatilite   : %{regime.atr_pct_4h*100:.2f} | Günlük Trend: {regime.daily_trend}")
    print(f"  Teşhis              : {regime.reason}")
    print("-" * 75)
    print(f"  Aktif Parametreler  : {json.dumps(result['active_parameters'], ensure_ascii=False)}")
    for note in result["learning_notes"]:
        print(f"  Öğrenme Notu        : {note}")
    print("=" * 75)
    print(f"  -> {BRAIN_CONFIG['WEIGHTS_FILE']} dosyası güncellendi.")
