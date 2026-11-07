import base64
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup


def unwrap(url):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if "duckduckgo.com" in (parsed.hostname or ""):
        return query.get("uddg", [url])[0]
    if (parsed.hostname or "").endswith("bing.com") and query.get("u", [""])[0].startswith("a1"):
        encoded = query["u"][0][2:]
        try:
            return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except (ValueError, UnicodeError):
            pass
    return url


def search_results(body, base, maximum):
    soup = BeautifulSoup(body, "html.parser")
    results, seen = [], set()
    for block in soup.select("li.b_algo, .result, tr:has(a.result-link)"):
        link = block.select_one("h2 > a, a.result__a, a.result-link")
        if not link or not link.get("href"):
            continue
        url = unwrap(urljoin(base, link["href"]))
        if urlsplit(url).scheme not in {"http", "https"} or url in seen:
            continue
        title = link.get_text(" ", strip=True)
        if not title:
            continue
        snippet = block.select_one(".b_caption p, .result__snippet, .result-snippet")
        results.append(
            {
                "title": title[:300],
                "url": url,
                "snippet": snippet.get_text(" ", strip=True)[:700] if snippet else "",
            }
        )
        seen.add(url)
        if len(results) >= maximum:
            break
    return results


async def search(http, query, maximum):
    warnings = []
    for provider, endpoint in (
        ("Bing", "https://cn.bing.com/search"),
        ("DuckDuckGo", "https://html.duckduckgo.com/html/"),
    ):
        try:
            body, _, url = await http.get(endpoint + "?" + urlencode({"q": query, "count": maximum}))
            results = search_results(body, url, maximum)
            if results:
                return {"query": query, "provider": provider, "results": results, "warnings": warnings}
            warnings.append(f"{provider} 未返回可解析结果")
        except Exception as exc:
            warnings.append(f"{provider} 请求失败：{type(exc).__name__}")
    return {"query": query, "provider": "none", "results": [], "warnings": warnings}


def readable(body, content_type):
    if "html" in content_type:
        soup = BeautifulSoup(body, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        for element in soup.select("script, style, nav, footer, noscript, svg"):
            element.decompose()
        text = soup.get_text("\n", strip=True)
    elif content_type.startswith("text/") or any(t in content_type for t in ("json", "xml")):
        title, text = "", body.decode("utf-8", errors="replace")
    else:
        raise ValueError("该资源不是网页或文本，请使用 download_url")
    return {"title": title[:300], "text": text[:20000], "truncated": len(text) > 20000}
