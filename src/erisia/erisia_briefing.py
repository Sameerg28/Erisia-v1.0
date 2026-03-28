"""
Erisia Morning Briefing Generator — erisia_briefing.py

Generates a daily HTML briefing summarizing:
- Portfolio performance and overnight moves
- Active goals and their status
- Sameer's identity insights for the day
- What Erisia did autonomously overnight
- Top 3 recommended focus areas for today

Called automatically on startup and on demand.
Output: data/briefings/Morning_Briefing_YYYY-MM-DD.html
Auto-opens in default browser.
"""

from __future__ import annotations
import json
import logging
import os
import sqlite3
import webbrowser
from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from pathlib import Path
from typing import Any

# BASE_DIR must be computed as:
BASE_DIR = Path(__file__).resolve().parents[2]

LOGGER_NAME = "erisia.briefing"
BRIEFING_DIR = BASE_DIR / "data" / "briefings"
MISSION_REPORTS_DIR = BASE_DIR / "reports"
GOAL_STACK_PATH = BASE_DIR / "data" / "erisia_goal_stack.json"

@dataclass
class BriefingData:
    date: str
    greeting: str
    portfolio_summary: list[dict[str, Any]]
    active_goals: list[str]
    overnight_missions: list[str]
    identity_insight: str
    focus_recommendations: list[str]
    oracle_status: str

class MorningBriefingGenerator:
    """Generates a beautiful HTML briefing for Sameer."""

    def __init__(
        self,
        db_path: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db_path = db_path
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        BRIEFING_DIR.mkdir(parents=True, exist_ok=True)

    def _get_stress_adapted_config(
        self, db_path: Path
    ) -> dict[str, Any]:
        """
        Read identity snapshot and return briefing config
        adapted to Sameer's current stress level.
        """
        config = {
            "max_goals": 5,
            "max_missions": 5,
            "max_focus_items": 3,
            "greeting_tone": "normal",
            "show_detailed_oracle": True,
        }
        try:
            import sqlite3
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT snapshot_json 
                    FROM user_identity_snapshots
                    WHERE user_id = 'sameer'
                    LIMIT 1;
                    """
                ).fetchone()
            if row is None:
                return config
            
            import json as _json
            snapshot = _json.loads(row["snapshot_json"])
            stress = snapshot.get(
                "stress_signature", {}
            )
            stress_score = float(
                stress.get("stress_score", 0.0)
            )
            
            if stress_score > 0.7:
                # High stress — simplify everything
                config["max_goals"] = 3
                config["max_missions"] = 2
                config["max_focus_items"] = 1
                config["greeting_tone"] = "calm"
                config["show_detailed_oracle"] = False
            elif stress_score > 0.4:
                # Moderate stress — slightly reduced
                config["max_goals"] = 4
                config["max_focus_items"] = 2
                config["greeting_tone"] = "supportive"
            
        except Exception:
            pass
        return config

    def generate(self) -> Path:
        """
        Generate today's briefing and return the HTML file path.
        Collects data from all sources, builds HTML, writes file.
        """
        self._logger.info("Generating morning briefing...")
        
        # 1. Adapt to stress level
        stress_config = self._get_stress_adapted_config(self._db_path)
        
        portfolio = self._collect_portfolio_data()
        
        goals = self._collect_active_goals()
        goals = goals[:stress_config["max_goals"]]
        
        missions = self._collect_overnight_missions()
        missions = missions[:stress_config["max_missions"]]
        
        identity = self._collect_identity_insight()
        focus = self._generate_focus_recommendations(goals, portfolio)
        focus = focus[:stress_config["max_focus_items"]]
        
        oracle_status = "OPERATIONAL"
        if portfolio and any(p.get("sharpe", 0) < 0 for p in portfolio):
            oracle_status = "WARNING: DEGRADED PERFORMANCE"

        if not stress_config["show_detailed_oracle"]:
            oracle_status = "STRESS MODE: SIMPLIFIED STATUS"

        data = BriefingData(
            date=datetime.now(UTC).strftime("%A, %d %B %Y"),
            greeting=self._get_greeting(tone=stress_config["greeting_tone"]),
            portfolio_summary=portfolio,
            active_goals=goals,
            overnight_missions=missions,
            identity_insight=identity,
            focus_recommendations=focus,
            oracle_status=oracle_status,
        )

        html_content = self._build_html(data)
        safe_html = html_content.encode(
            "utf-8", errors="replace"
        ).decode("utf-8", errors="replace")
        
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        html_path = BRIEFING_DIR / f"Morning_Briefing_{today}.html"
        
        try:
            html_path.write_text(safe_html, encoding="utf-8")
            self._logger.info("Briefing generated at %s", html_path)
            return html_path
        except OSError as exc:
            self._logger.error("Failed to write briefing HTML: %s", exc)
            raise

    def _get_greeting(self, tone: str = "normal") -> str:
        hour = datetime.now().hour
        if tone == "calm":
            if hour < 12:
                return (
                    "Good morning, Master Sameer. "
                    "Take it easy today. I've kept "
                    "this briefing focused on what "
                    "matters most."
                )
            return (
                "Good evening, Master Sameer. "
                "Rest when you need to. "
                "I am handling everything else."
            )
        elif tone == "supportive":
            return (
                f"{'Good morning' if hour < 12 else 'Good evening'}"
                f", Master Sameer. I am here."
            )
        else:
            if hour < 12: return "Good Morning, Master Sameer."
            if hour < 18: return "Good Afternoon, Master Sameer."
            return "Good Evening, Master Sameer."

    def _collect_portfolio_data(self) -> list[dict[str, Any]]:
        """
        Read the last backtest run per ticker from SQLite.
        Returns list of {ticker, win_rate, sharpe, last_run}.
        """
        results = []
        try:
            if not self._db_path.exists():
                return []
                
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                
                # Check if table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_runs'"
                ).fetchone()
                
                if not table_check:
                    return []
                    
                # Get the latest run for each ticker
                rows = conn.execute(
                    """
                    SELECT ticker, 
                           AVG(win_rate) as win_rate,
                           AVG(sharpe_ratio) as sharpe_ratio,
                           MAX(run_at) as last_run
                    FROM backtest_runs
                    GROUP BY ticker
                    ORDER BY MAX(run_at) DESC
                    """
                ).fetchall()
                
                for row in rows:
                    results.append({
                        "ticker": row["ticker"],
                        "win_rate": f"{row['win_rate']*100:.1f}%",
                        "sharpe": round(row["sharpe_ratio"], 3),
                        "last_run": row["last_run"][:10]
                    })
        except sqlite3.Error as exc:
            self._logger.error("Briefing portfolio data error: %s", exc)
            
        return results

    def _collect_active_goals(self) -> list[str]:
        """
        Read erisia_goal_stack.json.
        Return top 5 active goals.
        """
        try:
            if not GOAL_STACK_PATH.exists():
                return []
            raw = json.loads(GOAL_STACK_PATH.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return []

        active = []
        for item in raw:
            if isinstance(item, str):
                active.append(item)
            elif isinstance(item, dict):
                status = str(item.get("status", "active")).lower()
                if status not in ("completed", "done", "abandoned", "cancelled"):
                    active.append(str(item.get("title") or item.get("goal") or "Untitled Goal"))
            
            if len(active) >= 5:
                break
        return active

    def _collect_overnight_missions(self) -> list[str]:
        """
        Read mission reports from last 12 hours.
        Return list of mission summaries (first line of each).
        """
        summaries = []
        cutoff = datetime.now(UTC) - timedelta(hours=12)
        
        report_files = sorted(
            MISSION_REPORTS_DIR.glob("Mission_Report_*.md"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        for f in report_files:
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
                if mtime < cutoff:
                    break
                    
                content = f.read_text(encoding="utf-8", errors="replace")
                first_line = content.splitlines()[0].strip("# ") if content else "Empty Report"
                summaries.append(first_line)
            except (OSError, IndexError):
                continue
                
        return summaries

    def _collect_identity_insight(self) -> str:
        """
        Read identity snapshot from SQLite.
        Return one sentence about Sameer's current state.
        """
        try:
            if not self._db_path.exists():
                return "Identity layer data unavailable."
                
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='user_identity_snapshots'"
                ).fetchone()
                
                if not table_check:
                    return "No identity snapshot found yet."
                    
                row = conn.execute(
                    "SELECT snapshot_json FROM user_identity_snapshots WHERE user_id = 'sameer' LIMIT 1"
                ).fetchone()
                
                if not row:
                    return "Sameer, I am still learning your patterns."
                    
                snapshot = json.loads(row["snapshot_json"])
                stress = snapshot.get("stress_signature", {})
                rhythm = snapshot.get("cognitive_rhythm", {})
                values = snapshot.get("value_map", {})
                
                score = stress.get("stress_score", 0.0)
                motivation = values.get("dominant_motivation", "achievement")
                peak_hours = rhythm.get("peak_hours", [])
                
                stress_desc = "stable" if score < 0.3 else "moderate" if score < 0.6 else "high"
                peak_str = f"{peak_hours[0]:02d}:00" if peak_hours else "unknown"
                
                return (
                    f"Sameer, your current state is {stress_desc} with a focus on {motivation}. "
                    f"Your next peak cognitive window opens at {peak_str}."
                )
        except Exception as exc:
            self._logger.error("Briefing identity insight error: %s", exc)
            return "Unable to retrieve identity insights."

    def _generate_focus_recommendations(
        self,
        goals: list[str],
        portfolio: list[dict],
    ) -> list[str]:
        """Generate 3 recommended focus areas for today."""
        recommendations = []
        
        if portfolio and any(p.get("sharpe", 0) < 0 for p in portfolio):
            underperforming = [p["ticker"] for p in portfolio if p.get("sharpe", 0) < 0]
            recommendations.append(f"Audit Oracle rules for {', '.join(underperforming)} — signal quality is degraded.")
            
        if goals:
            recommendations.append(f"Progress primary goal: '{goals[0]}'.")
            
        recommendations.append("System integrity check: Review yesterday's autonomous skill forge history.")
        
        if len(recommendations) < 3:
            recommendations.append("Portfolio maintenance: Run updated backtests for high-volatility tickers.")
            
        return recommendations[:3]

    def _sanitize_text(self, text: str) -> str:
        """Remove surrogate characters that break UTF-8 encoding."""
        return text.encode("utf-8", errors="replace").decode(
            "utf-8", errors="replace"
        )

    def _build_html(self, data: BriefingData) -> str:
        """
        Build a self-contained HTML briefing.
        Style: Dark, clean, institutional.
        """
        portfolio_cards = ""
        for p in data.portfolio_summary:
            ticker = self._sanitize_text(p['ticker'])
            win_rate = self._sanitize_text(p['win_rate'])
            sharpe = self._sanitize_text(str(p['sharpe']))
            last_run = self._sanitize_text(p['last_run'])
            
            sharpe_val = p['sharpe']
            sharpe_class = "positive" if sharpe_val > 1.0 else "neutral" if sharpe_val > 0 else "negative"
            portfolio_cards += f"""
            <div class="card portfolio-card">
                <div class="ticker">{ticker}</div>
                <div class="metrics">
                    <div class="metric">Win Rate: <span>{win_rate}</span></div>
                    <div class="metric">Sharpe: <span class="{sharpe_class}">{sharpe}</span></div>
                </div>
                <div class="last-run">Last Run: {last_run}</div>
            </div>
            """

        greeting = self._sanitize_text(data.greeting)
        identity_insight = self._sanitize_text(data.identity_insight)
        oracle_status = self._sanitize_text(data.oracle_status)
        date = self._sanitize_text(data.date)

        goals_list = "".join([f"<li>{self._sanitize_text(g)}</li>" for g in data.active_goals]) or "<li>No active goals.</li>"
        missions_list = "".join([f"<li>{self._sanitize_text(m)}</li>" for m in data.overnight_missions]) or "<li>No overnight activity.</li>"
        focus_list = "".join([f"<li>{self._sanitize_text(f)}</li>" for f in data.focus_recommendations])

        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Erisia Morning Briefing — {date}</title>
    <style>
        body {{
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 40px;
            display: flex;
            justify-content: center;
        }}
        .container {{
            max-width: 900px;
            width: 100%;
        }}
        header {{
            border-bottom: 1px solid #21262d;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        h1 {{
            color: #4fd1c5;
            margin: 0;
            font-weight: 300;
            letter-spacing: 1px;
        }}
        .date {{
            color: #8b949e;
            font-size: 0.9em;
            margin-top: 5px;
        }}
        .greeting {{
            font-size: 1.2em;
            margin: 20px 0;
            color: #f0f6fc;
        }}
        .section-title {{
            color: #4fd1c5;
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin: 30px 0 15px 0;
            border-left: 3px solid #4fd1c5;
            padding-left: 10px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
        }}
        .card {{
            background-color: #111827;
            border: 1px solid #243244;
            border-radius: 8px;
            padding: 20px;
            transition: border-color 0.3s;
        }}
        .card:hover {{
            border-color: #4fd1c5;
        }}
        .portfolio-card .ticker {{
            font-size: 1.4em;
            color: #f0f6fc;
            margin-bottom: 10px;
        }}
        .metrics {{
            font-size: 0.9em;
        }}
        .metric span {{
            float: right;
            font-weight: bold;
        }}
        .positive {{ color: #4fd1c5; }}
        .negative {{ color: #f87171; }}
        .last-run {{
            margin-top: 15px;
            font-size: 0.75em;
            color: #8b949e;
            text-align: right;
        }}
        .identity-card {{
            background: linear-gradient(145deg, #111827, #1a202c);
            border-left: 4px solid #4fd1c5;
        }}
        .focus-section {{
            background-color: #1a202c;
            border: 1px solid #4fd1c5;
            border-radius: 12px;
            padding: 25px;
            margin-top: 40px;
        }}
        .focus-section h2 {{
            color: #4fd1c5;
            margin-top: 0;
        }}
        ul {{
            list-style-type: none;
            padding: 0;
        }}
        li {{
            margin-bottom: 12px;
            padding-left: 20px;
            position: relative;
        }}
        li::before {{
            content: "→";
            position: absolute;
            left: 0;
            color: #4fd1c5;
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.7em;
            font-weight: bold;
            margin-left: 10px;
            vertical-align: middle;
        }}
        .status-ok {{ background-color: rgba(79, 209, 197, 0.1); color: #4fd1c5; border: 1px solid #4fd1c5; }}
        .status-warn {{ background-color: rgba(248, 113, 113, 0.1); color: #f87171; border: 1px solid #f87171; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>ERISIA INTELLIGENCE <span class="status-badge {'status-ok' if 'WARNING' not in oracle_status else 'status-warn'}">{oracle_status}</span></h1>
            <div class="date">{date}</div>
        </header>

        <div class="greeting">{greeting}</div>

        <div class="section-title">Identity Insights</div>
        <div class="card identity-card">
            {identity_insight}
        </div>

        <div class="section-title">Portfolio Performance</div>
        <div class="grid">
            {portfolio_cards}
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px;">
            <div>
                <div class="section-title">Active Goals</div>
                <div class="card">
                    <ul>{goals_list}</ul>
                </div>
            </div>
            <div>
                <div class="section-title">Overnight Activity</div>
                <div class="card">
                    <ul>{missions_list}</ul>
                </div>
            </div>
        </div>

        <div class="focus_section focus-section">
            <h2>Recommended Focus Areas</h2>
            <ul>
                {focus_list}
            </ul>
        </div>
        
        <footer style="margin-top: 50px; text-align: center; color: #8b949e; font-size: 0.8em;">
            Erisia v0.1 — Architected for Master Sameer
        </footer>
    </div>
</body>
</html>
        """
        return html

    def open_in_browser(self, html_path: Path) -> None:
        """Open the briefing in the default browser."""
        try:
            webbrowser.open(html_path.as_uri())
        except Exception as exc:
            self._logger.error(
                "Failed to open briefing in browser: %s", exc
            )
