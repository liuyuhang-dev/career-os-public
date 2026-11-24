#!/usr/bin/env python3
"""
session_classifier.py — Classify a coaching request.

Determines:
  - Conversation type (interview, salary, difficult manager, etc.)
  - Mode (PRACTICE or PREP)
  - Urgency (today, tomorrow, this week, general)
  - Emotional stakes (high / medium / low)
  - Key context signals

Usage:
    python scripts/session_classifier.py --text "I have a job interview tomorrow at Google"
    python scripts/session_classifier.py --text "I need to ask my boss for a raise"
"""
import argparse, re, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import CONV_TYPES, URGENCY_LEVELS


# ─── Conversation Type Signals ────────────────────────────────────────────────

TYPE_SIGNALS = {
    "JOB_INTERVIEW": [
        "job interview", "interview", "hiring", "job application",
        "tell me about yourself", "behavioral question", "final round",
        "phone screen", "hiring manager", "panel interview",
    ],
    "SALARY_NEG": [
        "salary", "raise", "compensation", "pay increase", "negotiate my offer",
        "salary negotiation", "ask for more money", "counter offer",
        "they offered me", "want to negotiate", "deserve more",
    ],
    "MANAGER_DIFFICULT": [
        "my boss", "my manager", "talk to my manager", "conversation with my boss",
        "difficult conversation at work", "give feedback to my manager",
        "manager keeps", "my manager is", "boss is",
    ],
    "PERSONAL_DIFFICULT": [
        "my partner", "my spouse", "my boyfriend", "my girlfriend",
        "my friend", "my mom", "my dad", "my parent", "my sibling",
        "difficult conversation", "hard conversation", "set limits",
        "break up", "confront", "they keep doing", "our relationship",
        "money talk", "partner's family",
    ],
    "MEDICAL": [
        "doctor", "appointment", "medical", "specialist", "symptoms",
        "diagnosis", "treatment", "prescription", "second opinion",
        "insurance", "advocate for myself", "hospital", "therapist",
        "ask my doctor",
    ],
    "PRESENTATION": [
        "presentation", "present to", "pitch", "stakeholders", "leadership",
        "slide deck", "public speaking", "speak at", "Q&A", "board meeting",
        "client presentation", "pitch my idea",
    ],
    "NEGOTIATION_OTHER": [
        "negotiate", "negotiation", "vendor", "contract", "rate",
        "price", "freelance rate", "supplier", "car purchase",
        "negotiate with",
    ],
    "PROMOTION_ASK": [
        "promotion", "promote me", "want a promotion", "title change",
        "step up", "next level", "ask for a promotion",
    ],
    "QUIT_GRACEFULLY": [
        "quit", "resign", "resignation", "leave my job", "give notice",
        "two weeks notice", "tell my boss i'm leaving",
    ],
    "FEEDBACK_GIVING": [
        "give feedback", "tell them they", "feedback to", "have to tell someone",
        "address someone's behavior", "colleague keeps",
    ],
    "WORKPLACE_CONFLICT": [
        "conflict with coworker", "colleague is", "coworker keeps",
        "workplace issue", "hostile work", "HR meeting",
    ],
}

# ─── Mode Signals ─────────────────────────────────────────────────────────────

PRACTICE_SIGNALS = [
    "practice", "roleplay", "role play", "let's try it", "play the",
    "act as", "be the interviewer", "simulate", "run through it",
    "rehearse", "can we practice", "practice with me",
]

PREP_SIGNALS = [
    "prepare", "help me prepare", "what should i say", "how do i say",
    "what will they ask", "think through", "strategy", "tips",
    "advice", "help me think", "not sure what to say", "don't know how",
    "scared to say", "nervous about",
]

# ─── Urgency Signals ──────────────────────────────────────────────────────────

URGENCY_SIGNALS = {
    "TODAY": ["today", "in an hour", "in 30 minutes", "in a few hours", "this afternoon", "tonight"],
    "TOMORROW": ["tomorrow", "tomorrow morning", "tomorrow afternoon"],
    "THIS_WEEK": ["this week", "in a few days", "thursday", "friday", "wednesday"],
    "NEXT_WEEK": ["next week", "next monday", "next tuesday"],
    "SOON": ["soon", "coming up", "scheduled", "next month"],
}

# ─── Emotional Stakes Signals ─────────────────────────────────────────────────

HIGH_STAKES_SIGNALS = [
    "terrified", "scared", "nervous", "anxious", "dreading",
    "so important", "everything depends", "can't mess this up",
    "desperate", "really need this", "life changing",
    "never done this before", "history of", "last time i tried",
]

MEDIUM_STAKES_SIGNALS = [
    "nervous", "bit worried", "want to do well", "hoping",
    "want to get it right", "want to be ready",
]


def classify(text):
    text_lower = text.lower()

    # Detect conversation type
    type_scores = {}
    for conv_type, signals in TYPE_SIGNALS.items():
        score = sum(1 for s in signals if s in text_lower)
        if score > 0:
            type_scores[conv_type] = score

    # Priority for ties
    type_priority = [
        "SALARY_NEG", "JOB_INTERVIEW", "MEDICAL", "QUIT_GRACEFULLY",
        "PROMOTION_ASK", "MANAGER_DIFFICULT", "PERSONAL_DIFFICULT",
        "PRESENTATION", "FEEDBACK_GIVING", "WORKPLACE_CONFLICT",
        "NEGOTIATION_OTHER",
    ]

    detected_type = "UNKNOWN"
    if type_scores:
        max_score = max(type_scores.values())
        for t in type_priority:
            if type_scores.get(t, 0) == max_score:
                detected_type = t
                break

    # Detect mode
    practice_score = sum(1 for s in PRACTICE_SIGNALS if s in text_lower)
    prep_score = sum(1 for s in PREP_SIGNALS if s in text_lower)

    if practice_score > prep_score:
        mode = "PRACTICE"
    elif prep_score > practice_score:
        mode = "PREP"
    else:
        mode = "PREP"  # default to prep before practice

    # Detect urgency
    urgency = "GENERAL"
    for level, signals in URGENCY_SIGNALS.items():
        if any(s in text_lower for s in signals):
            urgency = level
            break

    # Detect emotional stakes
    high_count = sum(1 for s in HIGH_STAKES_SIGNALS if s in text_lower)
    medium_count = sum(1 for s in MEDIUM_STAKES_SIGNALS if s in text_lower)

    if high_count >= 1:
        emotional_stakes = "high"
    elif medium_count >= 1:
        emotional_stakes = "medium"
    else:
        emotional_stakes = "medium"  # default

    # Extract numeric clues (salary numbers, etc.)
    amounts = re.findall(r'\$[\d,]+[k]?|\b\d{2,3}k\b|\b\d{5,6}\b', text, re.IGNORECASE)

    # Detect difficulty request
    difficulty = "realistic"
    if any(w in text_lower for w in ["hard mode", "tough", "hardest", "most difficult", "stress test"]):
        difficulty = "hard"
    elif any(w in text_lower for w in ["easy", "gentle", "soft", "supportive"]):
        difficulty = "easy"

    return {
        "conversation_type": detected_type,
        "conversation_type_label": CONV_TYPES.get(detected_type, "General"),
        "mode": mode,
        "urgency": urgency,
        "urgency_label": URGENCY_LEVELS.get(urgency, "general preparation"),
        "emotional_stakes": emotional_stakes,
        "difficulty_requested": difficulty,
        "type_scores": type_scores,
        "practice_signals_found": practice_score,
        "prep_signals_found": prep_score,
        "amounts_mentioned": amounts[:3],
    }


def print_result(r):
    print(f"\n🎭 Session Classification")
    print(f"   Conversation type: {r['conversation_type_label']}")
    print(f"   Mode: {r['mode']}")
    print(f"   Urgency: {r['urgency_label']}")
    print(f"   Emotional stakes: {r['emotional_stakes']}")
    print(f"   Difficulty: {r['difficulty_requested']}")
    if r['amounts_mentioned']:
        print(f"   Amounts mentioned: {', '.join(r['amounts_mentioned'])}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Classify a coaching request")
    parser.add_argument("--text", help="User's request text")
    parser.add_argument("--file", help="File with text")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    result = classify(text)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_result(result)


if __name__ == "__main__":
    main()
