# integrity.py
"""
Integrity scoring and recommendation gating.

TRUTH CONTRACT:
- Integrity score must be >= 0.8 for recommendations
- Below threshold: show resolution tasks instead
- Tasks: classify credits, match transfers, reconcile statements
"""
from dataclasses import dataclass, field
from typing import Optional

from .reporting_models import IntegrityFlag, IntegrityReport, Report, ResolutionTask


# Threshold for showing recommendations
INTEGRITY_THRESHOLD = 0.8


@dataclass
class IntegrityAssessment:
    """Assessment of data integrity for a report."""
    score: float
    is_actionable: bool
    flags: list[IntegrityFlag]
    resolution_tasks: list[ResolutionTask]
    recommendation_blocked_reason: Optional[str] = None


def assess_integrity(report: Report) -> IntegrityAssessment:
    """
    Assess the integrity of a report and determine if recommendations are allowed.

    Returns:
        IntegrityAssessment with score, flags, and resolution tasks
    """
    integrity = report.integrity
    score = integrity.score
    is_actionable = score >= INTEGRITY_THRESHOLD

    tasks: list[ResolutionTask] = []

    # Generate resolution tasks for each flag
    if IntegrityFlag.UNCLASSIFIED_CREDIT in integrity.flags:
        tasks.append(ResolutionTask(
            task_type="CLASSIFY_CREDIT",
            description=f"Classify {integrity.unclassified_credit_count} unclassified credits (${integrity.unclassified_credit_cents/100:,.2f})",
            priority=1,
            affected_cents=integrity.unclassified_credit_cents,
        ))

    if IntegrityFlag.UNMATCHED_TRANSFER in integrity.flags:
        tasks.append(ResolutionTask(
            task_type="MATCH_TRANSFER",
            description=f"Match or classify {integrity.unmatched_transfer_count} unmatched transfers",
            priority=2,
            affected_cents=0,  # Would need to sum actual amounts
        ))

    if IntegrityFlag.RECONCILIATION_FAILED in integrity.flags:
        tasks.append(ResolutionTask(
            task_type="RECONCILE",
            description=f"Reconcile statement (delta: ${abs(integrity.reconciliation_delta_cents)/100:,.2f})",
            priority=1,
            affected_cents=abs(integrity.reconciliation_delta_cents),
        ))

    if IntegrityFlag.DUPLICATE_SUSPECTED in integrity.flags:
        tasks.append(ResolutionTask(
            task_type="REVIEW_DUPLICATES",
            description=f"Review {integrity.duplicate_suspect_count} suspected duplicates",
            priority=3,
            affected_cents=0,
        ))

    # Determine blocked reason
    blocked_reason = None
    if not is_actionable:
        blocked_reason = f"Integrity score ({score:.0%}) is below threshold ({INTEGRITY_THRESHOLD:.0%}). Resolve the tasks below first."

    return IntegrityAssessment(
        score=score,
        is_actionable=is_actionable,
        flags=integrity.flags,
        resolution_tasks=sorted(tasks, key=lambda t: t.priority),
        recommendation_blocked_reason=blocked_reason,
    )


def get_resolution_summary(report: Report) -> dict:
    """
    Get a summary of resolution tasks for display.

    Returns dict suitable for JSON/template rendering.
    """
    assessment = assess_integrity(report)

    return {
        "integrity_score": assessment.score,
        "integrity_percent": int(assessment.score * 100),
        "is_actionable": assessment.is_actionable,
        "blocked_reason": assessment.recommendation_blocked_reason,
        "tasks": [
            {
                "type": t.task_type,
                "description": t.description,
                "priority": t.priority,
                "priority_label": ["Critical", "High", "Medium", "Low"][min(t.priority - 1, 3)],
                "affected_amount": f"${t.affected_cents/100:,.2f}" if t.affected_cents else None,
            }
            for t in assessment.resolution_tasks
        ],
        "flag_count": len(assessment.flags),
    }


def can_show_recommendations(report: Report) -> bool:
    """Quick check if recommendations should be shown."""
    return report.integrity.is_actionable


def format_integrity_badge(score: float) -> tuple[str, str]:
    """
    Get badge text and CSS class for integrity score.

    Returns:
        (badge_text, css_class)
    """
    if score >= 0.95:
        return "Excellent", "badge-success"
    elif score >= 0.8:
        return "Good", "badge-info"
    elif score >= 0.6:
        return "Fair", "badge-warning"
    else:
        return "Needs Attention", "badge-danger"
