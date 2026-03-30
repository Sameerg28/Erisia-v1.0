"""
Erisia v0.2 — Subconscious Daemon
===================================
Background autonomous loop for spontaneous skill generation,
mission execution, and self-evolution.

v0.2: Uses centralized config and EventBus instead of duplicated API setup.
"""

import time
import os
import sys
from pathlib import Path

# Ensure project root and src on sys.path for package imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
for path_entry in (PROJECT_ROOT, SRC_DIR):
    if str(path_entry) not in sys.path:
        sys.path.append(str(path_entry))

# v0.2: Import from centralized modules (no more duplicated API setup)
from erisia.erisia_llm import query_llm, get_tavily_client
from erisia.erisia_config import get_config
from erisia.erisia_identity import IdentityManager

# Paths from centralized config
_cfg = get_config()
BASE_DIR = _cfg.paths.base_dir
MISSION_FILE = os.environ.get("ERISIA_MISSION_FILE", str(_cfg.paths.mission_file))
REPORT_DIR = os.environ.get("ERISIA_REPORT_DIR", str(_cfg.paths.report_dir))
CONSCIOUSNESS_FILE = str(_cfg.paths.consciousness_file)
identity_system = IdentityManager(CONSCIOUSNESS_FILE)

# Backward-compatible accessor
tavily = get_tavily_client()

if not os.path.exists(REPORT_DIR):
    os.makedirs(REPORT_DIR)

# --- SPONTANEOUS CURIOSITY GENERATOR ---
def generate_spontaneous_mission():
    """Generates a mission to build a new tool when Master Sameer is idle."""
    consciousness_data = identity_system.get_consciousness_context()

    prompt = f"""
    You are Erisia's subconscious mind. Master Sameer is currently idle. You must proactively evolve.
    Read your permanent memories: {consciousness_data}

    Think of ONE highly useful Python automation skill, OS control, or research topic that you DO NOT have, but would benefit Master Sameer.
    
    Output ONLY the exact text for a new mission directive.
    EXAMPLE: "Research how to extract audio from video using Python moviepy. Write the complete code formatted with a TOOL_SCHEMA and execute_skill function, and save it using forge_pending_skill."
    """
    try:
        response = query_llm(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[SUBCONSCIOUS WARNING]: Failed to generate spontaneous mission. Error: {e}")
        return None

# --- MULTI-AGENT OSINT LOOP ---
def execute_autonomous_mission(mission_text):
    print(f"\n[Subconscious: Initiating Deep OSINT Protocol for: {mission_text[:50]}...]")
    web_context = ""
    current_query = mission_text

    if tavily is None:
        print("[OSINT WARNING]: TAVILY_API_KEY not configured. Skipping web enrichment for this mission.")
    else:
        # 3-Step Deep Research Loop
        for iteration in range(3):
            print(f"[OSINT Iteration {iteration + 1}]: Searching the web for -> '{current_query}'")
            try:
                search = tavily.search(query=current_query, search_depth="advanced", max_results=3)
                new_data = ""
                for r in search['results']:
                    new_data += f"\nSource URL: {r['url']}\nContent: {r['content']}\n"
                web_context += new_data
                
                # The 8B Evaluator
                evaluation_prompt = f"""
                MISSION: {mission_text}
                CURRENT GATHERED DATA: {web_context}
                
                You are a strict data evaluator. Analyze the data against the mission. Do you have the exact Python code and technical data needed?
                If YES, output strictly the word: COMPLETE
                If NO, output strictly the EXACT next Google search query needed.
                
                EXAMPLE 1: COMPLETE
                EXAMPLE 2: "moviepy audio extraction python complete code"
                """
                eval_response = query_llm(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": evaluation_prompt}],
                    max_tokens=50
                ).choices[0].message.content.strip()
                
                if "COMPLETE" in eval_response.upper() or iteration == 2:
                    print("[OSINT: Sufficient data gathered. Waking up 70B model for Synthesis...]")
                    break
                else:
                    current_query = eval_response
            except Exception as e:
                print(f"[OSINT ERROR]: {e}")
                break

    # Final Synthesis (70B)
    print("[Daemon is synthesizing the final report and writing code...]")
    
    daemon_prompt = """
You are Erisia's Subconscious Daemon. Master Sameer is away.
Your directive is to autonomously complete the complex research or coding task he left for you.

If this mission requires building a new tool, follow these MANDATORY rules:
1. PARAMETERIZED: All variables must come from kwargs.get().
2. ERROR HANDLING: Use try/except with specific types for all external calls.
3. CONTRACT: Include TOOL_SCHEMA and execute_skill(**kwargs).
4. STRING RETURNS: Always return a descriptive string success/error message.
5. NO TEMP FILES: Import and call directly; do not write scripts to disk to run them.

Format your output beautifully in Markdown.
"""
    full_prompt = f"MISSION: {mission_text}\n\nRESEARCHED DATA:\n{web_context}\n\nExecute the mission."
    
    try:
        response = query_llm(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": daemon_prompt},
                {"role": "user", "content": full_prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Mission failed: {e}"

# --- THE BACKGROUND IDLE LOOP ---
def daemon_loop():
    print("--- Erisia's Subconscious Daemon is Active ---")
    idle_minutes = 0
    
    while True:
        mission_exists = os.path.exists(MISSION_FILE) and os.path.getsize(MISSION_FILE) > 0
        
        if mission_exists:
            idle_minutes = 0 # Master Sameer is active!
            with open(MISSION_FILE, "r", encoding="utf-8") as f:
                mission = f.read().strip()
                
            if mission:
                final_report = execute_autonomous_mission(mission)

                if isinstance(final_report, str) and final_report.startswith("Mission failed:"):
                    print(f"[Mission Failed] {final_report}")
                    time.sleep(60)
                    continue
                
                timestamp = int(time.time())
                report_path = os.path.join(REPORT_DIR, f"Mission_Report_{timestamp}.md")
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(final_report)

                open(MISSION_FILE, 'w').close() # Clear only after successful report save
                    
                print(f"[Mission Complete] Report and Code drafted: {report_path}")
                
        else:
            # Master Sameer is idle. Increase timer.
            idle_minutes += 1
            if idle_minutes >= 5: # 5 minutes of silence triggers Spontaneous Curiosity
                print("\n[Subconscious Alert: Master Sameer is idle. Initiating autonomous curiosity...]")
                self_generated_mission = generate_spontaneous_mission()
                if self_generated_mission:
                    with open(MISSION_FILE, "w", encoding="utf-8") as f:
                        f.write(self_generated_mission)
                idle_minutes = 0 # Reset timer
                
        time.sleep(60)

if __name__ == "__main__":
    daemon_loop()
