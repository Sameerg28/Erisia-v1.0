# Erisia Skill - execute_skill v8
# Improved from v7: autonomous re-forge routed to improvement
# Location before improvement: pending

TOOL_SCHEMA = {
    "location": {"type": "object"},
    "current": {"type": "object"}
}

def execute_skill(**kwargs):
    """
    This function executes the skill to reject the check_weather.py script based on the given weather data.
    """
    try:
        location = kwargs.get("location")
        current = kwargs.get("current")
        
        # Check if the location is valid
        if not location or not isinstance(location, dict):
            return "Error: Invalid location data"
        
        # Check if the current weather data is valid
        if not current or not isinstance(current, dict):
            return "Error: Invalid current weather data"
        
        # Extract the temperature and condition from the current weather data
        temp_c = current.get("temp_c")
        condition = current.get("condition")
        
        # Check if the temperature and condition are valid
        if temp_c is None or condition is None:
            return "Error: Invalid temperature or condition data"
        
        # Reject the check_weather.py script if the temperature is above 20°C and the condition is sunny
        if temp_c > 20 and condition.get("text") == "Sunny":
            return "Rejecting check_weather.py script due to high temperature and sunny condition"
        else:
            return "check_weather.py script is valid"
    
    except Exception as e:
        return f"Error: {str(e)}"

# Example usage:
weather_data = {
    'location': {'name': "N'amtic", 'region': 'Chiapas', 'country': 'Mexico', 'lat': 16.9634, 'lon': -92.5835, 'tz_id': 'America/Mexico_City', 'localtime_epoch': 1774966072, 'localtime': '2026-03-31 08:07'},
    'current': {'last_updated_epoch': 1774965600, 'last_updated': '2026-03-31 08:00', 'temp_c': 15.7, 'temp_f': 60.3, 'is_day': 1, 'condition': {'text': 'Sunny', 'icon': '//cdn.weatherapi.com/weather/64x64/day/113.png', 'code': 1000}, 'wind_mph': 2.2, 'wind_kph': 3.6, 'wind_degree': 124, 'wind_dir': 'SE', 'pressure_mb': 1019.0, 'pressure_in': 30.08, 'precip_mm': 0.0, 'precip_in': 0.0, 'humidity': 70, 'cloud': 12, 'feelslike_c': 15.9, 'feelslike_f': 60.5, 'windchill_c': 15.9, 'windchill_f': 60.5, 'heatindex_c': 15.7, 'heatindex_f': 60.3, 'dewpoint_c': 9.7, 'dewpoint_f': 49.4, 'vis_km': 10.0, 'vis_miles': 6.0, 'uv': 2.2, 'gust_mph': 6.7, 'gust_kph': 10.8}
}

result = execute_skill(**weather_data)
print(result)