from typing import List

from .config import FREE_MODE
from .logging_setup import logger

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None


def fetch_url(url: str, timeout: float = 8.0) -> str:
    if requests is None:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        logger.debug("fetch_url error %s: %s", url, e)
    return ""


def duckduckgo_search(query: str, n: int = 3) -> List[str]:
    if requests is None or BeautifulSoup is None:
        return []
    q = query.strip()
    url = f"https://duckduckgo.com/html/?q={requests.utils.quote(q)}&kl=ua-uk&kp=1"
    html = fetch_url(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a.result__a"):
        href = a.get("href")
        if href and href.startswith("http") and "duckduckgo.com" not in href:
            links.append(href)
        if len(links) >= n:
            break
    return links


def extract_text_from_html(html: str, max_chars: int = 4000) -> str:
    if BeautifulSoup is None:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()[:max_chars]


def build_web_context(query: str, max_pages: int = 3) -> str:
    if FREE_MODE:
        return ""
    urls = duckduckgo_search(query, n=max_pages)
    chunks = []
    for u in urls:
        html = fetch_url(u)
        if not html:
            continue
        plain = extract_text_from_html(html)
        if plain:
            chunks.append(f"[{u}]\n{plain}")
    return "\n\n---\n\n".join(chunks)[:6000]
