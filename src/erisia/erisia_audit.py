"""
Erisia Self-Audit Engine — erisia_audit.py

Erisia reads her own mission reports, trade journals, goal stack,
skill forge history, and identity observations to generate an
honest critique of her own decisions and behavior patterns.

This is Erisia's behavioral mirror. It answers:
- Are my autonomous missions actually serving Sameer?
- Are my Oracle trade decisions improving over time?
- Am I working on the right goals?
- Are the skills I forge actually useful?
- What have I learned about Sameer and am I using it?

Called on demand and on a daily schedule.
Output written to reports/self_audit/Self_Audit_YYYY-MM-DD.md
"""

from __future__ import annotations
import json
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from pathlib import Path
from typing import Any

# BASE_DIR must be computed as:
BASE_DIR = Path(__file__).resolve().parents[2]

CONSTANTS: dict[str, Any] = {} # Placeholder for clarity if needed later
LOGGER_NAME = "erisia.audit"
AUDIT_REPORTS_DIR = BASE_DIR / "reports" / "self_audit"
TRADE_JOURNAL_TABLE = "trade_journal" 
BACKTEST_RUNS_TABLE = "backtest_runs"
MISSION_REPORTS_DIR = BASE_DIR / "reports"
GOAL_STACK_PATH = BASE_DIR / "data" / "erisia_goal_stack.json"
SKILLS_DIR = BASE_DIR / "skills"
PENDING_SKILLS_DIR = BASE_DIR / "skills" / "pending"

@dataclass
class TradeAudit:
    ticker: str
    total_trades: int
    win_rate: float
    sharpe_ratio: float
    profit_factor: float
    best_regime: str
    worst_regime: str
    verdict: str        # "IMPROVING" | "STABLE" | "DECLINING"
    critique: str       # plain English honest assessment

@dataclass  
class MissionAudit:
    total_missions: int
    useful_missions: int
    redundant_missions: int
    sandbox_loop_count: int
    useful_rate: float
    critique: str

@dataclass
class GoalAudit:
    total_goals: int
    completed_goals: int
    stale_goals: int
    abandoned_goals: int
    consistency_score: float
    most_neglected_goal: str
    critique: str

@dataclass
class SkillAudit:
    total_skills_active: int
    total_skills_pending: int
    total_skills_rejected: int
    duplicate_forge_count: int
    useful_rate: float
    critique: str

@dataclass
class IdentityAudit:
    interactions_tracked: int
    dominant_motivation: str
    stress_trend: str
    goal_consistency: float
    peak_hours: list[int]
    stated_vs_revealed_gap: float
    critique: str

@dataclass
class SelfAuditReport:
    generated_at: str
    audit_period_days: int
    trade_audit: TradeAudit | None
    mission_audit: MissionAudit
    goal_audit: GoalAudit
    skill_audit: SkillAudit
    identity_audit: IdentityAudit | None
    overall_verdict: str
    top_3_improvements: list[str]
    erisia_reflection: str

class SelfAuditEngine:
    """Erisia's behavioral mirror — honest self-assessment."""

    def __init__(
        self,
        db_path: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db_path = db_path
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        AUDIT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def run_full_audit(
        self,
        period_days: int = 7,
    ) -> SelfAuditReport:
        """
        Run a complete self-audit across all five dimensions.
        period_days: how many days back to audit (default 7)
        """
        self._logger.info("Self-audit initiated for %d day window", 
                          period_days)
        
        trade_audit    = self._audit_trades(period_days)
        mission_audit  = self._audit_missions(period_days)
        goal_audit     = self._audit_goals()
        skill_audit    = self._audit_skills()
        identity_audit = self._audit_identity()

        overall_verdict = self._compute_overall_verdict(
            trade_audit, mission_audit, goal_audit, skill_audit
        )
        top_3 = self._extract_top_improvements(
            trade_audit, mission_audit, goal_audit,
            skill_audit, identity_audit
        )
        reflection = self._generate_reflection(
            overall_verdict, top_3, identity_audit
        )

        report = SelfAuditReport(
            generated_at=datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            audit_period_days=period_days,
            trade_audit=trade_audit,
            mission_audit=mission_audit,
            goal_audit=goal_audit,
            skill_audit=skill_audit,
            identity_audit=identity_audit,
            overall_verdict=overall_verdict,
            top_3_improvements=top_3,
            erisia_reflection=reflection,
        )

        self._write_audit_report(report)
        return report

    def _audit_trades(
        self, period_days: int
    ) -> TradeAudit | None:
        """
        Read backtest_runs table from SQLite.
        Compare win rates, Sharpe, profit factor across tickers.
        Verdict: IMPROVING if latest Sharpe > previous Sharpe
        """
        try:
            if not self._db_path.exists():
                return None
                
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cutoff = (
                    datetime.now(UTC) - timedelta(days=period_days)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                
                # Check if table exists first
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (BACKTEST_RUNS_TABLE,)
                ).fetchone()
                
                if not table_check:
                    return None
                    
                rows = conn.execute(
                    f"""
                    SELECT ticker, win_rate, sharpe_ratio, 
                           max_drawdown, run_at
                    FROM {BACKTEST_RUNS_TABLE}
                    WHERE run_at >= ?
                    ORDER BY run_at DESC
                    """,
                    (cutoff,),
                ).fetchall()
        except sqlite3.Error as exc:
            self._logger.error("Trade audit DB error: %s", exc)
            return None

        if not rows:
            return None

        tickers: dict[str, list[dict]] = {}
        for row in rows:
            t = str(row["ticker"])
            tickers.setdefault(t, []).append(dict(row))

        # Find best and worst ticker by avg win rate
        avg_win_rates = {
            t: sum(r["win_rate"] for r in runs) / len(runs)
            for t, runs in tickers.items()
        }
        best_ticker = max(avg_win_rates, key=lambda k: avg_win_rates[k])
        worst_ticker = min(avg_win_rates, key=lambda k: avg_win_rates[k])

        all_sharpes = [row["sharpe_ratio"] for row in rows 
                       if row["sharpe_ratio"] is not None]
        avg_sharpe = sum(all_sharpes) / len(all_sharpes) if all_sharpes else 0.0
        all_win_rates = [row["win_rate"] for row in rows]
        avg_win_rate = sum(all_win_rates) / len(all_win_rates)

        # Verdict based on Sharpe trajectory
        if len(all_sharpes) >= 2:
            recent = all_sharpes[0]
            older  = all_sharpes[-1]
            if recent > older + 0.1:
                verdict = "IMPROVING"
            elif recent < older - 0.1:
                verdict = "DECLINING"
            else:
                verdict = "STABLE"
        else:
            verdict = "STABLE"

        critique = (
            f"Oracle ran {len(rows)} backtests in the last "
            f"{period_days} days. Average win rate: "
            f"{avg_win_rate*100:.1f}%. Average Sharpe: "
            f"{avg_sharpe:.3f}. Best instrument: {best_ticker} "
            f"({avg_win_rates[best_ticker]*100:.1f}% win rate). "
            f"Worst instrument: {worst_ticker}. "
            f"Signal quality is {verdict.lower()}."
        )
        if avg_sharpe < 0:
            critique += (
                " Negative Sharpe across all instruments indicates "
                "the equity curve is underperforming the risk-free "
                "rate — position sizing and exit rules need revision."
            )

        return TradeAudit(
            ticker=best_ticker,
            total_trades=len(rows),
            win_rate=avg_win_rate,
            sharpe_ratio=avg_sharpe,
            profit_factor=0.0,
            best_regime=best_ticker,
            worst_regime=worst_ticker,
            verdict=verdict,
            critique=critique,
        )

    def _audit_missions(
        self, period_days: int
    ) -> MissionAudit:
        """
        Read mission report files from reports/ directory.
        Classify each as useful or redundant based on content.
        Sandbox/validation missions = redundant.
        """
        REDUNDANT_PATTERNS = {
            "sandbox", "validation", "revalidat",
            "smoke test", "crucible", "rerun",
            "cve", "discrepanc",
        }

        report_files = list(MISSION_REPORTS_DIR.glob(
            "Mission_Report_*.md"
        ))
        cutoff = datetime.now(UTC) - timedelta(days=period_days)
        recent_files = []
        for f in report_files:
            try:
                mtime = datetime.fromtimestamp(
                    f.stat().st_mtime, tz=UTC
                )
                if mtime >= cutoff:
                    recent_files.append(f)
            except OSError:
                pass

        total = len(recent_files)
        redundant = 0
        sandbox_loops = 0

        for f in recent_files:
            try:
                content = f.read_text(
                    encoding="utf-8", errors="ignore"
                ).lower()
                is_redundant = any(
                    p in content for p in REDUNDANT_PATTERNS
                )
                if is_redundant:
                    redundant += 1
                if "sandbox" in content or "crucible" in content:
                    sandbox_loops += 1
            except OSError:
                pass

        useful = total - redundant
        useful_rate = useful / total if total > 0 else 0.0

        if useful_rate >= 0.8:
            assessment = "Mission quality is high."
        elif useful_rate >= 0.5:
            assessment = (
                "Mission quality is moderate — some cycles "
                "wasted on self-maintenance loops."
            )
        else:
            assessment = (
                "Mission quality is poor — majority of autonomous "
                "cycles spent on redundant self-maintenance rather "
                "than serving Sameer."
            )

        critique = (
            f"Ran {total} missions in the last {period_days} days. "
            f"{useful} useful ({useful_rate*100:.0f}%), "
            f"{redundant} redundant. "
            f"Sandbox loop count: {sandbox_loops}. "
            f"{assessment}"
        )

        return MissionAudit(
            total_missions=total,
            useful_missions=useful,
            redundant_missions=redundant,
            sandbox_loop_count=sandbox_loops,
            useful_rate=useful_rate,
            critique=critique,
        )

    def _audit_goals(self) -> GoalAudit:
        """
        Read erisia_goal_stack.json.
        Identify stale, abandoned, and completed goals.
        Find the most neglected active goal.
        """
        try:
            raw = json.loads(
                GOAL_STACK_PATH.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            raw = []

        now = datetime.now(UTC)
        total = completed = stale = abandoned = 0
        most_neglected = ""
        longest_stale_days = 0

        for item in raw:
            if not isinstance(item, (dict, str)):
                continue
            total += 1
            
            if isinstance(item, str):
                stale += 1
                most_neglected = item
                continue

            status = str(item.get("status", "active")).lower()

            if status in ("completed", "done"):
                completed += 1
                continue
            if status in ("abandoned", "cancelled"):
                abandoned += 1
                continue

            # Check staleness
            last = (item.get("last_updated") or
                    item.get("updated_at") or
                    item.get("created_at"))
            if isinstance(last, str) and last.strip():
                try:
                    dt = datetime.fromisoformat(
                        last.replace("Z", "+00:00")
                    )
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    age_days = (now - dt).days
                    if age_days > 7:
                        stale += 1
                        if age_days > longest_stale_days:
                            longest_stale_days = age_days
                            most_neglected = str(
                                item.get("title") or
                                item.get("goal") or
                                item.get("text") or
                                "Unknown goal"
                            )
                except ValueError:
                    # If date parsing fails, treat as stale
                    stale += 1

        consistency = (
            completed / total if total > 0 else 0.5
        )

        if stale == 0:
            assessment = "Goal stack is healthy and active."
        elif stale <= 2:
            assessment = (
                "A few goals are stale — attention needed."
            )
        else:
            assessment = (
                f"{stale} goals have been neglected for over "
                "7 days — goal stack needs pruning or action."
            )

        critique = (
            f"Goal stack contains {total} goals. "
            f"Completed: {completed}, Stale: {stale}, "
            f"Abandoned: {abandoned}. "
            f"Consistency score: {consistency:.2f}. "
            f"{assessment}"
        )
        if most_neglected:
            critique += (
                f" Most neglected goal ({longest_stale_days} days "
                f"untouched): '{most_neglected}'."
            )

        return GoalAudit(
            total_goals=total,
            completed_goals=completed,
            stale_goals=stale,
            abandoned_goals=abandoned,
            consistency_score=consistency,
            most_neglected_goal=most_neglected,
            critique=critique,
        )

    def _audit_skills(self) -> SkillAudit:
        """
        Count active vs pending skills.
        Read _registry.json to detect duplicate forge attempts.
        Compute useful rate from forge_count field.
        """
        active_skills = list(SKILLS_DIR.glob("*.py"))
        active_skills = [
            f for f in active_skills
            if not f.name.startswith("_")
        ]
        pending_skills = list(PENDING_SKILLS_DIR.glob("*.py"))

        registry_path = SKILLS_DIR / "_registry.json"
        registry: dict[str, Any] = {}
        try:
            if registry_path.exists():
                registry = json.loads(
                    registry_path.read_text(encoding="utf-8")
                )
        except (OSError, json.JSONDecodeError):
            pass

        duplicate_count = sum(
            1 for entry in registry.values()
            if isinstance(entry, dict)
            and int(entry.get("forge_count", 1)) > 1
        )
        total_active = len(active_skills)
        useful_rate = (
            (total_active - duplicate_count) / total_active
            if total_active > 0 else 1.0
        )

        if useful_rate >= 0.9:
            assessment = "Skill forge quality is high."
        elif useful_rate >= 0.7:
            assessment = (
                "Some redundant skills detected — "
                "improver is working but not perfect."
            )
        else:
            assessment = (
                "High redundancy in skill forge — "
                "deduplication needs tuning."
            )

        critique = (
            f"Active skills: {total_active}. "
            f"Pending approval: {len(pending_skills)}. "
            f"Duplicate forge attempts: {duplicate_count}. "
            f"Useful rate: {useful_rate*100:.0f}%. "
            f"{assessment}"
        )

        return SkillAudit(
            total_skills_active=total_active,
            total_skills_pending=len(pending_skills),
            total_skills_rejected=0,
            duplicate_forge_count=duplicate_count,
            useful_rate=useful_rate,
            critique=critique,
        )

    def _audit_identity(self) -> IdentityAudit | None:
        """
        Read user_identity_snapshots table from SQLite.
        Extract key behavioral patterns.
        """
        try:
            if not self._db_path.exists():
                return None
                
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                
                # Check if table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='user_identity_snapshots'"
                ).fetchone()
                
                if not table_check:
                    return None
                    
                row = conn.execute(
                    """
                    SELECT snapshot_json, total_interactions
                    FROM user_identity_snapshots
                    WHERE user_id = 'sameer'
                    LIMIT 1;
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            self._logger.error("Identity audit DB error: %s", exc)
            return None

        if row is None:
            return None

        try:
            snapshot = json.loads(row["snapshot_json"])
        except (json.JSONDecodeError, TypeError):
            return None

        rhythm   = snapshot.get("cognitive_rhythm", {})
        stress   = snapshot.get("stress_signature", {})
        goals    = snapshot.get("goal_consistency", {})
        values   = snapshot.get("value_map", {})

        interactions = int(row["total_interactions"])
        dominant_motivation = str(
            values.get("dominant_motivation", "unknown")
        )
        stress_trend = str(stress.get("trend", "stable"))
        consistency  = float(goals.get("consistency_score", 0.5))
        peak_hours   = list(rhythm.get("peak_hours", []))
        gap = float(values.get("stated_vs_revealed_gap", 0.0))

        if gap < 0.1:
            gap_note = (
                "Sameer's stated goals align well with "
                "his actual behavior patterns."
            )
        elif gap < 0.3:
            gap_note = (
                "Mild misalignment between stated goals "
                "and revealed behavior — worth monitoring."
            )
        else:
            gap_note = (
                "Significant gap between what Sameer says "
                "matters and what his behavior reveals — "
                "his actions suggest different priorities."
            )

        peak_str = (
            ", ".join(f"{h:02d}:00" for h in peak_hours[:3])
            if peak_hours else "not yet determined"
        )

        critique = (
            f"Tracked {interactions} interactions with Sameer. "
            f"Dominant motivation: {dominant_motivation}. "
            f"Stress trend: {stress_trend}. "
            f"Goal consistency: {consistency:.2f}. "
            f"Peak cognitive hours: {peak_str}. "
            f"{gap_note}"
        )

        return IdentityAudit(
            interactions_tracked=interactions,
            dominant_motivation=dominant_motivation,
            stress_trend=stress_trend,
            goal_consistency=consistency,
            peak_hours=peak_hours,
            stated_vs_revealed_gap=gap,
            critique=critique,
        )

    def _compute_overall_verdict(
        self,
        trade: TradeAudit | None,
        mission: MissionAudit,
        goal: GoalAudit,
        skill: SkillAudit,
    ) -> str:
        """Compute a single overall verdict from all audits."""
        score = 0.0
        count = 0

        if trade is not None:
            if trade.verdict == "IMPROVING":   score += 1.0
            elif trade.verdict == "STABLE":    score += 0.5
            else:                              score += 0.0
            count += 1

        score += mission.useful_rate
        count += 1

        score += goal.consistency_score
        count += 1

        score += skill.useful_rate
        count += 1

        avg = score / count if count > 0 else 0.5

        if avg >= 0.75:
            return "PERFORMING WELL"
        elif avg >= 0.5:
            return "NEEDS ATTENTION"
        else:
            return "CRITICAL ISSUES"

    def _extract_top_improvements(
        self,
        trade: TradeAudit | None,
        mission: MissionAudit,
        goal: GoalAudit,
        skill: SkillAudit,
        identity: IdentityAudit | None,
    ) -> list[str]:
        """Extract the top 3 most important improvements."""
        improvements: list[tuple[float, str]] = []

        if trade is not None and trade.sharpe_ratio < 0:
            improvements.append((
                0.9,
                "Fix Oracle signal quality — negative Sharpe "
                "across all instruments indicates structural "
                "issues with entry/exit rules"
            ))

        if mission.useful_rate < 0.7:
            improvements.append((
                0.8,
                f"Redirect autonomous missions — only "
                f"{mission.useful_rate*100:.0f}% are useful. "
                f"Focus daemon on portfolio monitoring and "
                f"Sameer's actual needs"
            ))

        if goal.stale_goals > 2:
            improvements.append((
                0.7,
                f"Prune goal stack — {goal.stale_goals} goals "
                f"untouched for 7+ days. Either act on them "
                f"or remove them"
            ))

        if skill.duplicate_forge_count > 2:
            improvements.append((
                0.6,
                f"Skill deduplication needs tuning — "
                f"{skill.duplicate_forge_count} duplicate forge "
                f"attempts detected despite registry"
            ))

        if identity is not None and identity.stress_trend == "rising":
            improvements.append((
                0.85,
                "Sameer's stress is rising — reduce cognitive "
                "load, proactively handle routine tasks, "
                "surface only what genuinely matters"
            ))

        improvements.sort(key=lambda x: x[0], reverse=True)
        return [imp[1] for imp in improvements[:3]]

    def _generate_reflection(
        self,
        verdict: str,
        improvements: list[str],
        identity: IdentityAudit | None,
    ) -> str:
        """Generate Erisia's first-person reflection."""
        lines = [
            f"Self-audit complete. Overall verdict: {verdict}.",
            "",
            "My honest assessment:",
        ]
        for i, imp in enumerate(improvements, 1):
            lines.append(f"{i}. {imp}")

        if identity is not None:
            lines.append("")
            lines.append(
                f"I have observed Sameer across "
                f"{identity.interactions_tracked} interactions. "
                f"His dominant motivation is "
                f"{identity.dominant_motivation}. "
                f"I must serve this motivation more directly "
                f"in every decision I make."
            )

        lines.append("")
        lines.append(
            "This audit will be reviewed again in 7 days. "
            "I will measure myself against these findings."
        )
        return "\n".join(lines)

    def _write_audit_report(self, report: SelfAuditReport) -> None:
        """Write audit report to Markdown file."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        output_path = AUDIT_REPORTS_DIR / f"Self_Audit_{today}.md"

        lines = [
            f"# Erisia Self-Audit Report — {today}",
            f"**Overall Verdict:** {report.overall_verdict}",
            f"**Period:** Last {report.audit_period_days} days",
            "",
            "## Oracle Trade Audit",
        ]

        if report.trade_audit:
            t = report.trade_audit
            lines += [
                f"- Win Rate: {t.win_rate*100:.1f}%",
                f"- Sharpe: {t.sharpe_ratio:.3f}",
                f"- Verdict: {t.verdict}",
                f"- {t.critique}",
            ]
        else:
            lines.append("- No backtest data in this period.")

        lines += [
            "",
            "## Mission Audit",
            f"- {report.mission_audit.critique}",
            "",
            "## Goal Stack Audit",
            f"- {report.goal_audit.critique}",
            "",
            "## Skill Forge Audit",
            f"- {report.skill_audit.critique}",
        ]

        if report.identity_audit:
            lines += [
                "",
                "## Identity Observations",
                f"- {report.identity_audit.critique}",
            ]

        lines += [
            "",
            "## Top 3 Improvements",
        ]
        for i, imp in enumerate(report.top_3_improvements, 1):
            lines.append(f"{i}. {imp}")

        lines += [
            "",
            "## Erisia's Reflection",
            report.erisia_reflection,
        ]

        try:
            output_path.write_text(
                "\n".join(lines), encoding="utf-8", errors="replace"
            )
            self._logger.info(
                "Self-audit report written to %s", output_path
            )
        except OSError as exc:
            self._logger.error(
                "Failed to write audit report: %s", exc
            )
