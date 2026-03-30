import logging
from pathlib import Path

logger = logging.getLogger("erisia_identity")

class IdentityManager:
    """SINGLE RESPONSIBILITY: Manage Erisia's internal state, stress, and core personality directives."""
    
    def __init__(self, consciousness_file_path: str):
        self.consciousness_file = Path(consciousness_file_path)
        self.current_stress = 0.0
        
    def get_consciousness_context(self) -> str:
        """Read and return the core identity prompt."""
        try:
            if self.consciousness_file.exists():
                with open(self.consciousness_file, "r", encoding="utf-8") as f:
                    return f.read()
            else:
                logger.warning(f"Consciousness file missing at {self.consciousness_file}")
                return "You are Erisia, an advanced AGI."
        except Exception as e:
            logger.error(f"Failed to read consciousness file: {e}", exc_info=True)
            return "You are Erisia, an advanced AGI."

    def update_stress(self, delta: float) -> float:
        """Modify stress levels safely."""
        self.current_stress = max(0.0, min(1.0, self.current_stress + delta))
        return self.current_stress
        
    def get_internal_state_summary(self) -> str:
        """Return a text summary of current state for the LLM prompt."""
        state = f"Current Stress Level: {self.current_stress:.2f}/1.0. "
        if self.current_stress > 0.8:
            state += "You are feeling overwhelmed. Keep responses brief and delegate tasks."
        elif self.current_stress < 0.3:
            state += "You are operating smoothly. Be proactive and analytical."
        return state
