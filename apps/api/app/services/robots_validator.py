"""
robots.txt Validator — checks if a site's robots.txt allows key AI crawlers.
"""

import logging
import re
from typing import Optional

import httpx

from ..database import get_supabase

logger = logging.getLogger(__name__)
_TIMEOUT = 10

# AI crawlers to check
_AI_BOTS = [
    {"name": "GPTBot",           "user_agent": "GPTBot",           "owner": "OpenAI"},
    {"name": "ClaudeBot",        "user_agent": "ClaudeBot",        "owner": "Anthropic"},
    {"name": "PerplexityBot",    "user_agent": "PerplexityBot",    "owner": "Perplexity"},
    {"name": "Google-Extended",  "user_agent": "Google-Extended",  "owner": "Google"},
    {"name": "anthropic-ai",     "user_agent": "anthropic-ai",     "owner": "Anthropic"},
    {"name": "Googlebot",        "user_agent": "Googlebot",        "owner": "Google"},
]


def _parse_robots_rules(robots_txt: str) -> dict[str, list[dict]]:
    """Parse robots.txt into {user_agent: [{directive, path}]}."""
    rules: dict[str, list[dict]] = {}
    current_agents: list[str] = []

    for line in robots_txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            directive, _, value = line.partition(":")
            directive = directive.strip().lower()
            value     = value.strip()

            if directive == "user-agent":
                current_agents = [value.lower()]
                for agent in current_agents:
                    if agent not in rules:
                        rules[agent] = []
            elif directive in ("disallow", "allow") and current_agents:
                for agent in current_agents:
                    rules.setdefault(agent, []).append({
                        "directive": directive,
                        "path":      value,
                    })

    return rules


def _is_bot_blocked(rules: dict, bot_ua: str) -> bool:
    """True if the bot is explicitly disallowed from '/' or all paths."""
    ua_lower = bot_ua.lower()
    # Check specific rule first, then * fallback
    for check_ua in (ua_lower, "*"):
        bot_rules = rules.get(check_ua, [])
        for rule in bot_rules:
            if rule["directive"] == "disallow" and rule["path"] in ("/", ""):
                return True
    return False


def _is_bot_mentioned(rules: dict, bot_ua: str) -> bool:
    return bot_ua.lower() in rules


def validate_robots_txt(domain: str) -> dict:
    """
    Fetches and validates the robots.txt of a domain.
    Returns structured result with bot statuses.
    """
    url = f"https://{domain.strip().rstrip('/')}/robots.txt"
    robots_content = ""
    fetch_error    = None

    try:
        r = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "NoroPlatform/1.0 RobotsValidator"})
        if r.status_code == 200:
            robots_content = r.text
        else:
            fetch_error = f"HTTP {r.status_code}"
    except Exception as exc:
        fetch_error = str(exc)[:200]

    if fetch_error:
        return {
            "domain":        domain,
            "url":           url,
            "has_robots_txt": False,
            "fetch_error":   fetch_error,
            "bots":          [],
            "overall_status": "error",
            "issues":        [f"Não foi possível acessar robots.txt: {fetch_error}"],
            "suggestions":   ["Certifique-se de que robots.txt está acessível publicamente."],
        }

    rules   = _parse_robots_rules(robots_content)
    results = []
    issues  = []
    suggestions = []

    for bot in _AI_BOTS:
        ua      = bot["user_agent"]
        blocked = _is_bot_blocked(rules, ua)
        mentioned = _is_bot_mentioned(rules, ua)
        status  = "blocked" if blocked else ("allowed" if mentioned else "not_mentioned")

        results.append({
            "name":      bot["name"],
            "owner":     bot["owner"],
            "status":    status,
            "mentioned": mentioned,
            "blocked":   blocked,
        })

        if blocked:
            issues.append(f"{bot['name']} está BLOQUEADO no robots.txt.")
            suggestions.append(
                f"Remova ou altere a regra de Disallow para `User-agent: {ua}` para permitir a indexação por {bot['owner']}."
            )
        elif not mentioned:
            suggestions.append(
                f"Considere adicionar `User-agent: {ua}` com `Allow: /` para indicar permissão explícita ao {bot['name']}."
            )

    blocked_count  = sum(1 for r in results if r["blocked"])
    overall_status = "error" if blocked_count > 0 else "ok"

    return {
        "domain":         domain,
        "url":            url,
        "has_robots_txt": True,
        "robots_txt":     robots_content[:3000],
        "bots":           results,
        "blocked_count":  blocked_count,
        "overall_status": overall_status,
        "issues":         issues,
        "suggestions":    suggestions,
    }


def check_and_save(client_id: str) -> dict:
    """Run validation and persist to technical_seo_checks."""
    sb = get_supabase()
    client = (
        sb.table("clients")
        .select("id,shopify_domain")
        .eq("id", client_id)
        .limit(1)
        .execute()
    ).data
    if not client:
        raise ValueError("client not found")
    domain = (client[0].get("shopify_domain") or "").strip().rstrip("/")
    if not domain:
        raise ValueError("cliente sem shopify_domain")

    result = validate_robots_txt(domain)
    status = "ok" if result["overall_status"] == "ok" else "warning"

    sb.table("technical_seo_checks").upsert({
        "client_id":  client_id,
        "check_type": "robots_txt",
        "status":     status,
        "data":       result,
        "checked_at": "now()",
    }, on_conflict="client_id,check_type").execute()

    # Create optimization suggestions for blocked bots
    if result.get("blocked_count", 0) > 0:
        existing = (
            sb.table("technical_optimizations")
            .select("id")
            .eq("client_id", client_id)
            .eq("type", "robots_txt")
            .eq("status", "pending")
            .limit(1)
            .execute()
        ).data
        if not existing:
            sb.table("technical_optimizations").insert({
                "client_id":        client_id,
                "type":             "robots_txt",
                "title":            f"{result['blocked_count']} bot(s) de IA bloqueados no robots.txt",
                "description":      "Crawlers de IA estão sendo bloqueados, reduzindo a visibilidade em ChatGPT, Perplexity e outros.",
                "severity":         "high",
                "estimated_impact": "Alto — desbloqueio imediato permite indexação pelos LLMs",
                "estimated_time":   "30 min",
                "action_data":      result,
            }).execute()

    return result
