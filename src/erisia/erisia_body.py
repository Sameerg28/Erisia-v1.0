import psutil
import subprocess

def check_pc_health():
    """Erisia's Sentinel Protocol: Checks Master Sameer's system vitals."""
    cpu_usage = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    ram_usage = ram.percent
    
    health_report = f"CPU Usage: {cpu_usage}%. RAM Usage: {ram_usage}%."
    
    # The Guardian Trigger for an 8GB RAM system
    if ram_usage > 85:
        health_report += " [CRITICAL WARNING]: Master, your memory is almost full. Your VS Code might crash."
    else:
        health_report += " System is stable. You may continue your engineering, Master."
        
    return health_report

def open_engineering_workspace():
    """Erisia's Nervous System: Opens VS Code for Master Sameer."""
    try:
        # Opens VS Code in the current directory
        subprocess.Popen(["code", "."], shell=False)
        return "Master's workspace has been prepared."
    except Exception as e:
        return f"I failed to open the workspace, Master. Error: {e}"

# --- Quick Test ---
if __name__ == "__main__":
    print(check_pc_health())

