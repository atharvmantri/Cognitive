"""
Cognitive Server - Auto-Draft Generator
Context-aware response templates for scheduling deferral, capacity decline,
and focus-block notifications across Gmail and Slack.
"""

import datetime


# --- Template Library ---

TEMPLATES = {
    "deep_focus_deferral": {
        "gmail": (
            "I'm currently in a deep focus block and may be slow to respond. "
            "I'll get back to you by {predicted_low_load_time}. "
            "If this is urgent, please flag it with [URGENT] in the subject line."
        ),
        "slack": (
            "I'm in a focus block right now — I'll respond by {predicted_low_load_time}. "
            "If urgent, ping me again and I'll prioritize."
        ),
    },
    "calendar_reschedule": {
        "gmail": (
            "My current schedule suggests {suggested_time} would be the best time for this — "
            "my cognitive load is lowest then and I can give this my full attention. "
            "Does that work for you?"
        ),
        "slack": (
            "{suggested_time} would work best on my end — I'll have the clearest headspace then. "
            "Does that fit your schedule?"
        ),
    },
    "capacity_decline": {
        "gmail": (
            "I'm at capacity today and wouldn't be able to give this the attention it deserves. "
            "Could we revisit this on {predicted_low_load_day}? "
            "I want to make sure I'm fully present when we discuss it."
        ),
        "slack": (
            "I'm at capacity today — can this wait until {predicted_low_load_day}? "
            "I want to give it proper focus rather than rush through it."
        ),
    },
    "generic_deferral": {
        "gmail": (
            "Thanks for reaching out. I'm managing my focus time carefully today. "
            "I'll respond properly by {predicted_low_load_time}. "
            "Appreciate your understanding."
        ),
        "slack": (
            "Got it — I'm heads-down right now. "
            "I'll circle back by {predicted_low_load_time}. "
            "Thanks for understanding."
        ),
    },
    "urgency_ack": {
        "gmail": (
            "I see this is urgent and I'm acknowledging it. "
            "I'm currently in a focus block but will address {subject} as soon as "
            "I'm at a good break point (estimated: {predicted_low_load_time}). "
            "If truly immediate attention is needed, please call or text."
        ),
        "slack": (
            "I see the urgency. I'm mid-focus-block and will address this as soon as "
            "I hit a natural break (est. {predicted_low_load_time}). "
            "If it can't wait, give me a ping — I'll context-switch."
        ),
    },
}


def generate_scheduling_response(context: str, proposed_time: str,
                                  energy_level: float, load_state: str) -> str:
    """
    Generate an auto-response for scheduling requests.

    Args:
        context: Meeting context (e.g., "Sprint planning")
        proposed_time: The best suggested time slot
        energy_level: 0.0-1.0 predicted energy at that time
        load_state: Current load state label

    Returns:
        A natural-language response string.
    """
    energy_pct = energy_level * 100

    if energy_level > 0.7:
        # High predicted energy at that slot — confident acceptance
        if load_state in ("focused", "heavy", "overloaded"):
            return (
                f"My calendar suggests {proposed_time} would work best — "
                f"I'll have the clearest headspace then and can give "
                f"{context} my full attention. Does that fit your schedule?"
            )
        else:
            return (
                f"{proposed_time} works great for me — "
                f"I'll have strong focus at that time. Let's go with it?"
            )
    elif energy_level > 0.4:
        # Moderate energy — tentative acceptance with caveat
        return (
            f"{proposed_time} could work — I'll have reasonable bandwidth then. "
            f"My focus might not be at its peak, but I should be able to "
            f"contribute meaningfully to {context}."
        )
    else:
        # Low predicted energy — suggest alternative
        later_time = _suggest_later_slot(proposed_time)
        return (
            f"I'd struggle at {proposed_time} — my predicted energy is quite low then. "
            f"How about {later_time} instead? That should give us both a better window."
        )


def generate_deferral_response(platform: str, template_key: str,
                                load_state: str, predicted_low_load_time: str,
                                predicted_low_load_day: str = "",
                                subject: str = "") -> str:
    """
    Generate a context-aware deferral message for a given platform.

    Args:
        platform: 'gmail' or 'slack'
        template_key: Which template to use
        load_state: Current cognitive load state
        predicted_low_load_time: Next predicted low-load time
        predicted_low_load_day: Next predicted low-load day (for capacity_decline)
        subject: Email subject for Gmail urgency templates

    Returns:
        Formatted response string.
    """
    templates = TEMPLATES.get(template_key, TEMPLATES["generic_deferral"])
    template = templates.get(platform, templates.get("gmail", ""))

    # Fill placeholders
    response = template.format(
        predicted_low_load_time=predicted_low_load_time,
        predicted_low_load_day=predicted_low_load_day,
        subject=subject,
    )

    return response


def _suggest_later_slot(original_time: str) -> str:
    """Suggest a later time slot (roughly +2 hours from original)."""
    try:
        dt = datetime.datetime.fromisoformat(original_time.replace("Z", "+00:00"))
        later = dt + datetime.timedelta(hours=2)
        return later.strftime("%a %b %d, %I:%M %p UTC")
    except Exception:
        return "later today"