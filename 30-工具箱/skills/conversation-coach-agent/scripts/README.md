# Scripts Reference — Conversation Coach Agent

## Script Index

| Script | Purpose | Phase |
|---|---|---|
| `utils.py` | Shared constants, type codes | All |
| `session_classifier.py` | Classify request → type + mode + urgency + stakes | UNDERSTAND |
| `prep_builder.py` | Generate structured prep sheet by conversation type | PREPARE |
| `debrief_scorer.py` | Generate structured debrief from session signals | PRACTICE debrief |

## Typical Workflow

```bash
# Step 1: Classify the request
python scripts/session_classifier.py \
  --text "I have a salary negotiation tomorrow and I keep caving" \
  --json > /tmp/cca/session.json

# Step 2: Build prep sheet
python scripts/prep_builder.py \
  --classification /tmp/cca/session.json

# Step 3: (After roleplay) Generate debrief
python scripts/debrief_scorer.py \
  --type SALARY_NEG \
  --signals "caved_on_number,silence_not_held"
```

## Signal Keys for debrief_scorer.py

### Weaknesses
| Signal | When to Use |
|---|---|
| `caved_on_number` | User backed down on salary ask too quickly |
| `silence_not_held` | User filled silence after key ask |
| `no_result_stated` | Story told without stating outcome/result |
| `rambled_opening` | Opening was too long or chronological without hook |
| `vague_weakness_answer` | Weakness answer was cliché (perfectionist, etc.) |
| `blame_language` | Used "you always/never" framing |
| `no_concrete_ask` | Expressed concern but no specific ask made |
| `over_explained_weakness` | Too much justification/apologizing |
| `missed_symptom_impact` | Described symptoms without daily-life impact |
| `no_clear_call_to_action` | Presentation ended without clear next step |

### Strengths
| Signal | When to Use |
|---|---|
| `strong_opening` | Opening was specific and purposeful |
| `held_position` | Held position when pushed back on |
| `used_specific_examples` | Used concrete specifics, not generalities |
| `named_intent_first` | Named reason for conversation before content |
| `asked_good_question` | Asked a thoughtful, strategic question |

## Note

Scripts are signal detectors that inform the agent's coaching.
The agent (Claude) writes the actual roleplay dialogue and spoken coaching —
these scripts surface the structural framework.
