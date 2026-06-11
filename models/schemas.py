from dataclasses import dataclass, field, asdict
from typing import Optional
import json


@dataclass
class MarketConditions:
    date: str
    spy_trend: str
    qqq_trend: str
    vix_level: float
    regime: str
    spy_price: float = 0.0
    qqq_price: float = 0.0
    btc_price: float = 0.0
    btc_change_pct: float = 0.0
    eth_price: float = 0.0
    eth_change_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MarketConditions":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ScanCandidate:
    ticker: str
    company_name: str
    sector: str
    price: float
    volume_ratio: float
    price_change_pct: float
    market_cap: float
    avg_volume_20d: int
    high_52w: float
    low_52w: float
    scan_signals: list = field(default_factory=list)
    initial_score: float = 0.0
    setup_direction: str = "long"  # "long" | "short" — dirección del setup según scoring

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScanCandidate":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ScanResult:
    date: str
    candidates: list
    total_screened: int
    market_conditions: MarketConditions
    scan_notes: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "candidates": [c.to_dict() for c in self.candidates],
            "total_screened": self.total_screened,
            "market_conditions": self.market_conditions.to_dict(),
            "scan_notes": self.scan_notes,
        }


@dataclass
class FundamentalResult:
    ticker: str
    company_name: str = ""
    sector: str = ""
    earnings_date: str = "-"
    earnings_days_away: int = 999
    earnings_risk: bool = False
    short_float_pct: float = 0.0
    short_ratio: float = 0.0
    target_price: float = 0.0
    analyst_recom: float = 3.0
    insider_trans_pct: float = 0.0
    inst_trans_pct: float = 0.0
    forward_pe: float = 0.0
    debt_equity: float = 0.0
    profit_margin: float = 0.0
    beta: float = 1.0
    fundamental_score: float = 5.0
    risk_flags: list = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FundamentalResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TAResult:
    ticker: str
    analysis_date: str
    indicators: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    support_levels: list = field(default_factory=list)
    resistance_levels: list = field(default_factory=list)
    direction: str = "long"
    pattern_detected: str = "none"
    entry_trigger: str = ""
    ta_score: float = 0.0
    ta_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TAResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class NewsItem:
    title: str
    source: str
    published: str
    url: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SentimentResult:
    ticker: str
    analysis_date: str
    news_items: list = field(default_factory=list)
    overall_sentiment: str = "neutral"
    sentiment_score: float = 0.0
    catalyst_found: bool = False
    catalyst_description: str = ""
    risk_flags: list = field(default_factory=list)
    sentiment_score_normalized: float = 5.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SentimentResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RiskResult:
    ticker: str
    entry_price: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_per_share: float
    rr_ratio_1: float
    rr_ratio_2: float
    position_size_pct: float
    position_size_shares: int
    max_loss_dollars: float
    holding_days_estimate: str = "5-10 trading days"
    risk_score: float = 5.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RiskResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FinalCandidate:
    rank: int
    ticker: str
    company_name: str
    composite_score: float
    recommendation: str
    scan_data: ScanCandidate = None
    ta_data: TAResult = None
    fundamental_data: FundamentalResult = None
    sentiment_data: SentimentResult = None
    risk_data: RiskResult = None
    summary: str = ""
    current_price: float = 0.0

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "ticker": self.ticker,
            "company_name": self.company_name,
            "composite_score": self.composite_score,
            "recommendation": self.recommendation,
            "current_price": self.current_price,
            "scan_data": self.scan_data.to_dict() if self.scan_data else None,
            "ta_data": self.ta_data.to_dict() if self.ta_data else None,
            "fundamental_data": self.fundamental_data.to_dict() if self.fundamental_data else None,
            "sentiment_data": self.sentiment_data.to_dict() if self.sentiment_data else None,
            "risk_data": self.risk_data.to_dict() if self.risk_data else None,
            "summary": self.summary,
        }


@dataclass
class DailyReport:
    report_date: str
    market_conditions: MarketConditions
    candidates: list
    report_text: str
    report_json_path: str
    report_txt_path: str
    generation_time_seconds: float
    total_scanned: int
    total_analyzed: int

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date,
            "market_conditions": self.market_conditions.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "report_text": self.report_text,
            "report_json_path": self.report_json_path,
            "report_txt_path": self.report_txt_path,
            "generation_time_seconds": self.generation_time_seconds,
            "total_scanned": self.total_scanned,
            "total_analyzed": self.total_analyzed,
        }
