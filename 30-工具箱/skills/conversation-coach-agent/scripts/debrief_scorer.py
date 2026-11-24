#!/usr/bin/env python3
"""
debrief_scorer.py — Score a practice session and generate structured debrief.

Takes practice transcript analysis signals and produces a structured debrief
following the framework in references/debrief-framework.md.

Usage:
    python scripts/debrief_scorer.py --type SALARY_NEG --signals "caved_on_number,silence_not_held"
    python scripts/debrief_scorer.py --type JOB_INTERVIEW --signals "no_result_stated,strong_opening"
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import CONV_TYPES


# ─── Signal Library ───────────────────────────────────────────────────────────
# Maps detected weaknesses/strengths to coaching language

WEAKNESS_COACHING = {
    "caved_on_number": {
        "moment": "When you got pushback on the salary number",
        "problem": "You softened or lowered your ask before they gave you a real reason",
        "fix": "After you state your number, hold it with silence. If they push back, say: 'Help me understand the constraints' — let them move, not you.",
        "rewrite": "\"I was thinking $[X].\" [Pause. Let them respond. Don't add anything.]",
    },
    "silence_not_held": {
        "moment": "After you made your ask",
        "problem": "You filled the silence immediately — this undercuts the power of the ask",
        "fix": "The pause after a salary ask or key request is intentional. Count to 5 in your head if needed. The discomfort you feel is normal — let them break the silence.",
        "rewrite": "State your ask. Full stop. Wait.",
    },
    "no_result_stated": {
        "moment": "When telling your story or experience",
        "problem": "The answer described what you did, but not what happened because of it",
        "fix": "Every story needs an ending. Always add: 'The result was [specific outcome/metric].'",
        "rewrite": "\"...and as a result, [specific outcome — number, improvement, change].\"",
    },
    "rambled_opening": {
        "moment": "In your opening / 'tell me about yourself'",
        "problem": "The answer was long and chronological — it didn't give the listener a clear hook",
        "fix": "Structure it as: [Where I've been in one sentence] + [What I've built/achieved] + [Why THIS role]. End with why THIS role specifically.",
        "rewrite": "\"I've spent [X] years doing [thing]. Most recently, I [specific achievement]. I'm here because [specific reason this role].\"",
    },
    "vague_weakness_answer": {
        "moment": "When answering the weakness question",
        "problem": "The answer felt rehearsed or deflective (perfectionist, work too hard, etc.)",
        "fix": "Use a real weakness + what you've actively done about it + the progress you've made. Self-awareness + growth is what they want to see.",
        "rewrite": "\"I used to struggle with [real thing]. I noticed it when [situation]. I've worked on it by [specific action] and now [concrete progress].\"",
    },
    "blame_language": {
        "moment": "When describing the problem with your manager / partner",
        "problem": "The language used 'you always' or described the other person's intent rather than impact",
        "fix": "Focus on observable behavior and its impact, not intent. Use: 'When [specific thing happens], the impact for me is [specific impact].'",
        "rewrite": "\"When [specific observable thing], I notice [impact]. I wanted to talk about it because [reason].\"",
    },
    "no_concrete_ask": {
        "moment": "At the end of the difficult conversation",
        "problem": "The concern was expressed but no specific ask was made — this leaves the conversation open-ended",
        "fix": "Every difficult conversation needs a concrete ask. What do you want them to DO differently?",
        "rewrite": "\"What I'm asking for specifically is [concrete, actionable thing].\"",
    },
    "over_explained_weakness": {
        "moment": "When justifying your position",
        "problem": "Too much explaining or apologizing — reduces the power of the message",
        "fix": "State your point. Pause. Let it land. Over-explaining signals insecurity.",
        "rewrite": "State it clearly and stop. Trust the listener to process it.",
    },
    "missed_symptom_impact": {
        "moment": "When describing symptoms to the doctor",
        "problem": "The description focused on clinical terms but didn't communicate how this affects daily life",
        "fix": "Doctors hear symptoms all day. What gets attention is functional impact. Add: 'This has been significantly affecting my [sleep / work / daily activities].'",
        "rewrite": "\"...and this has been significantly affecting my [daily life area]. I can't [specific thing] because of it.\"",
    },
    "no_clear_call_to_action": {
        "moment": "At the end of the presentation",
        "problem": "The presentation concluded without a clear, specific ask for the audience",
        "fix": "End every presentation with one specific ask. 'I need you to [approve / fund / decide by] [specific thing by specific date].'",
        "rewrite": "\"What I'm asking you for today is [one specific thing]. Can we align on that?\"",
    },
    "strong_opening": {
        "type": "strength",
        "note": "The opening was specific, purposeful, and set the right tone immediately",
    },
    "held_position": {
        "type": "strength",
        "note": "When pushed back on, held position without caving — this is exactly right",
    },
    "used_specific_examples": {
        "type": "strength",
        "note": "Used specific, concrete examples rather than generalities — makes the case much stronger",
    },
    "named_intent_first": {
        "type": "strength",
        "note": "Named the reason for the conversation before the content — reduced defensiveness immediately",
    },
    "asked_good_question": {
        "type": "strength",
        "note": "Asked a thoughtful question that showed engagement and strategic thinking",
    },
}

READINESS_SIGNALS = [
    "held_position", "strong_opening", "used_specific_examples",
    "asked_good_question", "named_intent_first",
]


def score_session(conv_type, signals_list):
    """
    signals_list: list of signal keys from WEAKNESS_COACHING
    Returns structured debrief.
    """
    strengths = []
    weaknesses = []

    for signal in signals_list:
        coaching = WEAKNESS_COACHING.get(signal)
        if not coaching:
            continue
        if coaching.get("type") == "strength":
            strengths.append({"signal": signal, "note": coaching["note"]})
        else:
            weaknesses.append({
                "signal": signal,
                "moment": coaching["moment"],
                "problem": coaching["problem"],
                "fix": coaching["fix"],
                "rewrite": coaching.get("rewrite", ""),
            })

    # Pick the top weakness (most impactful for this type)
    type_priority_weaknesses = {
        "SALARY_NEG": ["caved_on_number", "silence_not_held", "over_explained_weakness"],
        "JOB_INTERVIEW": ["no_result_stated", "rambled_opening", "vague_weakness_answer"],
        "MANAGER_DIFFICULT": ["blame_language", "no_concrete_ask", "over_explained_weakness"],
        "PERSONAL_DIFFICULT": ["blame_language", "no_concrete_ask", "over_explained_weakness"],
        "MEDICAL": ["missed_symptom_impact", "no_concrete_ask"],
        "PRESENTATION": ["no_clear_call_to_action", "rambled_opening"],
    }

    priority = type_priority_weaknesses.get(conv_type, [])
    primary_weakness = None

    for pw in priority:
        for w in weaknesses:
            if w["signal"] == pw:
                primary_weakness = w
                break
        if primary_weakness:
            break

    if not primary_weakness and weaknesses:
        primary_weakness = weaknesses[0]

    # Readiness check
    strength_signals = {s["signal"] for s in strengths}
    ready_score = len(strength_signals.intersection(set(READINESS_SIGNALS)))
    is_ready = ready_score >= 2 and len(weaknesses) == 0

    return {
        "conversation_type": CONV_TYPES.get(conv_type, "Conversation"),
        "strengths": strengths[:2],
        "primary_weakness": primary_weakness,
        "all_weaknesses": weaknesses,
        "is_ready": is_ready,
        "ready_score": ready_score,
    }


def format_debrief(result):
    lines = []
    lines.append("\n" + "─" * 55)
    lines.append(f"DEBRIEF: {result['conversation_type'].upper()}")
    lines.append("─" * 55)

    if result["strengths"]:
        lines.append("\n✅ WHAT WORKED")
        for s in result["strengths"]:
            lines.append(f"   {s['note']}")

    pw = result["primary_weakness"]
    if pw:
        lines.append(f"\n🎯 ONE THING TO SHARPEN")
        lines.append(f"   Moment: {pw['moment']}")
        lines.append(f"   What happened: {pw['problem']}")
        lines.append(f"   The fix: {pw['fix']}")
        if pw.get("rewrite"):
            lines.append(f"\n💬 TRY THIS INSTEAD")
            lines.append(f"   {pw['rewrite']}")

    lines.append(f"\n➡️  NEXT STEP")
    if result["is_ready"]:
        lines.append("   You're ready for this conversation.")
        lines.append("   Is there anything that still feels unresolved?")
    else:
        lines.append("   Want to try that section again, or run the full thing once more?")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate debrief for a practice session")
    parser.add_argument("--type", default="UNKNOWN")
    parser.add_argument("--signals", help="Comma-separated signal keys")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    signals = [s.strip() for s in args.signals.split(",")] if args.signals else []
    result = score_session(args.type.upper(), signals)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_debrief(result))


if __name__ == "__main__":
    main()
