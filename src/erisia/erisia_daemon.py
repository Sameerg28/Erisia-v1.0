import logging
import time

logger = logging.getLogger("erisia_daemon")

class DaemonManager:
    """SINGLE RESPONSIBILITY: Manage Erisia's background missions and active task queues."""
    
    def __init__(self):
        self.active_mission_name = None
        self.active_mission_queue = []
        self.daemon_active = False
        
    def start_mission(self, mission_name: str, steps: list) -> str:
        """Initialize a new background mission."""
        self.active_mission_name = mission_name
        self.active_mission_queue = steps
        return f"[MISSION INITIALIZED]: {mission_name} with {len(steps)} steps."
        
    def mark_step_complete(self, step_summary: str) -> str:
        """Mark the current step complete and pop it from the queue."""
        if not self.active_mission_queue:
            self.active_mission_name = None
            return "[MISSION COMPLETE]: No active steps remain."
            
        completed_step = self.active_mission_queue.pop(0)
        status = f"[STEP COMPLETE]: {completed_step}\n[SUMMARY]: {step_summary}\n"
        
        if self.active_mission_queue:
            status += f"[NEXT STEP]: {self.active_mission_queue[0]}"
        else:
            self.active_mission_name = None
            status += "[MISSION FULLY ACCOMPLISHED]"
            
        return status
        
    def get_mission_status(self) -> str:
        """Return the current mission state for the LLM prompt."""
        if not self.active_mission_queue:
            return "No active background missions."
        return f"ACTIVE MISSION: {self.active_mission_name} | PENDING STEPS: {len(self.active_mission_queue)} | CURRENT: {self.active_mission_queue[0]}"
