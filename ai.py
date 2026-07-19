# -*- coding: utf-8 -*-
"""עטיפות ל-Claude API: סינון חכם, ניתוח אסטרטגי ותכנון שבועי."""

from __future__ import annotations

import json
from typing import Any

import anthropic

from strategy import STRATEGY_BRIEF, STRATEGY_FULL, VOICE_RULES
from news import Headline

# מודלים זמינים לבחירה בהגדרות
AVAILABLE_MODELS = {
    "Claude Sonnet 5 (מומלץ — מהיר ואיכותי)": "claude-sonnet-5",
    "Claude Opus 4.8 (העמוק ביותר)": "claude-opus-4-8",
    "Claude Haiku 4.5 (הזול והמהיר)": "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "claude-sonnet-5"


def get_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


def _extract_json(text: str) -> Any:
    """מחלץ בלוק JSON מתשובת המודל, גם אם עטוף ב-```json."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # ניסיון ראשון — כל הטקסט
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # נפילה חזרה — למצוא את הבלוק בין הסוגריים הראשונים לאחרונים
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("לא ניתן היה לפענח JSON מתשובת המודל")


def _message(client, model, prompt, max_tokens=4096, system=None) -> str:
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if block.type == "text")


# ---------------------------------------------------------------------------
# שלב 1 — סינון חכם ("המוח"): איזו כותרת רלוונטית אסטרטגית
# ---------------------------------------------------------------------------
def filter_relevant(client, headlines: list[Headline], model: str = DEFAULT_MODEL) -> list[int]:
    """
    מקבל כותרות גולמיות, מחזיר אינדקסים של הכותרות הרלוונטיות אסטרטגית בלבד.
    """
    if not headlines:
        return []
    numbered = "\n".join(f"{i}. {h.title}" for i, h in enumerate(headlines))
    prompt = f"""אתה האסטרטג של אייל נווה. להלן האסטרטגיה:

{STRATEGY_BRIEF}

להלן כותרות חדשות גולמיות מהתקשורת הישראלית. בחר אך ורק את הכותרות הרלוונטיות
אסטרטגית לאייל — גם אם אינן מכילות מילות מפתח ספציפיות, כל עוד יש בהן זווית שמחברת
לאחד הצירים או הפילרים. סנן החוצה רכילות, ספורט, בידור וכל מה שאין לו זווית אסטרטגית.

הכותרות:
{numbered}

החזר JSON בלבד, מערך של מספרי האינדקסים הרלוונטיים, מהחשוב לפחות חשוב.
לדוגמה: [3, 0, 7]. אם אף כותרת אינה רלוונטית — החזר []."""
    text = _message(client, model, prompt, max_tokens=1024)
    data = _extract_json(text)
    result = []
    for x in data if isinstance(data, list) else []:
        try:
            idx = int(x)
            if 0 <= idx < len(headlines):
                result.append(idx)
        except (ValueError, TypeError):
            continue
    return result


# ---------------------------------------------------------------------------
# שלב 2 — ניתוח אסטרטגי מלא + חבילת תוכן
# ---------------------------------------------------------------------------
def analyze_headline(client, headline: Headline, model: str = DEFAULT_MODEL) -> dict:
    """מחזיר ניתוח מלא + חבילת תוכן + הוראות הפקה בעברית, כמילון."""
    context = f"כותרת: {headline.title}\nמקור: {headline.source}"
    if headline.summary:
        context += f"\nתקציר: {headline.summary}"

    prompt = f"""אתה מנהל הסושיאל והאסטרטג של אייל נווה. נתח את הידיעה הבאה והפק חבילת
תוכן מלאה — הכל בעברית, בקולו של אייל.

## האסטרטגיה
{STRATEGY_FULL}

## כללי הקול
{VOICE_RULES}

## הידיעה
{context}

## המשימה
הפעל את המבחן: לאיזה ציר/פילר זה מתחבר, איזו אסוציאציה זה מזיז, ומגיבים או יוזמים.
צור חבילת תוכן מוכנה לשימוש.

החזר JSON בלבד במבנה המדויק הזה (כל הערכים בעברית):
{{
  "analysis": "ניתוח הידיעה במשפט אחד",
  "strategic_context": "ההקשר האסטרטגי — ציר/פילר + האסוציאציה שזה מזיז + החלטה (מגיבים/לא/ממתינים) + דחיפות",
  "narrative_angle": "הנרטיב והזווית — המסר האחד והמסגור שממצב את אייל בעמודה החדשה",
  "content_package": {{
    "facebook": "פוסט פייסבוק מלא ומוכן לפרסום",
    "instagram_caption": "כיתוב אינסטגרם קצר וחד",
    "instagram_visual": "רעיון ויזואלי לפוסט האינסטגרם — מה רואים",
    "story_sequence": "רצף סטורי (2-4 שקופיות) — מה כתוב בכל שקופית",
    "reel_hook": "הוק לטיקטוק/ריל — 3 השניות הראשונות",
    "reel_script": "תסריט קצר לריל/טיקטוק"
  }},
  "execution_guide": {{
    "filming": "הוראות צילום מעשיות — תאורה, מסגור, טון דיבור",
    "visual_template": "תבנית ויזואלית — טקסטים על המסך, צבעים, פונטים"
  }},
  "approval_flags": "רגישויות ואישורים — סמן אם יש חטופים/נפגעים/מספרים לאימות, או 'אין' אם אין",
  "next_step": "הצעד הבא של בר — פעולה אחת קונקרטית"
}}"""
    text = _message(client, model, prompt, max_tokens=4096)
    return _extract_json(text)


# ---------------------------------------------------------------------------
# מתכנן שבועי
# ---------------------------------------------------------------------------
def generate_weekly_plan(client, directions: str, model: str = DEFAULT_MODEL) -> list[dict]:
    """מחזיר תוכנית שבועית מובנית — רשימת פריטים עם יום, פילר, פורמט וטיוטה."""
    prompt = f"""אתה מנהל הסושיאל של אייל נווה. בנה תוכנית תוכן שבועית מלאה בעברית,
בקולו של אייל, לפי הכיוונים השבועיים של בר.

## האסטרטגיה
{STRATEGY_FULL}

## כללי הקול
{VOICE_RULES}

## תמהיל שבועי בסיס
- סטורי: יומי (קליל — מאחורי הקלעים, שטח, בעלי ברית)
- פוסט/קרוסלה: 3 בשבוע (לפחות אחד יוזם — פילר 2 או 3)
- ריל/טיקטוק: 1-2 בשבוע (הוק ב-3 שניות חובה)
- תגובה מהירה לאקטואליה: לפי הצורך
לפחות מחצית מהפריטים — תוכן יוזם (מנוע שינוי האסוציאציה).

## הכיוונים השבועיים של בר
{directions}

בנה תוכנית לשבוע (ראשון עד שבת). החזר JSON בלבד — מערך של פריטים, כל פריט במבנה:
{{
  "day": "יום בשבוע",
  "pillar": "שם הפילר (1-4)",
  "format": "סטורי/פוסט/קרוסלה/ריל/טיקטוק",
  "type": "יוזם או תגובתי",
  "draft": "טיוטת המלל המוכן לפריט"
}}
ודא כיסוי מאוזן של הפילרים לאורך השבוע."""
    text = _message(client, model, prompt, max_tokens=6144)
    data = _extract_json(text)
    return data if isinstance(data, list) else []
