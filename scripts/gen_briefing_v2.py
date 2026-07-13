#!/usr/bin/env python3
"""Generate Daily Briefing issue (v2: dual edition + GPT-5.5 + prose summary + breaking news).

Reads /tmp/candidates.json (produced by fetch_candidates.py).
Writes data/<edition>.json, data/latest.json, data/issues/<date>-<edition>.json,
and updates data/archive.json.

Usage:
    python3 scripts/gen_briefing_v2.py [--edition morning|evening]

If --edition is omitted, edition is inferred from Asia/Taipei hour:
    hour < 14 → morning,  else → evening.
"""
import argparse, json, os, sys, re, time, shutil, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

from openai import OpenAI


# --- Codex OAuth provider via hermes CLI -------------------------------------
# The ChatGPT Codex backend (chatgpt.com/backend-api/codex) needs special
# Cloudflare headers (originator=codex_cli_rs, ChatGPT-Account-ID) plus OAuth
# token refresh — all handled by the `hermes` CLI. Rather than reimplement that
# (and break in ~8 days when the access token expires), we shell out to
# `hermes chat --provider openai-codex`, which reuses hermes's auth/refresh.
HERMES_BIN = shutil.which("hermes") or "/home/hermes/.local/bin/hermes"


class HermesCodexClient:
    """Minimal OpenAI-SDK-shaped shim that routes chat completions through the
    `hermes chat --provider openai-codex` subprocess so the Daily Briefing can
    use the live Codex OAuth credential instead of a standalone API key."""

    def __init__(self, model: str, timeout: int = 300, provider: str = "openai-codex"):
        self._timeout = timeout
        self._provider = provider
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model: str, messages: list[dict], **_kwargs):
        prompt = "\n\n".join(str(m.get("content", "")) for m in messages).strip()
        cmd = [HERMES_BIN, "chat", "--provider", self._provider,
               "--model", model, "--toolsets", "safe", "-Q", "-q", prompt]
        result = subprocess.run(cmd, text=True, capture_output=True,
                                timeout=self._timeout + 30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-800:]
            raise RuntimeError(f"hermes codex subprocess failed ({result.returncode}): {detail}")
        lines = [ln for ln in result.stdout.splitlines() if not ln.startswith("session_id:")]
        content = "\n".join(lines).strip()
        if not content:
            raise RuntimeError("hermes codex subprocess returned empty content")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def load_env_file(path: Path) -> dict[str, str]:
    """Load a simple KEY=VALUE env file without printing secrets."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())
    return env


OPENAI_ENV = load_env_file(Path("/home/hermes/.hermes/credentials/openai.env"))

PROVIDERS: list[dict[str, object]] = []

# Primary writer: Grok (xai-oauth) via hermes. Article generation is BATCHED
# (see BRIEFING_CHUNK_SIZE below) into small per-call chunks so grok never
# hits the single-shot timeout it used to on the full ~27-article prompt.
# Grok is the default so the briefing does not burn Codex quota; Codex
# stays only as an emergency fallback if a grok chunk fails. Disable grok with
# BRIEFING_DISABLE_GROK=1.
if os.environ.get("BRIEFING_DISABLE_GROK", "").strip().lower() not in {"1", "true", "yes"}:
    PROVIDERS.append({
        "name": "grok",
        "model": "grok-4.5",
        "client": HermesCodexClient("grok-4.5", provider="xai-oauth", timeout=300),
    })

# Fallback: ChatGPT Codex OAuth via hermes (gpt-5.6-terra). Use unless explicitly
# disabled. This is the live credential as of 2026-06-24 after the standalone
# OpenAI API key was revoked server-side (401 invalid_api_key).
if os.environ.get("BRIEFING_DISABLE_CODEX", "").strip().lower() not in {"1", "true", "yes"}:
    PROVIDERS.append({
        "name": "codex",
        "model": "gpt-5.6-terra",
        "client": HermesCodexClient("gpt-5.6-terra"),
    })

# Fallback: standalone OpenAI API key. Auto-resumes as a provider if a valid
# key is restored in openai.env; kept after codex so codex stays primary.
OPENAI_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or os.environ.get("API_KEY")
    or OPENAI_ENV.get("OPENAI_API_KEY")
    or OPENAI_ENV.get("API_KEY")
)
if OPENAI_KEY:
    PROVIDERS.append({
        "name": "openai",
        "model": "gpt-5.6-terra",
        "client": OpenAI(
            api_key=OPENAI_KEY,
            base_url="https://api.openai.com/v1",
        ),
    })
if not PROVIDERS:
    raise SystemExit("No generation providers available for Daily Briefing")

MODEL = str(PROVIDERS[0]["model"])
ACTIVE_MODEL = MODEL


def create_chat_completion(messages: list[dict[str, str]], max_tokens: int, label: str):
    """Call the configured OpenAI generator with bounded retries."""
    global ACTIVE_MODEL
    last_exc: Exception | None = None
    ordered = sorted(
        PROVIDERS,
        key=lambda p: 0 if f"{p['name']}/{p['model']}" == ACTIVE_MODEL else 1,
    )
    for provider in ordered:
        name = str(provider["name"])
        model = str(provider["model"])
        client = provider["client"]
        print(f"[INFO] {label} → {name}/{model}...", file=sys.stderr)
        for attempt in range(1, 4):
            try:
                tokens_key = "max_completion_tokens" if model.startswith("gpt-5") else "max_tokens"
                resp = client.chat.completions.create(  # type: ignore[union-attr]
                    model=model,
                    messages=messages,
                    **{tokens_key: max_tokens},
                )
                ACTIVE_MODEL = f"{name}/{model}"
                return resp
            except Exception as exc:
                last_exc = exc
                print(f"[WARN] {label} failed on {name}/{model} attempt {attempt}/3: {type(exc).__name__}: {exc}", file=sys.stderr)
                is_transient = any(marker in str(exc) for marker in ("503", "UNAVAILABLE", "temporarily", "high demand"))
                if attempt < 3 and is_transient:
                    time.sleep(5 * attempt)
                    continue
                break
    raise RuntimeError(f"All generation providers failed for {label}") from last_exc


def extract_json_text(content: str) -> str:
    """Extract the outer JSON object from an LLM response."""
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if m:
        return m.group(1)
    s, e = content.find('{'), content.rfind('}')
    if s >= 0 and e > s:
        return content[s:e + 1]
    return content


def parse_llm_json(content: str, *, label: str, schema_hint: str) -> dict:
    """Parse LLM JSON, saving bad output and asking the model to repair malformed JSON once.

    GPT occasionally returns a nearly-valid object with a missing comma or quote. A single
    JSONDecodeError should not fail the whole briefing; the repair call is constrained to
    syntax repair only and the normal downstream validator still checks the final issue.
    """
    raw = extract_json_text(content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as first_exc:
        bad_path = Path(f"/tmp/daily_briefing_bad_{label}.json")
        bad_path.write_text(raw, encoding="utf-8")
        print(
            f"[WARN] {label} JSON parse failed at line {first_exc.lineno} col {first_exc.colno}: "
            f"{first_exc.msg}; saved raw output to {bad_path}; requesting syntax repair",
            file=sys.stderr,
        )
        repair_prompt = f"""Repair the following malformed JSON into one valid JSON object.
Rules:
- Preserve all keys, values, strings, URLs, IDs, and Traditional Chinese text exactly where possible.
- Only fix JSON syntax errors such as missing commas, extra trailing commas, unescaped quotes, or code fences.
- Do NOT summarize, omit, add commentary, or change the schema.
- Respond ONLY with the repaired JSON object.

Expected shape: {schema_hint}

Malformed JSON:
{raw}
"""
        repair_resp = create_chat_completion(
            messages=[{"role": "user", "content": repair_prompt}],
            max_tokens=16000,
            label=f"{label} JSON repair",
        )
        repaired = extract_json_text(repair_resp.choices[0].message.content)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as second_exc:
            repaired_path = Path(f"/tmp/daily_briefing_repaired_{label}.json")
            repaired_path.write_text(repaired, encoding="utf-8")
            raise RuntimeError(
                f"{label} JSON remained invalid after repair: "
                f"{second_exc.msg} at line {second_exc.lineno} col {second_exc.colno}; "
                f"raw={bad_path} repaired={repaired_path}"
            ) from second_exc

# --- Args & edition ----------------------------------------------------------
TPE = timezone(timedelta(hours=8))
def infer_edition() -> str:
    return "morning" if datetime.now(TPE).hour < 14 else "evening"

ap = argparse.ArgumentParser()
ap.add_argument("--edition", choices=["morning", "evening"], default=None)
args = ap.parse_args()
EDITION = args.edition or infer_edition()

REPO = Path("/home/hermes/Daily-briefing")
candidate_paths = [
    Path(os.environ.get(
        "DAILY_BRIEFING_CANDIDATES",
        "/home/hermes/.hermes/cache/daily_briefing/candidates.json",
    )),
    Path("/tmp/candidates.json"),
]
for candidate_path in candidate_paths:
    if candidate_path.exists():
        data = json.loads(candidate_path.read_text(encoding="utf-8"))
        break
else:
    raise SystemExit(
        "No Daily Briefing candidates found. Expected "
        + " or ".join(str(p) for p in candidate_paths)
    )
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

# --- Balanced selection with high-impact override ----------------------------
TARGET = {
    "ai-ml": 5, "research-papers": 3, "tech-product": 3, "vc-business": 2,
    "security": 2, "dev": 2, "ai-tools": 2, "taiwan": 4,
    "enterprise-cases": 2, "science": 1,
}
MIN_FLOORS = {"taiwan": 4, "enterprise-cases": 2}

MIN_SCORE = {
    "ai-ml": 1.6, "ai-tools": 1.4, "research-papers": 1.2, "security": 1.2,
    "tech-product": 1.45, "dev": 1.1, "vc-business": 1.35, "science": 1.0,
    "world": 1.45, "taiwan": 1.35, "enterprise-cases": 1.55,
}
HIGH_IMPACT_SIGNALS = {
    "new_release", "conference_signal", "ai_hardware_signal",
    "semiconductor_platform", "security_critical", "business_impact",
    "benchmark_sota", "official_page", "official_model_source",
    "official_model_release",
}


def _score(article: dict) -> float:
    return float(article.get("rank_score") or 0)


def _signals(article: dict) -> set[str]:
    return set(article.get("quality_signals") or [])


def _article_key(article: dict) -> str:
    return (article.get("url") or article.get("title") or "").strip().lower()


def _sorted_candidates(category: str) -> list[dict]:
    cands = cbc.get(category, [])
    cands = [a for a in cands if _score(a) >= MIN_SCORE.get(category, 1.0)]
    return sorted(cands, key=lambda a: (_score(a), a.get("published_at") or ""), reverse=True)


def _is_platform_event(article: dict) -> bool:
    sig = _signals(article)
    title = (article.get("title") or "").lower()
    body = f"{article.get('title') or ''} {article.get('summary') or ''}".lower()
    if re.search(r"\b(deal|saving|discount|coupon|best .* under|review)\b", title):
        return bool(sig & {"conference_signal", "new_release"})
    return bool(sig & {"conference_signal", "ai_hardware_signal"}) or bool(
        "semiconductor_platform" in sig and re.search(r"\b(computex|gtc|rtx|dgx|blackwell|rubin|cuda|gpu platform|ai pc)\b", body)
    )


official_sources = {
    "OpenAI Blog", "Anthropic News", "Google DeepMind Blog", "Google AI Blog",
    "Mistral News", "Meta AI Blog", "xAI News", "Hugging Face Blog",
}
MODEL_RELEASE_TITLE_RE = re.compile(
    r"\b(gpt|claude|gemini|gemma|mistral|llama|grok|opus|sonnet|haiku|"
    r"codestral|devstral|magistral|voxtral|sam|model)\b.*\b(\d|preview|ga|api|"
    r"release|launch|introduc|announc|open weights?)\b|"
    r"\b(launch|introduc|announc|release)\b.*\b(gpt|claude|gemini|llama|grok|mistral|model)\b",
    re.I,
)


def _is_first_party_model_release(article: dict) -> bool:
    if article.get("source_name") not in official_sources:
        return False
    text = f"{article.get('title') or ''} {article.get('summary') or ''}"
    sig = _signals(article)
    return "official_model_release" in sig or (
        bool(sig & {"new_release", "official_model_source", "official_page"})
        and bool(MODEL_RELEASE_TITLE_RE.search(text))
    )


selected = {cat: [] for cat in TARGET}
used: set[str] = set()

# First pass: protect platform-level / high-impact stories even if a category
# quota would otherwise bury them.
global_pool: list[tuple[str, dict]] = []
for cat in TARGET:
    for article in _sorted_candidates(cat):
        global_pool.append((cat, article))
global_pool.sort(key=lambda item: (_score(item[1]), len(_signals(item[1]) & HIGH_IMPACT_SIGNALS)), reverse=True)

for cat, article in global_pool:
    if len(selected[cat]) >= TARGET[cat] + 1:
        continue
    if _score(article) < 2.8 and not (_signals(article) & HIGH_IMPACT_SIGNALS):
        continue
    key = _article_key(article)
    if not key or key in used:
        continue
    selected[cat].append(article)
    used.add(key)
    if sum(len(v) for v in selected.values()) >= 6:
        break

# Second pass: fill category targets by precomputed editorial rank_score.
for cat, n in TARGET.items():
    for article in _sorted_candidates(cat):
        if len(selected[cat]) >= n:
            break
        key = _article_key(article)
        if not key or key in used:
            continue
        selected[cat].append(article)
        used.add(key)

# Minimum floors for local/enterprise categories: these are lower-bound quotas,
# not just soft caps, so they must be filled before generic backfill competes.
for cat, floor in MIN_FLOORS.items():
    for article in _sorted_candidates(cat):
        if len(selected[cat]) >= floor:
            break
        key = _article_key(article)
        if not key or key in used:
            continue
        selected[cat].append(article)
        used.add(key)

# Hardware/platform-event safety valve. This catches NVIDIA/AMD/Intel/TSMC
# platform launches that might otherwise lose to generic AI/business stories.
selected_flat = [a for articles in selected.values() for a in articles]
if not any(_is_platform_event(a) for a in selected_flat):
    for cat, article in global_pool:
        if cat not in {"ai-ml", "tech-product", "vc-business"}:
            continue
        if not _is_platform_event(article):
            continue
        key = _article_key(article)
        if not key or key in used:
            continue
        if len(selected[cat]) < TARGET[cat] + 1:
            selected[cat].append(article)
            used.add(key)
        else:
            replace_at = min(range(len(selected[cat])), key=lambda i: _score(selected[cat][i]))
            old_key = _article_key(selected[cat][replace_at])
            selected[cat][replace_at] = article
            used.discard(old_key)
            used.add(key)
        break

# Official model-lab safety valve. If a first-party model release is available,
# keep the strongest one and later force it to headline.
selected_flat = [a for articles in selected.values() for a in articles]
first_party_release_candidates = [
    article for cat, article in global_pool
    if cat == "ai-ml" and _is_first_party_model_release(article)
]
if first_party_release_candidates and not any(_is_first_party_model_release(a) for a in selected_flat):
    article = first_party_release_candidates[0]
    cat = "ai-ml"
    key = _article_key(article)
    if key and key not in used:
        if len(selected[cat]) < TARGET[cat] + 1:
            selected[cat].append(article)
            used.add(key)
        else:
            replace_at = min(range(len(selected[cat])), key=lambda i: _score(selected[cat][i]))
            old_key = _article_key(selected[cat][replace_at])
            selected[cat][replace_at] = article
            used.discard(old_key)
            used.add(key)
elif not any((a.get("source_name") in official_sources) for a in selected_flat):
    for cat, article in global_pool:
        if cat != "ai-ml" or article.get("source_name") not in official_sources:
            continue
        key = _article_key(article)
        if not key or key in used:
            continue
        if len(selected[cat]) < TARGET[cat] + 1:
            selected[cat].append(article)
            used.add(key)
        else:
            replace_at = min(range(len(selected[cat])), key=lambda i: _score(selected[cat][i]))
            old_key = _article_key(selected[cat][replace_at])
            selected[cat][replace_at] = article
            used.discard(old_key)
            used.add(key)
        break

# Third pass: if strict categories run short, backfill with best remaining
# high-signal stories rather than shipping a thin issue.
MIN_ARTICLES = 16
MAX_ARTICLES = 22
if sum(len(v) for v in selected.values()) < MIN_ARTICLES:
    for cat, article in global_pool:
        if sum(len(v) for v in selected.values()) >= MAX_ARTICLES:
            break
        if _score(article) < 1.8:
            continue
        key = _article_key(article)
        if not key or key in used:
            continue
        selected[cat].append(article)
        used.add(key)

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
            "credibility_tier": a.get("credibility_tier") or a.get("source_tier", "secondary"),
            "rank_score": a.get("rank_score"),
            "quality_signals": a.get("quality_signals", []),
        })
        idx += 1
first_party_headline_id = next(
    (a["id"] for a in articles_input if a["source_name"] in official_sources
     and (
         "official_model_release" in set(a.get("quality_signals") or [])
         or MODEL_RELEASE_TITLE_RE.search(f"{a.get('title_orig') or ''} {a.get('summary_orig') or ''}")
     )),
    None,
)

edition_zh = "早報" if EDITION == "morning" else "晚報"
edition_en = "Morning Edition" if EDITION == "morning" else "Evening Edition"

# --- Stage 1: write the issue prose ------------------------------------------
prompt = f"""You are the editor of an AI Intelligence Briefing ({edition_zh} / {edition_en}) for an AI Engineer / AI product builder in Taiwan.
Audience reads it quickly. This is NOT a generic world-news briefing. Focus on AI model labs, papers, benchmarks,
SOTA models, AI products, platform wars, community heat, Chinese-language tech, technical risk, industry depth,
and practical AI business applications.

Editorial mission:
  - 最新論文、benchmark、SOTA 模型
  - 社群討論熱度高的 AI 技術與產品
  - 中文科技與台灣 AI / 半導體 / 企業 IT
  - 每日 AI 快訊與一手模型消息
  - 產品發布與平台戰爭
  - AI 研究趨勢與技術風險
  - 產業深度與 AI 商業應用

Be concise, information-dense, and practical. Avoid generic politics, celebrity/news filler, and vague thought leadership.

EDITORIAL WEIGHTING (use these 7 criteria to judge story value — favor articles that score well across them):
  1. AI 屬性強（與 AI / ML / LLM / Agent / 模型 / AI 工具直接相關）
  2. 新手可上手（概念清楚、不過度艱深）
  3. 有趣（讓人想點開、有故事性或新意）
  4. 企業實用（在職上班族 / 主管能在工作中真的用上）
  5. 安全可信（無資安疑慮、來源可信）
  6. 免費或便宜（有免費版 / 免費試用 / 低門檻）
  7. 新技術（前沿、最近發表、不是舊聞回鍋）

The upstream fetcher already assigns each article:
- rank_score: editorial score; higher means more likely to matter today.
- quality_signals: examples include new_release, conference_signal, ai_hardware_signal,
  semiconductor_platform, security_critical, business_impact, developer_relevance.

Respect these signals. Prefer platform-level launches, major model/product releases,
AI hardware / semiconductor news (NVIDIA, AMD, Intel, RTX, DGX, Blackwell, Rubin,
Computex, GTC), critical security incidents, and large business/regulatory moves.
Do NOT make generic opinion posts, generic local politics, sales/deals, webinars,
roundups, or minor feature tweaks the headline unless no stronger story exists.

When choosing the headline, prefer in this order:
  1. Official model-lab or platform release (OpenAI / Anthropic / DeepMind / Mistral / Meta AI / xAI / Hugging Face / NVIDIA)
  2. Benchmark/SOTA/research result that changes the frontier
  3. Major AI hardware / compute / platform-war event
  4. Critical AI-related security or policy risk
  5. Major AI business application or enterprise adoption story

For each of the articles below, generate (CHINESE ONLY — do NOT write English prose):
- title_zh: punchy Traditional Chinese title (Taiwan terminology, e.g. 元件 not 組件; NEVER simplified Chinese)
- lede_zh: 用繁體中文寫一段**完整、有觀點的原創報導**（不是一句話概述、不是條列），350-500 字，4-6 句連貫成文。
  必須涵蓋：① 發生了什麼事（具體） ② 為何重要 ③ 對台灣 AI 工程師 / 產品開發者 / 企業的實際影響或可上手之處
  ④ 若你掌握相關資訊，補一句開發者 / 技術社群對它的討論角度或反應（例如爭議點、期待、比較對象）。
  用生動、有記者敘事感的筆調，帶讀者理解來龍去脈，不要八股、不要空泛的「值得關注」。
  ⚠️ 嚴禁捏造：不可虛構具體數字、引言、跑分或某人說過的話；不確定的社群反應請用「開發者社群普遍關注…」「技術圈對此看法分歧…」這類概括表述，不要編造具體來源。
  頭條那則請寫得最完整（可到 500 字）。
- reading_time_min: integer 2-6
- is_headline: true for exactly ONE article (most impactful AI/frontier news)
- is_teaching_material: true ONLY if the article meets MOST of the 7 criteria above AND introduces a tool /
  concept that a Taiwan office worker or manager could realistically learn and apply (e.g. Gemini in Google
  Vids/Forms/Slides/Docs, NotebookLM, Gemini Gem, ChatGPT new feature, Make / Zapier free tier, Claude Skills).
  Be selective — not every AI article qualifies. Aim for 0-5 per issue.
- teaching_takeaway: ONLY when is_teaching_material=true. About 50 Traditional Chinese characters that name the
  ONE key 知識點 the reader should grasp (e.g. 「NotebookLM 背後是 RAG（檢索增強生成）：把私有文件變成模型可查的知識庫」).
  Omit this field entirely when is_teaching_material=false.

Proper nouns (GPT-5, Anthropic, etc.) stay as-is in the Chinese text — do NOT translate them.
Skip English rewrites entirely; the script will copy `_zh` values into `_en` fields automatically.
Do NOT include a `bullets_zh` field.

Respond ONLY with a JSON object:
{{"articles": [{{"id": "...", "title_zh": "...", "lede_zh": "...",
                 "reading_time_min": 3, "is_headline": false,
                 "is_teaching_material": false}}, ...]}}

Keep id values exactly as provided.
"""

# Batched generation: grok-4.3 times out on a single ~27-article prompt, so we
# split into small chunks and call the writer once per chunk. Keeps grok as the
# primary writer (no Codex/gpt-5.5 quota burn) while staying within its latency.
CHUNK_SIZE = max(1, int(os.environ.get("BRIEFING_CHUNK_SIZE", "5")))
_chunks = [articles_input[i:i + CHUNK_SIZE] for i in range(0, len(articles_input), CHUNK_SIZE)]
print(f"[INFO] {date_taipei} {EDITION} issue=#{issue_number}; providers={[p['name'] for p in PROVIDERS]}; "
      f"{len(articles_input)} articles in {len(_chunks)} chunk(s) of {CHUNK_SIZE}", file=sys.stderr)
generated = {"articles": []}
for _ci, _chunk in enumerate(_chunks, 1):
    _chunk_prompt = f"{prompt}\n\nSource articles:\n{json.dumps(_chunk, ensure_ascii=False, indent=1)}"
    _resp = create_chat_completion(
        messages=[{"role": "user", "content": _chunk_prompt}],
        max_tokens=8000,
        label=f"article generation (chunk {_ci}/{len(_chunks)})",
    )
    _part = parse_llm_json(
        _resp.choices[0].message.content,
        label=f"article_generation_chunk_{_ci}",
        schema_hint='{"articles":[{"id":"...","title_zh":"...","lede_zh":"...","reading_time_min":3,"is_headline":false,"is_teaching_material":false}]}',
    )
    generated["articles"].extend(_part.get("articles", []))

# The real headline is enforced downstream (first_party_headline_id); clear any
# per-chunk is_headline so batching never yields multiple headlines.
for _ga in generated["articles"]:
    _ga["is_headline"] = False
id_to_meta = {a["id"]: a for a in articles_input}
final_articles = []
headline_set = False
seen_article_ids: set[str] = set()
for ga in generated["articles"]:
    meta = id_to_meta.get(ga["id"])
    if not meta: continue
    seen_article_ids.add(ga["id"])
    is_h = ga.get("is_headline", False)
    if is_h and not headline_set:
        headline_set = True
    elif is_h:
        is_h = False
    title_en = ga.get("title_en") or ga["title_zh"]
    lede_en  = ga.get("lede_en")  or ga["lede_zh"]
    is_teaching = bool(ga.get("is_teaching_material", False))
    teaching_takeaway = (ga.get("teaching_takeaway") or "").strip() if is_teaching else ""
    final_articles.append({
        "id": ga["id"], "category": meta["category"], "is_headline": is_h,
        "title_zh": ga["title_zh"], "title_en": title_en,
        "lede_zh": ga["lede_zh"], "lede_en": lede_en,
        "bullets_zh": [], "bullets_en": [],
        "is_teaching_material": is_teaching,
        "teaching_takeaway": teaching_takeaway,
        "source": {"name": meta["source_name"], "url": meta["url"],
                   "published_at": meta["published_at"],
                   "reading_time_min": int(ga.get("reading_time_min", 3)),
                   "credibility_tier": meta["credibility_tier"]},
        "fetched_via": "rss",
    })

# Some fallback models occasionally omit low-salience IDs even when instructed
# to process every candidate. Keep the pipeline reliable by appending a truthful
# local fallback card for any missing source article instead of silently shipping
# a thin issue.
for meta in articles_input:
    if meta["id"] in seen_article_ids:
        continue
    title = (meta.get("title_orig") or "未命名新聞").strip()
    summary = (meta.get("summary_orig") or "").strip()
    if len(summary) < 80:
        summary = f"這則來自 {meta.get('source_name') or 'RSS'} 的更新值得後續追蹤；重點在於它可能影響 AI、科技產品或企業工作流程的實際應用。"
    lede = summary[:280]
    final_articles.append({
        "id": meta["id"], "category": meta["category"], "is_headline": False,
        "title_zh": title, "title_en": title,
        "lede_zh": lede, "lede_en": lede,
        "bullets_zh": [], "bullets_en": [],
        "is_teaching_material": False,
        "teaching_takeaway": "",
        "source": {"name": meta["source_name"], "url": meta["url"],
                   "published_at": meta["published_at"],
                   "reading_time_min": 3,
                   "credibility_tier": meta["credibility_tier"]},
        "fetched_via": "rss",
    })
if first_party_headline_id and any(a["id"] == first_party_headline_id for a in final_articles):
    for article in final_articles:
        article["is_headline"] = article["id"] == first_party_headline_id
elif not headline_set and final_articles:
    final_articles[0]["is_headline"] = True

# --- Industry tags + 機器人 soft category(內容關鍵字後處理)---------------------
# 跨分類的產業標籤(chip 顯示在卡片上,做垂直導覽);標題有強機器人訊號 → 升格 robotics 軟分類。
# 純內容判斷,不動來源驅動的原分類機制;robotics 無配額/保底,有內容才出現、空著就不顯示。
_TAG_RULES = [
    ("機器人", re.compile(r"機器人|機械臂|人形|具身|embodied|humanoid|robot|LeRobot|manipulat", re.I)),
    ("金融",   re.compile(r"金融|銀行|金控|券商|保險|支付|理財|信用卡|fintech|banking|payment", re.I)),
    ("製造",   re.compile(r"智慧製造|工廠|產線|工業\s*4|Industry\s*4|manufactur|factory", re.I)),
    ("零售",   re.compile(r"零售|新零售|電商|超商|購物|消費者購|retail|e-?commerce|POS", re.I)),
]
_ROBOT_STRONG = re.compile(r"機器人|人形機器|具身智慧|embodied|humanoid|LeRobot|機械臂", re.I)
for _a in final_articles:
    _text = f"{_a.get('title_zh', '')} {_a.get('lede_zh', '')}"
    _tags = [name for name, rx in _TAG_RULES if rx.search(_text)]
    if _tags:
        _a["industry_tags"] = _tags
    if not _a.get("is_headline") and _ROBOT_STRONG.search(_a.get("title_zh", "")):
        _a["category"] = "robotics"

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
- Official model-lab release or capability jump from OpenAI, Anthropic, Google DeepMind, Mistral, Meta AI, xAI, Hugging Face, NVIDIA
- Benchmark/SOTA result that clearly changes the AI frontier or developer practice
- Major AI hardware / semiconductor platform launch (NVIDIA/AMD/Intel/TSMC, RTX/DGX/CUDA/Blackwell/Rubin, Computex/GTC keynote)
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
    bn_resp = create_chat_completion(
        messages=[{"role": "user", "content": bn_prompt}],
        max_tokens=400,
        label="breaking-news selector",
    )
    bn_content = bn_resp.choices[0].message.content
    bn_obj = parse_llm_json(
        bn_content,
        label="breaking_news",
        schema_hint='{"breaking_news":["<id>"]}',
    )
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
        {"id":"ai-ml","name_zh":"一手模型消息","name_en":"Model Labs"},
        {"id":"robotics","name_zh":"機器人／具身智慧","name_en":"Robotics / Embodied AI"},
        {"id":"research-papers","name_zh":"論文／Benchmark","name_en":"Papers / Benchmarks"},
        {"id":"tech-product","name_zh":"產品發布／平台戰爭","name_en":"Products / Platform Wars"},
        {"id":"vc-business","name_zh":"產業深度／商業應用","name_en":"Industry / Business"},
        {"id":"security","name_zh":"技術風險","name_en":"Technical Risk"},
        {"id":"dev","name_zh":"社群熱度／開發者","name_en":"Community / Developers"},
        {"id":"ai-tools","name_zh":"AI 工具","name_en":"AI Tools"},
        {"id":"taiwan","name_zh":"中文科技","name_en":"Chinese Tech"},
        {"id":"enterprise-cases","name_zh":"企業 AI 案例","name_en":"Enterprise AI Cases"},
        {"id":"science","name_zh":"AI 研究趨勢","name_en":"AI Research Trends"},
    ],
    "articles": final_articles,
    "breaking_news": breaking_ids,
    "meta": {"generated_at": datetime.now(TPE).isoformat(),
             "total_articles": len(final_articles),
             "sources_used": {"rss": len(final_articles), "firecrawl": 0},
             "schema_version": "2.0",
             "edition": EDITION,
             "model": ACTIVE_MODEL},
}

# --- Enrich with hero images (og:image) so every issue is 圖文並茂 ------------
# Real per-article news images beat generic stock: free, no API key, relevant.
# Best-effort — a fetch/parse miss just leaves the article imageless (the
# renderer falls back to a category gradient), never blocking publication.
try:
    from enrich_images import extract_og_image, _unsplash_key, _query_from_article, unsplash_image
    from concurrent.futures import ThreadPoolExecutor
    _ukey = _unsplash_key()

    def _attach_image(a: dict) -> None:
        if a.get("image_url"):
            return
        src_url = (a.get("source") or {}).get("url")
        img = extract_og_image(src_url) if src_url else None
        if img:
            a["image_url"] = img
            return
        # 來源沒有 og:image → Unsplash 後援(主題相關圖,key 只在此伺服器端用)
        if _ukey:
            u, credit = unsplash_image(_query_from_article(a), _ukey)
            if u:
                a["image_url"] = u
                a["image_source"] = "unsplash"
                if credit:
                    a["image_credit"] = credit

    with ThreadPoolExecutor(max_workers=12) as _img_pool:
        list(_img_pool.map(_attach_image, issue_obj.get("articles", [])))
    _img_n = sum(1 for a in issue_obj.get("articles", []) if a.get("image_url"))
    _img_us = sum(1 for a in issue_obj.get("articles", []) if a.get("image_source") == "unsplash")
    print(f"[INFO] image enrichment: {_img_n}/{len(issue_obj.get('articles', []))} articles"
          f" ({_img_us} via Unsplash)", file=sys.stderr)
except Exception as _img_exc:  # noqa: BLE001 — never block publish on images
    print(f"[WARN] image enrichment skipped: {type(_img_exc).__name__}: {_img_exc}", file=sys.stderr)

# --- Community Pulse: Grok + x_search live X/community sentiment --------------
# Grok's real edge: it searches X live. We spend it where it matters — a short
# 「社群怎麼看」on the top ~5 stories. Sequential (concurrent grok calls contend
# on the xAI OAuth). Best-effort; never blocks publish. Skip with
# BRIEFING_SKIP_PULSE=1.
if os.environ.get("BRIEFING_SKIP_PULSE", "").strip().lower() not in {"1", "true", "yes"}:
    try:
        from community_pulse import _query_grok
        # Every story gets a live x_search attempt (user wants full coverage).
        # Empty-chatter stories (e.g. arxiv papers) simply get nothing — never
        # fabricated. Sequential, so this adds real time; the pipeline timeout is
        # sized for it.
        _stories = list(issue_obj.get("articles", []))
        for _pa in _stories:
            _pulse = _query_grok(_pa.get("title_zh", ""), (_pa.get("source") or {}).get("name", ""))
            if _pulse:
                _pa["community_pulse"] = _pulse
                _pa["community_pulse_source"] = "Grok · x_search"
        _pn = sum(1 for _a in issue_obj.get("articles", []) if _a.get("community_pulse"))
        print(f"[INFO] community pulse: {_pn}/{len(_stories)} stories", file=sys.stderr)
    except Exception as _pexc:  # noqa: BLE001 — never block publish on pulse
        print(f"[WARN] community pulse skipped: {type(_pexc).__name__}: {_pexc}", file=sys.stderr)

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
