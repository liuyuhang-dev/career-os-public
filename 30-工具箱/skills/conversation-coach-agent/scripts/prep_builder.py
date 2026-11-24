#!/usr/bin/env python3
"""
prep_builder.py — Generate a structured preparation sheet for a conversation.

Takes a classified session and builds the prep components:
  - Core message template
  - Top 2-3 anticipated objections for this type
  - Opening line suggestions
  - Key phrase bank

Usage:
    python scripts/prep_builder.py --type SALARY_NEG --target 95000 --current 82000
    python scripts/prep_builder.py --classification /tmp/session.json
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import CONV_TYPES


# ─── Top Objections by Type ───────────────────────────────────────────────────

TOP_OBJECTIONS = {
    "JOB_INTERVIEW": [
        "\"Your experience doesn't perfectly match what we're looking for\"",
        "\"Why do you want to leave your current job?\"",
        "\"We have other strong candidates\"",
        "\"Where do you see yourself in 5 years?\"",
    ],
    "SALARY_NEG": [
        "\"That's outside our budget / range\"",
        "\"This is our standard offer — we don't negotiate\"",
        "\"We'll revisit compensation in 6 months at your review\"",
        "\"That's more than others at your level\"",
    ],
    "MANAGER_DIFFICULT": [
        "\"I don't think that's how it happened\"",
        "\"Are you happy here?\"",
        "\"Everyone has to deal with this\"",
        "\"I'll take that into consideration\" (without committing)",
    ],
    "PERSONAL_DIFFICULT": [
        "\"You're being too sensitive\"",
        "\"This is coming out of nowhere\"",
        "\"So you're saying I'm a bad [partner/friend]?\"",
        "Turning it around: \"What about what I need?\"",
    ],
    "MEDICAL": [
        "\"Let's just wait and see\"",
        "\"That's probably just stress/anxiety\"",
        "\"The test results are within normal range\"",
        "\"There isn't much more we can do\"",
    ],
    "PRESENTATION": [
        "\"How confident are you in those numbers?\"",
        "\"We tried something like this before and it didn't work\"",
        "\"What's the biggest risk here?\"",
        "\"What do you need from us to make this happen?\"",
    ],
    "PROMOTION_ASK": [
        "\"You're not quite ready yet\"",
        "\"There's no headcount / budget right now\"",
        "\"Let's see how Q3 goes first\"",
        "\"What makes you feel you're ready for the next level?\"",
    ],
    "QUIT_GRACEFULLY": [
        "\"Is there anything we can do to keep you?\"",
        "\"I thought you were happy here\"",
        "\"Can you stay longer to help with the transition?\"",
        "Counter-offer situation",
    ],
}


# ─── Core Message Templates by Type ──────────────────────────────────────────

CORE_MESSAGE_TEMPLATES = {
    "JOB_INTERVIEW": (
        "[My background summary in 1 sentence] + "
        "[What I've specifically achieved/built] + "
        "[Why THIS role matches where I want to go]"
    ),
    "SALARY_NEG": (
        "[Genuine enthusiasm for the opportunity] + "
        "[Specific value/experience I bring] + "
        "[My number, stated confidently] + "
        "[Openness to discuss]"
    ),
    "MANAGER_DIFFICULT": (
        "[I'm raising this because I care about working well here] + "
        "[Specific situation/pattern, no blame] + "
        "[What I'm asking for, concretely]"
    ),
    "PERSONAL_DIFFICULT": (
        "[I'm raising this because the relationship matters to me] + "
        "[How this has been affecting me, using 'I' not 'you'] + "
        "[What I'm hoping can change]"
    ),
    "MEDICAL": (
        "[When it started + how often + severity] + "
        "[How it's affecting my daily life — this communicates importance] + "
        "[Specific ask: test, referral, treatment change]"
    ),
    "PRESENTATION": (
        "[The problem/opportunity — in their terms] + "
        "[Why this matters now] + "
        "[What I'm proposing + what I need from them]"
    ),
    "PROMOTION_ASK": (
        "[What I've taken on beyond my current role] + "
        "[Specific results/impact] + "
        "[Where I want to grow] + "
        "[What I'm asking for and why the timing makes sense]"
    ),
    "QUIT_GRACEFULLY": (
        "[Gratitude for the opportunity — genuine] + "
        "[This was a hard decision, framed as growth] + "
        "[Transition plan/support offer]"
    ),
}


# ─── Key Phrases by Type ──────────────────────────────────────────────────────

KEY_PHRASES = {
    "JOB_INTERVIEW": [
        "\"In this [role title], I would...\" (use the actual role title)",
        "\"The result was [specific metric/outcome]\"",
        "\"What drew me specifically to [company] was...\"",
        "\"That's a great question — let me think about that for a moment\" (graceful recovery)",
    ],
    "SALARY_NEG": [
        "\"I was thinking [NUMBER].\" (Then: silence.)",
        "\"Help me understand the constraints\"",
        "\"Is there flexibility on [base / bonus / equity / start date]?\"",
        "\"I want to find a way to make this work for both of us\"",
    ],
    "MANAGER_DIFFICULT": [
        "\"I wanted to raise this because I want to make sure we're working well together\"",
        "\"When [specific situation] happens, the impact for me is [specific thing]\"",
        "\"I'm raising this because I want to stay here and do good work\"",
        "\"What I'm asking for is [concrete, specific ask]\"",
    ],
    "PERSONAL_DIFFICULT": [
        "\"I'm bringing this up because [relationship] matters to me\"",
        "\"I feel [emotion] when [specific situation]\"",
        "\"I'm not saying you're a bad [person] — I'm saying this specific thing has been affecting me\"",
        "\"Can we finish this before we move on?\" (if they redirect)",
    ],
    "MEDICAL": [
        "\"What would need to be true for you to [take further action]?\"",
        "\"This has been significantly affecting my [daily life/work/sleep]\"",
        "\"I'd like to make sure we've explored all the options available\"",
        "\"I'm thinking about getting a second opinion — not because I doubt you, but...\"",
    ],
    "PRESENTATION": [
        "\"The problem we're solving is [specific, quantified if possible]\"",
        "\"What I'm asking you for today is [one clear thing]\"",
        "\"I don't have that data right now — let me follow up with you directly\"",
        "[Pause before answering Q&A — it reads as confidence, not weakness]",
    ],
    "PROMOTION_ASK": [
        "\"I've been [doing specific things beyond my current role]\"",
        "\"I'd like to discuss a path to [next level]\"",
        "\"What would it take for this to happen in the next [6/12] months?\"",
        "\"I'm committed to this team — I want to grow here\"",
    ],
    "QUIT_GRACEFULLY": [
        "\"This was a genuinely hard decision\"",
        "\"I want to make this transition as smooth as possible for the team\"",
        "\"I'm happy to [document / train / help find my replacement]\"",
        "\"I'm grateful for what I've learned here\"",
    ],
}


def build_prep_sheet(conv_type, context=None):
    """Build a complete preparation sheet for the conversation type."""
    label = CONV_TYPES.get(conv_type, "Conversation")
    objections = TOP_OBJECTIONS.get(conv_type, ["Pushback on your main point"])
    core_msg = CORE_MESSAGE_TEMPLATES.get(conv_type, "[Your context] + [What you need] + [Why it matters]")
    phrases = KEY_PHRASES.get(conv_type, [])

    sheet = {
        "conversation_type": label,
        "core_message_template": core_msg,
        "top_objections_to_prepare_for": objections[:3],
        "key_phrases_to_memorize": phrases[:4],
        "graceful_recovery_phrase": "\"That's a good point — let me think about that for a second.\"",
        "your_goal_prompt": "Complete this: 'By the end of this conversation, the other person will ___'",
        "emotional_foundation": "Your value is not on the table here. One conversation does not define you.",
    }

    if context:
        sheet["context_provided"] = context

    return sheet


def format_sheet(sheet):
    lines = []
    lines.append(f"\n📋 PREPARATION SHEET: {sheet['conversation_type'].upper()}")
    lines.append("=" * 55)

    lines.append(f"\n🎯 YOUR GOAL")
    lines.append(f"   {sheet['your_goal_prompt']}")

    lines.append(f"\n💬 CORE MESSAGE STRUCTURE")
    lines.append(f"   {sheet['core_message_template']}")

    lines.append(f"\n⚡ PREPARE FOR THESE MOMENTS")
    for i, obj in enumerate(sheet['top_objections_to_prepare_for'], 1):
        lines.append(f"   {i}. {obj}")

    lines.append(f"\n🔑 KEY PHRASES TO HAVE READY")
    for phrase in sheet['key_phrases_to_memorize']:
        lines.append(f"   • {phrase}")

    lines.append(f"\n🆘 GRACEFUL RECOVERY (MEMORIZE THIS ONE)")
    lines.append(f"   {sheet['graceful_recovery_phrase']}")

    lines.append(f"\n🧠 MINDSET")
    lines.append(f"   {sheet['emotional_foundation']}")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Build a preparation sheet")
    parser.add_argument("--type", help="Conversation type code (e.g. SALARY_NEG)")
    parser.add_argument("--classification", help="JSON from session_classifier.py")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.classification:
        with open(args.classification) as f:
            data = json.load(f)
        conv_type = data.get("conversation_type", "UNKNOWN")
    elif args.type:
        conv_type = args.type.upper()
    else:
        conv_type = "UNKNOWN"

    sheet = build_prep_sheet(conv_type)

    if args.json:
        print(json.dumps(sheet, indent=2))
    else:
        print(format_sheet(sheet))


if __name__ == "__main__":
    main()
