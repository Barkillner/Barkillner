# -*- coding: utf-8 -*-
"""
מנהל התוכן האסטרטגי — אייל נווה
דשבורד Streamlit לניהול מותג עם סריקת חדשות חכמה מבוססת Claude.
הרצה:  streamlit run app.py
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import ai
import news
from strategy import DEFAULT_FEEDS

st.set_page_config(page_title="מנהל התוכן האסטרטגי — אייל נווה", page_icon="🎯", layout="wide")

# ---------------------------------------------------------------------------
# עיצוב RTL בעברית
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stApp, .main, section[data-testid="stSidebar"] { direction: rtl; }
    h1, h2, h3, h4, h5, h6, p, label, div, span, li { text-align: right; }
    .stApp { font-family: "Segoe UI", "Assistant", "Rubik", sans-serif; }
    [data-testid="stMarkdownContainer"] { direction: rtl; text-align: right; }
    .stButton button { width: 100%; }
    .strategic-card {
        border: 1px solid #2b3a55; border-radius: 12px; padding: 16px 20px;
        margin-bottom: 14px; background: rgba(43,58,85,0.12);
    }
    .card-source { color: #7c8db5; font-size: 0.85rem; }
    textarea, input { direction: rtl; text-align: right; }
    .stTextInput input, .stTextArea textarea { direction: rtl; text-align: right; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# מצב (session_state)
# ---------------------------------------------------------------------------
def _secret_api_key() -> str:
    """מפתח Anthropic מסוד Streamlit אם הוגדר (למשל בפריסה), אחרת מחרוזת ריקה.
    עטוף ב-try כי גישה ל-st.secrets ללא קובץ סודות עלולה לזרוק חריגה."""
    try:
        return str(st.secrets.get("ANTHROPIC_API_KEY", "")).strip()
    except Exception:
        return ""


_defaults = {
    "api_key": _secret_api_key(),   # ברירת מחדל מסוד אם קיים; אחרת המשתמש מדביק ידנית
    "feeds": "\n".join(DEFAULT_FEEDS),
    "model": ai.DEFAULT_MODEL,
    "headlines": [],          # רשימת Headline מסוננות
    "scan_errors": [],
    "analysis": {},           # key -> dict ניתוח
    "weekly_plan": [],
    "view": "דשבורד חי",
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)


def _client_or_warn():
    """מחזיר לקוח Claude, או None + אזהרה אם אין מפתח."""
    key = st.session_state.api_key.strip()
    if not key:
        st.warning("⚠️ יש להזין מפתח Anthropic API בסרגל הצד (הגדרות) לפני הפעלת ה-AI.")
        return None
    try:
        return ai.get_client(key)
    except Exception as e:
        st.error(f"שגיאה באתחול הלקוח: {e}")
        return None


# ---------------------------------------------------------------------------
# סרגל צד — ניהול והגדרות
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🎯 ניהול והגדרות")
    st.caption("מנהל תוכן אסטרטגי — אייל נווה")

    st.session_state.view = st.radio(
        "ניווט",
        ["דשבורד חי", "תוכן שבועי", "הגדרות"],
        index=["דשבורד חי", "תוכן שבועי", "הגדרות"].index(st.session_state.view),
    )

    st.divider()
    st.subheader("מפתח API")
    st.session_state.api_key = st.text_input(
        "Anthropic API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="sk-ant-...",
    )

    st.subheader("פידי RSS")
    st.session_state.feeds = st.text_area(
        "כתובות פידים (אחת בכל שורה)",
        value=st.session_state.feeds,
        height=140,
    )

    st.subheader("מודל")
    model_label = st.selectbox(
        "מנוע ה-AI",
        list(ai.AVAILABLE_MODELS.keys()),
        index=list(ai.AVAILABLE_MODELS.values()).index(st.session_state.model)
        if st.session_state.model in ai.AVAILABLE_MODELS.values()
        else 0,
    )
    st.session_state.model = ai.AVAILABLE_MODELS[model_label]


def _feed_urls() -> list[str]:
    return [u.strip() for u in st.session_state.feeds.splitlines() if u.strip()]


# רשת ביטחון מילולית צרה בלבד — ביטויי קריאה/גיוס חד-משמעיים. הסימון העיקרי מגיע
# מהדגל המובנה political_ask שהמתכנן מחזיר; זה רק גיבוי לצמצום החמצות, בלי מונחים
# דו-משמעיים (למשל "מצביע"=מעיד, "רשימה"=רשימת תוכן) שגורמים לסימוני שווא.
_POLITICAL_TERMS = ("להצביע", "הצביעו", "תצביעו", "לתרום", "תרמו", "תרומה", "תתרמו")


def _approval_marker(row: dict) -> str:
    """סימון אישור לשורת תוכנית — דגל political_ask מהמתכנן, עם גיבוי מילולי צר."""
    flagged = bool(row.get("political_ask"))
    if not flagged:
        text = " ".join(str(row.get(k, "")) for k in ("draft", "type", "format"))
        flagged = any(term in text for term in _POLITICAL_TERMS)
    return "⚠️ אישור אייל (פוליטי/גיוס)" if flagged else "טיוטה — לאישור אייל"


# ---------------------------------------------------------------------------
# תצוגה 1 — דשבורד חי (מרכז בקרה)
# ---------------------------------------------------------------------------
def view_dashboard():
    st.title("📡 מרכז בקרה — דשבורד חי")
    st.write("סריקה חכמה של החדשות וסינון אסטרטגי אוטומטי לפי האסטרטגיה של אייל.")

    col1, col2 = st.columns([1, 3])
    with col1:
        scan = st.button("🔍 סרוק חדשות עכשיו", type="primary")

    if scan:
        client = _client_or_warn()
        if client:
            urls = _feed_urls()
            if not urls:
                st.error("לא הוגדרו פידי RSS. הוסף כתובות בסרגל הצד.")
            else:
                # איפוס מיידי — לא משאירים סריקה ישנה שנראית עדכנית אם החדשה נכשלת
                st.session_state.headlines = []
                st.session_state.analysis = {}
                st.session_state.scan_errors = []
                with st.spinner("שלב 1/2 — גירוד כותרות מהפידים..."):
                    raw, errors = news.fetch_all(urls)
                st.session_state.scan_errors = errors
                if not raw:
                    st.error("לא נשלפו כותרות. בדוק את כתובות הפידים.")
                else:
                    with st.spinner(f"שלב 2/2 — סינון אסטרטגי ({len(raw)} כותרות) דרך Claude..."):
                        try:
                            idxs = ai.filter_relevant(client, raw, st.session_state.model)
                            st.session_state.headlines = [raw[i] for i in idxs]
                        except Exception as e:
                            st.error(f"שגיאה בסינון: {e}")

    if st.session_state.scan_errors:
        st.caption("⚠️ פידים שלא נטענו: " + ", ".join(st.session_state.scan_errors))

    headlines = st.session_state.headlines
    if not headlines:
        st.info("אין עדיין כותרות מסוננות. לחץ על \"סרוק חדשות עכשיו\" כדי להתחיל.")
        return

    st.success(f"נמצאו {len(headlines)} כותרות רלוונטיות אסטרטגית.")
    st.divider()

    for idx, h in enumerate(headlines):
        with st.container():
            # escaping — source/title מגיעים מהפיד (מקור לא מהימן), למנוע הזרקת HTML
            st.markdown(
                f"<div class='strategic-card'>"
                f"<div class='card-source'>{html.escape(h.source)}</div>"
                f"<h4>{html.escape(h.title)}</h4>"
                f"</div>",
                unsafe_allow_html=True,
            )
            cols = st.columns([1, 1, 3])
            with cols[0]:
                analyze = st.button("🧠 נתח אסטרטגית", key=f"btn_{idx}")
            with cols[1]:
                if h.link:
                    st.link_button("🔗 לכתבה", h.link)

            if analyze:
                client = _client_or_warn()
                if client:
                    with st.spinner("מנתח ומפיק חבילת תוכן..."):
                        try:
                            st.session_state.analysis[idx] = ai.analyze_headline(
                                client, h, st.session_state.model
                            )
                        except Exception as e:
                            st.error(f"שגיאה בניתוח: {e}")

            if idx in st.session_state.analysis:
                _render_analysis(st.session_state.analysis[idx], idx)


def _render_analysis(a: dict, idx: int):
    """מציג ניתוח מלא + חבילת תוכן + הוראות הפקה בתוך אקספנדר פתוח."""
    with st.expander("📋 ניתוח וחבילת תוכן", expanded=True):
        st.markdown(f"**ניתוח הידיעה:** {a.get('analysis', '')}")
        st.markdown(f"**הקשר אסטרטגי:** {a.get('strategic_context', '')}")
        st.markdown(f"**הנרטיב והזווית:** {a.get('narrative_angle', '')}")

        flags = a.get("approval_flags", "").strip()
        if flags and flags != "אין":
            st.warning(f"🔒 רגישות ואישורים: {flags}")

        st.markdown("### 📦 חבילת תוכן")
        cp = a.get("content_package", {})
        tabs = st.tabs(["פייסבוק", "אינסטגרם", "סטורי", "טיקטוק / ריל"])
        with tabs[0]:
            st.text_area("פוסט פייסבוק", cp.get("facebook", ""), height=180,
                         key=f"fb_{idx}")
        with tabs[1]:
            st.text_area("כיתוב אינסטגרם", cp.get("instagram_caption", ""), height=120,
                         key=f"ig_{idx}")
            st.markdown(f"**רעיון ויזואלי:** {cp.get('instagram_visual', '')}")
        with tabs[2]:
            st.text_area("רצף סטורי", cp.get("story_sequence", ""), height=140,
                         key=f"st_{idx}")
        with tabs[3]:
            st.markdown(f"**הוק:** {cp.get('reel_hook', '')}")
            st.text_area("תסריט ריל / טיקטוק", cp.get("reel_script", ""), height=160,
                         key=f"rl_{idx}")

        st.markdown("### 🎬 הוראות הפקה")
        eg = a.get("execution_guide", {})
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**צילום:** {eg.get('filming', '')}")
        with c2:
            st.markdown(f"**תבנית ויזואלית:** {eg.get('visual_template', '')}")

        st.info(f"👉 הצעד הבא של בר: {a.get('next_step', '')}")


# ---------------------------------------------------------------------------
# תצוגה 2 — מתכנן שבועי
# ---------------------------------------------------------------------------
def view_weekly():
    st.title("🗓️ מתכנן שבועי")
    st.write("הזן כיוונים שבועיים וקבל תוכנית תוכן מובנית לפי הפילרים והתמהיל.")

    directions = st.text_area(
        "כיוונים שבועיים",
        placeholder="לדוגמה: השבוע להתמקד בחמ\"ל הבחירות ובסיפורי מתנדבים; יש אירוע שטח ביום רביעי...",
        height=120,
    )

    if st.button("✨ צור תוכנית שבועית", type="primary"):
        if not directions.strip():
            st.warning("יש להזין כיוונים שבועיים.")
        else:
            client = _client_or_warn()
            if client:
                with st.spinner("בונה תוכנית שבועית..."):
                    try:
                        st.session_state.weekly_plan = ai.generate_weekly_plan(
                            client, directions, st.session_state.model
                        )
                    except Exception as e:
                        st.error(f"שגיאה ביצירת התוכנית: {e}")

    plan = st.session_state.weekly_plan
    if plan:
        st.divider()
        st.subheader("התוכנית השבועית")
        df = pd.DataFrame(plan)
        # שער אישור — כל שורה טיוטה לאישור אייל; שורה עם קריאה אלקטורלית/גיוס
        # מסומנת מפורשות כדורשת אישור פוליטי לפני שהיא עוזבת את הדשבורד (כולל בייצוא).
        df["approval"] = [_approval_marker(r) for r in plan]
        political = df["approval"].str.contains("פוליטי").any()
        if political:
            st.warning("⚠️ חלק מהשורות כוללות קריאה אלקטורלית/גיוס — דורשות **אישור אייל (פוליטי)** לפני פרסום. הסימון נשמר גם בקובץ המיוצא.")
        else:
            st.caption("כל שורה היא טיוטה לאישור אייל.")
        rename = {"day": "יום", "pillar": "פילר", "format": "פורמט",
                  "type": "אופי", "draft": "טיוטה", "approval": "אישור"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        order = [c for c in ["יום", "פילר", "פורמט", "אופי", "טיוטה", "אישור"] if c in df.columns]
        st.dataframe(df[order] if order else df, use_container_width=True, hide_index=True)

        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ הורד כ-CSV", csv, "weekly_plan.csv", "text/csv")


# ---------------------------------------------------------------------------
# תצוגה 3 — הגדרות
# ---------------------------------------------------------------------------
def view_settings():
    st.title("⚙️ הגדרות")
    st.write("ההגדרות נשמרות בסרגל הצד. כאן סקירה ובדיקות.")

    st.subheader("סטטוס")
    st.markdown(f"- מפתח API: {'✅ הוגדר' if st.session_state.api_key.strip() else '❌ חסר'}")
    st.markdown(f"- מספר פידים: **{len(_feed_urls())}**")
    st.markdown(f"- מודל פעיל: **{st.session_state.model}**")

    st.divider()
    st.subheader("בדיקת פידים")
    if st.button("🔌 בדוק חיבור לפידים"):
        urls = _feed_urls()
        if not urls:
            st.warning("לא הוגדרו פידים.")
        for url in urls:
            try:
                items = news.fetch_feed(url, limit=3)
                if items:
                    st.success(f"✅ {items[0].source} — {len(items)} כותרות ({url})")
                else:
                    st.error(f"❌ אין כותרות: {url}")
            except Exception as e:
                st.error(f"❌ {url} — {e}")

    st.divider()
    st.caption(
        "האסטרטגיה של אייל מוטמעת במנוע והיא מזרימה כל סינון, ניתוח ותכנון. "
        "כל התוכן הוא טיוטה לאישור אייל."
    )


# ---------------------------------------------------------------------------
# ניתוב
# ---------------------------------------------------------------------------
view = st.session_state.view
if view == "דשבורד חי":
    view_dashboard()
elif view == "תוכן שבועי":
    view_weekly()
else:
    view_settings()
