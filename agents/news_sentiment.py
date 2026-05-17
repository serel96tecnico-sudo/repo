from datetime import datetime

import anthropic

from agents.base_agent import BaseAgent
from config import SENTIMENT_TOP_N
from data.news_fetcher import NewsFetcher
from models.schemas import TAResult, SentimentResult


SENTIMENT_SYSTEM = """You are a financial news analyst specializing in assessing news impact on swing trades.

Given recent news headlines for a stock, you will:
1. Determine overall sentiment: "positive", "negative", or "neutral"
2. Assign a sentiment score from -1.0 (very negative) to +1.0 (very positive)
3. Identify if there is a catalyst (earnings beat, analyst upgrade, product launch, contract win, FDA approval, etc.)
4. List any risk flags that could negatively impact the trade:
   - "earnings_imminent" (earnings within 5 trading days — adds gap risk)
   - "fda_decision_pending"
   - "legal_risk" (lawsuit, SEC investigation, fraud allegations)
   - "dilution_risk" (secondary offering, convertible notes)
   - "analyst_downgrade"
   - "macro_headwind" (tariffs, rate impact, regulatory)

Be conservative — if uncertain about risk, flag it.
Focus on what matters for a 2-10 day swing trade, not long-term fundamentals."""


class NewsSentimentAnalyst(BaseAgent):
    def __init__(self, client: anthropic.Anthropic, news_fetcher: NewsFetcher):
        super().__init__(client)
        self.news_fetcher = news_fetcher

    def run(self, ta_results: list) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        top = ta_results[:SENTIMENT_TOP_N]
        self.logger.info(f"Running sentiment analysis on {len(top)} tickers...")

        for i, ta in enumerate(top):
            self.logger.info(f"  Sentiment [{i+1}/{len(top)}]: {ta.ticker}")
            result = self._analyze_ticker_sentiment(ta.ticker, today)
            results.append(result)

        return results

    def _analyze_ticker_sentiment(self, ticker: str, today: str) -> SentimentResult:
        try:
            news_items = self.news_fetcher.fetch_all(ticker)

            if not news_items:
                return SentimentResult(
                    ticker=ticker,
                    analysis_date=today,
                    overall_sentiment="neutral",
                    sentiment_score=0.0,
                    sentiment_score_normalized=5.0,
                )

            news_text = "\n".join(
                f"- [{item.source}] {item.title} ({item.published})"
                for item in news_items[:20]
            )

            schema = (
                '{"overall_sentiment": "positive|negative|neutral", '
                '"sentiment_score": -1.0, '
                '"catalyst_found": true, '
                '"catalyst_description": "string", '
                '"risk_flags": ["earnings_imminent", ...], '
                '"reasoning": "string"}'
            )

            user_msg = f"""Stock: {ticker}
Recent news headlines:
{news_text}

Assess sentiment and risk for a 2-10 day swing trade."""

            resp = self._call_claude_json(SENTIMENT_SYSTEM, user_msg, schema_hint=schema)

            raw_score = float(resp.get("sentiment_score", 0.0))
            normalized = round((raw_score + 1.0) / 2.0 * 10.0, 2)

            return SentimentResult(
                ticker=ticker,
                analysis_date=today,
                news_items=[n.to_dict() for n in news_items],
                overall_sentiment=resp.get("overall_sentiment", "neutral"),
                sentiment_score=raw_score,
                catalyst_found=bool(resp.get("catalyst_found", False)),
                catalyst_description=resp.get("catalyst_description", ""),
                risk_flags=resp.get("risk_flags", []),
                sentiment_score_normalized=normalized,
            )

        except Exception as e:
            self.logger.error(f"Sentiment analysis failed for {ticker}: {e}")
            return SentimentResult(
                ticker=ticker,
                analysis_date=today,
                overall_sentiment="neutral",
                sentiment_score=0.0,
                sentiment_score_normalized=5.0,
            )
