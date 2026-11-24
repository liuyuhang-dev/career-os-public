#!/usr/bin/env python3
"""utils.py — Shared utilities for Conversation Coach Agent scripts"""
import json, sys, re
from pathlib import Path
from datetime import datetime

CCA_VERSION = "1.0"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(data, path, pretty=True):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2 if pretty else None, default=str)
    print(f"  -> Saved: {path}", file=sys.stderr)

def now_date():
    return datetime.now().strftime("%Y-%m-%d")

# Conversation type codes
CONV_TYPES = {
    "JOB_INTERVIEW": "Job Interview",
    "SALARY_NEG": "Salary / Compensation Negotiation",
    "MANAGER_DIFFICULT": "Difficult Conversation with Manager",
    "PERSONAL_DIFFICULT": "Difficult Personal Conversation",
    "MEDICAL": "Medical / Healthcare Appointment",
    "PRESENTATION": "Presentation or Pitch",
    "NEGOTIATION_OTHER": "Negotiation (Non-Salary)",
    "WORKPLACE_CONFLICT": "Workplace Conflict",
    "FEEDBACK_GIVING": "Giving Feedback to Someone",
    "PROMOTION_ASK": "Asking for a Promotion",
    "QUIT_GRACEFULLY": "Resignation Conversation",
    "UNKNOWN": "General Conversation Coaching",
}

URGENCY_LEVELS = {
    "TODAY": "conversation is happening today",
    "TOMORROW": "conversation is tomorrow",
    "THIS_WEEK": "happening this week",
    "NEXT_WEEK": "next week",
    "SOON": "coming up soon",
    "GENERAL": "no specific time / general preparation",
}
