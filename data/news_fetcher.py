import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger

logger = get_logger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


@dataclass
class NewsItem:
    title: str
    source: str
    published: str
    url: str = ""
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "published": self.published,
            "url": self.url,
            "summary": self.summary,
        }


class NewsFetcher:
    def __init__(self, delay_seconds: float = 2.0):
        self.delay = delay_seconds
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    def fetch_from_yahoo_rss(self, ticker: str) -> list:
        url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            items = []
            for item in root.findall(".//item")[:10]:
                title = item.findtext("title", "").strip()
                pub = item.findtext("pubDate", "").strip()
                link = item.findtext("link", "").strip()
                if title:
                    items.append(NewsItem(title=title, source="Yahoo Finance", published=pub, url=link))
            return items
        except Exception as e:
            logger.debug(f"Yahoo RSS failed for {ticker}: {e}")
            return []

    def fetch_from_finviz(self, ticker: str) -> list:
        url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&ta=1&p=d"
        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="fullview-news-outer")
            if not table:
                return []
            items = []
            for row in table.find_all("tr")[:15]:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    pub = cells[0].text.strip()
                    link_tag = cells[1].find("a")
                    if link_tag:
                        title = link_tag.text.strip()
                        href = link_tag.get("href", "")
                        source_tag = cells[1].find("span")
                        source = source_tag.text.strip() if source_tag else "Finviz"
                        if title:
                            items.append(NewsItem(title=title, source=source, published=pub, url=href))
            return items
        except Exception as e:
            logger.debug(f"Finviz scrape failed for {ticker}: {e}")
            return []

    def fetch_all(self, ticker: str) -> list:
        yahoo = self.fetch_from_yahoo_rss(ticker)
        time.sleep(self.delay)
        finviz = self.fetch_from_finviz(ticker)

        seen = set()
        combined = []
        for item in yahoo + finviz:
            key = item.title[:60].lower()
            if key not in seen:
                seen.add(key)
                combined.append(item)

        return combined[:20]
