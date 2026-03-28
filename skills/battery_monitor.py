import psutil

def get_battery_info():
    battery = psutil.sensors_battery()
    if battery is None:
        return "No battery found"
    else:
        return {
            "percentage": battery.percent,
            "power_plugged": battery.power_plugged
        }

def execute_skill(**kwargs):
    battery_info = get_battery_info()
    if isinstance(battery_info, dict):
        return f"Battery percentage: {battery_info['percentage']}%\nPower plugged: {battery_info['power_plugged']}"
    else:
        return battery_info

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "battery_monitor",
        "description": "Fetch the exact battery percentage of the laptop using the psutil library.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}