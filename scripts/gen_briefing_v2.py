#!/usr/bin/env python3
"""Generate Daily Briefing issue (v2: dual edition + Haiku 4.5 + prose summary + breaking news).

Reads /tmp/candidates.json (produced by fetch_candidates.py).
Writes data/<edition>.json, data/latest.json, data/issues/<date>-<edition>.json,
and updates data/archive.json.

Usage:
    python3 scripts/gen_briefing_v2.py [--edition morning|evening]

If --edition is omitted, edition is inferred from Asia/Taipei hour:
    hour < 14 → morning,  else → evening.
"""
import argparse, json, os, sys, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Load OpenRouter creds ---------------------------------------------------
env_file = Path.home() / ".hermes/credentials/openrouter.env"
for line in env_file.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

from openai import OpenAI
client = OpenAI(api_key=os.environ.get("API_KEY"), base_url="https://openrouter.ai/api/v1")
MODEL = "anthropic/claude-haiku-4.5"

# --- Args & edition ----------------------------------------------------------
TPE = timezone(timedelta(hours=8))
def infer_edition() -> str:
    return "morning" if datetime.now(TPE).hour < 14 else "evening"

ap = argparse.ArgumentParser()
ap.add_argument("--edition", choices=["morning", "evening"], default=None)
args = ap.parse_args()
EDITION = args.edition or infer_edition()

REPO = Path("/home/hermes/Daily-briefing")
data = json.load(open("/tmp/candidates.json"))
date_taipei = data["date_taipei"]
weather_report = data["weather_report"]
cbc = data["candidates_by_category"]

archive = json.load(open(REPO / "data/archive.json"))
key_for_today = f"{date_taipei}-{EDITION}"
existing = [i for i in archive["issues"] if i.get("key") == key_for_today]
if existing:
    issue_number = existing[0]["issue_number"]
else:
    issue_number = max((i["issue_number"] for i in archive["issues"]), default=0) + 1

# --- Balanced selection — target 15 articles ---------------------------------
TARGET = {
    "ai-ml": 3, "ai-tools": 1, "research-papers": 2, "security": 1,
    "tech-product": 2, "dev": 1, "vc-business": 2, "science": 1,
    "world": 1, "taiwan": 1,
}
selected = {}
overflow = 0
for cat, n in TARGET.items():
    avail = cbc.get(cat, [])
    selected[cat] = avail[:min(n, len(avail))]
    overflow += (n - len(selected[cat]))
if overflow > 0:
    pools = [(c, [a for a in cbc.get(c, []) if a not in selected[c]]) for c in TARGET]
    pools.sort(key=lambda x: -len(x[1]))
    for c, extras in pools:
        if overflow <= 0: break
        take = min(overflow, len(extras))
        selected[c].extend(extras[:take])
        overflow -= take

articles_input = []
idx = 1
for cat, arts in selected.items():
    for a in arts:
        articles_input.append({
            "id": f"{date_taipei}-{EDITION[0]}{idx:03d}",
            "category": cat,
            "title_orig": a.get("title", ""),
            "summary_orig": (a.get("summary") or "")[:600],
            "url": a.get("url"),
            "source_name": a.get("source_name") or a.get("feed_name"),
            "published_at": a.get("published_at"),
            "credibility_tier": a.get("credibility_tier", "secondary"),
        })
        idx += 1

edition_zh = "早報" if EDITION == "morning" else "晚報"
edition_en = "Morning Edition" if EDITION == "morning" else "Evening Edition"

# --- Stage 1: write the issue prose ------------------------------------------
prompt = f"""You are the editor of a bilingual Daily Briefing ({edition_zh} / {edition_en}) for an AI Engineer at Taiwan AI Academy.
Audience reads it quickly. Focus on WHAT the new technology is, WHY it matters, and HOW it can be applied.
Be concise, information-dense, and avoid pain-point framing.

For each of the {len(articles_input)} articles below, generate (CHINESE ONLY — do NOT write English prose):
- title_zh: punchy Traditional Chinese title (Taiwan terminology, e.g. 元件 not 組件; NEVER simplified Chinese)
- lede_zh: a flowing Traditional Chinese **prose summary** of 150-300 characters, written as 2-3 connected sentences
  (NOT bullet points). Cover what happened, why it matters, and the practical takeaway.
- reading_time_min: integer 2-6
- is_headline: true for exactly ONE article (most impactful AI/frontier news)

Proper nouns (GPT-5, LangChain, Anthropic, etc.) stay as-is in the Chinese text — do NOT translate them.
Skip English rewrites entirely; the script will copy `_zh` values into `_en` fields automatically.
Do NOT include a `bullets_zh` field.

Respond ONLY with a JSON object:
{{"articles": [{{"id": "...", "title_zh": "...", "lede_zh": "...",
                 "reading_time_min": 3, "is_headline": false}}, ...]}}

Keep id values exactly as provided.

Source articles:
{json.dumps(articles_input, ensure_ascii=False, indent=1)}
"""

print(f"[INFO] {date_taipei} {EDITION} issue=#{issue_number} → {MODEL}...", file=sys.stderr)
resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": prompt}],
    max_tokens=16000,
)
content = resp.choices[0].message.content
m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
if m:
    content = m.group(1)
else:
    s, e = content.find('{'), content.rfind('}')
    if s >= 0 and e > s: content = content[s:e+1]

generated = json.loads(content)
id_to_meta = {a["id"]: a for a in articles_input}
final_articles = []
headline_set = False
for ga in generated["articles"]:
    meta = id_to_meta.get(ga["id"])
    if not meta: continue
    is_h = ga.get("is_headline", False)
    if is_h and not headline_set:
        headline_set = True
    elif is_h:
        is_h = False
    title_en = ga.get("title_en") or ga["title_zh"]
    lede_en  = ga.get("lede_en")  or ga["lede_zh"]
    final_articles.append({
        "id": ga["id"], "category": meta["category"], "is_headline": is_h,
        "title_zh": ga["title_zh"], "title_en": title_en,
        "lede_zh": ga["lede_zh"], "lede_en": lede_en,
        "bullets_zh": [], "bullets_en": [],
        "source": {"name": meta["source_name"], "url": meta["url"],
                   "published_at": meta["published_at"],
                   "reading_time_min": int(ga.get("reading_time_min", 3)),
                   "credibility_tier": meta["credibility_tier"]},
        "fetched_via": "rss",
    })
if not headline_set and final_articles:
    final_articles[0]["is_headline"] = True

# --- Stage 2: Breaking News selector -----------------------------------------
breaking_ids: list[str] = []
try:
    bn_input = [{
        "id": a["id"],
        "category": a["category"],
        "title_zh": a["title_zh"],
        "lede_zh": a["lede_zh"],
        "source": a["source"]["name"],
    } for a in final_articles]
    bn_prompt = f"""You are a breaking-news triage editor. From the {len(bn_input)} articles below,
identify 0-3 that qualify as TRUE BREAKING NEWS — "anyone alive in tech should know this today".

Qualifies (high bar):
- Major foundation model GA release (GPT-x, Claude x, Gemini x, Llama x major version)
- Major security incident (widespread exploit, large breach, critical CVE in widely-used software)
- Major government / policy move directly affecting AI, tech, or geopolitics (e.g. export controls, antitrust ruling)
- M&A or funding round greater than ~$1B
- Major scientific breakthrough with broad implications

Does NOT qualify: incremental product updates, opinion pieces, blog posts, minor research papers,
analyst reports, regional non-major news.

Be strict. If nothing qualifies, return an empty array. Cap at 3.

Respond ONLY with JSON: {{"breaking_news": ["<id>", ...]}}

Articles:
{json.dumps(bn_input, ensure_ascii=False, indent=1)}
"""
    bn_resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": bn_prompt}],
        max_tokens=400,
    )
    bn_content = bn_resp.choices[0].message.content
    bm = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', bn_content, re.DOTALL)
    if bm:
        bn_content = bm.group(1)
    else:
        s, e = bn_content.find('{'), bn_content.rfind('}')
        if s >= 0 and e > s: bn_content = bn_content[s:e+1]
    bn_obj = json.loads(bn_content)
    valid_ids = {a["id"] for a in final_articles}
    breaking_ids = [i for i in (bn_obj.get("breaking_news") or []) if i in valid_ids][:3]
except Exception as exc:
    print(f"[WARN] breaking-news selector failed: {exc}", file=sys.stderr)
    breaking_ids = []

# --- Assemble issue ----------------------------------------------------------
dt = datetime.strptime(date_taipei, "%Y-%m-%d")
wd_zh = ["一","二","三","四","五","六","日"]
wd_en = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
mo_en = ["January","February","March","April","May","June","July","August","September","October","November","December"]

tagline_zh = "由 AI 為你策展，專屬早晨讀物" if EDITION == "morning" else "由 AI 為你策展，傍晚收束今日"
tagline_en = "AI-curated, your morning read"   if EDITION == "morning" else "AI-curated, your evening wrap"

issue_obj = {
    "issue_number": issue_number, "date": date_taipei,
    "edition": EDITION,
    "edition_label_zh": edition_zh,
    "edition_label_en": edition_en,
    "date_display_zh": f"{dt.year}年{dt.month}月{dt.day}日·星期{wd_zh[dt.weekday()]}·{edition_zh}",
    "date_display_en": f"{wd_en[dt.weekday()]}, {mo_en[dt.month-1]} {dt.day}, {dt.year} · {edition_en}",
    "weather": weather_report["weather"],
    "weather_locations": weather_report["weather_locations"],
    "tagline_zh": tagline_zh,
    "tagline_en": tagline_en,
    "categories": [
        {"id":"ai-ml","name_zh":"AI／機器學習","name_en":"AI / Machine Learning"},
        {"id":"ai-tools","name_zh":"AI 工具","name_en":"AI Tools"},
        {"id":"research-papers","name_zh":"研究論文","name_en":"Research Papers"},
        {"id":"security","name_zh":"資訊安全","name_en":"Security"},
        {"id":"tech-product","name_zh":"科技／產品","name_en":"Tech / Product"},
        {"id":"dev","name_zh":"程式開發","name_en":"Development"},
        {"id":"vc-business","name_zh":"創投／商業","name_en":"VC / Business"},
        {"id":"science","name_zh":"科學研究","name_en":"Science"},
        {"id":"world","name_zh":"國際時事","name_en":"World"},
        {"id":"taiwan","name_zh":"臺灣本地","name_en":"Taiwan"},
    ],
    "articles": final_articles,
    "breaking_news": breaking_ids,
    "meta": {"generated_at": datetime.now(TPE).isoformat(),
             "total_articles": len(final_articles),
             "sources_used": {"rss": len(final_articles), "firecrawl": 0},
             "schema_version": "2.0",
             "edition": EDITION,
             "model": MODEL},
}

# --- Write outputs -----------------------------------------------------------
(REPO / "data/issues").mkdir(parents=True, exist_ok=True)
issue_path = REPO / f"data/issues/{date_taipei}-{EDITION}.json"
issue_path.write_text(json.dumps(issue_obj, ensure_ascii=False, indent=2))
(REPO / f"data/{EDITION}.json").write_text(json.dumps(issue_obj, ensure_ascii=False, indent=2))
(REPO / "data/latest.json").write_text(json.dumps(issue_obj, ensure_ascii=False, indent=2))

headline = next((a for a in final_articles if a["is_headline"]), final_articles[0])
new_entry = {
    "issue_number": issue_number, "date": date_taipei,
    "edition": EDITION,
    "key": key_for_today,
    "headline_zh": headline["title_zh"], "headline_en": headline["title_en"],
    "article_count": len(final_articles),
    "breaking_news_count": len(breaking_ids),
    "file": f"data/issues/{date_taipei}-{EDITION}.json",
}
archive["issues"] = [i for i in archive["issues"] if i.get("key") != key_for_today and not (
    i.get("date") == date_taipei and i.get("edition") == EDITION
)] + [new_entry]
archive["issues"].sort(key=lambda x: (x.get("date",""), 0 if x.get("edition")=="evening" else 1), reverse=True)
archive["total_issues"] = len(archive["issues"])
archive["last_updated"] = datetime.now(TPE).isoformat()
(REPO / "data/archive.json").write_text(json.dumps(archive, ensure_ascii=False, indent=2))

print(json.dumps({"issue_number": issue_number, "date": date_taipei,
                  "edition": EDITION,
                  "total_articles": len(final_articles),
                  "breaking_news": breaking_ids,
                  "by_category": {c: sum(1 for a in final_articles if a["category"]==c) for c in TARGET}},
                 ensure_ascii=False, indent=2))
