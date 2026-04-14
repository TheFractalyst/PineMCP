#!/usr/bin/env python3
"""Scrape PineScript v6 docs using fetch + html2text.
No browser needed - uses TradingView's server-rendered HTML.
Usage: python scripts/dev/scrape_docs.py
"""
import gzip
import io
import os
import re
import time
import urllib.request
import urllib.error
import html

OUT_DIR = "/Users/fractalyst/pinescript_mcp/data/pinescriptv6/scraped_v6_docs"
BASE = "https://www.tradingview.com"

PAGES = [
    ("primer/first-steps", "/pine-script-docs/primer/first-steps"),
    ("primer/first-indicator", "/pine-script-docs/primer/first-indicator"),
    ("primer/next-steps", "/pine-script-docs/primer/next-steps"),
    ("language/arrays", "/pine-script-docs/language/arrays"),
    ("language/built-ins", "/pine-script-docs/language/built-ins"),
    ("language/conditional-structures", "/pine-script-docs/language/conditional-structures"),
    ("language/declaration-statements", "/pine-script-docs/language/declaration-statements"),
    ("language/enums", "/pine-script-docs/language/enums"),
    ("language/identifiers", "/pine-script-docs/language/identifiers"),
    ("language/loops", "/pine-script-docs/language/loops"),
    ("language/maps", "/pine-script-docs/language/maps"),
    ("language/matrices", "/pine-script-docs/language/matrices"),
    ("language/script-structure", "/pine-script-docs/language/script-structure"),
    ("language/type-system", "/pine-script-docs/language/type-system"),
    ("language/user-defined-functions", "/pine-script-docs/language/user-defined-functions"),
    ("language/variable-declarations", "/pine-script-docs/language/variable-declarations"),
    ("concepts/alerts", "/pine-script-docs/concepts/alerts"),
    ("concepts/bar-states", "/pine-script-docs/concepts/bar-states"),
    ("concepts/chart-information", "/pine-script-docs/concepts/chart-information"),
    ("concepts/inputs", "/pine-script-docs/concepts/inputs"),
    ("concepts/libraries", "/pine-script-docs/concepts/libraries"),
    ("concepts/non-standard-charts-data", "/pine-script-docs/concepts/non-standard-charts-data"),
    ("concepts/other-timeframes-and-data", "/pine-script-docs/concepts/other-timeframes-and-data"),
    ("concepts/repainting", "/pine-script-docs/concepts/repainting"),
    ("concepts/sessions", "/pine-script-docs/concepts/sessions"),
    ("concepts/strategies", "/pine-script-docs/concepts/strategies"),
    ("concepts/strings", "/pine-script-docs/concepts/strings"),
    ("concepts/time", "/pine-script-docs/concepts/time"),
    ("errors/overview", "/pine-script-docs/errors/overview"),
    ("errors/CE10101", "/pine-script-docs/errors/CE10101"),
    ("errors/CW10003", "/pine-script-docs/errors/CW10003"),
    ("errors/RE10139", "/pine-script-docs/errors/RE10139"),
    ("errors/RE10143", "/pine-script-docs/errors/RE10143"),
    ("faq/general", "/pine-script-docs/faq/general"),
    ("faq/programming", "/pine-script-docs/faq/programming"),
    ("faq/variables-and-operators", "/pine-script-docs/faq/variables-and-operators"),
    ("faq/functions", "/pine-script-docs/faq/functions"),
    ("faq/data-structures", "/pine-script-docs/faq/data-structures"),
    ("faq/indicators", "/pine-script-docs/faq/indicators"),
    ("faq/strategies", "/pine-script-docs/faq/strategies"),
    ("faq/alerts", "/pine-script-docs/faq/alerts"),
    ("faq/other-data-and-timeframes", "/pine-script-docs/faq/other-data-and-timeframes"),
    ("faq/strings-and-formatting", "/pine-script-docs/faq/strings-and-formatting"),
    ("faq/times-dates-and-sessions", "/pine-script-docs/faq/times-dates-and-sessions"),
    ("faq/visuals", "/pine-script-docs/faq/visuals"),
    ("faq/techniques", "/pine-script-docs/faq/techniques"),
    ("migration-guides/overview", "/pine-script-docs/migration-guides/overview"),
    ("migration-guides/to-pine-version-6", "/pine-script-docs/migration-guides/to-pine-version-6"),
    ("writing/limitations", "/pine-script-docs/writing/limitations"),
    ("writing/profiling-and-optimization", "/pine-script-docs/writing/profiling-and-optimization"),
    ("writing/style-guide", "/pine-script-docs/writing/style-guide"),
    ("writing/debugging", "/pine-script-docs/writing/debugging"),
    ("writing/publishing", "/pine-script-docs/writing/publishing"),
    ("release-notes", "/pine-script-docs/release-notes"),
    ("visuals/overview", "/pine-script-docs/visuals/overview"),
    ("visuals/plots", "/pine-script-docs/visuals/plots"),
    ("visuals/lines-and-boxes", "/pine-script-docs/visuals/lines-and-boxes"),
    ("visuals/tables", "/pine-script-docs/visuals/tables"),
    ("visuals/backgrounds", "/pine-script-docs/visuals/backgrounds"),
    ("visuals/bar-coloring", "/pine-script-docs/visuals/bar-coloring"),
    ("visuals/bar-plotting", "/pine-script-docs/visuals/bar-plotting"),
    ("visuals/colors", "/pine-script-docs/visuals/colors"),
    ("visuals/fills", "/pine-script-docs/visuals/fills"),
    ("visuals/levels", "/pine-script-docs/visuals/levels"),
    ("visuals/text-and-shapes", "/pine-script-docs/visuals/text-and-shapes"),
]


def html_to_md(html_text: str, url_path: str = "") -> str:
    """Convert HTML to rough markdown preserving code blocks and structure."""
    # Extract title
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""

    # Remove nav, footer, sidebar
    html_text = re.sub(r"<nav[^>]*>.*?</nav>", "", html_text, flags=re.DOTALL)
    html_text = re.sub(r"<footer[^>]*>.*?</footer>", "", html_text, flags=re.DOTALL)
    html_text = re.sub(r'<aside[^>]*>.*?</aside>', "", html_text, flags=re.DOTALL)
    html_text = re.sub(r"<header[^>]*>.*?</header>", "", html_text, flags=re.DOTALL)

    # Extract main content area
    main_m = re.search(
        r'<(?:main|article|div[^>]*class="[^"]*vp-doc[^"]*")[^>]*>(.*)</(?:main|article|div)>',
        html_text,
        re.DOTALL | re.IGNORECASE,
    )
    if main_m:
        html_text = main_m.group(1)

    # Preserve code blocks — TradingView uses two patterns:
    # 1. <div class="pine-colorizer ...">CODE</div>  (primary, used for PineScript examples)
    # 2. <pre><code class="language-X">CODE</code></pre>  (standard, used occasionally)
    code_blocks = []
    def save_code(m):
        lang_m = re.search(r'class="language-(\w+)"', m.group(0))
        lang = lang_m.group(1) if lang_m else "pine"
        code = re.sub(r"<[^>]+>", "", m.group(1))
        code = html.unescape(code)
        idx = len(code_blocks)
        code_blocks.append(f"\n```{lang}\n{code}\n```\n")
        return f"__CODEBLOCK_{idx}__"
    # Pattern 1: pine-colorizer divs
    html_text = re.sub(r'<div class="pine-colorizer[^"]*">(.*?)</div>', save_code, html_text, flags=re.DOTALL)
    # Pattern 2: standard pre>code blocks
    def save_pre(m):
        lang_m = re.search(r'class="language-(\w+)"', m.group(0))
        lang = lang_m.group(1) if lang_m else ""
        code = re.sub(r"<[^>]+>", "", m.group(1))
        code = html.unescape(code)
        idx = len(code_blocks)
        code_blocks.append(f"\n```{lang}\n{code}\n```\n")
        return f"__CODEBLOCK_{idx}__"
    html_text = re.sub(r"<pre[^>]*>(.*?)</pre>", save_pre, html_text, flags=re.DOTALL)

    # Inline code
    html_text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html_text, flags=re.DOTALL)

    # Headings
    for i in range(4, 0, -1):
        html_text = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, lvl=i: "\n" + "#" * lvl + " " + re.sub(r"<[^>]+>", "", m.group(1)).strip() + "\n",
            html_text,
            flags=re.DOTALL,
        )

    # Paragraphs
    html_text = re.sub(r"<p[^>]*>", "\n", html_text)
    html_text = re.sub(r"</p>", "\n", html_text)

    # Lists
    html_text = re.sub(r"<li[^>]*>", "- ", html_text)
    html_text = re.sub(r"</li>", "\n", html_text)

    # Tables
    html_text = re.sub(r"<t[hd][^>]*>", " | ", html_text)
    html_text = re.sub(r"</t[hd]>", "", html_text)

    # Bold/italic
    html_text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", html_text, flags=re.DOTALL)
    html_text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", html_text, flags=re.DOTALL)

    # Links
    html_text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", html_text, flags=re.DOTALL)

    # Remove remaining tags
    html_text = re.sub(r"<[^>]+>", "", html_text)

    # Decode HTML entities
    html_text = html.unescape(html_text)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        html_text = html_text.replace(f"__CODEBLOCK_{i}__", block)

    # Clean up whitespace
    html_text = re.sub(r"\n{3,}", "\n\n", html_text)
    html_text = re.sub(r"[ \t]+", " ", html_text)

    return f"# {title}\n\nSource: https://www.tradingview.com{url_path}\n\n{html_text.strip()}"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    saved = 0
    failed = 0

    print(f"Scraping {len(PAGES)} pages...")

    for name, url_path in PAGES:
        out_path = os.path.join(OUT_DIR, name + ".md")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        url = BASE + url_path
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) PineScriptMCP/1.0",
                "Accept": "text/html",
                "Accept-Encoding": "gzip, deflate, identity",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw_bytes = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw_bytes).decode("utf-8")
                elif resp.headers.get("Content-Encoding") == "deflate":
                    raw = raw_bytes.decode("utf-8")
                else:
                    raw = raw_bytes.decode("utf-8")

            md = html_to_md(raw, url_path)
            if len(md) > 200:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(md)
                saved += 1
                print(f"  OK {name} ({len(md)/1024:.1f}KB)")
            else:
                failed += 1
                print(f"  SKIP {name} (content too short: {len(md)} chars)")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {e}")

        time.sleep(0.3)  # Be polite

    print(f"\nDone: {saved} saved, {failed} failed out of {len(PAGES)} pages")


if __name__ == "__main__":
    main()
